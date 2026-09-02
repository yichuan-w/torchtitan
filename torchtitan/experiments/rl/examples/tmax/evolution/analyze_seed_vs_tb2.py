#!/usr/bin/env python3
"""Distribution comparison: TerminalWorld seeds vs Terminal-Bench 2.

Yichuan's ask: 「看下数据 和TB2是不是分布比较对」. Axes:
  1. instruction length (tokens) - quartiles
  2. solution size (lines) and command count - quartiles
  3. domain composition - TW has labels; TB2 tasks get a label by kNN vote
     over instruction embeddings (k=5, from the contamination-audit embeddings)
  4. coverage - for each TB2 task, cosine to nearest TW task (how much of
     TB2's space the seeds reach)

Inputs: results/contamination/inputs/*.jsonl + embeddings.npz (built by
contamination_audit.py). Output: results/seed_vs_tb2.md
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
AUD = ROOT / "results" / "contamination"
OUT = ROOT / "results" / "seed_vs_tb2.md"


def read(name: str) -> list[dict]:
    with open(AUD / "inputs" / f"{name}.jsonl") as f:
        return [json.loads(l) for l in f]


def quartiles(xs: list[float]) -> str:
    q = statistics.quantiles(xs, n=4)
    return f"{q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f}"


def solution_stats(root: Path, glob: str) -> tuple[list[int], list[int]]:
    lines, cmds = [], []
    for p in root.glob(glob):
        text = p.read_text(errors="replace")
        body = [l for l in text.splitlines()
                if l.strip() and not l.strip().startswith("#")]
        lines.append(len(body))
        cmds.append(sum(1 for l in body if not l.strip().startswith(("if", "fi",
                    "then", "else", "for", "done", "while", "case", "esac", "}"))))
    return lines, cmds


def main() -> None:
    tw, tb2 = read("terminalworld"), read("tb2")
    emb = np.load(AUD / "embeddings.npz")
    tw_emb, tb2_emb = emb["terminalworld"], emb["tb2"]

    # 1. instruction length
    tw_len = [len(r["tokens"]) for r in tw]
    tb2_len = [len(r["tokens"]) for r in tb2]

    # 2. solution size (from the actual packages)
    import tarfile
    tw_lines, tw_cmds = [], []
    with tarfile.open(ROOT / "data/seed-dataset/data/tasks-00000.tar") as tf:
        for m in tf.getmembers():
            if m.name.endswith("solution/solve.sh") and m.isfile():
                body = [l for l in tf.extractfile(m).read().decode("utf-8", "replace")
                        .splitlines() if l.strip() and not l.strip().startswith("#")]
                tw_lines.append(len(body))
    tb2_lines, _ = solution_stats(ROOT / "data/tb2", "*/solution/solve.sh")

    # 3. domain composition: TB2 via kNN vote over TW labels
    domains = [r["domain"] for r in tw]
    sims = tb2_emb @ tw_emb.T
    tb2_domains = []
    for i in range(len(tb2)):
        top = np.argsort(sims[i])[::-1][:5]
        vote = Counter(domains[j] for j in top).most_common(1)[0][0]
        tb2_domains.append(vote)
    tw_share = Counter(domains)
    tb2_share = Counter(tb2_domains)

    # 4. coverage: nearest-TW cosine per TB2 task
    nn = sims.max(axis=1)

    lines = ["# Seeds (TerminalWorld 1,530) vs Terminal-Bench 2 (89)", ""]
    lines += ["## 题面长度（token 数，Q1/中位/Q3）",
              f"- seeds: {quartiles(tw_len)}",
              f"- TB2:   {quartiles(tb2_len)}", ""]
    lines += ["## 参考解规模（非空非注释行数，Q1/中位/Q3）",
              f"- seeds: {quartiles(tw_lines)}",
              f"- TB2:   {quartiles(tb2_lines)}", ""]
    lines += ["## 领域构成（TB2 由 embedding kNN(k=5) 投票映射到 TW 的领域标签）",
              "", "| 领域 | seeds % | TB2 % |", "|---|---|---|"]
    for dom, n in tw_share.most_common():
        lines.append(f"| {dom} | {100*n/len(tw):.1f} | "
                     f"{100*tb2_share.get(dom,0)/len(tb2):.1f} |")
    extra = [d for d in tb2_share if d not in tw_share]
    for d in extra:
        lines.append(f"| {d} (TB2-only) | 0 | {100*tb2_share[d]/len(tb2):.1f} |")
    lines += ["", "## 覆盖度（每道 TB2 题到最近 seed 的 cosine）",
              f"- 中位 {np.median(nn):.3f}，Q1 {np.quantile(nn,0.25):.3f}，"
              f"Q3 {np.quantile(nn,0.75):.3f}",
              f"- cosine<0.30 的 TB2 题（seed 空间够不着的）: "
              f"{int((nn<0.30).sum())}/89: "
              + ", ".join(tb2[i]['id'] for i in np.argsort(nn)[:8]) + " …按距离升序前8", ""]
    OUT.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
