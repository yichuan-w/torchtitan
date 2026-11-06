"""Item 6 follow-up: split trained all-failed groups by whether all 12 submitted; non-submitted endings by reason."""
import json, sys
from collections import Counter
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv(sys.argv[1]); out = sys.argv[2]
t = df.dropna(subset=["train_step", "solved", "rev"]).copy()
t = t[t.solved == 0]
t["kind"] = (t.rev > 0).map({False: "original", True: "rewritten"})
fr = t.finish.apply(lambda s: json.loads(s) if isinstance(s, str) else {})
t["all_sub"] = (t.n_submitted == t.n_hdr) & (t.n_hdr == 12)
t["n_ctx"] = fr.apply(lambda d: d.get("hit_context_limit", 0))
t["n_time"] = fr.apply(lambda d: d.get("hit_time_budget", 0))
t["n_other_nonsub"] = t.n_hdr - t.n_submitted - t.n_ctx - t.n_time
t["bin"] = (t.train_step // 50) * 50
g = t.groupby(["kind", "bin"]).agg(groups=("group", "size"), all_submitted=("all_sub", "mean"),
                                   ctx_rollouts=("n_ctx", "sum"), time_rollouts=("n_time", "sum"),
                                   other_nonsub=("n_other_nonsub", "sum"), rollouts=("n_hdr", "sum"))
for c in ("ctx_rollouts", "time_rollouts", "other_nonsub"):
    g[c.replace("_rollouts", "") + "_frac"] = (g[c] / g.rollouts).round(3)
g["all_submitted"] = g.all_submitted.round(3)
print(g[["groups", "all_submitted", "ctx_frac", "time_frac", "other_nonsub"]].to_string())
tot = t.groupby("kind").agg(groups=("group", "size"), all_sub=("all_sub", "sum"), ctx=("n_ctx", "sum"), time=("n_time", "sum"), rollouts=("n_hdr", "sum"), sub=("n_submitted", "sum"))
print(tot.to_string())
g.to_csv(f"{out}/item6_allfailed_split.csv")
f, axes = plt.subplots(1, 2, figsize=(11, 3.4), sharey=True)
for ax, kind in zip(axes, ["original", "rewritten"]):
    x = t[t.kind == kind].groupby(["train_step", "all_sub"]).size().unstack(fill_value=0).reindex(columns=[True, False], fill_value=0).rolling(5, min_periods=1).mean()
    ax.stackplot(x.index, x[True], x[False], labels=["all 12 submitted (wrong answers)", "some hit time / context limit"], colors=["#eb6834", "#f4b393"], edgecolor="white", linewidth=0.5)
    ax.set_title(f"All-failed trained groups per step: {kind}", loc="left", fontsize=10); ax.set_xlabel("Training step")
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
axes[0].set_ylabel("Groups"); axes[0].legend(frameon=False, fontsize=8)
f.tight_layout(); f.savefig(f"{out}/item6_allfailed_split.png", dpi=170)
