"""Split representatives into embedding shards ({id, text} per line) plus the queries.

Shards are filled in id order and sized by character count, so each takes
about the same GPU time.
"""
import json
from common import ROOT, log, read_jsonl

N_SHARDS = 64
MAXCHARS = 12000  # well past the 2048-token window the embedder truncates at


def main():
    out = ROOT / "work/embed_input"
    out.mkdir(exist_ok=True)
    docs = [(d["id"], d["text"][:MAXCHARS]) for d in read_jsonl(ROOT / "work/reps.jsonl")]
    total = sum(len(t) for _, t in docs)
    target, k, acc = total / N_SHARDS, 0, 0
    fh = (out / f"shard_{k:03d}.jsonl").open("w")
    for i, t in docs:
        if acc >= target * (k + 1) and k < N_SHARDS - 1:
            fh.close(); k += 1; fh = (out / f"shard_{k:03d}.jsonl").open("w")
        fh.write(json.dumps({"id": i, "text": t}, ensure_ascii=False) + "\n"); acc += len(t)
    fh.close()
    with (out / "queries.jsonl").open("w") as q:
        for d in read_jsonl(ROOT / "work/queries.jsonl"):
            q.write(json.dumps({"id": d["query_id"], "text": d["instruction"]}, ensure_ascii=False) + "\n")
    log("shards", item="done", docs=len(docs), shards=k + 1, total_chars=total)


if __name__ == "__main__":
    main()
