#!/usr/bin/env python3
"""Read one ab_verifier_author.sh record and print the two modes side by side.

Per task and mode: what the round decided, how many sessions it took, wall
clock per session and in total, how far the rewrite sits above the seed by
the size rule, and what the names audit had to say. Nothing here is a
verdict on the flag; the numbers are what the flag costs and what it changes,
for the person deciding the default.

    python3 ab_verifier_author_summary.py <dev-workdir>/ab/<stamp>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_size as ts  # noqa: E402


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _trace(evo: Path, rel: str) -> dict:
    d = evo / rel
    meta = {}
    try:
        meta = json.loads((d / "trace.json").read_text())
    except (OSError, ValueError):
        pass
    t0, t1 = meta.get("started_time_unix_ns"), meta.get("finished_time_unix_ns")
    secs = round((t1 - t0) / 1e9) if t0 and t1 else None
    repairs = meta.get("repairs") or []
    pkg = d / "pkg"
    size = None
    try:
        seed = json.loads((pkg / "run" / "seed_size.json").read_text())
        vrel = next(c for c in ("tests/test_state.py", "tests/test.sh") if (pkg / c).exists())
        now = ts.size_of_package(pkg, vrel)
        size = {"lines": now["solution_lines"] - seed["solution_lines"],
                "asserts": now["verifier_asserts"] - seed["verifier_asserts"]}
    except Exception:  # noqa: BLE001 -- a verifier trace has no solution to size
        pass
    advice = None
    try:
        last = [json.loads(l) for l in (pkg / "run" / "checks.jsonl").read_text().splitlines() if l.strip()][-1]
        advice = last.get("dark_literals")
    except Exception:  # noqa: BLE001
        pass
    return {"job": meta.get("job"), "status": meta.get("status"), "secs": secs,
            "repairs": len(repairs), "size": size, "advice": advice}


def summarise(ab: Path) -> dict:
    evo = ab.parents[1] / "evolution"
    out = {}
    for mode in ("same", "blind"):
        d = ab / mode
        timing = json.loads((d / "timing.json").read_text()) if (d / "timing.json").exists() else {}
        rows = {}
        for e in _read_jsonl(d / "lineage.jsonl"):
            tid = e.get("task_id")
            if e.get("event") == "retune_finished":
                traces = [_trace(evo, r) for r in (e.get("codex_trace_dirs") or [])]
                rows[tid] = {"status": e.get("status"), "action": e.get("action"),
                             "sessions": len(traces),
                             "session_secs": [t["secs"] for t in traces],
                             "total_secs": sum(t["secs"] or 0 for t in traces),
                             "repairs": sum(t["repairs"] for t in traces),
                             "size": next((t["size"] for t in traces if t["size"]), None),
                             "advice": next((t["advice"] for t in traces if t["advice"]), None),
                             "folded": False}
            elif e.get("event") == "folded" and tid in rows:
                rows[tid]["folded"] = True
        out[mode] = {"wall_s": timing.get("wall_s"), "tasks": rows}
    return out


def main() -> None:
    ab = Path(sys.argv[1])
    s = summarise(ab)
    tids = sorted(set(s["same"]["tasks"]) | set(s["blind"]["tasks"]))
    print(f"record {ab}")
    print(f"{'task':<12} {'mode':<6} {'status':<14} {'fold':<5} {'sess':<5} {'secs':<22} {'total':<6} {'+lines':<7} {'+asserts':<9} advice")
    for tid in tids:
        for mode in ("same", "blind"):
            r = s[mode]["tasks"].get(tid)
            if not r:
                print(f"{tid:<12} {mode:<6} (no record)")
                continue
            size = r["size"] or {}
            print(f"{tid:<12} {mode:<6} {str(r['status']):<14} {'yes' if r['folded'] else 'no':<5} "
                  f"{r['sessions']:<5} {str(r['session_secs']):<22} {r['total_secs']:<6} "
                  f"{str(size.get('lines', '?')):<7} {str(size.get('asserts', '?')):<9} "
                  f"{len(r['advice'] or [])}")
    for mode in ("same", "blind"):
        rows = s[mode]["tasks"].values()
        n = len(rows)
        folded = sum(r["folded"] for r in rows)
        secs = [r["total_secs"] for r in rows if r["total_secs"]]
        print(f"\n{mode}: {n} tasks, {folded} folded, round wall {s[mode]['wall_s']}s, "
              f"per-task session time median {sorted(secs)[len(secs)//2] if secs else '?'}s "
              f"max {max(secs) if secs else '?'}s, "
              f"repairs {sum(r['repairs'] for r in rows)}")


if __name__ == "__main__":
    main()
