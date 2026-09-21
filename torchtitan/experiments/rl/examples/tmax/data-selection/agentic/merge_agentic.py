#!/usr/bin/env python
"""Validate the agents' rerank JSONs and merge them into the final CSVs.

Every task_id is checked against the pool sqlite, so a hallucinated id fails
loudly instead of ending up in the training mix. Run with --strict to exit
non-zero when anything is wrong.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RERANK = HERE / "rerank"           # the agents' judgements (kept, not regenerable)
OUT = HERE / "results"             # derived CSVs (regenerable from RERANK)
VSTRENGTH = HERE / "work" / "verifier_strength.json"
OVERRIDES = HERE / "verifier_overrides.json"
CORPORA_V2 = [
    "TMax-15K",
    "Terminal-Lego-15k",
    "CalibForge",
    "Recursive-Task-Synthesis",
    "TerminalWorld-Seeds-Clean",
    "SWE-Smith-Seeds-Clean",
    "SWE-Rebench-Tasks-Clean",
]
# NB: not "out" — the repo root .gitignore has a bare `out` rule that would
# silently drop these files from every commit.
DB = HERE / "work" / "pool.sqlite"
CORPORA = [
    "TMax-15K",
    "Recursive-Task-Synthesis",
    "TerminalWorld-Seeds-Clean",
    "SWE-Smith-Seeds-Clean",
    "SWE-Rebench-Tasks-Clean",
]
AXES = ("skill", "domain", "task_form", "overall")


def load_pool() -> dict[str, tuple[str, str]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return {
        t: (s, i)
        for t, s, i in con.execute(
            "SELECT task_id, source, instruction FROM doc WHERE source != 'TB2.1'"
        )
    }


def check(rec: dict, pool: dict, tb_ids: set[str], corpora: list[str],
          vgrade: dict[str, str] | None = None) -> list[str]:
    bad: list[str] = []
    tb = rec.get("tb21_id")
    if tb not in tb_ids:
        return [f"unknown tb21_id {tb!r}"]

    def check_entry(e: dict, where: str) -> None:
        tid = e.get("task_id")
        if tid not in pool:
            bad.append(f"{tb}/{where}: id not in pool: {tid!r}")
            return
        for ax in AXES:
            v = e.get(ax)
            if not isinstance(v, int) or not 1 <= v <= 5:
                bad.append(f"{tb}/{where}/{tid}: {ax}={v!r} not an int 1-5")
        if not str(e.get("reason", "")).strip():
            bad.append(f"{tb}/{where}/{tid}: empty reason")

    per = rec.get("per_source") or {}
    for corpus in corpora:
        lst = per.get(corpus) or []
        if len(lst) != 10:
            bad.append(f"{tb}/per_source/{corpus}: {len(lst)} entries, want 10")
        ids = [e.get("task_id") for e in lst]
        if len(set(ids)) != len(ids):
            bad.append(f"{tb}/per_source/{corpus}: duplicate ids")
        for e in lst:
            check_entry(e, f"per_source/{corpus}")
            if e.get("task_id") in pool and pool[e["task_id"]][0] != corpus:
                bad.append(
                    f"{tb}/per_source/{corpus}: {e['task_id']} is actually "
                    f"from {pool[e['task_id']][0]}"
                )

    fin = rec.get("final_top10") or []
    if len(fin) != 10:
        bad.append(f"{tb}/final_top10: {len(fin)} entries, want 10")
    fids = [e.get("task_id") for e in fin]
    if len(set(fids)) != len(fids):
        bad.append(f"{tb}/final_top10: duplicate ids")
    pool_ids = {e.get("task_id") for lst in per.values() for e in lst}
    for e in fin:
        check_entry(e, "final_top10")
        if e.get("task_id") not in pool_ids:
            bad.append(f"{tb}/final_top10: {e.get('task_id')} not in per_source")
        # v2 hard rule: a task whose whole suite is satisfiable without solving
        # it must not reach the training mix, however well it matches.
        if vgrade and vgrade.get(e.get("task_id")) == "FREE":
            bad.append(
                f"{tb}/final_top10: {e.get('task_id')} has a FREE verifier "
                f"(rank {e.get('rank')}) - not allowed in final_top10"
            )
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", type=Path, default=RERANK)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--v2", action="store_true",
                    help="seven-corpus pool; enforce the FREE-verifier rule")
    args = ap.parse_args()

    corpora = CORPORA_V2 if args.v2 else CORPORA
    vgrade = None
    if args.v2 and VSTRENGTH.exists():
        vgrade = {k: v["grade"] for k, v in json.loads(VSTRENGTH.read_text()).items()}
        if OVERRIDES.exists():
            ov = {k: v for k, v in json.loads(OVERRIDES.read_text()).items()
                  if not k.startswith("_")}
            for tid, rec in ov.items():
                vgrade[tid] = rec.get("actually", vgrade.get(tid))
            print(f"[merge] {len(ov)} hand-read verifier override(s) applied")
        print(f"[merge] v2: {len(corpora)} corpora, FREE rule on "
              f"({sum(1 for g in vgrade.values() if g == 'FREE')} FREE tasks in pool)")
    pool = load_pool()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tb_ids = {r[0] for r in con.execute("SELECT task_id FROM doc WHERE source='TB2.1'")}
    print(f"[merge] pool={len(pool)} tb_tasks={len(tb_ids)}")

    recs, problems = {}, []
    args.out.mkdir(parents=True, exist_ok=True)
    for p in sorted(args.rerank.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            rec = json.loads(p.read_text())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{p.name}: unparseable ({exc})")
            continue
        errs = check(rec, pool, tb_ids, corpora, vgrade)
        if errs:
            problems.extend(errs)
        else:
            recs[rec["tb21_id"]] = rec

    missing = sorted(tb_ids - set(recs))
    if missing:
        problems.append(f"missing/rejected tasks ({len(missing)}): {', '.join(missing)}")
    for line in problems:
        print(f"[merge] PROBLEM {line}")
    if not recs:
        print("[merge] nothing valid to write")
        return 1

    def preview(tid: str) -> str:
        return " ".join(pool[tid][1].split())[:240]

    top10 = args.out / "tb21_agentic_top10.csv"
    with top10.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["tb21_id", "rank", "task_id", "source", "skill", "domain",
             "task_form", "overall", "reason", "preview"]
        )
        for tb in sorted(recs):
            for e in sorted(recs[tb]["final_top10"], key=lambda x: x["rank"]):
                w.writerow(
                    [tb, e["rank"], e["task_id"], pool[e["task_id"]][0], e["skill"],
                     e["domain"], e["task_form"], e["overall"], e["reason"],
                     preview(e["task_id"])]
                )

    ids_csv = args.out / "tb21_agentic_top10_ids.csv"
    with ids_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tb21_id", "top10_task_ids", "top10_sources", "mean_overall"])
        for tb in sorted(recs):
            fin = sorted(recs[tb]["final_top10"], key=lambda x: x["rank"])
            w.writerow(
                [tb,
                 "|".join(e["task_id"] for e in fin),
                 "|".join(pool[e["task_id"]][0] for e in fin),
                 round(sum(e["overall"] for e in fin) / len(fin), 2)]
            )

    per_csv = args.out / "tb21_agentic_per_source_top10.csv"
    with per_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["tb21_id", "source", "rank", "task_id", "skill", "domain",
             "task_form", "overall", "reason", "preview"]
        )
        for tb in sorted(recs):
            for corpus in corpora:
                for e in sorted(recs[tb]["per_source"][corpus], key=lambda x: x["rank"]):
                    w.writerow(
                        [tb, corpus, e["rank"], e["task_id"], e["skill"], e["domain"],
                         e["task_form"], e["overall"], e["reason"],
                         preview(e["task_id"])]
                    )

    # Near-duplicate clusters: several corpora carry clone families (one repo's
    # many bugs, one generator's many variants). Flag them by text similarity
    # rather than by id shape, and only report -- which clone to keep is a
    # judgement the agent already made.
    from sklearn.feature_extraction.text import TfidfVectorizer

    near_dup = {}
    for tb, r in recs.items():
        ids = [e["task_id"] for e in r["final_top10"]]
        texts = [pool[i][1] for i in ids]
        try:
            X = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit_transform(texts)
        except ValueError:
            continue
        S = (X @ X.T).toarray()
        pairs = [
            [ids[a], ids[b], round(float(S[a, b]), 3)]
            for a in range(len(ids)) for b in range(a + 1, len(ids))
            if S[a, b] >= 0.75
        ]
        if pairs:
            near_dup[tb] = sorted(pairs, key=lambda x: -x[2])

    src = Counter(
        pool[e["task_id"]][0] for r in recs.values() for e in r["final_top10"]
    )
    rank1 = Counter(
        pool[e["task_id"]][0]
        for r in recs.values() for e in r["final_top10"] if e["rank"] == 1
    )
    allf = [e for r in recs.values() for e in r["final_top10"]]
    summary = {
        "n_tb21_done": len(recs),
        "n_tb21_expected": len(tb_ids),
        "n_rows_final": len(allf),
        "final_top10_source_slots": dict(src),
        "final_rank1_source_wins": dict(rank1),
        "mean_scores_final": {
            ax: round(sum(e[ax] for e in allf) / len(allf), 3) for ax in AXES
        },
        "overall_histogram": dict(sorted(Counter(e["overall"] for e in allf).items())),
        "single_source_top10": sorted(
            tb for tb, r in recs.items()
            if len({pool[e["task_id"]][0] for e in r["final_top10"]}) == 1
        ),
        "near_duplicate_pairs_in_top10": near_dup,
        "problems": problems,
    }
    (args.out / "agentic_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[merge] wrote {top10.name}, {ids_csv.name}, {per_csv.name}")
    print(json.dumps({k: v for k, v in summary.items() if k != "problems"}, indent=2))
    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
