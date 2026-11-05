"""One row per training group: lineage (solved, step, dropped, duration) + headers (rev, finish reasons)."""
import json, sys
from collections import Counter, defaultdict
import pandas as pd

base = sys.argv[1]  # e.g. .../1030c
lin = {}
for line in open(f"{base}/lineage_events.jsonl"):
    d = json.loads(line)
    if "group_id" not in d:
        continue
    r = lin.setdefault(d["group_id"], {"group": d["group_id"], "task": d.get("task_id")})
    e = d["event"]
    if e == "admitted":
        r["admitted_ns"] = d["time_unix_ns"]
    elif e == "finalized":
        r.update(solved=d["num_solved"], total=d["num_rollouts"], scored=d["num_scored_rollouts"],
                 dur=d["rollout_duration_sec"], finalized_ns=d["time_unix_ns"])
    elif e == "trained":
        r["train_step"] = d["train_step"]
    elif e == "dropped":
        r["dropped"] = d.get("reason")

# headers: keep the latest run's copy of each (group, rollout)
best = {}
for line in open(f"{base}/rollout_headers.jsonl"):
    h = json.loads(line)
    k = (h["group"], h["rollout"])
    if k not in best or (h["run"] or "") > (best[k]["run"] or ""):
        best[k] = h
agg = defaultdict(lambda: {"revs": Counter(), "fr": Counter(), "n": 0, "sub": 0, "full": 0, "secs": 0.0})
for (g, _), h in best.items():
    a = agg[g]
    a["revs"][h["rev"]] += 1
    a["fr"][h["finish_reason"]] += 1
    a["n"] += 1
    a["sub"] += bool(h["submitted"])
    a["full"] += (h["reward"] or 0) >= 1.0
    a["secs"] += h["secs"] or 0.0
    a["task_h"] = h["task"]
rows = []
for g, r in lin.items():
    a = agg.get(g)
    if a:
        r.update(rev=a["revs"].most_common(1)[0][0], n_hdr=a["n"], n_submitted=a["sub"], n_full=a["full"],
                 rollout_secs=a["secs"], finish=json.dumps(dict(a["fr"])))
    rows.append(r)
df = pd.DataFrame(rows).sort_values("group")
df.to_csv(sys.argv[2], index=False)
print(len(df), "groups;", df["rev"].notna().sum(), "with headers;", df["train_step"].notna().sum(), "trained")
chk = df.dropna(subset=["solved", "n_full"])
print("solved==n_full frac", round((chk["solved"] == chk["n_full"]).mean(), 4))
