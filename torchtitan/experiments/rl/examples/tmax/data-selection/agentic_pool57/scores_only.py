"""Copy just ids, qids and the 89 x n score matrix out of each finished embed shard.

The full .npz also carries 4096-d vectors (~9 GB over 64 shards); merging dense
recall needs only the scores, so this is what travels to Centralia. Vectors stay
on della for later reuse.
"""
import sys
from pathlib import Path

import numpy as np

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
for f in sorted(src.glob("shard_[0-9][0-9][0-9].npz")):
    out = dst / f.name
    if out.exists():
        continue
    z = np.load(f)
    tmp = out.with_suffix(".partial.npz")
    np.savez(tmp, ids=z["ids"], qids=z["qids"], scores=z["scores"])
    tmp.replace(out)
    print("wrote", out.name, z["scores"].shape, flush=True)
