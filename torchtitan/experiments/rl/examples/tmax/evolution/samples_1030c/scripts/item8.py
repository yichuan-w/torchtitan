"""Item 8: share of hint-carrying (easier-derived) versions among trained mixed groups, two candidate rules."""
import json, os, sys
import pandas as pd

groups, meta, out = pd.read_csv(sys.argv[1]), sys.argv[2], sys.argv[3]
path_jobs = {}  # (task, rev) -> list of jobs along the fold path from r0
for task in os.listdir(meta):
    p = f"{meta}/{task}/lineage.jsonl"
    if not os.path.exists(p):
        continue
    jobs = {0: []}
    rw = {}
    for line in open(p):
        d = json.loads(line)
        if d.get("event") == "rewrite":
            rw[d["rewrite"]] = d.get("job")
    for line in open(p):
        d = json.loads(line)
        if d.get("event") == "fold":
            job = rw.get(d["rewrite"]) or ("easier" if d["rewrite"].endswith("easier") else "harder")
            jobs[d["to_rev"]] = jobs.get(d["from_rev"], []) + [job]
    for r, js in jobs.items():
        path_jobs[(task, r)] = js
t = groups.dropna(subset=["train_step", "solved", "rev"]).copy()
t = t[(t.solved > 0) & (t.solved < t.total)]
t["path"] = [path_jobs.get((a, int(b))) for a, b in zip(t.task, t.rev)]
t = t[t.path.notna()]
t["any_easier"] = t.path.apply(lambda p: "easier" in p)
t["last_easier"] = t.path.apply(lambda p: bool(p) and p[-1] == "easier")
t["rewritten"] = t.path.apply(bool)
t["bin"] = (t.train_step // 25) * 25
tab = t.groupby("bin")[["rewritten", "any_easier", "last_easier"]].mean().round(3)
tab["n_mixed"] = t.groupby("bin").size()
print(tab.to_string())
tab.to_csv(f"{out}/item8_hint_share.csv")
for lo, hi in ((150, 175), (0, 175), (150, 300)):
    s = t[(t.train_step >= lo) & (t.train_step <= hi)]
    print(f"steps {lo}-{hi}: rewritten {s.rewritten.mean():.3f} any_easier {s.any_easier.mean():.3f} last_easier {s.last_easier.mean():.3f}")
