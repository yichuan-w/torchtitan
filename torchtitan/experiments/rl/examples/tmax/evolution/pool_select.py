"""The EvolveBench pool's direction map (EVOLVE_BENCH.md, Standard 2026-11-06).

Each candidate's original is measured at k=12 on base 122B and base 9B. A task
is easier when base 122B solves none of its 12 graded attempts, harder when
base 9B solves all 12; anything else is out. A task meeting both rules is a
conflict and is left out (listed in the report). A task without 12 graded
attempts on either student is unmeasured and left out: its direction, or a
conflict, cannot be ruled out. Infra-failed attempts are not counted; a k=16
measurement contributes its first 12 graded attempts. Where two evals measure
one task on one student, the first given wins.

    pool_select.py --eval-122b <entry122b evals>... BASE__=<combo1 eval> \\
        --eval-9b <entry9b evals>... --labels <defects tsv> --out <map.json>

``PREFIX=DIR`` reads only the eval's ``PREFIX<id>`` tasks, as ``<id>``. Writes
``<out>`` ({task: "easier"|"harder"}, the input of ``evolve_bench.py freeze
--direction-map``) and ``<out stem>.report.json``, and prints the counts.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_bench as eb  # noqa: E402

K = 12
REPAIR_CORPORA = ("agentpick-repair", "agentpick-calibforge-repair")


def measure(eval_args: list[str], k: int = K) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """task -> {"passed", "graded", "eval"} over each task's first k graded
    attempts, task -> its row's metadata, and the tasks measured twice."""
    out: dict[str, dict] = {}
    meta: dict[str, dict] = {}
    twice: list[str] = []
    for arg in eval_args:
        prefix, ev = eb.split_eval_arg(arg)
        e = eb.read_eval(ev)
        rows = {eb._iid(r): r for r in eb._rows(Path(e["evalset"]["path"]))}
        for row_id, t in e["tasks"].items():
            if prefix is not None and not row_id.startswith(prefix):
                continue
            tid = row_id[len(prefix):] if prefix is not None else row_id
            if tid in out:
                twice.append(tid)
                continue
            attempts = sorted(t["attempts"], key=lambda a: int(eb.RECORD_NAME.search(a["record"]).group(2)))
            graded = [a for a in attempts if a["state"] != "INFRA"][:k]
            out[tid] = {"passed": sum(a["state"] == "PASS" for a in graded), "graded": len(graded),
                        "eval": str(ev.resolve())}
            meta[tid] = rows[row_id]["metadata"]
    return out, meta, sorted(set(twice))


def select(m122: dict[str, dict], m9: dict[str, dict], k: int = K) -> dict:
    """The selection rule over the union of both students' tasks."""
    directions: dict[str, str] = {}
    conflict, unmeasured, neither = [], {}, []
    for tid in sorted(set(m122) | set(m9)):
        a, b = m122.get(tid), m9.get(tid)
        ok122 = a is not None and a["graded"] == k
        ok9 = b is not None and b["graded"] == k
        if not (ok122 and ok9):
            unmeasured[tid] = {"122b": f"{a['passed']}/{a['graded']}" if a else None,
                               "9b": f"{b['passed']}/{b['graded']}" if b else None}
            continue
        easier, harder = a["passed"] == 0, b["passed"] == k
        if easier and harder:
            conflict.append(tid)
        elif easier:
            directions[tid] = "easier"
        elif harder:
            directions[tid] = "harder"
        else:
            neither.append(tid)
    return {"directions": directions, "conflict": conflict, "unmeasured": unmeasured, "neither": neither}


def labels(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with open(path) as f:
        return {r["task_id"]: r["defect_class"] for r in csv.DictReader(f, delimiter="\t")}


def report(sel: dict, meta: dict[str, dict], defects: dict[str, str]) -> dict:
    by_corpus: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_kind: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_defect: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for tid, d in sel["directions"].items():
        corpus = meta.get(tid, {}).get("corpus") or "?"
        by_corpus[corpus][d] += 1
        by_kind["repair" if corpus in REPAIR_CORPORA else "other"][d] += 1
        if tid in defects:
            by_defect[defects[tid]][d] += 1
    return {
        "k": K,
        "pool": len(sel["directions"]),
        "directions": dict(collections.Counter(sel["directions"].values())),
        "by_corpus": {c: dict(v) for c, v in sorted(by_corpus.items())},
        "repair_vs_other": {c: dict(v) for c, v in sorted(by_kind.items())},
        "by_defect_class": {c: dict(v) for c, v in sorted(by_defect.items())},
        # Both rules hold (122B 0/12 and 9B 12/12): left out of the map.
        "conflict": sel["conflict"],
        "unmeasured": sel["unmeasured"],
        "neither": len(sel["neither"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-122b", nargs="+", required=True, help="base 122B evals of the originals (PREFIX=DIR ok)")
    ap.add_argument("--eval-9b", nargs="+", required=True, help="base 9B evals of the originals (PREFIX=DIR ok)")
    ap.add_argument("--labels", type=Path, help="defects TSV (task_id, defect_class, ...), read only")
    ap.add_argument("--out", type=Path, required=True, help="the direction map json")
    a = ap.parse_args()
    m122, meta122, twice122 = measure(a.eval_122b)
    m9, meta9, twice9 = measure(a.eval_9b)
    sel = select(m122, m9)
    res = report(sel, {**meta122, **meta9}, labels(a.labels))
    res["inputs"] = {"eval_122b": a.eval_122b, "eval_9b": a.eval_9b,
                     "labels": str(a.labels.resolve()) if a.labels else None, "bench_commit": eb._bench_commit()}
    res["measured_twice"] = {"122b": twice122, "9b": twice9}
    if a.out.exists():
        raise SystemExit(f"{a.out} exists")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    eb.layout.write_json_atomic(a.out, dict(sorted(sel["directions"].items())))
    rep_path = a.out.with_name(a.out.stem + ".report.json")
    eb.layout.write_json_atomic(rep_path, res)
    print(json.dumps({k: v for k, v in res.items() if k not in ("unmeasured", "inputs")}, indent=1))
    print(f"unmeasured (fewer than {K} graded on a student, left out): {len(sel['unmeasured'])}")
    if sel["conflict"]:
        print(f"CONFLICT: {len(sel['conflict'])} tasks are 122B 0/{K} and 9B {K}/{K}; left out of the map: "
              f"{sel['conflict']}")
    print(f"-> {a.out} ({len(sel['directions'])} tasks), {rep_path}")


if __name__ == "__main__":
    main()
