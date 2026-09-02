#!/usr/bin/env python3
"""Build mix_v3.jsonl: the solvable-filtered training mix (run on della).

TW side: rows rebuilt from the ORIGINAL adapted packages (tw_all.jsonl), so
retuned instructions from earlier evolution rounds are reset, restricted to
tasks that are (a) in the previous mix's TW membership (oracle pass, not
verdict-flipped, not fragile-build) and (b) solvable = measured pass@5 != 0
(metadata/solvable_ids.txt on the HF dataset). SWE and Turing Labs rows pass
through unchanged. Output is written next to the inputs; swapping it into
mix_live.jsonl is a separate, deliberate step.

Provenance of the filter files:
  tw_solvable.ids  <- results/solve_all861.jsonl, solved > 0 per task_id
  mix_live.jsonl   <- previous live mix (defines TW membership to intersect)
"""
import json
from pathlib import Path

BASE = Path("/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix")

solv = {l.strip() for l in open(BASE / "tw_solvable.ids") if l.strip()}
mix_tw = set()
for ln in open(BASE / "mix_live.jsonl"):
    if ln.strip():
        iid = json.loads(ln)["metadata"]["instance_id"]
        if iid.startswith("tw_"):
            mix_tw.add(iid)
skip_p = BASE / "daytona_unstartable.ids"
skip = ({l.strip() for l in open(skip_p) if l.strip()}
        if skip_p.exists() else set())
keep_tw = (mix_tw & solv) - skip

n = {"tw": 0, "swe": 0, "turing": 0}
with open(BASE / "mix_v3.jsonl", "w") as out:
    for ln in open(BASE / "tw_all.jsonl"):
        if ln.strip() and json.loads(ln)["label"] in keep_tw:
            out.write(ln if ln.endswith("\n") else ln + "\n"); n["tw"] += 1
    for src, key in ((BASE / "swe_main.jsonl", "swe"),
                     (BASE / "turing_labs_data.jsonl", "turing")):
        for ln in open(src):
            if ln.strip() and json.loads(ln)["label"] not in skip:
                out.write(ln if ln.endswith("\n") else ln + "\n"); n[key] += 1
print("mix_v3:", n, "total", sum(n.values()))
