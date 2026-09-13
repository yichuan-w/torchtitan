# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from torchtitan.experiments.rl.harness.agents import claude_code
from torchtitan.experiments.rl.harness.sandbox import daytona_diagnostics as diagnostics
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox


@dataclass
class _Metrics:
    disk_used: int = 42


@pytest.mark.parametrize(
    "mode",
    [
        "partial",
        "timeout",
        "large",
        "pages",
        "stalled",
        "deleted",
        "hosted",
        "auth",
        "page_failure",
        "page_timeout",
    ],
)
def test_provider_capture_preserves_results_and_excludes_discovery_secrets(
    tmp_path, monkeypatch, mode
):
    async def run():
        requests = []
        origin = ""

        async def handle(request):
            requests.append(request.path)
            if request.path == "/api/config":
                assert "Authorization" not in request.headers
                return web.json_response(
                    {"analyticsApiUrl": origin, "secret": "config-secret"}
                )
            assert request.headers["Authorization"] == "Bearer private-key"
            if request.path == "/api/api-keys/current":
                return web.json_response(
                    {"organizationId": "org", "value": "private-key"}
                )
            if request.path.endswith("/logs"):
                if mode == "auth" and requests.count(request.path) == 1:
                    return web.json_response({"message": "Unauthorized"}, status=401)
                if mode == "hosted" and request.path.startswith("/api/sandbox/"):
                    return web.json_response(
                        {
                            "message": "Telemetry endpoints are disabled when Analytics API is configured"
                        },
                        status=403,
                    )
                if mode == "deleted" and request.path.startswith("/api/sandbox/"):
                    return web.json_response({"message": "deleted"}, status=404)
                if mode == "large":
                    return web.json_response(["x" * 4096])
                return web.json_response([{"body": "daemon failure"}])
            if request.path.endswith("/traces"):
                if mode == "timeout":
                    await asyncio.sleep(1)
                if mode in ("pages", "stalled", "page_failure", "page_timeout"):
                    page = int(request.query["page"])
                    assert int(request.query["offset"]) == (page - 1) * 2
                    items = [{"traceId": "a"}, {"traceId": "b"}]
                    if page > 1 and mode == "page_failure":
                        return web.json_response({"message": "unavailable"}, status=503)
                    if page > 1 and mode == "page_timeout":
                        await asyncio.sleep(1)
                    if page > 1 and mode == "pages":
                        items = [{"traceId": "c"}]
                    return web.json_response(items)
                return web.json_response([])
            if request.path.endswith("/metrics"):
                return web.json_response({"message": "unavailable"}, status=503)
            assert request.query["targetId[eq]"] == "sb"
            return web.json_response({"items": [{"action": "create"}]})

        app = web.Application()
        app.router.add_get("/{path:.*}", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        client = SimpleNamespace(
            _api_url=origin + "/api",
            _api_client=SimpleNamespace(
                default_headers={"Authorization": "Bearer private-key"}
            ),
        )
        if mode in ("timeout", "page_timeout"):
            monkeypatch.setattr(diagnostics, "_COLLECTION_TIMEOUT_SEC", 0.1)
        if mode == "large":
            monkeypatch.setattr(diagnostics, "_RESPONSE_LIMIT_BYTES", 1024)
        if mode in ("pages", "stalled", "page_failure", "page_timeout"):
            monkeypatch.setattr(diagnostics, "_PAGE_SIZE", 2)
        try:
            await diagnostics.collect_failure_diagnostics(
                client,
                SimpleNamespace(
                    id="sb", get_metrics_latest=AsyncMock(return_value=_Metrics())
                ),
                tmp_path,
                "2026-09-10T08:00:00Z",
                ValueError("failed"),
            )
        finally:
            await runner.cleanup()
        raw = (tmp_path / "sb.json").read_text()
        assert "private-key" not in raw and "config-secret" not in raw
        record = json.loads(raw)
        assert record["requests"]["metrics_latest"]["payload"]["disk_used"] == 42
        assert record["status"] == (
            "timeout" if mode in ("timeout", "page_timeout") else "partial"
        )
        assert record["requests"]["audit"]["payload"]["items"] == [{"action": "create"}]
        assert record["requests"]["metrics"]["http_status"] == 503
        if mode == "large":
            assert record["requests"]["logs"]["status"] == "response_too_large"
        else:
            assert record["requests"]["logs"]["payload"] == [{"body": "daemon failure"}]
        assert "/api/sandbox/sb/telemetry/logs" in requests
        if mode in ("deleted", "hosted"):
            assert "/organization/org/sandbox/sb/telemetry/logs" in requests
        if mode == "auth":
            assert record["requests"]["logs"]["attempts"] == 2
        if mode == "pages":
            assert record["requests"]["traces"]["payload"] == [
                {"traceId": "a"},
                {"traceId": "b"},
                {"traceId": "c"},
            ]
            assert record["requests"]["traces"]["complete"] is True
        if mode == "stalled":
            assert record["requests"]["traces"]["status"] == "pagination_stalled"
            assert record["requests"]["traces"]["complete"] is False
        if mode in ("page_failure", "page_timeout"):
            assert record["requests"]["traces"]["payload"] == [
                {"traceId": "a"},
                {"traceId": "b"},
            ]
            assert record["requests"]["traces"]["complete"] is False

    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_boot_passes_failure_and_collects_before_delete(tmp_path, monkeypatch, failure):
    order = []
    sandbox = DaytonaSandbox("image", failure_diagnostics_dir=tmp_path)
    sandbox._sb = SimpleNamespace(id="sb")
    sandbox._client = SimpleNamespace(
        delete=AsyncMock(side_effect=lambda _: order.append("delete"))
    )
    monkeypatch.setattr(DaytonaSandbox, "__aenter__", AsyncMock(return_value=sandbox))

    def make(*args, **kwargs):
        assert kwargs["failure_diagnostics_dir"] == tmp_path
        return sandbox

    monkeypatch.setattr(claude_code, "make_sandbox", make)
    monkeypatch.setattr(claude_code, "_BOOT_SEM", None)

    async def collect(*args):
        assert args[-1] is original
        order.append("collect")

    monkeypatch.setattr(diagnostics, "collect_failure_diagnostics", collect)
    original = ValueError("parser failed")

    async def run():
        async with claude_code.boot_agent_sandbox(
            "image", install_claude=False, failure_diagnostics_dir=tmp_path
        ):
            if failure:
                raise original

    if failure:
        with pytest.raises(ValueError) as caught:
            asyncio.run(run())
        assert caught.value is original
    else:
        asyncio.run(run())
    assert order == (["collect", "delete"] if failure else ["delete"])


def test_cancellation_during_capture_still_deletes(tmp_path, monkeypatch):
    sandbox = DaytonaSandbox("image", failure_diagnostics_dir=tmp_path)
    sandbox._sb = SimpleNamespace(id="sb")
    sandbox._client = SimpleNamespace(delete=AsyncMock())
    monkeypatch.setattr(
        diagnostics,
        "collect_failure_diagnostics",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sandbox.__aexit__(ValueError, ValueError("failed"), None))
    sandbox._client.delete.assert_awaited_once_with(sandbox._sb)
