#!/usr/bin/env python3
"""Score a v13-rst run. Usage: score_run_rst.py <run_dir> [--rows rows_dir]

Reports coverage, schema validity, lints (L1-L16 of v13 plus LR1-LR5), tiers, verdicts, fired rules and notes, and
three things specific to this corpus:
  * the bootstrap record two ways: what the stager found mechanically in every staged bootstrap, and what the judge
    wrote in bootstrap_fetch, so a miss by the judge is visible instead of silent;
  * the tier under both readings of that record: as written (no tier effect) and strict (a row whose bootstrap
    fetches from the network counts as held back), so the choice needs no re-judging;
  * each tier against the runtime gate the RST campaign already ran (reference solution scores 1, empty run scores 0).
Prints aggregates and ids only.
"""
import glob
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jsonschema  # noqa: E402
import lint_v13_rst  # noqa: E402
from lint_v13_rst import lint_v13  # noqa: E402

LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
BSIDE = ("overspecific_check", "unstable_reward")
NOTES = ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")


def main():
    run = os.path.abspath(sys.argv[1])
    rows_dir = os.path.join(run, "rows")
    if "--rows" in sys.argv:
        rows_dir = os.path.abspath(sys.argv[sys.argv.index("--rows") + 1])
    V = jsonschema.Draft202012Validator(json.load(open(os.path.join(HERE, "output_schema_v13_rst.json"))))
    staged = sorted(d for d in os.listdir(os.path.join(run, "tasks")) if d.startswith("rts_task_"))
    side = {t: json.load(open(os.path.join(run, "sidecar", t + ".json"))) for t in staged}
    rows = {}
    for f in sorted(glob.glob(os.path.join(rows_dir, "*.jsonl"))):
        tid = os.path.basename(f)[:-6]
        try:
            rows[tid] = json.loads(open(f).readline())
        except Exception:
            rows[tid] = None
    ok = {t: r for t, r in rows.items() if r and t in side}
    bad_schema = {t: [e.message[:100] for e in V.iter_errors(r)][:2] for t, r in ok.items() if list(V.iter_errors(r))}
    lints = {}
    for t, r in ok.items():
        try:
            lv = lint_v13_rst.lint(lint_v13.load_task(os.path.join(run, "tasks"), t), r,
                                   lint_v13.load_candidates(os.path.join(run, "candidates"), t), side[t])
        except Exception as ex:
            lv = [f"LINT-ERROR:{type(ex).__name__}"]
        if lv:
            lints[t] = lv
    fetches = {t: bool(s["bootstrap"]["fetches"]) for t, s in side.items()}

    def tier(r):
        return r.get("tier", "MISSING")     # a row missing a key is already counted as schema-invalid; do not die on it

    def strict(t, r):
        return "FLAGGED(bootstrap only)" if tier(r) == "SEED" and fetches[t] else tier(r)

    out = {"staged": len(staged), "judged": len(ok), "missing": [t for t in staged if t not in rows],
           "unparseable": [t for t, r in rows.items() if r is None],
           "tiers": dict(Counter(tier(r) for r in ok.values())),
           "tiers_strict_bootstrap_blocks": dict(Counter(strict(t, r) for t, r in ok.items())),
           "verdicts": dict(Counter(r.get("verdict", "MISSING") for r in ok.values())),
           "fired": {k: sum(1 for r in ok.values() if r.get(k)) for k in LEAK + BSIDE + ("ambiguity", "uncertain_fact")},
           "notes": {k: sum(1 for r in ok.values() if r.get(k)) for k in NOTES},
           "pin_candidates": sum(1 for r in ok.values() if r.get("protected_paths")),
           "bootstrap": {"staged_with_a_fetch (mechanical)": sum(fetches.values()),
                         "by_kind (mechanical)": dict(Counter(k for s in side.values() for k in s["bootstrap"]["fetches"])),
                         "set_e": sum(1 for s in side.values() if s["bootstrap"]["set_e"]),
                         "no_fallback_trap": sum(1 for s in side.values() if not s["bootstrap"]["fallback_trap"]),
                         "task_specific_with_packages": dict(Counter(p for s in side.values() for p in s["bootstrap"]["with_packages"]
                                                                     if p not in ("pytest", "pytest-json-ctrf")).most_common()),
                         "judged_rows_with_a_fetch": sum(1 for t in ok if fetches[t]),
                         "judge_wrote_bootstrap_fetch": sum(1 for t, r in ok.items() if fetches[t] and r.get("bootstrap_fetch")),
                         "judge_missed_it": [t for t, r in ok.items() if fetches[t] and not r.get("bootstrap_fetch")],
                         "leak_or_b3_anchored_in_bootstrap": [t for t, v in lints.items() if "LR3" in v]},
           "tier_by_gate": {g: dict(Counter(tier(r) for t, r in ok.items() if side[t]["gate"] == g))
                            for g in sorted({side[t]["gate"] for t in ok})},
           "seed_and_gate_pass": sorted(t for t, r in ok.items() if tier(r) == "SEED" and side[t]["gate"] == "pass"),
           "gate_by_compose (all staged)": {("compose file" if c else "no compose file"): dict(Counter(
               s["gate"] for s in side.values() if bool(s["dockerfile"]["context"]["compose_file"]) == c)) for c in (True, False)},
           "cmd_never_started (all staged)": dict(Counter(s["dockerfile"].get("cmd_never_started") for s in side.values())),
           "gate_by_cmd_kind (all staged)": {k: dict(Counter(s["gate"] for s in side.values() if s["dockerfile"].get("cmd_never_started") == k))
                                             for k in sorted({s["dockerfile"].get("cmd_never_started") or "?" for s in side.values()})},
           "instruction_points_at_pipeline_json (all staged)": {
               "tasks": sum(1 for s in side.values() if s["dockerfile"].get("instruction_names_pipeline_json")),
               "file_absent_from_image": sorted(t for t, s in side.items() if s["dockerfile"].get("instruction_names_pipeline_json")
                                                and not s["dockerfile"].get("pipeline_json_in_image")),
               "judged_of_those_by_tier": dict(Counter(tier(r) for t, r in ok.items()
                                                       if side[t]["dockerfile"].get("instruction_names_pipeline_json")
                                                       and not side[t]["dockerfile"].get("pipeline_json_in_image")))},
           "harness_compat_tags": {t: side[t]["harness_compat"] for t in ok if side[t]["harness_compat"]},
           "schema_invalid": bad_schema, "rows_with_lint": len(lints),
           "lint_distribution": dict(Counter(l for v in lints.values() for l in v).most_common()),
           "lints_by_task": lints,
           "expectation_source": dict(Counter(r.get("expectation_source", "MISSING") for r in ok.values()))}
    json.dump(out, open(os.path.join(run, "score.json"), "w"), indent=1)
    n = max(len(ok), 1)
    b = out["bootstrap"]
    print(f"== staged {out['staged']} | judged {out['judged']} | missing {len(out['missing'])} | unparseable {len(out['unparseable'])}")
    print(f"== tiers {out['tiers']}  ->  SEED {100 * out['tiers'].get('SEED', 0) / n:.1f}%")
    print(f"== tiers if a fetching bootstrap counted as held back {out['tiers_strict_bootstrap_blocks']}")
    print(f"== verdicts {out['verdicts']}")
    print(f"== blocking rules fired {out['fired']}")
    print(f"== notes {out['notes']} | rows with pin candidates {out['pin_candidates']}")
    print(f"== bootstrap, mechanical: {b['staged_with_a_fetch (mechanical)']} of {out['staged']} staged fetch at grade time {b['by_kind (mechanical)']}; set -e {b['set_e']}; no fallback trap {b['no_fallback_trap']}")
    print(f"== bootstrap, judge: wrote the record on {b['judge_wrote_bootstrap_fetch']} of {b['judged_rows_with_a_fetch']} judged rows that have a fetch; missed {len(b['judge_missed_it'])}; a leak or B3 wrongly anchored in the bootstrap {len(b['leak_or_b3_anchored_in_bootstrap'])}")
    print(f"== tier by runtime gate {out['tier_by_gate']} | SEED and gate pass {len(out['seed_and_gate_pass'])}")
    print(f"== staged, gate by compose file {out['gate_by_compose (all staged)']}")
    print(f"== staged, CMD the sandbox never starts {out['cmd_never_started (all staged)']} | gate by that {out['gate_by_cmd_kind (all staged)']}")
    pj = out["instruction_points_at_pipeline_json (all staged)"]
    print(f"== staged, instruction points at a generator JSON {pj['tasks']}; absent from the image {len(pj['file_absent_from_image'])}; judged of those {pj['judged_of_those_by_tier']}")
    print(f"== schema invalid {len(bad_schema)} | rows with a lint {out['rows_with_lint']} {out['lint_distribution']}")
    print("written", os.path.join(run, "score.json"))


if __name__ == "__main__":
    main()
