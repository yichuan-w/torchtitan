#!/usr/bin/env python
"""Find v2 tasks whose final_top10 was distorted by the stale verifier grades.

The v2 packets were built at ~22:56 with a verifier grader that flagged 4,528
tasks FREE. The grader was then corrected -- it had been enumerating what counts
as *good* verification, an open-ended list, so careful verifiers kept falling
through it -- and the real figure is 602. The 18 reranking agents therefore ran
against a signal that wrongly barred ~3,926 pool tasks from `final_top10`.

The error is one-directional: a wrongly-barred candidate could only be pushed
out of the final ten, never into it, so every agent's output is still valid.
What it costs is the occasional slot. This finds exactly where.

A task needs a second look when a candidate that
  - the agent ranked highly in `per_source` (overall >= PROMOTE_MIN), and
  - is absent from `final_top10`, and
  - was FREE under the stale grades but is not FREE now
exists. Those are the slots the stale signal plausibly took.

  promote_pass.py                 report which tasks are affected
  promote_pass.py --dispatch      print a rerun prompt for each of them
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RERANK = HERE / "rerank_v2"
PACKETS = HERE / "work" / "packets_v2"
VSTRENGTH = HERE / "work" / "verifier_strength.json"
PROMOTE_MIN = 4


def stale_grades() -> dict[str, str]:
    """The grades as the packets froze them -- what the agents actually saw."""
    out = {}
    for p in PACKETS.glob("*.json"):
        d = json.loads(p.read_text())
        for c in d["candidates"].values():
            for e in c["shown"]:
                if e.get("verifier"):
                    out[e["task_id"]] = e["verifier"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dispatch", action="store_true")
    ap.add_argument("--min-overall", type=int, default=PROMOTE_MIN)
    args = ap.parse_args()

    now = {k: v["grade"] for k, v in json.loads(VSTRENGTH.read_text()).items()}
    then = stale_grades()
    rehabilitated = {t for t, g in then.items() if g == "FREE" and now.get(t) != "FREE"}
    print(f"[promote] candidates shown to the agents: {len(then)}")
    print(f"[promote] of those, marked FREE then but not now: {len(rehabilitated)}")

    affected: dict[str, list] = defaultdict(list)
    for p in sorted(RERANK.glob("*.json")):
        if p.name.startswith("_"):
            continue
        rec = json.loads(p.read_text())
        final = {e["task_id"] for e in rec.get("final_top10", [])}
        for corpus, lst in (rec.get("per_source") or {}).items():
            for e in lst:
                tid = e.get("task_id")
                if (tid in rehabilitated and tid not in final
                        and int(e.get("overall", 0)) >= args.min_overall):
                    affected[rec["tb21_id"]].append(
                        {"task_id": tid, "source": corpus, "rank": e.get("rank"),
                         "overall": e.get("overall"), "now": now.get(tid),
                         "reason": e.get("reason", "")[:90]}
                    )

    print(f"[promote] tasks needing a second look: {len(affected)}/"
          f"{len(list(RERANK.glob('*.json')))}\n")
    for tb, items in sorted(affected.items(), key=lambda kv: -len(kv[1])):
        print(f"{tb}  ({len(items)} rehabilitated candidate(s) scored >= {args.min_overall})")
        for i in sorted(items, key=lambda x: -x["overall"])[:5]:
            print(f"    [{i['source'][:18]:18}] {i['task_id'][:40]:40} "
                  f"o{i['overall']} now={i['now']:6} {i['reason']}")

    if args.dispatch and affected:
        print("\n" + "=" * 78)
        for tb, items in sorted(affected.items()):
            ids = ", ".join(sorted({i["task_id"] for i in items}))
            print(f"""
### rerun: {tb}
Re-open `rerank_v2/{tb}.json`. It was produced against a verifier grader that
wrongly marked these candidates FREE, which barred them from `final_top10`:
  {ids}
They are not FREE (their current grades are in work/verifier_strength.json).
Re-read them, and if any belongs in the final ten on skill/domain/task_form,
rewrite `final_top10` to include it -- still exactly 10, still all drawn from
`per_source`, still no candidate that is FREE *now*. Leave `per_source`
unchanged. Say in `verifier_notes` what you changed and why.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
