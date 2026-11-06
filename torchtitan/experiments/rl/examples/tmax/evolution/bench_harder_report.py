#!/usr/bin/env python3
"""EvolveBench harder-rewrite report (#319): per arm and rep, over the whole
slice, how many tasks were rewritten into the target band, with the paired
per-task comparison between arms.

For each task of the set and each arm root (one rep of one arm):
  accepted   the rewrite was accepted (an unaccepted task counts as out of band)
  k/n        the accepted revision's attempts in the evals given (first eval
             that scored it), graded attempts only
  band       3-8 of 12 (scaled to n), mixed, still all-solved, or none-solved
  clean      no audit finding the verifier confirmed or called partly (when an
             audit and its key are given; otherwise unknown)
  expected   the author's ``expected: "k of n"`` (rewrite.json harder_expected,
             or the line in notes/hardening.md)
  W          the revision's median turns over the parent's, both on the same
             checkpoint (the parent's from the set's before-eval)

Primary: accepted & in 3-8 & clean, over the slice. Co-primary: still 12/12
(accepted and all-solved, or not accepted). Pairing: each task's score summed
over reps, sign test between arms, and a bootstrap over tasks of the
difference in totals.

    bench_harder_report.py --set <set dir> --before <parent eval> \
        --arm off=<root>[,<root>...] --arm ht=<root>... \
        [--audit <bundle> --key <key.json>] --out report.json
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import random
import re
import statistics
from pathlib import Path

_K_OF_N = re.compile(r'expected:\s*"?(\d+)\s+of\s+(\d+)"?')


def _records(ev: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for f in glob.glob(str(ev / "validation_rollouts" / "*" / "*.jsonl")):
        with open(f) as fh:
            h = json.loads(fh.readline())
        out[h["task"]].append(h)
    return out


def _band(k: int, n: int, lo: int = 3, hi: int = 8, of: int = 12) -> str:
    if n < 2:
        return "ungraded"
    if k == n:
        return "all_solved"
    if k == 0:
        return "none_solved"
    lo_n, hi_n = lo * n / of, hi * n / of
    return "band" if lo_n - 1e-9 <= k <= hi_n + 1e-9 else "mixed_out"


def _expected(rw_dir: Path, meta: dict) -> tuple[int, int] | None:
    text = meta.get("harder_expected") or ""
    if not text:
        for p in (rw_dir / "notes" / "hardening.md", rw_dir / "package" / "run" / "hardening.md"):
            if p.is_file():
                m = _K_OF_N.search(p.read_text(errors="replace"))
                if m:
                    return int(m[1]), int(m[2])
        return None
    m = re.search(r"(\d+)\s+of\s+(\d+)", text)
    return (int(m[1]), int(m[2])) if m else None


def _audit_defects(audit: Path | None, key: Path | None) -> dict[tuple[str, str], bool]:
    """(arm label, task) -> has a confirmed/partly defect. The bundle's arm
    labels are "<arm>-r<rep>", as --arm label and its root order give them."""
    if audit is None or key is None:
        return {}
    k = json.load(open(key))["tasks"]
    out = {}
    for task, variants in k.items():
        if not isinstance(variants, dict):
            continue
        for variant, label in variants.items():
            vd = audit / task / variant
            bad = False
            try:
                verify = json.load(open(vd / "verify.json"))
                bad = any(v in ("confirmed", "partly") for kk, v in verify.items() if kk != "metadata")
            except (OSError, ValueError):
                if not (vd / "verdict.json").exists():
                    continue
            out[(label, task)] = bad
    return out


def task_rows(root: Path, tasks: list[str], evals: list[Path], parent_turns: dict[str, float]) -> dict[str, dict]:
    recs: dict[str, list[dict]] = {}
    for ev in evals:
        for t, v in _records(ev).items():
            recs.setdefault(t, v)
    rows = {}
    for t in tasks:
        rws = sorted(glob.glob(str(root / "evolution" / "tasks" / t / "rewrites" / "*" / "rewrite.json")))
        metas = [(Path(p).parent, json.load(open(p))) for p in rws]
        acc = [(d, m) for d, m in metas if m.get("status") == "accepted"]
        last = metas[-1][1] if metas else {}
        row = {"accepted": bool(acc), "final_status": last.get("status") or "never_handled",
               "reason": (last.get("reason") or "")[:160], "rewrites": len(metas)}
        if acc:
            d, m = acc[-1]
            row["expected"] = _expected(d, m)
            row["result_rev"] = m.get("result_rev")
            row["secs"] = None
            try:
                from datetime import datetime
                f = "%Y%m%d-%H%M%SZ"
                row["secs"] = (datetime.strptime(m["finished"], f) - datetime.strptime(m["started"], f)).total_seconds()
            except (KeyError, TypeError, ValueError):
                pass
            v = [h for h in recs.get(t, []) if int(h.get("rev") or 0) == int(m.get("result_rev") or -1)]
            g = [h for h in v if not h.get("infra_failed")]
            if g:
                k = sum(h["reward"] >= 1.0 for h in g)
                row.update(k=k, n=len(g), infra=len(v) - len(g), band=_band(k, len(g)),
                           finish=dict(collections.Counter(h.get("finish_reason") for h in g)))
                turns = statistics.median(h.get("turns") or 0 for h in g)
                row["W"] = turns / parent_turns[t] if parent_turns.get(t) else None
            else:
                row["band"] = "unscored"
        rows[t] = row
    return rows


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def bootstrap(diffs: list[int], iters: int = 10000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(diffs)
    sums = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) for _ in range(iters))
    return sums[int(0.025 * iters)], sums[int(0.975 * iters)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--before", type=Path, required=True, help="the parent rows' eval on the same checkpoint")
    ap.add_argument("--arm", action="append", required=True, help="label=root[,root...] (one root per rep)")
    ap.add_argument("--audit", type=Path)
    ap.add_argument("--key", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    tasks = sorted(json.load(open(a.set / "selected.json")))
    parent = _records(a.before)
    parent_turns = {t: statistics.median(h.get("turns") or 0 for h in parent[t] if not h.get("infra_failed"))
                    for t in tasks if t in parent}
    defects = _audit_defects(a.audit, a.key)
    report: dict = {"set": str(a.set), "tasks": len(tasks), "before": str(a.before), "arms": {}}
    scores: dict[str, dict[str, dict[str, int]]] = {}
    for spec in a.arm:
        label, roots = spec.split("=", 1)
        reps = []
        per_task = collections.defaultdict(lambda: collections.Counter())
        for i, r in enumerate(roots.split(",")):
            root = Path(r)
            evals = sorted(p for p in (root / "evals").glob("*") if p.is_dir())
            rows = task_rows(root, tasks, evals, parent_turns)
            rep_label = f"{label}-r{i + 1}"
            for t, row in rows.items():
                if row.get("accepted") and (rep_label, t) in defects:
                    row["clean"] = not defects[(rep_label, t)]
                elif row.get("accepted"):
                    row["clean"] = None
            c = collections.Counter(row.get("band", "not_accepted") if row["accepted"] else "not_accepted"
                                    for row in rows.values())
            prim = [t for t, row in rows.items() if row["accepted"] and row.get("band") == "band" and row.get("clean") is not False]
            prim_strict = [t for t in prim if rows[t].get("clean") is True]
            still = [t for t, row in rows.items() if not row["accepted"] or row.get("band") == "all_solved"]
            exp = [(row["expected"][0] * 12 / row["expected"][1], row["k"] * 12 / row["n"])
                   for row in rows.values() if row.get("expected") and row.get("n")]
            ws = [row["W"] for row in rows.values() if row.get("W")]
            secs = [row["secs"] for row in rows.values() if row.get("secs")]
            rep = {
                "root": str(root), "accepted": sum(r_["accepted"] for r_ in rows.values()),
                "bands": dict(c), "band_3_8_clean_or_unaudited": len(prim), "band_3_8_clean": len(prim_strict),
                "still_12_of_12": len(still),
                "scored": sum(1 for r_ in rows.values() if r_.get("n")),
                "expected_vs_measured": {
                    "n": len(exp),
                    "mean_expected_of_12": round(statistics.mean(e for e, _ in exp), 2) if exp else None,
                    "mean_measured_of_12": round(statistics.mean(m for _, m in exp), 2) if exp else None,
                    "mean_abs_error": round(statistics.mean(abs(e - m) for e, m in exp), 2) if exp else None,
                },
                "W_median": round(statistics.median(ws), 2) if ws else None,
                "rewrite_minutes_median": round(statistics.median(secs) / 60, 1) if secs else None,
                "final_status": dict(collections.Counter(r_["final_status"] for r_ in rows.values())),
                "per_task": rows,
            }
            reps.append(rep)
            for t, row in rows.items():
                per_task[t]["band"] += int(t in prim)
                per_task[t]["still"] += int(t in still)
        report["arms"][label] = {"reps": reps}
        scores[label] = {t: dict(v) for t, v in per_task.items()}
    labels = list(scores)
    pairs = {}
    for i, x in enumerate(labels):
        for y in labels[i + 1:]:
            for metric in ("band", "still"):
                d = [scores[y][t].get(metric, 0) - scores[x][t].get(metric, 0) for t in tasks]
                w, l_ = sum(v > 0 for v in d), sum(v < 0 for v in d)
                lo, hi = bootstrap(d)
                pairs[f"{y}-vs-{x}:{metric}"] = {"diff_total": sum(d), "tasks_better": w, "tasks_worse": l_,
                                                 "sign_p": round(sign_test(w, l_), 4), "bootstrap95": [lo, hi]}
    report["paired"] = pairs
    a.out.write_text(json.dumps(report, indent=1, default=str))
    for label, arm in report["arms"].items():
        for i, rep in enumerate(arm["reps"]):
            print(f"{label}-r{i + 1}: accepted {rep['accepted']}/{len(tasks)}  3-8&clean {rep['band_3_8_clean_or_unaudited']} "
                  f"(audited-clean {rep['band_3_8_clean']})  still-12/12 {rep['still_12_of_12']}  bands {rep['bands']}  "
                  f"W {rep['W_median']}  exp-vs-meas {rep['expected_vs_measured']}  rewrite-min {rep['rewrite_minutes_median']}")
    for k, v in pairs.items():
        print(k, v)


if __name__ == "__main__":
    main()
