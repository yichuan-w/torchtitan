#!/usr/bin/env python3
"""Sweep OUR failed Daytona sandboxes, preserving their evidence first.

BUILD_FAILED / ERROR sandboxes never reach Stopped, so neither
auto_delete_interval nor ephemeral delete-on-stop ever fires for them; they sit
in the console holding disk quota (63 of them once filled a whole tier and
blocked every create for 13h -- see sweep_orphans.py). This sweeper:

  1. lists the account's sandboxes and keeps only OURS (label owner= matches
     SWEEP_LABELS; the harness stamps {"owner": TT_DAYTONA_LABEL} on every
     sandbox precisely so cleanup can target only our tenant),
  2. keeps only dead states (BUILD_FAILED / ERROR) -- no age-based deletion,
     live agentic rollouts legitimately run 40+ min (that rule made
     sweep_orphans.py unsafe for the training account),
  3. dumps each victim's full model (error_reason, build_info, labels, disk,
     timestamps) to SWEEP_EVIDENCE_DIR/<utc-day>/<id>.json BEFORE deleting --
     the build error text is what the Dockerfile-repair queue needs,
  4. deletes it, logging one line per sandbox.

Safe to run any time, idempotent. Deployed as a systemd --user timer on
della-tridao (daytona-sweep.timer, 10 min).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from daytona import Daytona

DEAD_STATES = ("BUILD_FAILED", "ERROR")
# Age backstop for sandboxes the cloud reaper misses. The normal chain
# (auto_stop_interval=10 idle-stop, auto_delete_interval=0 delete-on-stop)
# reaps orphans within ~10-15 min; this catches the residue that stays
# STARTED anyway (observed 08-29: one from 08-25 and one from 08-28 still
# running). The old age rule that made sweep_orphans.py unsafe used a TTL
# below a live rollout's lifetime; today the legit maximum is bounded by
# SANDBOX boot allowance (2700s) + rollout guard (budget 2400 + 900), i.e.
# ~100 min, so 180 min cannot touch a live rollout. 0 disables.
STALE_STARTED_MIN = int(os.environ.get("SWEEP_STALE_STARTED_MIN", "180"))
OUR_OWNERS = set(
    filter(None, os.environ.get(
        "SWEEP_LABELS",
        # Measurement and verification runs boot under their own labels; left
        # out, their BUILD_FAILED sandboxes are swept by nobody.
        "new_titan_swe_r2e,titan_swe_r2e,resource_measure,"
        "resource_measure_tmax,provision_check").split(","))
)
EVIDENCE_DIR = os.environ.get(
    "SWEEP_EVIDENCE_DIR",
    "/scratch/gpfs/TRIDAO/al9080/terminal-rl/logs/sandbox-failures",
)


def _ours(sb) -> bool:
    labels = getattr(sb, "labels", None) or {}
    return isinstance(labels, dict) and labels.get("owner") in OUR_OWNERS


def main() -> None:
    d = Daytona()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    outdir = os.path.join(EVIDENCE_DIR, day)
    os.makedirs(outdir, exist_ok=True)
    seen = ours = swept = failed = 0
    for sb in d.list():
        seen += 1
        state = str(getattr(sb, "state", "")).upper()
        if not _ours(sb):
            continue
        dead = any(s in state for s in DEAD_STATES)
        stale = False
        if not dead and STALE_STARTED_MIN > 0:
            created = getattr(sb, "created_at", None)
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    created = None
            if created is not None:
                age_min = (datetime.now(timezone.utc) - created).total_seconds() / 60
                stale = age_min > STALE_STARTED_MIN
        if not dead and not stale:
            continue
        ours += 1
        sid = getattr(sb, "id", "unknown")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with open(os.path.join(outdir, f"{sid}.json"), "w") as f:
                json.dump(sb.to_dict(), f, indent=2, default=str)
        except Exception as e:  # noqa: BLE001 -- evidence is best-effort
            print(f"[sweep] {stamp} evidence dump failed {sid}: {e}",
                  file=sys.stderr)
        err = str(getattr(sb, "error_reason", "") or "").replace("\n", " ")[:140]
        try:
            d.delete(sb)
            swept += 1
            kind = "dead" if dead else f"stale>{STALE_STARTED_MIN}min"
            print(f"[sweep] {stamp} deleted {sid} state={state} ({kind}) err={err!r}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[sweep] {stamp} delete FAILED {sid}: "
                  f"{type(e).__name__} {str(e)[:100]}", file=sys.stderr)
    print(f"[sweep] {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
          f"done seen={seen} ours-dead={ours} swept={swept} failed={failed}")


if __name__ == "__main__":
    main()
