from pathlib import Path

p = Path("scripts/rebench_agentic_live_plumbing.py")
s = p.read_text()

# 1. the shipping gate constants + evaluator
anchor = '''def _assembly_receipt_value(
'''
gate = '''# ---------------------------------------------------------------------------
# (c') SHIPPING GATE. Under the split modes the parent-separation release gates
# AUTHORING: a station that depends on parent_validation must carry a release row
# (core.validate_request). (c') authors everything in one call that necessarily runs
# BEFORE parent_validation, so release_gated_stages("rst_merged") is EMPTY and no
# authoring can be gated -- the instruction's authoring protection ends here, and
# that is a cost of merging, not a control that moved.
#
# What replaces it is a gate on SHIPPING: model output must not be ASSEMBLED into a
# task before the parent separation released it. The refusal already existed for live
# candidates (core._candidate_receipt_value's "live candidate release invalid: " and
# assemble_task's "assembly parent-separation release drifted: "), but BOTH are
# guarded by `live_stage_chain`, computed from the FOUR author receipts -- with one
# `author` receipt that predicate is False and the walls vanish in silence. So the
# gate here is explicit, named, and RECORDED: a run whose assembly receipt has no
# shipping_gate field proves nothing about the gate, because "never evaluated" and
# "evaluated and passed" would otherwise look identical.
SHIPPING_GATE_FIELD = "shipping_gate"
SHIPPING_GATE_REFUSAL = "merged_assembly_without_parent_separation_release"


def _shipping_gate(release_receipt: object) -> dict[str, Any]:
    """Evaluate the (c') shipping gate and return what to record.

    Raises with SHIPPING_GATE_REFUSAL -- a string that appears at exactly one
    producing line in the tree, so a refused assembly is attributable to THIS gate
    and not to any other assembly failure.
    """
    present = isinstance(release_receipt, Mapping) and bool(
        release_receipt.get("receipt_sha256"))
    record = {"evaluated": True, "release_present": present,
              "result": "pass" if present else "refused",
              "authority": "merged_shipping_gate"}
    if not present:
        raise ValueError(
            SHIPPING_GATE_REFUSAL + ": rst_merged assembly requires a parent "
            "separation release receipt; model output must not be assembled "
            "before parent validation released it")
    return record


'''
assert s.count(anchor) == 1
s = s.replace(anchor, gate + anchor, 1)

# 2. thread the boundary environment receipt + evaluate the gate
old = '''    release_receipt = candidate.get("parent_separation_release_receipt")
    controls = {
        "parent_separation": separation_receipt,
        "solution_portability": portability_receipt,
    }'''
new = '''    release_receipt = candidate.get("parent_separation_release_receipt")
    environment_receipt = candidate.get("environment_boundary_receipt")
    shipping_gate = (_shipping_gate(release_receipt)
                     if core.AUTHOR_MODE == "rst_merged" else None)
    controls = {
        "parent_separation": separation_receipt,
        "solution_portability": portability_receipt,
    }'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''    candidate_issue = core.candidate_problem(
        candidate_receipt, run_id=run_id, seed=seed,
        stage_receipts=receipts, artifacts=artifacts,
        control_receipts=control_receipts or None,
        source_authority_receipt=source_receipt,
        control_resources=control_resources,'''
new = '''    candidate_issue = core.candidate_problem(
        candidate_receipt, run_id=run_id, seed=seed,
        stage_receipts=receipts, artifacts=artifacts,
        control_receipts=control_receipts or None,
        source_authority_receipt=source_receipt,
        environment_receipt=environment_receipt,
        control_resources=control_resources,'''
assert s.count(old) == 1
s = s.replace(old, new)
p.write_text(s)
print("gate written")
