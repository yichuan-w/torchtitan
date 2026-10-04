"""Stage 4a: agent-sized packets, plus the search indexes the agents use.

Per query and group, the per-source recall lists are interleaved by rank
(round-robin), so every source in a group puts its best forward before any
source's second-best; SHOW of them carry snippets, the rest are bare ids.
Also writes text/<group>.tsv (one representative per line, for ripgrep) and
meta.sqlite (pool doc id -> representative, for FTS lookups through the
pool's own index).
"""

from __future__ import annotations

import json
import os
import sqlite3
from itertools import zip_longest

from common import GROUPS, PRIOR, ROOT, log, read_jsonl

SHOW = 16
SNIPPET = 460


def load_grades():
    grades = json.loads((PRIOR / "inputs/verifier_strength.json").read_text())
    for tid, o in json.loads((PRIOR / "inputs/verifier_overrides.json").read_text()).items():
        if not tid.startswith("_"):
            grades.setdefault(tid, {})["grade"] = o["actually"]
    return grades


def grade_of(rep, grades):
    """Best-known grade over the family's occurrences; 'unknown' means never graded."""
    found = [grades[o[1]]["grade"] for o in rep["occ"] if o[1] in grades and "grade" in grades[o[1]]]
    if not found:
        return "unknown"
    order = ["repo-tests", "strong", "ok", "behavioral", "weak", "absent", "FREE"]
    return min(found, key=lambda g: order.index(g) if g in order else len(order))


def build_indexes(work, grades):
    reps = {}
    (work / "text").mkdir(exist_ok=True)
    tsv = {g: (work / "text" / f"{g}.tsv").open("w") for g in GROUPS}
    meta = sqlite3.connect(work / "meta.partial")
    meta.execute("DROP TABLE IF EXISTS m")
    meta.execute("CREATE TABLE m(doc INTEGER PRIMARY KEY, rep TEXT, grp TEXT)")
    rows = []
    for d in read_jsonl(work / "reps.jsonl"):
        srcs = sorted({o[0] for o in d["occ"]})
        reps[d["id"]] = {"group": d["group"], "text": d["text"], "sources": srcs,
                         "family_size": len(d["family"]), "verifier": grade_of(d, grades)}
        tsv[d["group"]].write(f"{d['id']}\t{','.join(srcs)}\t{' '.join(d['text'].split())}\n")
        rows.extend((int(m[1:]), d["id"], d["group"]) for m in d["family"])
    for fh in tsv.values():
        fh.close()
    meta.executemany("INSERT INTO m VALUES (?,?,?)", rows)
    meta.commit(); meta.close()
    (work / "meta.partial").replace(work / "meta.sqlite")
    (work / "rep_index.json").write_text(json.dumps(
        {i: {k: v for k, v in r.items() if k != "text"} for i, r in reps.items()}))
    log("packets", item="indexes", reps=len(reps), meta_rows=len(rows))
    return reps


def main():
    import sys
    work = ROOT / "work"
    grades = load_grades()
    reps = build_indexes(work, grades) if not (work / "meta.sqlite").exists() else None
    if "--indexes-only" in sys.argv:
        return
    if reps is None:
        idx = json.loads((work / "rep_index.json").read_text())
        reps = {}
        for d in read_jsonl(work / "reps.jsonl"):
            reps[d["id"]] = {**idx[d["id"]], "text": d["text"]}

    recall = {}
    # recall_full = lexical + dense (the real run); POOL57_RECALL=recall only for harness smoke tests.
    for f in sorted((work / os.environ.get("POOL57_RECALL", "recall_full")).glob("*.json")):
        recall[f.stem] = json.loads(f.read_text())
    queries = list(read_jsonl(work / "queries.jsonl"))
    (ROOT / "packets").mkdir(exist_ok=True)
    for qi, q in enumerate(queries):
        packet = {"query_id": q["query_id"], "tb21_instruction": q["instruction"], "candidates": {}}
        for g, sources in GROUPS.items():
            lists = [recall[s][qi] for s in sources if s in recall]
            merged, seen = [], set()
            for tier in zip_longest(*lists):
                for e in tier:
                    if e and e["id"] not in seen:
                        seen.add(e["id"]); merged.append(e)
            shown = []
            for e in merged[:SHOW]:
                r = reps[e["id"]]
                shown.append({"id": e["id"], "source": e["source"], "also_in": [s for s in r["sources"] if s != e["source"]],
                              "family_size": r["family_size"], "n_hits": e["n_hits"], "rrf": e["rrf"],
                              "found_by": sorted(e["signals"]), "inst_chars": len(r["text"]),
                              "snippet": " ".join(r["text"].split())[:SNIPPET], "verifier": r["verifier"]})
            packet["candidates"][g] = {"sources": sources, "n_total": len(merged), "shown": shown,
                                       "rest_ids": [e["id"] for e in merged[SHOW:]]}
        (ROOT / "packets" / f"{q['query_id']}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1))
        log("packets", item=q["query_id"], status="done",
            sizes=",".join(str(packet["candidates"][g]["n_total"]) for g in GROUPS))


if __name__ == "__main__":
    main()
