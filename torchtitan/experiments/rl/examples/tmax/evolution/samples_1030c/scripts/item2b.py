"""Item 2 robustness: fixed-sample variants, excluding security, and split by whether the signal group fully submitted."""
import glob, json, sys
from datetime import datetime, timezone
import pandas as pd

groups = pd.read_csv(sys.argv[1]); meta = sys.argv[2]; out = sys.argv[3]
gid = groups.set_index("group")
tr = groups.dropna(subset=["train_step", "solved", "rev"]).sort_values("group")
by_task = {t: d for t, d in tr.groupby("task")}
ns = lambda s: int(datetime.strptime(s, "%Y%m%d-%H%M%SZ").replace(tzinfo=timezone.utc).timestamp() * 1e9)
rows = []
for f in glob.glob(f"{meta}/*/rewrites/*/rewrite.json"):
    d = json.load(open(f))
    st = d.get("status")
    if st not in ("accepted", "failed", "kept", "rejected") or not d.get("finished") or not d.get("signal"):
        continue
    task, g = d["signal"].split("/")[-1].rsplit("--g", 1); g = int(g)
    if g not in gid.index or pd.isna(gid.loc[g, "solved"]):
        continue
    arm = "accepted" if st == "accepted" else "rejected/failed"
    want = d["result_rev"] if arm == "accepted" else d["input_rev"]
    after = by_task.get(task)
    after = after[after.admitted_ns > ns(d["finished"])] if after is not None else None
    same = list(after[after.rev == want].solved.head(3)) if after is not None else []      # original (rev must match)
    anyrev = list(after.solved.head(3)) if after is not None else []                      # keep counting after rev changes
    sig = gid.loc[g]
    rows.append({"task": task, "job": d["job"], "arm": arm, "m1": sig.solved,
                 "sig_all_submitted": sig.n_submitted == sig.n_hdr == 12,
                 "security": task.startswith("security_"),
                 **{f"s{i+1}": v for i, v in enumerate(same)}, **{f"a{i+1}": v for i, v in enumerate(anyrev)}})
df = pd.DataFrame(rows)


def summ(s, pre):
    out = []
    for c in ["m1", f"{pre}1", f"{pre}2", f"{pre}3"]:
        v = s[c].dropna() if c in s else pd.Series(dtype=float)
        out.append(f"{v.mean():.2f}(n={len(v)})" if len(v) else "-")
    return " | ".join(out)


lines = []
def block(title, frame, pre="s"):
    lines.append(f"\n### {title}\n| | -1 | +1 | +2 | +3 |\n|---|---|---|---|---|")
    for job in ("easier", "harder"):
        for arm in ("accepted", "rejected/failed"):
            s = frame[(frame.job == job) & (frame.arm == arm)]
            lines.append(f"| {job}, {arm} | {summ(s, pre)} |")

block("original (rev must match)", df)
fixed = df.dropna(subset=["s1", "s2", "s3"])
block("fixed sample: +1, +2, +3 all on the same rev", fixed)
block("count after rev changes (first 3 trained groups after the rewrite, any rev)", df, pre="a")
block("excluding security tasks", df[~df.security])
block("signal group: all 12 submitted", df[df.sig_all_submitted])
block("signal group: some not submitted", df[~df.sig_all_submitted])
block("excluding security, signal group all 12 submitted", df[(~df.security) & df.sig_all_submitted])
txt = "\n".join(lines)
open(f"{out}/item2_robustness.md", "w").write(txt)
print(txt)
