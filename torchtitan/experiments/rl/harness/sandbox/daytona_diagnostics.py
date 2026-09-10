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
_COLLECTION_TIMEOUT_SEC = 10
_RESPONSE_LIMIT_BYTES = 4 * 1024 * 1024


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
                base = (
                    f"{analytics.rstrip('/')}/organization/{organization}/sandbox/{sandbox_id}"
                    if analytics
                    else f"{api}/sandbox/{sandbox_id}"
                )
                window = {"from": started_at, "to": now}
                await asyncio.gather(
                    get(
                        "audit",
                        f"{api}/audit/organizations/{organization}",
                        {"targetId[eq]": sandbox.id, "limit": 100, **window},
                    ),
                    get("logs", base + "/telemetry/logs", {"limit": 1000, **window}),
                    get(
                        "traces", base + "/telemetry/traces", {"limit": 1000, **window}
                    ),
                    get("metrics", base + "/telemetry/metrics", window),
                )
                record["status"] = "finished"
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
