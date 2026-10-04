"""Stage 2: screen out TB2.1 and collapse near-duplicates.

The pool itself only removed byte-identical instructions (SHA-256 of
instruction.strip()), which misses the same task with a canary comment or a
reworded line: TB2-Verified and TB2.1 share 42 texts by hash but all 89 by
name. Two things are done here, both on word 5-gram shingles of normalised
text:

1. Contamination: a row is dropped when one of its task ids names a TB2.1
   task, when its normalised text equals one, or when it contains at least
   CONTAM of some TB2.1 task's shingles. Every row with containment >= AUDIT
   is written out with its score so the threshold can be revisited.
2. Near-duplicates: MinHash (64 hashes, 16 bands x 4 rows) proposes pairs; a
   pair with estimated Jaccard >= NEAR is merged. Each family keeps one
   representative and carries every member's sources, so a task republished
   by an aggregator appears once.

Lexical only. A paraphrase of a TB2.1 task gets past it; the selected top-10s
are screened again with dense embeddings after the merge.
"""

from __future__ import annotations

import json
import re
import zlib
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

from common import GROUPS, PRIOR, ROOT, SOURCE_GROUP, log, normalize, read_jsonl

CONTAM = 0.5
AUDIT = 0.2
NEAR = 0.8
NPERM, BANDS, ROWS = 64, 16, 4
WORKERS = 128
K = 5
_TOK = re.compile(r"\w+")
_rng = np.random.default_rng(20261004)
A = (_rng.integers(1, 2**63, NPERM, dtype=np.uint64) | np.uint64(1))
B = _rng.integers(0, 2**63, NPERM, dtype=np.uint64)
MULT = np.array([np.uint64(1000003) ** np.uint64(i) for i in range(K)], dtype=np.uint64)
# Representative preference: sources whose verifiers we can grade first.
GROUP_PRIORITY = ["CalibForge", "TMax", "Terminal-Lego", "SWE-repo", "TerminalWorld",
                  "Other-benchmarks", "Terminal-env", "Bulk-synthetic"]

_Q = None  # (sorted query shingles, owner query index per shingle, |Q_i|, names, norm texts)


def shingles(text: str) -> np.ndarray:
    toks = np.array([zlib.crc32(t.encode()) for t in _TOK.findall(normalize(text))], dtype=np.uint64)
    if toks.size == 0:
        return np.zeros(1, dtype=np.uint64)
    if toks.size < K:
        return np.array([np.sum(toks * MULT[: toks.size])], dtype=np.uint64)
    win = np.lib.stride_tricks.sliding_window_view(toks, K)
    return np.unique((win * MULT).sum(axis=1))


def init(qdata):
    global _Q
    np.seterr(over="ignore")
    _Q = qdata


def work_chunk(lines):
    qsh, qown, qsize, names, qnorm = _Q
    out = []
    for line in lines:
        d = json.loads(line)
        sh = shingles(d["text"])
        sig = ((sh[:, None] * A[None, :]) + B[None, :]).min(axis=0)
        hits = []
        name_hit = sorted({seg for _, tid, _ in d["occ"] for seg in re.split(r"[/:]", tid or "") if seg in names})
        exact = normalize(d["text"]) in qnorm
        m = np.isin(qsh, sh, assume_unique=False)
        if m.any():
            cnt = np.bincount(qown[m], minlength=len(qsize))
            for qi in np.nonzero(cnt)[0]:
                c = cnt[qi] / qsize[qi]
                if c >= AUDIT:
                    hits.append([int(qi), round(float(c), 4)])
        out.append((d["id"], sig.tobytes(), int(sh.size), hits, name_hit, exact))
    return out


def chunks(path, n=2000):
    buf = []
    with path.open() as fh:
        for line in fh:
            buf.append(line)
            if len(buf) == n:
                yield buf
                buf = []
    if buf:
        yield buf


class DSU:
    def __init__(self, n):
        self.p = np.arange(n)

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def main():
    work = ROOT / "work"
    np.seterr(over="ignore")
    queries = list(read_jsonl(work / "queries.jsonl"))
    qsets = [shingles(q["instruction"]) for q in queries]
    qsh = np.concatenate(qsets)
    qown = np.concatenate([np.full(s.size, i) for i, s in enumerate(qsets)])
    qsize = np.array([s.size for s in qsets])
    names = {q["name"] for q in queries}
    qnorm = {normalize(q["instruction"]) for q in queries}
    blocked = {l.split("\t")[0] for l in (PRIOR / "inputs/calibforge_inspector_filler.txt").read_text().splitlines() if l.strip()}
    log("dedup", item="start", queries=len(queries), contam=CONTAM, audit=AUDIT, near=NEAR, workers=WORKERS)

    ids, sigs, sizes, contam_rows = [], [], [], []
    with Pool(WORKERS, initializer=init, initargs=((qsh, qown, qsize, names, qnorm),)) as pool:
        for k, res in enumerate(pool.imap(work_chunk, chunks(work / "docs.jsonl")), 1):
            for did, sig, n, hits, name_hit, exact in res:
                ids.append(did); sigs.append(np.frombuffer(sig, dtype=np.uint64)); sizes.append(n)
                if hits or name_hit or exact:
                    contam_rows.append({"id": did, "name_hit": name_hit, "exact": exact,
                                        "containment": [[queries[q]["query_id"], c] for q, c in sorted(hits, key=lambda x: -x[1])]})
            if k % 50 == 0:
                log("dedup", item="shingle_progress", docs=len(ids))
    log("dedup", item="shingle_done", docs=len(ids), audit_rows=len(contam_rows))

    excluded = {}
    for r in contam_rows:
        top = r["containment"][0][1] if r["containment"] else 0.0
        r["excluded"] = bool(r["name_hit"] or r["exact"] or top >= CONTAM)
        if r["excluded"]:
            excluded[r["id"]] = r
    with (work / "contamination.jsonl").open("w") as fh:
        for r in sorted(contam_rows, key=lambda x: -(x["containment"][0][1] if x["containment"] else 1)):
            fh.write(json.dumps(r) + "\n")
    log("dedup", item="contamination", excluded=len(excluded), audited=len(contam_rows))

    S = np.stack(sigs)
    del sigs
    dsu = DSU(len(ids))
    pairs = merged = 0
    for b in range(BANDS):
        keys = S[:, b * ROWS:(b + 1) * ROWS]
        h = (keys * MULT[:ROWS]).sum(axis=1)
        order = np.argsort(h, kind="stable")
        hs = h[order]
        starts = np.nonzero(np.r_[True, hs[1:] != hs[:-1]])[0]
        ends = np.r_[starts[1:], len(hs)]
        for s, e in zip(starts, ends):
            if e - s < 2:
                continue
            members = order[s:e]
            head = members[0]
            est = (S[members[1:]] == S[head]).mean(axis=1)
            pairs += len(members) - 1
            for m, j in zip(members[1:], est):
                if j >= NEAR:
                    dsu.union(int(head), int(m)); merged += 1
        log("dedup", item=f"band_{b}", pairs=pairs, merged_edges=merged)

    fam = defaultdict(list)
    for i in range(len(ids)):
        fam[dsu.find(i)].append(i)
    log("dedup", item="families", n=len(fam))

    docs = {}
    for d in read_jsonl(work / "docs.jsonl"):
        docs[d["id"]] = d
    stats = defaultdict(lambda: defaultdict(int))
    n_rep = 0
    with (work / "reps.partial").open("w") as fh:
        for members in fam.values():
            mids = [ids[i] for i in members]
            kept = []
            for mid in mids:
                d = docs[mid]
                srcs = {o[0] for o in d["occ"]}
                if mid in excluded:
                    for s in srcs: stats[s]["contaminated"] += 1
                    continue
                if any(o[1] in blocked for o in d["occ"] if o[0] == "CalibForge"):
                    for s in srcs: stats[s]["filler_blocked"] += 1
                    continue
                kept.append(mid)
            if not kept:
                continue
            def prio(mid):
                d = docs[mid]
                g = min(GROUP_PRIORITY.index(SOURCE_GROUP[o[0]]) for o in d["occ"])
                return (g, int(mid[1:]))
            kept.sort(key=prio)
            rep = docs[kept[0]]
            occ = [o for mid in kept for o in docs[mid]["occ"]]
            grp = GROUP_PRIORITY[prio(kept[0])[0]]
            for mid in kept[1:]:
                for s in {o[0] for o in docs[mid]["occ"]}: stats[s]["merged_into_family"] += 1
            for s in {o[0] for o in rep["occ"]}: stats[s]["representative"] += 1
            fh.write(json.dumps({"id": rep["id"], "group": grp, "text": rep["text"], "occ": occ,
                                 "family": kept}, ensure_ascii=False) + "\n")
            n_rep += 1
    (work / "reps.partial").replace(work / "reps.jsonl")
    summary = {"docs_in": len(ids), "families": len(fam), "representatives": n_rep,
               "contaminated_excluded": len(excluded), "params": {"contam": CONTAM, "audit": AUDIT, "near": NEAR,
               "nperm": NPERM, "bands": BANDS, "rows": ROWS, "shingle": f"word {K}-gram"},
               "per_source": {s: dict(v) for s, v in sorted(stats.items())},
               "per_group_reps": {g: 0 for g in GROUPS}}
    for d in read_jsonl(work / "reps.jsonl"):
        summary["per_group_reps"][d["group"]] += 1
    (work / "dedup_summary.json").write_text(json.dumps(summary, indent=1))
    log("dedup", item="done", representatives=n_rep, excluded=len(excluded))


if __name__ == "__main__":
    main()
