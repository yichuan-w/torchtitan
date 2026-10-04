"""Look at one query's top dense hits over whatever score shards have arrived (sanity check, read-only)."""
import json, sqlite3, sys
import numpy as np
from common import POOL, ROOT

qid, k = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10
watch = sys.argv[3:]
ids, sc = [], []
for f in sorted((ROOT / "work/embed_out").glob("shard_[0-9][0-9][0-9].npz")):
    z = np.load(f); qi = list(z["qids"]).index(qid)
    ids += z["ids"].tolist(); sc.append(z["scores"][qi].astype(np.float32))
s = np.concatenate(sc); order = np.argsort(-s)
idx = json.loads((ROOT / "work/rep_index.json").read_text())
pool = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
print(f"{qid}: {len(ids)} docs scored; max {s.max():.4f} median {np.median(s):.4f}")
for r, j in enumerate(order[:k], 1):
    t = pool.execute("SELECT instruction FROM docs WHERE id=?", (int(ids[j][1:]),)).fetchone()[0]
    print(f"{r:3d} {s[j]:.4f} {ids[j]} {','.join(idx[ids[j]]['sources'])} | {' '.join(t.split())[:110]}")
for w in watch:
    if w in ids:
        j = ids.index(w); print(f"watch {w}: cos {s[j]:.4f} rank {(s > s[j]).sum() + 1}")
