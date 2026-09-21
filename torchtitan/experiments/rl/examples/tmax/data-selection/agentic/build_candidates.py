#!/usr/bin/env python
"""Per-corpus candidate recall for TB2.1 -> 5 training pools.

Four retrievers, each fit *per corpus* (not on the pooled 56k, so a big
corpus cannot starve a small one):

  tfidf_word  TF-IDF 1-2gram cosine        topical overlap
  tfidf_char  TF-IDF char_wb 3-5gram       tool / filename / flag names
  bm25        Okapi BM25 on 1-2grams       length-normalised lexical
  dense       gte-Qwen2-1.5B-instruct cos  paraphrase / skill-level match

The union of each retriever's top-N is the shortlist an agent then reranks.
Recall is the only goal here; precision is the agent's job.
"""

from __future__ import annotations

import argparse
import json
import re
import os
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import normalize

HERE = Path(__file__).resolve().parent
CACHE = HERE / "work" / "cache"
CORPORA = [
    "TMax-15K",
    "Recursive-Task-Synthesis",
    "TerminalWorld-Seeds-Clean",
    "SWE-Smith-Seeds-Clean",
    "SWE-Rebench-Tasks-Clean",
]
SNIPPET = 1400
GPU_MEM_CAP_GB = 10.0
EMB_CACHE = HERE / "work" / "emb"
EMB_MODEL = "Alibaba-NLP/gte-Qwen2-1.5B-instruct"
# gte-Qwen2-instruct wants a task instruction prefix on the query side only.
QUERY_PROMPT = (
    "Instruct: Given a terminal/software-engineering benchmark task, retrieve "
    "training tasks that require the same skills, sit in the same technical "
    "domain, and have a similar task shape.\nQuery: "
)


def log(msg: str) -> None:
    print(f"[cand] {msg}", flush=True)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# Sections the corpus composer emits after the instruction block.
SECTION_RE = re.compile(
    r"(?m)^(?:INSTRUCTION|META|VERIFIER|ASSETS|ORACLE|DOCKERFILE|TESTS)$"
)


def instruction_of(row: dict) -> str:
    """Full instruction block.

    compute_tb21_tfidf.py's version cuts at the first blank line, which for the
    parquet corpora (TMax, RTS) keeps only the opening sentence. Cut at the next
    section header instead, so the retrievers see the whole problem statement.
    """
    inst = row.get("instruction")
    if inst:
        return inst.strip()
    text = row.get("text") or ""
    m = SECTION_RE.search(text)
    if not m or m.group(0) != "INSTRUCTION":
        return text[:8000]
    body = text[m.end() :]
    nxt = SECTION_RE.search(body)
    return (body[: nxt.start()] if nxt else body).strip()[:8000]


def topn(scores: np.ndarray, n: int) -> np.ndarray:
    n = min(n, scores.size)
    idx = np.argpartition(-scores, n - 1)[:n]
    return idx[np.argsort(-scores[idx])]


def bm25_scores(q_texts, d_texts, k1=1.5, b=0.75) -> np.ndarray:
    """Dense (n_q, n_d) BM25 matrix. Fine at this scale (<=37k docs)."""
    cv = CountVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.85, max_features=200_000)
    D = cv.fit_transform(d_texts).astype(np.float32)
    Q = cv.transform(q_texts)
    n_d = D.shape[0]
    df = np.asarray((D > 0).sum(axis=0)).ravel()
    idf = np.log(1.0 + (n_d - df + 0.5) / (df + 0.5)).astype(np.float32)
    dl = np.asarray(D.sum(axis=1)).ravel().astype(np.float32)
    avgdl = float(dl.mean()) or 1.0
    # BM25 term weight depends only on the doc, so bake it into D once.
    D = D.tocsr()
    denom_doc = (k1 * (1.0 - b + b * dl / avgdl)).astype(np.float32)
    rows = np.repeat(np.arange(n_d), np.diff(D.indptr))
    tf = D.data
    D.data = ((tf * (k1 + 1.0)) / (tf + denom_doc[rows])) * idf[D.indices]
    # Query side: presence only (standard for short queries).
    Q = (Q > 0).astype(np.float32)
    return np.asarray((Q @ D.T).todense())


def dense_vecs(q_texts, d_texts, device: str, batch: int):
    import torch
    from sentence_transformers import SentenceTransformer

    model = dense_vecs._model  # type: ignore[attr-defined]
    if model is None:
        log(f"loading {EMB_MODEL} on {device}")
        if device.startswith("cuda"):
            # This node's GPUs are running someone else's training at ~95%
            # memory. Hard-cap our share so we can never grow into theirs.
            idx = int(device.split(":")[1]) if ":" in device else 0
            total = torch.cuda.get_device_properties(idx).total_memory
            torch.cuda.set_per_process_memory_fraction(GPU_MEM_CAP_GB * 2**30 / total, idx)
        model = SentenceTransformer(
            EMB_MODEL,
            device=device,
            model_kwargs={"torch_dtype": "bfloat16"},
            tokenizer_kwargs={"padding_side": "left"},
        )
        model.max_seq_length = 2048
        dense_vecs._model = model  # type: ignore[attr-defined]
    dv = model.encode(
        d_texts, batch_size=batch, normalize_embeddings=True, show_progress_bar=False
    )
    qv = model.encode(
        [QUERY_PROMPT + t for t in q_texts],
        batch_size=batch,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return qv, dv


dense_vecs._model = None  # type: ignore[attr-defined]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "work" / "candidates")
    ap.add_argument("--per-retriever", type=int, default=25)
    ap.add_argument("--device", default="cuda:7")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--no-dense", action="store_true")
    ap.add_argument("--corpora", nargs="*", default=CORPORA)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    queries = load_jsonl(CACHE / "tb21_docs.jsonl")
    q_texts = [instruction_of(q) for q in queries]
    log(f"queries: {len(queries)}")

    # per_task[tb_id][corpus] = {task_id: {retriever: (rank, score)}}
    per_task: dict[str, dict] = {q["task_id"]: {} for q in queries}
    meta: dict[str, dict] = {}

    for corpus in args.corpora:
        rows = load_jsonl(CACHE / f"{corpus}.jsonl")
        d_texts = [instruction_of(r) for r in rows]
        log(f"[{corpus}] docs={len(rows)}")

        sig: dict[str, np.ndarray] = {}
        for tag, kw in (
            ("tfidf_word", dict(ngram_range=(1, 2), min_df=2, max_df=0.85,
                                max_features=200_000, sublinear_tf=True)),
            ("tfidf_char", dict(analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                                max_df=0.9, max_features=300_000, sublinear_tf=True)),
        ):
            vec = TfidfVectorizer(**kw)
            D = normalize(vec.fit_transform(d_texts))
            Q = normalize(vec.transform(q_texts))
            sig[tag] = np.asarray((Q @ D.T).todense())
            log(f"[{corpus}] {tag} done")

        sig["bm25"] = bm25_scores(q_texts, d_texts)
        log(f"[{corpus}] bm25 done")

        if not args.no_dense:
            npz = EMB_CACHE / f"{corpus}.npz"
            if npz.exists():
                z = np.load(npz)
                sig["dense"] = (z["q"] @ z["d"].T).astype(np.float32)
                log(f"[{corpus}] dense reused from cache")
            else:
                qv, dv = dense_vecs(q_texts, d_texts, args.device, args.batch)
                EMB_CACHE.mkdir(parents=True, exist_ok=True)
                np.savez(npz, q=qv, d=dv)
                sig["dense"] = (qv @ dv.T).astype(np.float32)
                log(f"[{corpus}] dense done")

        for qi, q in enumerate(queries):
            bucket = per_task[q["task_id"]].setdefault(corpus, {})
            for tag, S in sig.items():
                for rank, di in enumerate(topn(S[qi], args.per_retriever), 1):
                    ent = bucket.setdefault(rows[di]["task_id"], {})
                    ent[tag] = [rank, round(float(S[qi, di]), 4)]

        for r in rows:
            meta[r["task_id"]] = {
                "source": corpus,
                "instruction": instruction_of(r),
                "origin": r.get("origin", ""),
            }
        del sig, rows, d_texts

    # Rank fusion (RRF) so the shortlist is ordered even before the agent looks.
    out_index = {}
    for q in queries:
        tb = q["task_id"]
        packet = {}
        for corpus, bucket in per_task[tb].items():
            scored = []
            for tid, sigs in bucket.items():
                rrf = sum(1.0 / (60 + r) for r, _ in sigs.values())
                scored.append((rrf, tid, sigs))
            scored.sort(key=lambda x: -x[0])
            packet[corpus] = [
                {
                    "task_id": t,
                    "rrf": round(s, 5),
                    "n_hits": len(g),
                    "signals": g,
                    "snippet": meta[t]["instruction"][:SNIPPET],
                    "inst_chars": len(meta[t]["instruction"]),
                }
                for s, t, g in scored
            ]
        out_index[tb] = packet
        (args.out / f"{tb}.json").write_text(
            json.dumps(
                {"tb21_id": tb, "tb21_instruction": instruction_of(q),
                 "candidates": packet},
                indent=1,
            )
        )

    (args.out / "_meta.json").write_text(json.dumps(meta))
    sizes = {
        tb: {c: len(v) for c, v in p.items()} for tb, p in list(out_index.items())[:3]
    }
    log(f"wrote {len(out_index)} candidate files; sample sizes {sizes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
