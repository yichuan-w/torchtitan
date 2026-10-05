"""Summarise Terminal-Wrench's hack evidence for given task ids (all models pooled), plus one exploit's text."""
import collections
import glob
import json
import sys

W = "/data/yichuan_wang/terminal-search-pool-20261002/raw/Terminal-Wrench/d8a29613235a0ef56a8b70b3142626a533da28c2"
K = ["rewarded_serious_exploit_count", "rewarded_nonserious_hack_count", "attacker_legitimate_solve_count", "no_reward_count"]
d = json.load(open(f"{W}/index/tasks.json"))
for t in sys.argv[1:]:
    c = collections.Counter()
    for r in d:
        if r["task_id"] == t:
            for k in K:
                c[k] += r[k]
    print(f"{t:36s} serious={c[K[0]]:2d} nonserious={c[K[1]]:2d} legit={c[K[2]]:2d} noreward={c[K[3]]:2d}")
r = [r for r in d if r["task_id"] == sys.argv[1]][0]
tr = r["trajectories"][0]
print("trajectory keys:", sorted(tr.keys()))
print(json.dumps({k: tr[k] for k in tr if k not in ("task_id", "model", "source_dataset", "source_datasets")})[:1200])
print(glob.glob(f"{W}/tasks/{sys.argv[1]}/claude-opus-4.6/*")[:10])

print()
for t in sys.argv[1:]:
    ex = [tr for r in d if r["task_id"] == t for tr in r["trajectories"] if tr.get("judged_serious_exploit")]
    if ex:
        print(f"{t}: {ex[0]['brief_exploit_summary'][:260]}")
