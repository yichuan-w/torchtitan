#!/usr/bin/env python3
"""Delete leaked Daytona sandboxes.

Two leak sources, both observed on 2026-08-12 (63 sandboxes accumulated and
filled the 30GiB tier quota, which failed every subsequent create and stalled
the validator for ~13 hours):

  1. A create() that ends in BUILD_FAILED still registers a sandbox consuming
     quota, but raises — so the validator's `finally: delete(sandbox)` never
     sees an object to delete.
  2. A batch killed by the supervisor's timeout leaves its in-flight sandboxes
     running.

So: delete any sandbox in a terminal/dead state regardless of age, plus any
sandbox older than the age cutoff whatever its state (in-flight ones are
younger). Safe to run while a batch is active.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from daytona import Daytona

DEAD_STATES = ("BUILD_FAILED", "STOPPED", "ERROR", "DESTROYED", "ARCHIVED")
AGE_CUTOFF_S = 1800


def main() -> None:
    d = Daytona()
    now = datetime.now(timezone.utc)
    total = deleted = failed = 0
    for sb in d.list():
        total += 1
        state = str(getattr(sb, "state", "")).upper()
        created = getattr(sb, "created_at", None)
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                created = None
        age = (now - created).total_seconds() if created else 9e9
        if not (any(s in state for s in DEAD_STATES) or age > AGE_CUTOFF_S):
            continue
        try:
            d.delete(sb)
            deleted += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"sweep: delete failed ({type(e).__name__}): {str(e)[:80]}",
                  file=sys.stderr)
    print(f"sweep: {total} seen, {deleted} deleted, {failed} failed")


if __name__ == "__main__":
    main()
