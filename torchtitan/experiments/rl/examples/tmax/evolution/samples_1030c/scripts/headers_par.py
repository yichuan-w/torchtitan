"""Parallel first-line extraction of rollout records (drops `exec`, keeps compact fields)."""
import glob, json, os, sys
from concurrent.futures import ThreadPoolExecutor

root, out, kind, nthreads = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
KEEP = ("task", "rev", "version", "run", "group", "rollout", "reward", "status", "finish_reason", "submitted",
        "format_errors", "turns", "secs", "started", "infra_failed", "budget_sec", "failure", "error")


def one(f):
    try:
        with open(f) as r:
            d = json.loads(r.readline())
    except Exception as e:
        return {"_file": os.path.relpath(f, root), "_err": str(e)[:200]}
    rec = {k: d.get(k) for k in KEEP}
    v = d.get("verifier")
    if isinstance(v, dict):
        rec["verifier"] = {k: x for k, x in v.items() if not isinstance(x, str) or len(x) <= 300}
    rec["_file"] = os.path.relpath(f, root)
    return rec


dirs = sorted(glob.glob(os.path.join(root, "runs", "*", kind, "*")))
with ThreadPoolExecutor(nthreads) as ex:
    files = [f for fs in ex.map(lambda d: glob.glob(os.path.join(d, "*.jsonl")), dirs) for f in fs]
print("files", len(files), flush=True)
n = 0
with open(out, "w") as w, ThreadPoolExecutor(nthreads) as ex:
    for rec in ex.map(one, files, chunksize=64):
        w.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n += 1
        if n % 5000 == 0:
            print(n, flush=True)
print("done", n, flush=True)
