"""Time one dedup chunk and check the contamination screen on a known positive."""
import json, time
import numpy as np
import dedup
from common import ROOT, read_jsonl, normalize

work = ROOT / "work"
np.seterr(over="ignore")
queries = list(read_jsonl(work / "queries.jsonl"))
qsets = [dedup.shingles(q["instruction"]) for q in queries]
qdata = (np.concatenate(qsets), np.concatenate([np.full(s.size, i) for i, s in enumerate(qsets)]),
         np.array([s.size for s in qsets]), {q["name"] for q in queries}, {normalize(q["instruction"]) for q in queries})
dedup.init(qdata)
lines = next(dedup.chunks(work / "docs.jsonl"))
t = time.time(); res = dedup.work_chunk(lines); dt = time.time() - t
print(f"chunk of {len(lines)} took {dt:.2f}s -> est {2149222/len(lines)*dt/dedup.WORKERS/60:.1f} min on {dedup.WORKERS} workers")
pos = json.dumps({"id": "dX", "text": queries[0]["instruction"], "occ": [["TB2-Verified", "x/" + queries[0]["name"], ""]]})
print("positive:", dedup.work_chunk([pos])[0][3:])
print("flagged in chunk:", sum(1 for r in res if r[3] or r[4] or r[5]))
