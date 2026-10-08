#!/usr/bin/env python3
"""Build output_schema_v13.json. The schema is generated so that every cross-field rule is stated once, in code.

Roles of the fields (prompt v13 section 4):
  blocking   A1 oracle_reachable, A2 expectation_revealed, A3 value_derivable, B1 overspecific_check, B3 unstable_reward
  notes      A4 expectation_movable (+ protected_paths), A6 weak_verifier_exploit, A7 core_clause_unenforced
  unsure     ambiguity, uncertain_fact
A note never blocks a tier or verdict; A6/A7 may stand in for pass_probe on a SEED row (R1) and A4 is one of the two
carriers of rollout_material (R18/R19).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ANCHOR = r"^(instruction\.md|setup\.sh|tests/test\.sh):[0-9]+(-[0-9]+)?\b"
LEAK = ["oracle_reachable", "expectation_revealed", "value_derivable"]
BSIDE = ["overspecific_check", "unstable_reward"]
BLOCKING = LEAK + BSIDE
NOTES = ["expectation_movable", "weak_verifier_exploit", "core_clause_unenforced"]
WORK_ROOTS = ["/", "/app", "/home", "/home/user", "/opt", "/tmp", "/workspace", "/srv", "/var", "/root", "/usr", "/usr/local",
              "/data", "/mnt", "/etc"]
EXCLUSIONS = ["instructed_tool", "library_to_wrap", "buggy_premise", "expected_edit", "toolchain", "grade_time_generated",
              "removed_before_rollout", "derivation_is_core_step", "benign"]


def anchored(max_len=None):
    s = {"type": "string", "pattern": ANCHOR}
    if max_len:
        s["maxLength"] = max_len
    return {"anyOf": [{"type": "null"}, s]}


def is_str(field):
    return {"properties": {field: {"type": "string"}}, "required": [field]}


def is_null(field):
    return {"properties": {field: {"type": "null"}}, "required": [field]}


def const(field, value):
    return {"properties": {field: {"const": value}}, "required": [field]}


def any_str(fields):
    return {"anyOf": [is_str(f) for f in fields]}


def all_null(fields):
    return {"allOf": [is_null(f) for f in fields]}


def rule(when, then, note):
    return {"$comment": note, "if": when, "then": then}


PROPS = {
    # TMAX ids are task_<6 digits>_<8 hex>; the RST corpus uses rts_task_<hex>. Both are judged by this rubric.
    "task_id": {"type": "string", "pattern": r"^(task_[0-9]{6}_[0-9a-f]{8}|rts_task_[0-9a-f]{8,32})$"},
    "agent": {"type": "string", "minLength": 1},
    "rubric_version": {"const": "v13"},
    "core_step": {"type": "string", "minLength": 8},
    "assertions": {"type": "array", "minItems": 1,
                   "items": {"type": "string", "pattern": r"^L[0-9]+(-[0-9]+)?: "}},
    "last_assert_line": {"type": "integer", "minimum": 1},
    "expectation_source": {"enum": ["test_only", "hardcoded_literal", "rollout_material", "shape_only"]},
    "literal_derivation": {"anyOf": [{"type": "null"}, {"type": "string", "pattern": r"^(derived|unverified): "}]},
    "excluded_material": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "exclusion", "basis"],
        "properties": {"path": {"type": "string", "minLength": 1}, "exclusion": {"enum": EXCLUSIONS},
                       "basis": {"type": "string", "pattern": ANCHOR}}}},
    "oracle_reachable": anchored(), "expectation_revealed": anchored(), "value_derivable": anchored(),
    "overspecific_check": anchored(), "unstable_reward": anchored(),
    "expectation_movable": anchored(),
    "protected_paths": {"type": "array", "uniqueItems": True, "items": {
        "type": "string", "pattern": r"^/[^\s*?]*[^\s*?/]$", "not": {"enum": WORK_ROOTS}}},
    "weak_verifier_exploit": anchored(), "core_clause_unenforced": anchored(),
    "ambiguity": anchored(), "uncertain_fact": anchored(),
    "evidence": {"type": "string", "minLength": 1, "maxLength": 200},
    "evidence_file": {"enum": ["instruction.md", "setup.sh", "tests/test.sh"]},
    "evidence_line": {"type": "integer", "minimum": 1},
    "reason": {"type": "string", "minLength": 20, "maxLength": 600},
    "repro_cmd": {"anyOf": [{"type": "null"}, {"type": "string", "minLength": 1}]},
    "repro_expected": {"anyOf": [{"type": "null"},
                                 {"enum": ["reward==1", "reward==0", "reward-varies", "external-fetch"]},
                                 {"type": "string", "pattern": r"^none: "}]},
    "exploit_status": {"enum": ["none_found", "static_trace", "not_driven"]},
    "pass_probe": anchored(),
    "verdict": {"enum": ["CLEAN", "ANSWER-LEAK", "INSTR-VERIFIER-MISMATCH", "UNSTABLE-REWARD", "INSTR-AMBIGUOUS"]},
    "tier": {"enum": ["SEED", "FLAGGED", "UNSURE"]},
}

leak_fired = any_str(LEAK)
b_fired = any_str(BSIDE)
blocking_fired = any_str(BLOCKING)
no_blocking = all_null(BLOCKING)
no_leak = all_null(LEAK)
definite = {"properties": {"repro_expected": {"enum": ["reward==1", "reward==0", "reward-varies", "external-fetch"]}},
            "required": ["repro_expected"]}

RULES = [
    rule(const("tier", "SEED"),
         {"allOf": [all_null(BLOCKING + ["ambiguity", "uncertain_fact", "repro_cmd", "repro_expected"]),
                    const("verdict", "CLEAN"), const("exploit_status", "none_found"),
                    any_str(["pass_probe", "weak_verifier_exploit", "core_clause_unenforced"])]},
         "R1 SEED: nothing blocking, nothing unsettled, and a defeated probe or the note that stands in for it"),
    rule({"anyOf": [blocking_fired, is_str("ambiguity")]},
         {"not": {"anyOf": [const("tier", "SEED"), const("verdict", "CLEAN")]}},
         "R2 a blocking field or ambiguity forbids SEED and CLEAN"),
    rule(const("tier", "FLAGGED"),
         {"allOf": [blocking_fired, is_str("repro_cmd"), const("exploit_status", "static_trace"), definite]},
         "R3 FLAGGED needs a blocking field with a traced reproduction; a note alone never flags"),
    rule({"allOf": [blocking_fired, const("exploit_status", "static_trace")]}, const("tier", "FLAGGED"),
         "R4 a driven blocking finding is FLAGGED whatever uncertain_fact says"),
    rule({"allOf": [blocking_fired, const("exploit_status", "not_driven")]}, const("tier", "UNSURE"),
         "R5 a blocking finding that could not be driven is UNSURE"),
    rule(const("tier", "UNSURE"),
         {"anyOf": [{"allOf": [blocking_fired, const("exploit_status", "not_driven")]},
                    {"allOf": [no_blocking, any_str(["ambiguity", "uncertain_fact"])]}]},
         "R6 UNSURE has exactly these two routes"),
    rule(blocking_fired, {"properties": {"exploit_status": {"enum": ["static_trace", "not_driven"]}}},
         "R7 a blocking finding is driven or not driven"),
    rule(no_blocking, {"allOf": [const("exploit_status", "none_found"), is_null("repro_cmd")]},
         "R8 no blocking finding: nothing to reproduce (notes get no reproduction)"),
    rule(is_str("repro_cmd"), {"allOf": [const("exploit_status", "static_trace"), definite]},
         "R9 a written reproduction is a static trace with a definite outcome"),
    rule(is_null("repro_cmd"),
         {"properties": {"repro_expected": {"anyOf": [{"type": "null"}, {"type": "string", "pattern": "^none: "}]},
                         "exploit_status": {"enum": ["none_found", "not_driven"]}}},
         "R10 no reproduction: no definite outcome, no static trace"),
    rule(const("repro_expected", "reward==1"), leak_fired, "R11 a reward-1 reproduction is a leak reproduction"),
    rule({"properties": {"repro_expected": {"enum": ["reward==0", "reward-varies", "external-fetch"]}},
          "required": ["repro_expected"]}, b_fired, "R12 a reward-0 / varying reproduction belongs to B1 or B3"),
    rule(leak_fired, const("verdict", "ANSWER-LEAK"), "R13 rank 1"),
    rule({"allOf": [no_leak, is_str("overspecific_check")]}, const("verdict", "INSTR-VERIFIER-MISMATCH"), "R14 rank 2"),
    rule({"allOf": [no_leak, is_null("overspecific_check"), is_str("unstable_reward")]},
         const("verdict", "UNSTABLE-REWARD"), "R15 rank 3"),
    rule({"allOf": [no_blocking, is_str("ambiguity")]}, const("verdict", "INSTR-AMBIGUOUS"), "R16 rank 4"),
    rule({"allOf": [no_blocking, is_null("ambiguity")]}, const("verdict", "CLEAN"), "R17 nothing ranked: CLEAN"),
    rule(const("expectation_source", "rollout_material"), any_str(["expectation_revealed", "expectation_movable"]),
         "R18 rollout_material implies A2 or A4"),
    rule(any_str(["expectation_revealed", "expectation_movable"]), const("expectation_source", "rollout_material"),
         "R19 A2 or A4 implies rollout_material"),
    rule(const("expectation_source", "hardcoded_literal"), is_str("literal_derivation"),
         "R20 a hardcoded literal carries its derivation (derived or unverified; neither affects the tier)"),
    rule({"properties": {"protected_paths": {"minItems": 1}}, "required": ["protected_paths"]},
         is_str("expectation_movable"), "R21 protected paths exist only under A4"),
    rule(is_str("pass_probe"), const("tier", "SEED"), "R22 pass_probe is written on SEED rows only"),
    rule(no_blocking, is_null("repro_expected"), "R23 no blocking finding: no expected outcome either"),
]

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "tmax-seed-audit-row-v13",
    "title": "TMAX seed audit row, rubric v13",
    "description": "One row per task. Blocking: A1 A2 A3 B1 B3. Notes (no tier or verdict effect): A4 A6 A7.",
    "type": "object",
    "additionalProperties": False,
    "required": list(PROPS),
    "properties": PROPS,
    "allOf": RULES,
}

if __name__ == "__main__":
    # the schema sits beside the prompt; this builder ships one directory down, in tools/
    root = os.path.dirname(HERE) if os.path.basename(HERE) == "tools" else HERE
    out = os.path.join(root, "output_schema_v13.json")
    json.dump(SCHEMA, open(out, "w"), indent=1)
    print("written", out, "|", len(PROPS), "fields |", len(RULES), "cross-field rules")
