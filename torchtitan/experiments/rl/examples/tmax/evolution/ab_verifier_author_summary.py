#!/usr/bin/env python3
"""Read one ab_verifier_author.sh record and print the two modes side by side.

Per signal and mode: what the round decided, how many sessions it took, wall
clock per session and in total, how far the rewrite sits above its input
revision by the size rule, and what the names audit had to say. Nothing here
is a verdict on the flag; the numbers are what the flag costs and what it
changes, for the person deciding the default.

    python3 ab_verifier_author_summary.py <dev-root>/logs/ab_verifier_author--<stamp>

The record holds, per mode, the ledger lines the round appended and a copy
of every replayed task's directory as the round left it; a ledger line names
its rewrite, rewrite.json names its sessions, and the task's lineage says
whether the result was folded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout  # noqa: E402
import task_size as ts  # noqa: E402

MODES = ("same", "blind")
VERIFIERS = ("tests/test_state.py", "tests/test.sh")


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _size_delta(task: layout.TaskDir, rw: layout.RewriteDir, meta: dict) -> dict | None:
    """Lines and asserts the rewrite added over its input revision. An accepted
    rewrite's package became r<result_rev>; a rejected or failed one keeps
    package/. A rewrite that never wrote a package has no size."""
    before = task.rev(meta.get("input_rev", 0))
    after = task.rev(meta["result_rev"]) if meta.get("result_rev") is not None else rw.package
    vrel = next((v for v in VERIFIERS if (after / v).exists()), None)
    if vrel is None or not before.exists():
        return None
    a, b = ts.size_of_package(after, vrel), ts.size_of_package(before, vrel)
    return {"lines": a["solution_lines"] - b["solution_lines"],
            "asserts": a["verifier_asserts"] - b["verifier_asserts"]}


def _row(mode_dir: Path, entry: dict) -> dict:
    """One ledger line -> what its rewrite did. A deferred or junk line has no
    rewrite; its outcome stands in for the status."""
    task = layout.TaskDir(mode_dir / "tasks" / layout.safe(entry["task"]))
    rel = entry.get("rewrite")  # tasks/<task>/rewrites/<stamp>--<job>
    row = {"status": entry.get("outcome"), "job": entry.get("direction"), "sessions": 0,
           "session_secs": [], "total_secs": 0, "repairs": 0, "size": None,
           "advice": None, "folded": False}
    if entry.get("outcome") != "handled" or not rel:
        return row
    rw = layout.RewriteDir(task.rewrites / Path(rel).name)
    meta = _json(rw.meta)
    secs = []
    repairs = 0
    for s in rw.session_dirs():
        sm = _json(s.meta)
        done = sm.get("started") and sm.get("finished")  # a session still running has no length
        secs.append(round(layout.parse_stamp(sm["finished"]) - layout.parse_stamp(sm["started"]))
                    if done else None)
        repairs += sm.get("kind") == "repair"
    # The lineage names a rewrite relative to the task directory.
    mine = f"rewrites/{rw.path.name}"
    folded = any(e.get("event") == "fold" and e.get("rewrite") == mine
                 for e in layout.read_jsonl(task.lineage))
    row.update(status=meta.get("status", row["status"]), job=meta.get("job", row["job"]),
               sessions=len(secs), session_secs=secs, total_secs=sum(s or 0 for s in secs),
               repairs=repairs, size=_size_delta(task, rw, meta) if meta else None,
               advice=(meta.get("verdicts") or {}).get("dark_literals"), folded=folded)
    return row


def summarise(ab: Path) -> dict:
    out = {}
    for mode in MODES:
        d = ab / mode
        timing = _json(d / "timing.json")
        rows = {e["signal"]: _row(d, e) for e in layout.read_jsonl(d / "ledger.jsonl")}
        out[mode] = {"wall_s": timing.get("wall_s"), "tasks": rows}
    return out


def main() -> None:
    ab = Path(sys.argv[1])
    s = summarise(ab)
    sigs = sorted(set(s["same"]["tasks"]) | set(s["blind"]["tasks"]))
    print(f"record {ab}")
    print(f"{'signal':<28} {'mode':<6} {'status':<10} {'fold':<5} {'sess':<5} {'secs':<22} "
          f"{'total':<6} {'+lines':<7} {'+asserts':<9} advice")
    for sig in sigs:
        short = sig.split("/", 1)[-1]
        for mode in MODES:
            r = s[mode]["tasks"].get(sig)
            if not r:
                print(f"{short:<28} {mode:<6} (no record)")
                continue
            size = r["size"] or {}
            print(f"{short:<28} {mode:<6} {str(r['status']):<10} {'yes' if r['folded'] else 'no':<5} "
                  f"{r['sessions']:<5} {str(r['session_secs']):<22} {r['total_secs']:<6} "
                  f"{str(size.get('lines', '?')):<7} {str(size.get('asserts', '?')):<9} "
                  f"{len(r['advice'] or [])}")
    for mode in MODES:
        rows = s[mode]["tasks"].values()
        n = len(rows)
        folded = sum(r["folded"] for r in rows)
        secs = [r["total_secs"] for r in rows if r["total_secs"]]
        print(f"\n{mode}: {n} signals, {folded} folded, round wall {s[mode]['wall_s']}s, "
              f"per-signal session time median {sorted(secs)[len(secs)//2] if secs else '?'}s "
              f"max {max(secs) if secs else '?'}s, "
              f"repairs {sum(r['repairs'] for r in rows)}")


if __name__ == "__main__":
    main()
