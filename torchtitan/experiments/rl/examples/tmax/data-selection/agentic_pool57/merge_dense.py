"""Stage 3b: add the dense retriever to each source's recall lists.

Reads work/embed_out/shard_*.npz (89 x n Qwen3-Embedding-8B scores per shard),
takes each source's top PER_RETRIEVER by dense score, and re-fuses the four
retrievers with RRF exactly as recall.py and agentic/build_candidates.py do.
Writes work/recall_full/<source>.json; recall/ is left as the lexical-only record.
"""

from __future__ import annotations

import json
from collections import defaultdict

import numpy as np

from common import ROOT, SOURCE_GROUP, log, read_jsonl
from recall import PER_RETRIEVER, topn


def main():
    work = ROOT / "work"
    queries = [q["query_id"] for q in read_jsonl(work / "queries.jsonl")]
    # 64 pool shards plus shard_064, the extra.py tasks (Turing Labs).
    shards = sorted((work / "embed_out").glob("shard_[0-9][0-9][0-9].npz"))
    assert len(shards) == 65, len(shards)
    ids, scores = [], []
    for f in shards:
        z = np.load(f)
        assert list(z["qids"]) == queries, f
        ids.extend(z["ids"].tolist()); scores.append(z["scores"].astype(np.float32))
    S = np.concatenate(scores, axis=1)
    log("merge_dense", item="loaded", docs=len(ids), shards=len(shards))
    src_of = {}
    for d in read_jsonl(work / "reps.jsonl"):
        src_of[d["id"]] = next(o[0] for o in d["occ"] if SOURCE_GROUP[o[0]] == d["group"])
    assert len(src_of) == len(ids) and set(src_of) == set(ids)
    cols = defaultdict(list)
    for j, i in enumerate(ids):
        cols[src_of[i]].append(j)
    out = work / "recall_full"
    out.mkdir(exist_ok=True)
    for source, js in sorted(cols.items()):
        js = np.array(js)
        lex = json.loads((work / "recall" / f"{source}.json").read_text())
        full = []
        for qi in range(len(queries)):
            bucket = {e["id"]: dict(e["signals"]) for e in lex[qi]}
            sub = S[qi, js]
            for rank, k in enumerate(topn(sub, PER_RETRIEVER), 1):
                bucket.setdefault(ids[js[k]], {})["dense"] = [rank, round(float(sub[k]), 4)]
            scored = sorted(((sum(1.0 / (60 + r) for r, _ in g.values()), t, g) for t, g in bucket.items()),
                            key=lambda x: -x[0])
            full.append([{"id": t, "source": source, "rrf": round(s, 5), "n_hits": len(g), "signals": g}
                         for s, t, g in scored])
        (out / f"{source}.json").write_text(json.dumps(full))
        log("merge_dense", item=source, status="done", docs=len(js))
    np.save(work / "dense_scores_ids.npy", np.array(ids))
    np.save(work / "dense_scores.npy", S.astype(np.float16))
    log("merge_dense", item="end", status="done")


if __name__ == "__main__":
    main()
