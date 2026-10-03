#!/usr/bin/env python3
"""Read the V12 rows already judged and give each task the tier the v13 policy assigns. No re-judging.

v13 blocks on A1/A2/A3 (leak), B1 (unfair check) and B3 (unstable reward); A4/A6/A7 are notes; A5, trivial, B2 and the
dependency hold are no longer the audit's business (runtime gates decide them). The mapping from a V12 row:

  FLAGGED   a blocking field is set and V12 drove it (that side's status is static_trace, or not_applicable because
            the single reproduction recorded the other side)
  UNSURE    a blocking field is set but was not driven; or only ambiguity / uncertain_fact is set
  SEED      everything else -- including rows V12 held back for A4, A6, A7, A5, env_mismatch, a dependency hold or an
            unverified literal

Each SEED row also carries what a seed list needs: its notes, pin candidates with their review flags, whether the V12
row was lint-clean, whether the historical empty run was anomalous, and whether any solve attempt ever scored 1.
Writes seed_list.csv / seed_list.jsonl next to this script. Prints aggregates and ids only.
"""
import csv
import glob
import importlib.util
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OF = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(OF, "..", ".."))
PROD = os.path.join(OF, "v12", "production")
LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
BSIDE = ("overspecific_check", "unstable_reward")
NOTES = ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")

spec = importlib.util.spec_from_file_location("disposition", os.path.join(PROD, "disposition.py"))
disp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(disp)


def v13_tier(r):
    leak = any(r.get(k) for k in LEAK)
    b = any(r.get(k) for k in BSIDE)
    if leak or b:
        driven = (leak and r.get("exploit_status_a") == "static_trace") or \
                 (b and r.get("exploit_status_b") in ("static_trace", "not_applicable")) or \
                 (leak and r.get("exploit_status_a") == "not_applicable")
        return "FLAGGED" if driven else "UNSURE"
    if r.get("ambiguity") or r.get("uncertain_fact"):
        return "UNSURE"
    return "SEED"


def load_rows(run):
    rows = {}
    for f in sorted(glob.glob(os.path.join(PROD, run, "rows", "*.jsonl"))):
        lines = [x for x in open(f).read().splitlines() if x.strip()]
        if lines:
            try:
                rows[os.path.basename(f)[:-6]] = json.loads(lines[0])
            except json.JSONDecodeError:
                pass
    return rows


def main():
    gate1 = {d["task_id"]: d.get("status") for d in json.load(open(os.path.join(REPO, "unified", "rounds", "gate1_empty_submission.json")))}
    solved, attempted = Counter(), set()
    for line in open(os.path.join(REPO, "unified", "rounds", "gate2_solve_1710.jsonl")):
        if line.strip():
            d = json.loads(line)
            attempted.add(d["task_id"])
            if str(d.get("reward")) == "1":
                solved[d["task_id"]] += 1
    lint = {"run600": json.load(open(os.path.join(PROD, "score_run600.json"))).get("lint_violations") or {},
            "run50_virgin": json.load(open(os.path.join(PROD, "run50_virgin", "score.json"))).get("lint_violations") or {}}
    out = []
    for run in ("run600", "run50_virgin"):
        rows = load_rows(run)
        tiers = Counter()
        moved = Counter()
        for tid, r in rows.items():
            t13 = v13_tier(r)
            tiers[t13] += 1
            if t13 == "SEED" and r.get("tier") != "PASS":
                why = [k for k in NOTES + ("env_mismatch",) if r.get(k)]
                a5 = (r.get("untouched_image_passes") or "")[:3]
                if a5 in ("yes", "unk"):
                    why.append("a5_" + a5)
                if (r.get("literal_derivation") or "").startswith("unverified"):
                    why.append("literal_unverified")
                moved["+".join(why) or "v12 REVIEW with nothing v13 blocks on"] += 1
            pins = disp.paths_in(r.get("expectation_movable")) if r.get("expectation_movable") else []
            out.append({
                "task_id": tid, "run": run, "v12_tier": r.get("tier"), "v13_tier": t13,
                "blocking": [k for k in LEAK + BSIDE if r.get(k)],
                "notes": [k for k in NOTES if r.get(k)],
                "pin_candidates": pins,
                "pin_review": disp.pin_review(r, pins) if pins or r.get("expectation_movable") else [],
                "v12_lints": lint[run].get(tid, []),
                "empty_run": gate1.get(tid, "no_record"),
                "solve_record": "solved" if tid in solved else ("0_of_3" if tid in attempted else "never_attempted"),
                "expectation_source": r.get("expectation_source"),
                "n_assertions": len(r.get("assertions") or []),
            })
        n = len(rows)
        seeds = [d for d in out if d["run"] == run and d["v13_tier"] == "SEED"]
        clean = [d for d in seeds if not d["v12_lints"] and d["empty_run"] == "ok" and not d["pin_review"]]
        print(f"== {run}: n={n} | v13 tiers {dict(tiers)} | SEED {100 * tiers['SEED'] / n:.1f}% (V12 PASS was "
              f"{100 * sum(1 for r in rows.values() if r.get('tier') == 'PASS') / n:.1f}%)")
        print(f"   SEED rows that V12 had held back, by what held them: {dict(moved.most_common())}")
        print(f"   SEED rows with no V12 lint, an ordinary empty run and no pin to review: {len(clean)} "
              f"| of those with a solve record: {sum(1 for d in clean if d['solve_record'] == 'solved')}")
        print(f"   SEED rows by note: {dict(Counter('+'.join(d['notes']) or 'none' for d in seeds).most_common())}")
    out.sort(key=lambda d: (d["run"], d["task_id"]))
    with open(os.path.join(HERE, "seed_list.jsonl"), "w") as fh:
        for d in out:
            fh.write(json.dumps(d) + "\n")
    with open(os.path.join(HERE, "seed_list.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        cols = ["task_id", "run", "v12_tier", "v13_tier", "blocking", "notes", "pin_candidates", "pin_review", "v12_lints",
                "empty_run", "solve_record", "expectation_source", "n_assertions"]
        w.writerow(cols)
        for d in out:
            w.writerow([" | ".join(d[c]) if isinstance(d[c], list) else d[c] for c in cols])
    print(f"written seed_list.csv / seed_list.jsonl ({len(out)} tasks)")


if __name__ == "__main__":
    sys.exit(main())
