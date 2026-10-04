"""Check the hf backend against vLLM output already computed for a shard (cosine per doc)."""
import sys, os, time
import numpy as np
os.environ["POOL57_BACKEND"] = "hf"
import embed

shard, n, ref = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
z = np.load(ref)
rows = [__import__("json").loads(l) for l in (embed.HERE / "embed_input" / f"shard_{shard:03d}.jsonl").open()][:n]
assert list(z["ids"][:n]) == [r["id"] for r in rows]
e = embed.HFEmbedder()
t = time.time(); v, ntok = e.embed([r["text"] for r in rows]); dt = time.time() - t
cos = (v * z["d"][:n].astype(np.float32)).sum(1)
qcos = (e.qv * z["q"].astype(np.float32)).sum(1)
print(f"docs={n} tokens={ntok} tok_per_s={ntok/dt:.0f}")
print(f"doc cosine hf vs vllm: min={cos.min():.4f} mean={cos.mean():.4f}")
print(f"query cosine hf vs vllm: min={qcos.min():.4f} mean={qcos.mean():.4f}")
