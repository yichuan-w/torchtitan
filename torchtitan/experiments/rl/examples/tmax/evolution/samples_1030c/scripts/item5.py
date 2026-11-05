"""Item 5: harder candidates (new rev first group solved <= 6, a later group >= first + 3), top 5 by gain."""
import glob, json, os, sys
import pandas as pd

groups = pd.read_csv(sys.argv[1])
meta, out = sys.argv[2], sys.argv[3]
fin = groups.dropna(subset=["solved", "rev"]).sort_values("group")
rows = []
for f in glob.glob(f"{meta}/*/rewrites/*harder/rewrite.json"):
    d = json.load(open(f))
    if d.get("status") != "accepted" or not d.get("signal"):
        continue
    task = d["task"]
    sig = int(d["signal"].rsplit("--g", 1)[1])
    g = fin[(fin.task == task)]
    s0 = g[g.group == sig]
    new = g[g.rev == d["result_rev"]]
    if new.empty or s0.empty:
        continue
    seq = list(new.solved.astype(int))
    if seq[0] > 6:
        continue
    later = seq[1:]
    if not later or max(later) < seq[0] + 3:
        continue
    first_solved_after = next((int(gid) for gid, sv in zip(new.group.iloc[1:], new.solved.iloc[1:]) if sv > 0), None)
    rows.append({"task": task, "signal_group": sig, "signal_solved": int(s0.solved.iloc[0]), "old_rev": d["input_rev"],
                 "new_rev": d["result_rev"], "new_seq": seq, "gain": max(later) - seq[0], "new_groups": list(new.group.astype(int)),
                 "first_solved_group_after": first_solved_after, "dir": os.path.dirname(f), "run": "1030c"})
df = pd.DataFrame(rows).sort_values("gain", ascending=False)
df.to_csv(f"{out}/item5_candidates.csv", index=False)
print(len(df), "candidates"); print(df.head(10)[["task", "signal_solved", "new_seq", "gain", "first_solved_group_after"]].to_string(index=False))
