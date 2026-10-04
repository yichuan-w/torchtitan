"""Stage 1: export the candidate pool and the TB2.1 queries from pool.sqlite.

Writes work/docs.jsonl (one line per distinct text, with every source it
appears under) and work/queries.jsonl (the 89 TB2.1 tasks).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from common import BENCHMARKS_ALLOWED, POOL, ROOT, SOURCE_GROUP, log


def main() -> None:
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    db = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
    log("export", item="start", pool=POOL)

    q = db.execute(
        "SELECT o.task_id, d.id, d.instruction FROM occurrences o JOIN docs d ON d.id=o.doc_id "
        "WHERE o.source='tb21_docs' AND o.task_id LIKE 'tb21__%' ORDER BY o.task_id"
    ).fetchall()
    assert len(q) == 89, len(q)
    with (work / "queries.jsonl").open("w") as fh:
        for tid, did, text in q:
            fh.write(json.dumps({"query_id": tid, "name": tid.split("__", 1)[1], "doc_id": did,
                                 "instruction": text}, ensure_ascii=False) + "\n")
    log("export", item="queries", n=len(q))

    occ = defaultdict(list)
    marks = ",".join("?" * len(BENCHMARKS_ALLOWED))
    cur = db.execute(
        "SELECT doc_id, source, kind, task_id, original_source FROM occurrences "
        f"WHERE kind='environment' OR (kind='benchmark' AND source IN ({marks}))",
        BENCHMARKS_ALLOWED,
    )
    unknown = set()
    for did, source, kind, tid, orig in cur:
        if source not in SOURCE_GROUP:
            unknown.add(source)
            continue
        occ[did].append([source, tid, orig])
    if unknown:
        log("export", item="unmapped_sources", status="skip", sources=sorted(unknown))
    log("export", item="occurrences", docs=len(occ))

    n = 0
    per_source = defaultdict(int)
    with (work / "docs.partial").open("w") as fh:
        # Ranged scan instead of IN (...): sqlite caps bound variables.
        top = max(occ)
        for lo in range(0, top + 1, 200000):
            rows = db.execute("SELECT id, instruction FROM docs WHERE id >= ? AND id < ?", (lo, lo + 200000))
            for did, text in rows:
                if did not in occ:
                    continue
                fh.write(json.dumps({"id": f"d{did}", "text": text, "occ": occ[did]}, ensure_ascii=False) + "\n")
                n += 1
                for s in {o[0] for o in occ[did]}:
                    per_source[s] += 1
            log("export", item="docs_progress", upto=lo + 200000, written=n)
    (work / "docs.partial").replace(work / "docs.jsonl")
    (work / "export_counts.json").write_text(json.dumps({"docs": n, "per_source": dict(per_source)}, indent=1))
    log("export", item="done", docs=n)


if __name__ == "__main__":
    main()
