"""Item 2: solved count aligned on each rewrite (-1 = signal group, +1..+3 = first trained groups after it finished)."""
import glob, json, sys
from datetime import datetime, timezone
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

groups = pd.read_csv(sys.argv[1])
meta, out = sys.argv[2], sys.argv[3]
g_by_id = groups.set_index("group")
tr = groups.dropna(subset=["train_step", "solved", "rev"]).sort_values("group")
by_task = {t: d for t, d in tr.groupby("task")}


def ns(stamp):
    return int(datetime.strptime(stamp, "%Y%m%d-%H%M%SZ").replace(tzinfo=timezone.utc).timestamp() * 1e9)


rows = []
for f in glob.glob(f"{meta}/*/rewrites/*/rewrite.json"):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    st = d.get("status")
    if st not in ("accepted", "failed", "kept", "rejected") or not d.get("finished") or not d.get("signal"):
        continue
    task, gtxt = d["signal"].split("/")[-1].rsplit("--g", 1)
    sig = int(gtxt)
    if sig not in g_by_id.index or pd.isna(g_by_id.loc[sig, "solved"]):
        continue
    arm = "accepted" if st == "accepted" else "rejected/failed"
    want_rev = d["result_rev"] if arm == "accepted" else d["input_rev"]
    after = by_task.get(task)
    vals = []
    if after is not None:
        a = after[(after.admitted_ns > ns(d["finished"])) & (after.rev == want_rev)].head(3)
        vals = list(a.solved)
    rows.append({"task": task, "job": d["job"], "arm": arm, "status": st, "signal_group": sig,
                 "m1": g_by_id.loc[sig, "solved"], **{f"p{i + 1}": v for i, v in enumerate(vals)}})
df = pd.DataFrame(rows)
df.to_csv(f"{out}/item2_rows.csv", index=False)
COL = {"accepted": "#2a78d6", "rejected/failed": "#eb6834"}
f, axes = plt.subplots(1, 2, figsize=(10, 4.6))
for ax, job in zip(axes, ["easier", "harder"]):
    for arm in ["accepted", "rejected/failed"]:
        s = df[(df.job == job) & (df.arm == arm)]
        xs, ys, ns_ = [], [], []
        for x, c in zip([-1, 1, 2, 3], ["m1", "p1", "p2", "p3"]):
            v = s[c].dropna() if c in s else pd.Series(dtype=float)
            if len(v):
                xs.append(x); ys.append(v.mean()); ns_.append(len(v))
        ax.plot(xs, ys, marker="o", ms=6, lw=2, color=COL[arm], label=f"{arm} (n={ns_[0] if ns_ else 0} rewrites)")
        for x, y, n in zip(xs, ys, ns_):
            ax.annotate(f"n={n}", (x, y), textcoords="offset points", xytext=(14 if x == -1 else 0, -14 if arm == "accepted" else 8), ha="center", fontsize=7, color="#52514e")
        print(job, arm, [round(y, 2) for y in ys], ns_)
    ax.axvline(0, color="#bdbcb6", lw=1, ls="--")
    ax.set_xticks([-1, 1, 2, 3]); ax.set_xticklabels(["-1\n(signal)", "+1", "+2", "+3"])
    ax.set_ylim(-0.3, 12.3); ax.set_ylabel("Mean solved (of 12)")
    ax.set_title(f"{job} rewrites", loc="left", fontsize=10)
    ax.grid(axis="y", color="#e6e5e1", lw=0.6)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=1)
f.tight_layout()
f.savefig(f"{out}/item2_aligned_on_rewrite.png", dpi=170)
print(df.groupby(["job", "arm", "status"]).size())
