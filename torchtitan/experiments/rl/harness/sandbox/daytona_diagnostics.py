# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Collect provider evidence before a failed sandbox is deleted."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)
_COLLECTION_TIMEOUT_SEC = 60
_RESPONSE_LIMIT_BYTES = 4 * 1024 * 1024
_PAGE_SIZE = 1000


async def collect_failure_diagnostics(
    client, sandbox, directory: Path, started_at: str, error: BaseException
) -> None:
    """Save bounded, best-effort telemetry without replacing the rollout error."""
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "sandbox_id": sandbox.id,
        "started_at": started_at,
        "collected_at": now,
        "error_type": type(error).__name__,
        "status": "collecting",
        "requests": {},
    }
    path = directory / f"{sandbox.id}.json"

    def save():
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.part")
        temporary.write_text(json.dumps(record, default=str) + "\n")
        temporary.replace(path)

    try:
        save()
        async with asyncio.timeout(_COLLECTION_TIMEOUT_SEC):
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5), trust_env=False
            ) as http:
                headers = dict(client._api_client.default_headers)
                api = client._api_url.rstrip("/")

                async def get(name, url, params=None, *, authenticated=True):
                    entry = {"status": "pending"}
                    previous = record["requests"].get(name, {})
                    if "pages" in previous:
                        entry.update(
                            payload=previous["payload"],
                            pages=previous["pages"],
                            complete=False,
                        )
                    record["requests"][name] = entry
                    try:
                        async with http.get(
                            url,
                            headers=headers if authenticated else {},
                            params=params,
                        ) as response:
                            entry["http_status"] = response.status
                            body = bytearray()
                            async for chunk in response.content.iter_chunked(65536):
                                body.extend(chunk)
                                if len(body) > _RESPONSE_LIMIT_BYTES:
                                    entry["status"] = "response_too_large"
                                    return None
                            payload = json.loads(body)
                            entry["status"] = (
                                "ok" if response.status == 200 else "http_error"
                            )
                            # Discovery replies contain API key/configuration fields.
                            if name not in ("key", "config"):
                                entry["payload"] = payload
                                if isinstance(payload, list) and len(payload) >= 1000:
                                    entry["possibly_truncated"] = True
                            return payload if response.status == 200 else None
                    except Exception as exc:
                        entry.update(
                            status="request_error", error_type=type(exc).__name__
                        )
                        return None
                    finally:
                        save()

                async def pages(name, url, params, fallback=None):
                    items = []
                    page = 1
                    while True:
                        payload = await get(
                            name,
                            url,
                            {
                                **params,
                                "page": page,
                                "offset": (page - 1) * params["limit"],
                            },
                        )
                        entry = record["requests"][name]
                        analytics_required = (
                            entry.get("http_status") == 403
                            and isinstance(entry.get("payload"), dict)
                            and entry["payload"].get("message")
                            == "Telemetry endpoints are disabled when Analytics API is configured"
                        )
                        if fallback and (
                            entry.get("http_status") == 404 or analytics_required
                        ):
                            # Hosted deployments serve telemetry from analytics.
                            url, fallback = fallback, None
                            continue
                        if payload is None:
                            entry["error_response"] = entry.get("payload")
                            entry.update(payload=items, pages=page - 1, complete=False)
                            save()
                            return
                        batch = (
                            payload
                            if isinstance(payload, list)
                            else payload.get("items")
                        )
                        if not isinstance(batch, list):
                            entry.update(
                                status="invalid_response", payload=items, complete=False
                            )
                            save()
                            return
                        if page > 1 and batch and batch == items[-len(batch) :]:
                            entry.update(
                                status="pagination_stalled",
                                payload=items,
                                complete=False,
                            )
                            save()
                            return
                        items.extend(batch)
                        complete = (
                            page >= payload["totalPages"]
                            if isinstance(payload, dict) and "totalPages" in payload
                            else len(batch) < params["limit"]
                        )
                        entry.update(payload=list(items), pages=page, complete=complete)
                        entry.pop("possibly_truncated", None)
                        save()
                        if complete:
                            return
                        page += 1

                async def latest_metrics():
                    entry = {"status": "pending"}
                    record["requests"]["metrics_latest"] = entry
                    try:
                        async with asyncio.timeout(5):
                            metrics = await sandbox.get_metrics_latest()
                        entry.update(status="ok", payload=asdict(metrics))
                    except Exception as exc:
                        entry.update(
                            status="request_error", error_type=type(exc).__name__
                        )
                    finally:
                        save()

                key, config, _ = await asyncio.gather(
                    get("key", api + "/api-keys/current"),
                    get("config", api + "/config", authenticated=False),
                    latest_metrics(),
                )
                organization = (key or {}).get("organizationId")
                if not organization:
                    record["status"] = "organization_unavailable"
                    return
                organization = quote(organization, safe="")
                sandbox_id = quote(sandbox.id, safe="")
                analytics = (config or {}).get("analyticsApiUrl")
                retained_base = (
                    f"{analytics.rstrip('/')}/organization/{organization}/sandbox/{sandbox_id}"
                    if analytics
                    else f"{api}/sandbox/{sandbox_id}"
                )
                base = f"{api}/sandbox/{sandbox_id}"
                window = {"from": started_at, "to": now}
                await asyncio.gather(
                    pages(
                        "audit",
                        f"{api}/audit/organizations/{organization}",
                        {"targetId[eq]": sandbox.id, "limit": 100, **window},
                    ),
                    pages(
                        "logs",
                        base + "/telemetry/logs",
                        {"limit": _PAGE_SIZE, **window},
                        retained_base + "/telemetry/logs" if analytics else None,
                    ),
                    pages(
                        "traces",
                        base + "/telemetry/traces",
                        {"limit": _PAGE_SIZE, **window},
                        retained_base + "/telemetry/traces" if analytics else None,
                    ),
                    get("metrics", retained_base + "/telemetry/metrics", window),
                )
                record["status"] = (
                    "finished"
                    if all(
                        entry.get("status") == "ok" and entry.get("complete", True)
                        for entry in record["requests"].values()
                    )
                    else "partial"
                )
    except TimeoutError:
        record["status"] = "timeout"
    except asyncio.CancelledError:
        record["status"] = "cancelled"
        raise
    except Exception as exc:
        record.update(status="collection_error", collection_error=type(exc).__name__)
    finally:
        try:
            save()
        except Exception as exc:
            logger.warning("Could not save Daytona diagnostics: %s", type(exc).__name__)
