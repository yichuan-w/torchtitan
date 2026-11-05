"""Item 6: mixed / all-solved / all-failed trained groups per step (original vs rewritten), and no-gradient time share."""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv(sys.argv[1])
out = sys.argv[2]
t = df.dropna(subset=["train_step", "solved", "rev"]).copy()
t["cls"] = "mixed"
t.loc[t.solved == 0, "cls"] = "all failed"
t.loc[t.solved == t.total, "cls"] = "all solved"
t["kind"] = (t.rev > 0).map({False: "original (r0)", True: "rewritten (rev>=1)"})
print(t.groupby(["kind", "cls"]).size().unstack())
COL = {"mixed": "#2a78d6", "all solved": "#1baf7a", "all failed": "#eb6834"}
SM = 5
f, axes = plt.subplots(1, 3, figsize=(15, 3.6))
for ax, kind in zip(axes[:2], ["original (r0)", "rewritten (rev>=1)"]):
    c = t[t.kind == kind].groupby(["train_step", "cls"]).size().unstack(fill_value=0)
    c = c.reindex(columns=["mixed", "all solved", "all failed"], fill_value=0).rolling(SM, min_periods=1).mean()
    ax.stackplot(c.index, [c[k] for k in c.columns], labels=list(c.columns), colors=[COL[k] for k in c.columns], alpha=0.9, edgecolor="white", linewidth=0.5)
    ax.set_title(f"Trained groups per step: {kind}", fontsize=10, loc="left")
    ax.set_xlabel("Training step"); ax.set_ylabel("Groups")
t["nograd"] = t.cls != "mixed"
s = t.groupby("train_step").apply(lambda x: x.loc[x.nograd, "rollout_secs"].sum() / x["rollout_secs"].sum())
axes[2].plot(s.index, s.rolling(SM, min_periods=1).mean() * 100, color="#0b0b0b", lw=1.8)
axes[2].set_title("Rollout time in all-solved/all-failed groups (%)", fontsize=10, loc="left")
axes[2].set_xlabel("Training step"); axes[2].set_ylim(0, None)
for ax in axes:
    ax.grid(axis="y", color="#e6e5e1", lw=0.6)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
h, l = axes[0].get_legend_handles_labels()
f.legend(h, l, loc="lower center", ncol=3, frameon=False)
f.tight_layout(rect=(0, 0.07, 1, 1))
f.savefig(f"{out}/item6_group_classes.png", dpi=170)
s.to_csv(f"{out}/item6_nograd_time_share.csv")
t.groupby(["train_step", "kind", "cls"]).size().unstack(fill_value=0).to_csv(f"{out}/item6_counts.csv")
print("no-grad time share by 50-step bin:"); print((s.groupby((s.index // 50) * 50).mean() * 100).round(1).to_string())
