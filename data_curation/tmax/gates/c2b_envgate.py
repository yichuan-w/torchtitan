from pathlib import Path

core_p = Path("scripts/run_rebench_agentic_stages.py")
s = core_p.read_text()
old = '''def contract_grounding_problem(contract: Mapping[str, Any], seed: TaskSeed) -> str:'''
new = '''ENVIRONMENT_GATE_REASONS = (
    # contract_grounding_problem
    "repository_authority_unresolved", "repo_evidence_unobserved",
    "repo_evidence_kind_invalid", "repo_evidence_path_unproved",
    # contract_unsupported_scope
    "external_runtime_dependency", "unsupported_persistent_process",
    "unsupported_repo_test_edit_capture_policy",
    # contract_requires_environment_delta
    "unsupported_environment_delta",
)


def environment_gate_problem(contract: Mapping[str, Any], seed: TaskSeed) -> str:
    """The environment station's whole gate: THREE predicates, EIGHT reasons.

    One composite, because the same three predicates in the same order were written
    out at three call sites (the split author path, the (c') boundary control, and
    scale300_live's run/resume gate). Under (c') the station is deleted, and three
    hand-copied composites are three chances to re-home two of them and lose the
    third in silence -- so they now share this definition and moving the gate is one
    edit. The reason strings are the station's, unchanged: what (c') moves is WHEN
    the gate runs, never WHAT it refuses.
    """
    problem = contract_grounding_problem(contract, seed)
    if not problem:
        problem = contract_unsupported_scope(contract)
    if not problem and contract_requires_environment_delta(contract):
        problem = "unsupported_environment_delta"
    return problem


def contract_grounding_problem(contract: Mapping[str, Any], seed: TaskSeed) -> str:'''
assert s.count(old) == 1
core_p.write_text(s.replace(old, new))

plumb_p = Path("scripts/rebench_agentic_live_plumbing.py")
s = plumb_p.read_text()
old = '''    problem = core.contract_grounding_problem(contract, seed) \\
        or core.contract_unsupported_scope(contract)
    if not problem and core.contract_requires_environment_delta(contract):
        problem = "unsupported_environment_delta"
    if problem:
        return core.quarantine_before_solution(
            seed, run_id, author_receipt, artifacts, model, resources,
            task_root / "stages/environment", problem), problem'''
new = '''    problem = core.environment_gate_problem(contract, seed)
    if problem:
        return core.quarantine_before_solution(
            seed, run_id, author_receipt, artifacts, model, resources,
            task_root / "stages/environment", problem), problem'''
assert s.count(old) == 1
s = s.replace(old, new)
old = '''    problem = core.contract_grounding_problem(contract, seed) \\
        or core.contract_unsupported_scope(contract)
    if not problem and core.contract_requires_environment_delta(contract):
        problem = "unsupported_environment_delta"
    if problem:
        environment = core.quarantine_before_solution('''
new = '''    problem = core.environment_gate_problem(contract, seed)
    if problem:
        environment = core.quarantine_before_solution('''
assert s.count(old) == 1
s = s.replace(old, new)
plumb_p.write_text(s)

live_p = Path("scripts/rebench_scale300_live.py")
s = live_p.read_text()
old = '''    """Main agentic grounding/scope/delta gate, shared by run and resume."""
    problem = core.contract_grounding_problem(contract, seed)
    if not problem:
        problem = core.contract_unsupported_scope(contract)
    if not problem and core.contract_requires_environment_delta(contract):
        problem = "unsupported_environment_delta"
    return problem'''
new = '''    """Main agentic grounding/scope/delta gate, shared by run and resume.

    Delegates to core.environment_gate_problem: the same three predicates used to be
    written out here, in the author path and at the (c') boundary, and (c') deletes
    the station they belonged to. Three copies would be three chances to re-home two
    and lose the third."""
    return core.environment_gate_problem(contract, seed)'''
assert s.count(old) == 1
live_p.write_text(s.replace(old, new))

ctl_p = Path("scripts/run_rebench_scale300_live_controller.py")
s = ctl_p.read_text()
old = '''SEMANTIC_QUARANTINE_REASONS = {
    "repo_evidence_unobserved",
    "repo_evidence_kind_invalid", "repo_evidence_path_unproved",
    "external_runtime_dependency", "unsupported_persistent_process",
    "unsupported_repo_test_edit_capture_policy",
    "unsupported_environment_delta", "parent_leak",
}'''
new = '''# Every reason the environment gate can emit, plus parent_leak. Derived from
# core.ENVIRONMENT_GATE_REASONS rather than re-typed: the hand-written set was
# missing `repository_authority_unresolved`, which the gate CAN emit (seed.repository
# unresolved) -- a cell quarantined for it would have been judged non-semantic by the
# controller. Found while moving the gate off the station in (c'); the asymmetry
# predates the merge.
SEMANTIC_QUARANTINE_REASONS = {*core.ENVIRONMENT_GATE_REASONS, "parent_leak"}'''
assert s.count(old) == 1
ctl_p.write_text(s.replace(old, new))
print("environment gate unified")
