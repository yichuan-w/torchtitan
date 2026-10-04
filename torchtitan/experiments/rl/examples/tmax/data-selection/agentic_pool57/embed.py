"""Dense half of recall: embed shards with Qwen3-Embedding-8B and score them
against the 89 TB2.1 queries.

Same role as the gte-Qwen2-1.5B retriever in agentic/build_candidates.py
(2048-token window, instruction on the query side only, v2's task wording),
with the strongest current Qwen text embedder instead. Runs on della (queue
pilots and della-tridao); writes embed_out/shard_NNN.npz (doc ids, float16
vectors, float16 89 x n score matrix).

Workers coordinate through lock files on the shared GPFS: a shard is done
when its .npz exists, taken while its .lock exists (stale after STALE_S).
  --shard N   do shard N if nobody has it
  --claim     load the model once, then take shards until none are left
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "embed_out"
MODEL = os.environ.get("POOL57_EMBED_MODEL", "/scratch/gpfs/TRIDAO/al9080/models/Qwen3-Embedding-8B")
MAXTOK = 2048
N_SHARDS = 64
STALE_S = 3600
CHUNK = 2000
TASK = ("Given a terminal/software-engineering benchmark task, retrieve training tasks that require the same "
        "skills, sit in the same technical domain, and have a similar task shape.")
HOST = os.uname().nodename


def log(**kw):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] host={HOST} pid={os.getpid()} " + \
           " ".join(f"{k}={v}" for k, v in kw.items())
    print(line, flush=True)
    with (HERE / "logs" / "embed.log").open("a") as fh:
        fh.write(line + "\n")


def acquire(s, tag=""):
    out, lock = OUT / f"shard_{s:03d}{tag}.npz", OUT / f"shard_{s:03d}{tag}.lock"
    if out.exists():
        return None
    if lock.exists() and time.time() - lock.stat().st_mtime > STALE_S:
        log(item=f"shard_{s:03d}", status="stale_lock_removed", lock=lock.read_text().strip())
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, f"{HOST} {os.getpid()} {time.time():.0f}\n".encode()); os.close(fd)
    if out.exists():  # finished between the check and the lock
        lock.unlink(missing_ok=True); return None
    return out, lock


class HFEmbedder:
    """Plain transformers forward, Qwen3-Embedding's reference recipe: append EOS,
    take the last token's hidden state, L2-normalise. The vLLM dev build on
    della-tridao's B300s stalls after a few chunks (engine sleeping, GPU at 0%), so
    there this backend is used instead (POOL57_BACKEND=hf)."""

    TOKEN_BUDGET = 96 * 1024  # padded tokens per forward

    def __init__(self):
        import torch
        from transformers import AutoModel, AutoTokenizer
        t = time.time()
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(MODEL).backend_tokenizer
        self.tok.enable_truncation(MAXTOK)
        self.tok.no_padding()
        self.model = AutoModel.from_pretrained(MODEL, dtype=torch.bfloat16, attn_implementation="sdpa").cuda().eval()
        queries = [json.loads(l) for l in (HERE / "embed_input/queries.jsonl").open()]
        self.qids = [q["id"] for q in queries]
        self.qv = self.embed([f"Instruct: {TASK}\nQuery:{q['text']}" for q in queries])[0]
        log(item="model", status="loaded", backend="hf", model=MODEL, load_s=round(time.time() - t, 1),
            gpu=os.environ.get("CUDA_VISIBLE_DEVICES"))

    def embed(self, texts, item=None):
        torch = self.torch
        enc = [e.ids for e in self.tok.encode_batch(texts, add_special_tokens=True)]
        order = sorted(range(len(enc)), key=lambda i: -len(enc[i]))
        out = np.zeros((len(enc), self.model.config.hidden_size), dtype=np.float32)
        i, done, t0 = 0, 0, time.time()
        with torch.inference_mode():
            while i < len(order):
                L = len(enc[order[i]])
                n = max(1, self.TOKEN_BUDGET // L)
                idx = order[i:i + n]
                ids = torch.zeros((len(idx), L), dtype=torch.long)
                mask = torch.zeros((len(idx), L), dtype=torch.long)
                for r, j in enumerate(idx):
                    ids[r, :len(enc[j])] = torch.tensor(enc[j]); mask[r, :len(enc[j])] = 1
                h = self.model(input_ids=ids.cuda(), attention_mask=mask.cuda()).last_hidden_state
                last = mask.sum(1).cuda() - 1
                v = torch.nn.functional.normalize(h[torch.arange(len(idx)), last].float(), dim=-1)
                out[idx] = v.cpu().numpy()
                i += len(idx); done += len(idx)
                if item and done // CHUNK != (done - len(idx)) // CHUNK:
                    log(item=item, status="progress", done=done, of=len(enc), backend="hf",
                        tok_per_s=round(sum(len(enc[k]) for k in order[:i]) / (time.time() - t0)))
        return out, sum(len(x) for x in enc)

    def shard(self, s, out, lock, limit=None):
        Embedder.shard(self, s, out, lock, limit)


class Embedder:
    def __init__(self):
        from transformers import AutoTokenizer
        from vllm import LLM
        t = time.time()
        # The Rust backend's encode_batch is parallel; the transformers __call__
        # converts each encoding in Python and left the GPU idle for minutes.
        self.tok = AutoTokenizer.from_pretrained(MODEL).backend_tokenizer
        # Qwen3-Embedding pools the last token and expects it to be <|endoftext|>,
        # which this tokenizer's post-processor appends only with special tokens on.
        self.tok.enable_truncation(MAXTOK)
        self.tok.no_padding()
        # Pooling defaults schedule about one 2048-token prompt per step, which left
        # the GPU at 5-16% utilisation; pack many prompts into each step instead.
        self.llm = LLM(model=MODEL, runner="pooling", max_model_len=MAXTOK, dtype="bfloat16",
                       gpu_memory_utilization=0.85, max_num_batched_tokens=131072, max_num_seqs=512)
        queries = [json.loads(l) for l in (HERE / "embed_input/queries.jsonl").open()]
        self.qids = [q["id"] for q in queries]
        self.qv = self.embed([f"Instruct: {TASK}\nQuery:{q['text']}" for q in queries])[0]
        log(item="model", status="loaded", model=MODEL, load_s=round(time.time() - t, 1),
            gpu=os.environ.get("CUDA_VISIBLE_DEVICES"))

    def embed(self, texts, item=None):
        # One llm.embed call over a whole 16-60k-doc shard ran far slower than the
        # 3k-doc smoke rate (30+ min without finishing); calls of CHUNK docs keep the
        # smoke regime and give a progress line per chunk.
        if len(texts) > CHUNK:
            vs, n = [], 0
            for i in range(0, len(texts), CHUNK):
                t = time.time()
                v, k = self.embed(texts[i:i + CHUNK])
                vs.append(v); n += k
                if item:
                    log(item=item, status="progress", done=min(i + CHUNK, len(texts)), of=len(texts),
                        tok_per_s=round(k / (time.time() - t)))
            return np.concatenate(vs), n
        enc = [e.ids for e in self.tok.encode_batch(texts, add_special_tokens=True)]
        outs = self.llm.embed([{"prompt_token_ids": ids} for ids in enc], use_tqdm=False)
        data = getattr(outs[0].outputs, "data", None)
        if data is not None:
            import torch
            v = torch.stack([o.outputs.data for o in outs]).float().cpu().numpy()
        else:
            v = np.array([o.outputs.embedding for o in outs], dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True), sum(len(x) for x in enc)

    def shard(self, s, out, lock, limit=None):
        rows = [json.loads(l) for l in (HERE / "embed_input" / f"shard_{s:03d}.jsonl").open()]
        rows = rows[:limit] if limit else rows
        log(item=f"shard_{s:03d}", status="start", docs=len(rows))
        t = time.time()
        dv, ntok = self.embed([r["text"] for r in rows], item=f"shard_{s:03d}")
        tmp = out.with_suffix(".partial.npz")
        np.savez(tmp, ids=np.array([r["id"] for r in rows]), qids=np.array(self.qids), d=dv.astype(np.float16),
                 q=self.qv.astype(np.float16), scores=(self.qv @ dv.T).astype(np.float16))
        tmp.replace(out)
        lock.unlink(missing_ok=True)
        dt = time.time() - t
        log(item=f"shard_{s:03d}", status="done", docs=len(rows), tokens=ntok, embed_s=round(dt, 1),
            docs_per_s=round(len(rows) / dt, 1), tok_per_s=round(ntok / dt))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int)
    ap.add_argument("--claim", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tag", default="")
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--last", type=int, default=N_SHARDS - 1)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    emb = None
    if a.shard is not None:
        got = acquire(a.shard, a.tag)
        if not got:
            log(item=f"shard_{a.shard:03d}", status="skip", reason="done_or_taken"); return
        emb = HFEmbedder() if os.environ.get("POOL57_BACKEND") == "hf" else Embedder()
        try:
            emb.shard(a.shard, *got, limit=a.limit)
        except BaseException:
            got[1].unlink(missing_ok=True); raise
        return
    assert a.claim
    while True:
        got = None
        for s in range(a.first, a.last + 1):
            got = acquire(s)
            if got:
                break
        if not got:
            log(item="claim", status="end", reason="no_shards_left"); return
        try:
            emb = emb or (HFEmbedder() if os.environ.get("POOL57_BACKEND") == "hf" else Embedder())
            emb.shard(s, *got)
        except BaseException:
            got[1].unlink(missing_ok=True); raise


if __name__ == "__main__":
    main()
