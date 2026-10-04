"""Stage 6: dense contamination screen over the selected tasks.

The lexical screen in dedup.py misses paraphrases (v2 caught polyglot-c-py's
twin only by dense similarity). Uses the Qwen3-Embedding-8B query-document
cosines already computed for recall (work/dense_scores.npy), lists every
(query, selected task) pair at or above FLAG, and reports the known twin's
score so the threshold can be read against a real positive.
"""

from __future__ import annotations

import csv
import json
import sys

import numpy as np

from common import ROOT, log, read_jsonl

FLAG = float(sys.argv[1]) if len(sys.argv) > 1 else 0.80
KNOWN_TWIN = ("tb21__polyglot-c-py", "d197717")  # CalibForge software-engineering_20260617_210354_017


def main():
    work = ROOT / "work"
    q = [x["query_id"] for x in read_jsonl(work / "queries.jsonl")]
    ids = np.load(work / "dense_scores_ids.npy")
    S = np.load(work / "dense_scores.npy", mmap_mode="r")
    pos = {str(x): j for j, x in enumerate(ids)}
    rows = list(csv.DictReader((ROOT / "results/tb21_top10.csv").open()))
    flagged = []
    for r in rows:
        row = np.asarray(S[q.index(r["query_id"])], dtype=np.float32)
        c = float(row[pos[r["id"]]])
        r["dense_cos"] = round(c, 4)
        r["dense_rank_in_pool"] = int((row > c).sum()) + 1
        if c >= FLAG:
            flagged.append({k: r[k] for k in ["query_id", "rank", "id", "sources", "original_ids", "dense_cos",
                                              "dense_rank_in_pool"]})
    with (ROOT / "results/tb21_top10_dense.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    qt, dt = KNOWN_TWIN
    twin = float(np.asarray(S[q.index(qt)], dtype=np.float32)[pos[dt]]) if qt in q and dt in pos else None
    all_top = np.sort(np.asarray(S, dtype=np.float32).max(axis=1))[::-1]
    out = {"model": "Qwen/Qwen3-Embedding-8B", "flag": FLAG, "known_twin": {"pair": KNOWN_TWIN, "cos": twin},
           "per_query_max_cos_quantiles": {p: round(float(np.quantile(all_top, p / 100)), 4) for p in (10, 50, 90)},
           "flagged": sorted(flagged, key=lambda x: -x["dense_cos"])}
    (ROOT / "results/dense_contamination.json").write_text(json.dumps(out, indent=1))
    log("dense_screen", item="done", flag=FLAG, flagged=len(flagged), known_twin_cos=twin)
    print(json.dumps({k: v for k, v in out.items() if k != "flagged"}, indent=1), "\nflagged:", len(flagged))


if __name__ == "__main__":
    main()
