"""Stage 3: per-source lexical recall over the deduplicated representatives.

Follows agentic/build_candidates.py: each retriever is fit per source so a
1.6M-row aggregator cannot starve a 47-row benchmark, each contributes its
top PER_RETRIEVER, and the union is ordered by reciprocal-rank fusion. Two
departures, both forced by scale: no dense retriever (the node's GPUs are held
by other people's training, and 2M docs at 2048 tokens is hours of GPU), and
no char 3-5gram TF-IDF above CHAR_MAX docs, where its matrix runs to billions
of non-zeros. Agents' own searches cover what lexical recall misses, as in v2.
"""

from __future__ import annotations

import json
from collections import defaultdict
from multiprocessing import Pool

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import normalize as l2

from common import ROOT, SOURCE_GROUP, log, read_jsonl

PER_RETRIEVER = 25
CHAR_MAX = 200_000
MAXLEN = 8000


def topn(scores, n):
    n = min(n, scores.size)
    idx = np.argpartition(-scores, n - 1)[:n]
    return idx[np.argsort(-scores[idx])]


def bm25(q_texts, d_texts, k1=1.5, b=0.75):
    cv = CountVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.85, max_features=200_000, dtype=np.float32)
    D = cv.fit_transform(d_texts).tocsr()
    Q = (cv.transform(q_texts) > 0).astype(np.float32)
    n_d = D.shape[0]
    df = np.bincount(D.indices, minlength=D.shape[1])
    idf = np.log(1.0 + (n_d - df + 0.5) / (df + 0.5)).astype(np.float32)
    dl = np.asarray(D.sum(axis=1)).ravel()
    denom = (k1 * (1.0 - b + b * dl / (dl.mean() or 1.0))).astype(np.float32)
    rows = np.repeat(np.arange(n_d), np.diff(D.indptr))
    D.data = ((D.data * (k1 + 1.0)) / (D.data + denom[rows])) * idf[D.indices]
    return (Q @ D.T).toarray()


def one_source(args):
    source, ids, d_texts, q_texts = args
    sig = {}
    try:
        configs = [("tfidf_word", dict(ngram_range=(1, 2), min_df=2, max_df=0.85, max_features=200_000, sublinear_tf=True))]
        if len(ids) <= CHAR_MAX:
            configs.append(("tfidf_char", dict(analyzer="char_wb", ngram_range=(3, 5), min_df=3, max_df=0.9,
                                               max_features=300_000, sublinear_tf=True)))
        for tag, kw in configs:
            # min_df=2/3 is meaningless on a 47-row benchmark: relax it there.
            if len(ids) < 500:
                kw = {**kw, "min_df": 1}
            vec = TfidfVectorizer(dtype=np.float32, **kw)
            D = l2(vec.fit_transform(d_texts))
            sig[tag] = (l2(vec.transform(q_texts)) @ D.T).toarray()
        try:
            sig["bm25"] = bm25(q_texts, d_texts)
        except ValueError:  # tiny source: min_df=2 leaves no vocabulary
            pass
    except Exception as exc:
        return source, None, repr(exc)
    out = []
    for qi in range(len(q_texts)):
        bucket = defaultdict(dict)
        for tag, S in sig.items():
            for rank, di in enumerate(topn(S[qi], PER_RETRIEVER), 1):
                bucket[ids[di]][tag] = [rank, round(float(S[qi, di]), 4)]
        scored = sorted(((sum(1.0 / (60 + r) for r, _ in g.values()), t, g) for t, g in bucket.items()),
                        key=lambda x: -x[0])
        out.append([{"id": t, "source": source, "rrf": round(s, 5), "n_hits": len(g), "signals": g}
                     for s, t, g in scored])
    return source, out, sorted(sig)


def main():
    work = ROOT / "work"
    out_dir = work / "recall"
    out_dir.mkdir(exist_ok=True)
    queries = list(read_jsonl(work / "queries.jsonl"))
    q_texts = [q["instruction"][:MAXLEN] for q in queries]
    by_source = defaultdict(lambda: ([], []))
    for d in read_jsonl(work / "reps.jsonl"):
        # A representative is retrieved under its own primary source: the first
        # of its occurrences that lies in the group it was filed under.
        src = next(o[0] for o in d["occ"] if SOURCE_GROUP[o[0]] == d["group"])
        by_source[src][0].append(d["id"]); by_source[src][1].append(d["text"][:MAXLEN])
    todo = [(s, ids, texts, q_texts) for s, (ids, texts) in sorted(by_source.items(), key=lambda x: -len(x[1][0]))
            if not (out_dir / f"{s}.json").exists()]
    log("recall", item="start", sources=len(by_source), todo=len(todo), per_retriever=PER_RETRIEVER, char_max=CHAR_MAX)
    for s, ids, _, _ in todo:
        log("recall", item=s, status="start", docs=len(ids))
    with Pool(min(len(todo), 32) or 1) as pool:
        for source, res, info in pool.imap_unordered(one_source, todo):
            if res is None:
                log("recall", item=source, status="fail", reason=info)
                continue
            (out_dir / f"{source}.json").write_text(json.dumps(res))
            log("recall", item=source, status="done", retrievers=",".join(info))
    missing = [s for s in by_source if not (out_dir / f"{s}.json").exists()]
    log("recall", item="end", status="fail" if missing else "done", missing=missing)


if __name__ == "__main__":
    main()
