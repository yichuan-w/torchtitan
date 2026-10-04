"""Stage 5: validated rerank files -> CSVs and a summary.

Every file is re-validated (run_agents.validate); a failing one is named in
the summary, never merged silently. Each row is expanded back to the original
dataset ids so a mixer can fetch the task, and carries its family size.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter

from common import POOL, ROOT, read_jsonl
from run_agents import load_index, validate

FIELDS = ["query_id", "rank", "id", "group", "sources", "original_ids", "family_size", "verifier",
          "skill", "domain", "task_form", "overall", "reason"]


def main():
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    index = load_index()
    pool = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
    queries = [q["query_id"] for q in read_jsonl(ROOT / "work/queries.jsonl")]
    good, problems = {}, {}
    for q in queries:
        try:
            good[q] = validate(q, index)
        except Exception as exc:
            problems[q] = repr(exc)

    def row(q, e, group):
        r = index[e["task_id"]]
        occ = pool.execute("SELECT source, task_id FROM occurrences WHERE doc_id=?", (int(e["task_id"][1:]),)).fetchall()
        return {"query_id": q, "rank": e["rank"], "id": e["task_id"], "group": group, "sources": "|".join(r["sources"]),
                "original_ids": "|".join(f"{s}:{t}" for s, t in occ), "family_size": r["family_size"],
                "verifier": r["verifier"], **{k: e[k] for k in ["skill", "domain", "task_form", "overall"]},
                "reason": e["reason"]}

    final_rows, per_rows = [], []
    for q, d in good.items():
        final_rows += [row(q, e, e["source"]) for e in d["final_top10"]]
        for g, entries in d["per_source"].items():
            per_rows += [row(q, e, g) for e in entries]
    for name, rows in [("tb21_top10.csv", final_rows), ("tb21_per_group_top10.csv", per_rows)]:
        with (out / name).open("w", newline="") as fh:
            w = csv.DictWriter(fh, FIELDS); w.writeheader(); w.writerows(rows)
    with (out / "tb21_top10_ids.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["query_id", "top10_ids", "groups", "mean_overall"])
        for q, d in good.items():
            f = d["final_top10"]
            w.writerow([q, "|".join(e["task_id"] for e in f), "|".join(e["source"] for e in f),
                        round(sum(e["overall"] for e in f) / 10, 2)])

    near_copy = {q: d.get("notes", "") for q, d in good.items() if "NEAR-COPY OF QUERY" in (d.get("notes") or "")}
    src_slots = Counter(s for r in final_rows for s in r["sources"].split("|"))
    summary = {
        "n_queries": len(queries), "n_merged": len(good), "problems": problems,
        "final_group_slots": dict(Counter(r["group"] for r in final_rows)),
        "final_source_slots_any_occurrence": dict(src_slots.most_common()),
        "rank1_group_wins": dict(Counter(d["final_top10"][0]["source"] for d in good.values())),
        "final_verifier": dict(Counter(r["verifier"] for r in final_rows)),
        "mean_scores_final": {k: round(sum(r[k] for r in final_rows) / max(len(final_rows), 1), 3)
                              for k in ["skill", "domain", "task_form", "overall"]},
        "overall_histogram": dict(sorted(Counter(r["overall"] for r in final_rows).items())),
        "unique_selected": len({r["id"] for r in final_rows}),
        "near_copy_flags": near_copy,
        "dedup": json.loads((ROOT / "work/dedup_summary.json").read_text()),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "dedup"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
