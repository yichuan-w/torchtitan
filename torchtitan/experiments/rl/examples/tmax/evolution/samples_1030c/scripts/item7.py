"""Item 7: lineage trajectories (both directions, >=3 revs, >=3 groups per rev), top 4 by rev count."""
import glob, json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

groups = pd.read_csv(sys.argv[1])
meta, out = sys.argv[2], sys.argv[3]
tr = groups.dropna(subset=["train_step", "solved", "rev"]).sort_values("group")
cands = []
for task, g in tr.groupby("task"):
    acc = []
    for f in glob.glob(f"{meta}/{task}/rewrites/*/rewrite.json"):
        d = json.load(open(f))
        if d.get("status") == "accepted":
            acc.append(d)
    dirs = {d["job"] for d in acc}
    per_rev = g.groupby("rev").size()
    if dirs >= {"easier", "harder"} and len(per_rev) >= 3 and (per_rev >= 3).all():
        cands.append((len(per_rev), len(g), task, sorted(acc, key=lambda d: d["result_rev"])))
cands.sort(reverse=True)
print(len(cands), "lineages qualify;", [(c[2], c[0], c[1]) for c in cands[:6]])
f, axes = plt.subplots(2, 2, figsize=(12, 6.5), sharey=True)
for ax, (nrev, ng, task, acc) in zip(axes.flat, cands[:4]):
    g = tr[tr.task == task]
    ax.plot(g.train_step, g.solved, color="#2a78d6", lw=1.6, marker="o", ms=4)
    for d in acc:
        first = g[g.rev == d["result_rev"]]
        if first.empty:
            continue
        x = first.train_step.iloc[0] - 0.5
        ax.axvline(x, color="#1baf7a" if d["job"] == "easier" else "#eb6834", lw=1.2, ls="--")
        ax.text(x, 12.6, f"r{d['result_rev']} {d['job']}", rotation=90, fontsize=7, va="bottom", ha="right", color="#52514e")
    ax.set_title(f"{task} ({nrev} revs, {ng} groups)", fontsize=9, loc="left", pad=38)
    ax.set_ylim(-0.5, 12.5); ax.set_xlabel("Training step", fontsize=8); ax.set_ylabel("Solved (of 12)", fontsize=8)
    ax.grid(axis="y", color="#e6e5e1", lw=0.6)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
f.tight_layout()
f.savefig(f"{out}/item7_lineages.png", dpi=170)
for nrev, ng, task, acc in cands[:4]:
    g = tr[tr.task == task]
    print(task, {int(r): list(x.solved.astype(int)) for r, x in g.groupby("rev")}, [(d["result_rev"], d["job"]) for d in acc])
