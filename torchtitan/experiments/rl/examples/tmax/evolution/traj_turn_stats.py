#!/usr/bin/env python3
"""Terminus-2 turn-count distribution from RST's released trajectories.

Yichuan asked how many turns Terminus-2 takes on RST tasks (his vanillux
harness counts one bash command per turn; Terminus-2 batches keystrokes, so
turns are fewer and longer). The released 327K trajectories were collected
with Terminus-2 (agent.name field), so the answer can be read off them
directly — no new rollouts needed. This samples the downloaded shards.

Output: results/terminus2_turns.md
"""
from __future__ import annotations

import json
import statistics
import tarfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "data" / "rst-traj-sample"
OUT = ROOT / "results" / "terminus2_turns.md"


def main() -> None:
    import pyarrow.parquet as pq

    # trajectory -> metadata (round, verdict) if available
    meta = {}
    pqf = SAMPLE / "trajectories.parquet"
    if pqf.exists():
        t = pq.read_table(pqf)
        cols = t.schema.names
        print("metadata columns:", cols)
        key = "trajectory_id" if "trajectory_id" in cols else cols[0]
        rows = [dict(zip(cols, r)) for r in zip(*[t[c].to_pylist() for c in cols])]
        meta = {r[key]: r for r in rows}

    turns, tokens, by_round = [], [], defaultdict(list)
    agents = Counter()
    n = 0
    for shard in sorted(SAMPLE.glob("trajectories-*.tar")):
        with tarfile.open(shard) as tf:
            for m in tf.getmembers():
                if not (m.isfile() and m.name.endswith("trajectory.json")):
                    continue
                d = json.loads(tf.extractfile(m).read())
                agents[d.get("agent", {}).get("name", "?")] += 1
                nt = sum(1 for s in d.get("steps", []) if s.get("source") == "agent")
                turns.append(nt)
                fm = d.get("final_metrics") or {}
                tokens.append(fm.get("total_completion_tokens", 0))
                tid = m.name.split("/")[1]
                r = meta.get(tid)
                if r:
                    rd = r.get("synthesis_round") or r.get("round") or ""
                    if rd != "":
                        by_round[rd].append(nt)
                n += 1
        print(f"{shard.name}: cumulative {n} trajectories")

    q = statistics.quantiles(turns, n=20)
    lines = [
        "# Terminus-2 turn counts on RST tasks (from the released trajectories)",
        "",
        f"Sample: {n} trajectories from 3 of 71 shards; agent field: {dict(agents)}.",
        "A turn = one agent-source step in trajectory.json (one model call).",
        "",
        f"- median {statistics.median(turns):.0f} turns; mean {statistics.mean(turns):.1f}",
        f"- p25 {q[4]:.0f} / p75 {q[14]:.0f} / p90 {q[17]:.0f} / p95 {q[18]:.0f}; max {max(turns)}",
        f"- completion tokens per trajectory: median {statistics.median(tokens):.0f}",
        "",
        "Turn histogram (bucketed):",
    ]
    hist = Counter(min(t // 10 * 10, 100) for t in turns)
    for b in sorted(hist):
        label = f"{b}-{b+9}" if b < 100 else "100+"
        lines.append(f"- {label}: {hist[b]} ({100*hist[b]/n:.1f}%)")
    if by_round:
        lines += ["", "By synthesis round (median turns):"]
        for rd in sorted(by_round):
            lines.append(f"- {rd}: {statistics.median(by_round[rd]):.0f}"
                         f"  (n={len(by_round[rd])})")
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:14]))


if __name__ == "__main__":
    main()
