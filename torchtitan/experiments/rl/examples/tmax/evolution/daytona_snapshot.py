#!/usr/bin/env python3
"""Snapshot what the Daytona account is actually holding, right now.

Answers "how much CPU/memory/disk are our sandboxes really using", which the
config files cannot: per-sandbox resources come from the DATA ROW first and the
TT_DAYTONA_* env only where a row declares nothing, so neither source alone
tells you the live allocation.

Do NOT reach for the REST endpoint to do this. `GET /api/sandbox` ignores its
`page` parameter and returns the same first window on every page, so a paging
loop silently multiplies one 200-row window into any total you ask for. The SDK's
`Daytona().list()` is the only enumeration here that is actually complete.

Usage (from the laptop):  scripts/della.sh -p scripts/daytona_snapshot.py
                          scripts/della.sh -p scripts/daytona_snapshot.py --json
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import datetime, timezone

from daytona import Daytona

LIVE_STATES = ("STARTED", "STARTING", "CREATING", "RESTORING", "PULLING")


def _live(state: str) -> bool:
    return any(s in state for s in LIVE_STATES)


def main() -> None:
    as_json = "--json" in sys.argv
    rows = []
    for sb in Daytona().list():
        labels = getattr(sb, "labels", None) or {}
        rows.append({
            "id": getattr(sb, "id", ""),
            "owner": labels.get("owner", "-") if isinstance(labels, dict) else "-",
            "state": str(getattr(sb, "state", "")).upper().rsplit(".", 1)[-1],
            "cpu": getattr(sb, "cpu", None) or 0,
            "mem": getattr(sb, "memory", None) or 0,
            "disk": getattr(sb, "disk", None) or 0,
            "created_at": str(getattr(sb, "created_at", "")),
        })

    by_owner_state = collections.Counter()
    live_shape = collections.Counter()
    totals = collections.Counter()
    for r in rows:
        by_owner_state[(r["owner"], r["state"])] += 1
        if _live(r["state"]):
            live_shape[(r["owner"], r["cpu"], r["mem"], r["disk"])] += 1
            totals["n"] += 1
            totals["cpu"] += r["cpu"]
            totals["mem"] += r["mem"]
            totals["disk"] += r["disk"]

    out = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "sandboxes_total": len(rows),
        "live_totals": {
            "sandboxes": totals["n"], "vcpu": totals["cpu"],
            "mem_gib": totals["mem"], "disk_gib": totals["disk"],
        },
        "by_owner_state": {f"{o}/{s}": n for (o, s), n in by_owner_state.most_common()},
        "live_by_shape": {
            f"{o} {c}c/{m}g/{d}g": n
            for (o, c, m, d), n in live_shape.most_common()
        },
    }
    if as_json:
        print(json.dumps(out, indent=2))
        return

    print(f"[{out['taken_at']}] {out['sandboxes_total']} sandboxes visible to this key")
    lt = out["live_totals"]
    print(f"LIVE: {lt['sandboxes']} sandboxes = {lt['vcpu']} vCPU, "
          f"{lt['mem_gib']} GiB mem, {lt['disk_gib']} GiB disk")
    print("--- owner / state ---")
    for k, n in out["by_owner_state"].items():
        print(f"  {k}: {n}")
    print("--- live shapes (cpu/mem/disk) ---")
    for k, n in out["live_by_shape"].items():
        print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
