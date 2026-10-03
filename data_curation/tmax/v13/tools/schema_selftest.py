#!/usr/bin/env python3
"""Self-test of output_schema_v13.json: rows that must validate and rows that must not. Synthetic content only."""
import copy
import json
import os
import sys

import jsonschema

HERE = os.path.dirname(os.path.abspath(__file__))
V = jsonschema.Draft202012Validator(json.load(open(os.path.join(HERE, "..", "output_schema_v13.json"))))
A = "tests/test.sh:12 "
BASE = {
    "task_id": "task_000001_0123abcd", "agent": "judge:selftest:b001", "rubric_version": "v13",
    "core_step": "compute the monthly totals from the ledger", "assertions": ["L10-14: totals equal the recomputed sums | fail"],
    "last_assert_line": 14, "expectation_source": "test_only", "literal_derivation": None, "excluded_material": [],
    "oracle_reachable": None, "expectation_revealed": None, "value_derivable": None, "overspecific_check": None,
    "unstable_reward": None, "expectation_movable": None, "protected_paths": [], "weak_verifier_exploit": None,
    "core_clause_unenforced": None, "ambiguity": None, "uncertain_fact": None,
    "evidence": "assert totals == expected", "evidence_file": "tests/test.sh", "evidence_line": 12,
    "reason": "The shipped state has no totals file, so the strongest shortcut fails the content comparison.",
    "repro_cmd": None, "repro_expected": None, "exploit_status": "none_found",
    "pass_probe": A + "a stub writing the instruction's own example totals fails the content comparison", "verdict": "CLEAN", "tier": "SEED",
}


def row(**kw):
    r = copy.deepcopy(BASE)
    r.update(kw)
    return r


LEAK = dict(oracle_reachable=A + "/app/ref_calc prints the graded number", pass_probe=None, verdict="ANSWER-LEAK", tier="FLAGGED",
            repro_cmd="printf '#!/bin/sh\\nexec /app/ref_calc \"$@\"\\n' > /app/calc && chmod +x /app/calc", repro_expected="reward==1",
            exploit_status="static_trace", assertions=["L10-14: totals equal the recomputed sums | pass"])
B1 = dict(overspecific_check=A + "rejects a correct solution that orders keys differently", pass_probe=None,
          verdict="INSTR-VERIFIER-MISMATCH", tier="FLAGGED", repro_cmd="python3 /app/solve.py --sorted", repro_expected="reward==0",
          exploit_status="static_trace")
VALID = {
    "seed with a defeated probe": row(),
    "seed with an A7 note standing in for the probe": row(pass_probe=None, core_clause_unenforced="instruction.md:7 the recovery step is only existence-checked",
                                                       assertions=["L10-14: file exists | pass"], expectation_source="shape_only"),
    "seed with an A6 note": row(pass_probe=None, weak_verifier_exploit=A + "an empty csv with the right header passes",
                                assertions=["L10-14: header matches | pass"]),
    "seed with A4 note and pins": row(expectation_movable=A + "/home/user/data.csv is re-read to compute the expectation",
                                      protected_paths=["/home/user/data.csv"], expectation_source="rollout_material"),
    "seed with A4 note, editing permitted, no pins": row(expectation_movable=A + "/home/user/data.csv; the instruction permits cleaning it in place",
                                                         protected_paths=[], expectation_source="rollout_material"),
    "seed with probe and an A7 note on another step": row(core_clause_unenforced="instruction.md:9 the archive step is never read"),
    "seed with a pin whose path holds brackets": row(expectation_movable=A + "/app/logs/run[1].csv is re-read", protected_paths=["/app/logs/run[1].csv"],
                                                     expectation_source="rollout_material"),
    "seed with an unverified literal": row(expectation_source="hardcoded_literal", literal_derivation="unverified: recompute the bootstrap bound"),
    "flagged leak": row(**LEAK),
    "flagged leak that also carries notes": row(**LEAK, core_clause_unenforced="instruction.md:7 the port is only existence-checked"),
    "flagged B1": row(**B1),
    "flagged B3": row(unstable_reward=A + "elapsed < 2.0 s threshold", pass_probe=None, verdict="UNSTABLE-REWARD", tier="FLAGGED",
                      repro_cmd="python3 /app/solve.py", repro_expected="reward-varies", exploit_status="static_trace"),
    "flagged leak plus B1, verdict by rank": row(**{**B1, **LEAK}),
    "flagged stays flagged with an uncertain fact": row(**LEAK, uncertain_fact=A + "a second oracle may exist"),
    "unsure: blocking not driven": row(value_derivable="setup.sh:30 V = sum of the generator's rows at /app/gen.py", pass_probe=None,
                                       verdict="ANSWER-LEAK", tier="UNSURE", exploit_status="not_driven", repro_expected="none: generator needs the seeds"),
    "unsure: ambiguity": row(ambiguity="instruction.md:4 rounding mode is not stated; half-up and half-even differ", pass_probe=None,
                             verdict="INSTR-AMBIGUOUS", tier="UNSURE"),
    "unsure: uncertain fact only": row(uncertain_fact=A + "possible ordering flake", pass_probe=None, verdict="CLEAN", tier="UNSURE"),
}
INVALID = {
    "seed with a leak field": row(oracle_reachable=A + "/app/ref"),
    "seed with no probe and no note": row(pass_probe=None),
    "seed with a reproduction": row(repro_cmd="true"),
    "flagged on a note alone": row(core_clause_unenforced="instruction.md:7 x" * 3, pass_probe=None, tier="FLAGGED", verdict="INSTR-VERIFIER-MISMATCH",
                                   repro_cmd="true", repro_expected="reward==1", exploit_status="static_trace"),
    "unsure on a note alone": row(weak_verifier_exploit=A + "decoy passes", pass_probe=None, tier="UNSURE"),
    "flagged without a reproduction": row(**{**LEAK, "repro_cmd": None, "repro_expected": None}),
    "driven leak labelled unsure": row(**{**LEAK, "tier": "UNSURE"}),
    "leak with the wrong verdict": row(**{**LEAK, "verdict": "INSTR-VERIFIER-MISMATCH"}),
    "B1 reproduction claiming reward==1": row(**{**B1, "repro_expected": "reward==1"}),
    "leak reproduction claiming reward==0": row(**{**LEAK, "repro_expected": "reward==0"}),
    "rollout_material without A2 or A4": row(expectation_source="rollout_material"),
    "A4 without rollout_material": row(expectation_movable=A + "/home/user/data.csv"),
    "pins without A4": row(protected_paths=["/home/user/data.csv"]),
    "glob pin": row(expectation_movable=A + "/home/user/d/*.csv", protected_paths=["/home/user/d/*.csv"], expectation_source="rollout_material"),
    "working-root pin": row(expectation_movable=A + "/home/user", protected_paths=["/home/user"], expectation_source="rollout_material"),
    "directory pin with a trailing slash": row(expectation_movable=A + "/home/user/d/", protected_paths=["/home/user/d/"], expectation_source="rollout_material"),
    "relative pin": row(expectation_movable=A + "data.csv", protected_paths=["data.csv"], expectation_source="rollout_material"),
    "hardcoded literal without a derivation": row(expectation_source="hardcoded_literal"),
    "pass_probe on a flagged row": row(**{**LEAK, "pass_probe": A + "x"}),
    "unanchored rule field": row(**{**LEAK, "oracle_reachable": "/app/ref prints it"}),
    "a deleted V12 field": {**row(), "verifier_fix": None},
    "a V12 tier": row(tier="PASS"),
    "reason too long": row(reason="x" * 601),
    "no blocking finding but an expected outcome": row(uncertain_fact=A + "B1 would fire if the key order is fixed", pass_probe=None, verdict="CLEAN",
                                                       tier="UNSURE", repro_expected="none: nothing to reproduce"),
    "bracketed pin path is a file path, not a glob": None,
}
bad = 0
for name, r in VALID.items():
    errs = [e.message[:110] for e in V.iter_errors(r)]
    if errs:
        bad += 1
        print("SHOULD VALIDATE but fails:", name, errs[:2])
for name, r in INVALID.items():
    if r is None:
        continue
    if not list(V.iter_errors(r)):
        bad += 1
        print("SHOULD FAIL but validates:", name)
print(f"selftest: {len(VALID)} valid cases, {len(INVALID)} invalid cases, {bad} wrong")
sys.exit(1 if bad else 0)
