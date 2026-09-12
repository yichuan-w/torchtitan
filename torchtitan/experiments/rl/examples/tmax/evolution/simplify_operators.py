# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Trace-based choices for reducing one obstacle in a terminal task."""

from __future__ import annotations

import json
from pathlib import Path

CARDS = {
    "reduce_scale": "Use when individual objects succeed but multiple objects cause omissions or state confusion. Reduce one count or input dimension; retain the operation on each remaining object. Do not use when even one object cannot be handled. Restore the count to increase difficulty.",
    "relax_constraint": "Use when the basic result is correct but a resource, compatibility or implementation restriction blocks success. Remove one stated restriction and its corresponding checks, preserving correctness checks. Do not remove the capability chosen for training. Restore that restriction to increase difficulty; do not change fleet resources or episode budgets.",
    "provide_initial_state": "Use when prerequisites consume the attempts before the intended skill is reached. Materialize one valid prerequisite in the environment; keep the remaining goal and its checks. Do not precompute the output of the retained skill. Remove the supplied state to restore difficulty.",
    "extract_subtask": "Use when surrounding stages prevent meaningful attempts at a failing stage or interfere with its state. Keep that stage with realistic inputs and a checkable output; remove other goals and only their checks. If attempts already reach the stage and repeat the same incorrect method, removing completed work does not address that obstacle. Do not remove the failing skill itself. Reattach the surrounding stages to restore difficulty.",
    "reduce_distractors": "Use when the agent repeatedly confuses irrelevant files or similar records with relevant evidence. Remove one source of irrelevant material while retaining all evidence and the core inference. Missing evidence is a task defect, not a distractor. Restore the removed material to increase difficulty.",
    "add_scaffold": "Use when the agent has the evidence and can use the tools but cannot organize the next step. Add one directional hint or intermediate goal; specific guidance is allowed only at the configured hint level. Never provide the final answer, exact patch or full command recipe. Remove the hint to restore difficulty.",
    "enhance_feedback": "Use when repeated execution errors show the agent cannot interpret the tool response. Improve one task-local error message to identify the violated input or precondition, without supplying the solution. Preserve tool semantics and private grading. Restore the original message to increase difficulty; do not modify the shared harness.",
}


def prompt(hint: str) -> str:
    if hint not in ("none", "vague", "specific"):
        raise ValueError(f"unknown simplify hint level: {hint}")
    cards = {
        k: v
        for k, v in CARDS.items()
        if hint != "none" or k not in ("add_scaffold", "enhance_feedback")
    }
    return "\n\n".join(f"{key}: {rule}" for key, rule in cards.items()) + f"""

Hint level: {hint}. none permits structural changes only; vague permits a
direction or subgoal, not concrete solution steps; specific permits one
trace-supported step, never the complete solution or private verifier details.

Read multiple attempts, including successful steps. Identify a recurring
obstacle, then state the skill this variant retains. A timeout or 0/k alone
does not identify that obstacle. Compare the failed check with the submitted
artifact and the visible requirement before choosing a card. A reasonable
interpretation rejected by an unstated exact-string or formatting requirement
is a specification defect, even when a hint could make the test pass.
Use verifier diagnostics when present. A student's success claim or local
self-test proves only the property it exercised; repeated execution on one
unchanged input does not establish invariance under changed input order.
Infrastructure failures, missing required
evidence, inconsistent specifications and wrong grading require repair:
write BLOCKED: repair_required: <reason> to run/verdict.txt and stop.
If the work is already correct but the agent fails to submit, do not remove
unrelated task requirements. With insufficient evidence, write GIVE UP with
the reason and keep the task unchanged.

Choose exactly one card. Before editing, write run/simplify.json containing:
operator (one card id), retained_skill, bottleneck, change, restore, and
prediction, plus evidence (a nonempty list of objects with attempt, turn,
observation).
attempt is a basename from traces/, turn is an integer turn in that file,
and observation explains what the command and output show. Each other field
is a nonempty string. Describe the concrete size of the change in change.
The caller checks the declaration and evidence locations, not the truth of
your diagnosis; ground the diagnosis in what actually happened.

In prediction, name the observed failing action or decision and explain how
the proposed change enables a different next action. State an observable
result that would disprove this explanation in a new rollout. A shorter task,
fewer assertions, or a reminder to be careful is not a causal explanation.
For a hint, identify what direction the student lacked; repeating a direction
it already followed adds no help. For structural changes, identify the
prerequisite, interference, or search barrier being removed. Distinguish
evidence that is absent from evidence the student cannot yet discover: the
latter can justify supplying one starting artifact while retaining the
inference from it. Choose a different card or GIVE UP if the same failed
method would remain unchanged after the proposed edit.

Make only that change. Update instruction, environment, reference solution
and verifier together where needed. Checks may be removed only for the
explicitly removed goal or constraint; remaining goals retain semantic and
shortcut checks. Do not loosen checks to accept an incorrect remaining goal.
Preserve existing guidance and acceptance tolerances for the retained skill.
Extracting a stage must not silently add precision requirements or make its
test inputs harder. If an existing check is defective, request repair instead
of combining that repair with simplification.
The untouched environment must fail and the reference solution must pass.
Run ./sandbox check before finishing. Passing proves task validity, not its
difficulty: subsequent training rollouts measure whether it became easier.
"""


def read_decision(pkg: Path, hint: str) -> dict:
    decision = json.loads((pkg / "run/simplify.json").read_text())
    for field in (
        "operator",
        "retained_skill",
        "bottleneck",
        "change",
        "restore",
        "prediction",
    ):
        if not isinstance(decision.get(field), str) or not decision[field].strip():
            raise ValueError(f"simplify decision requires {field}")
    op = decision["operator"]
    if op not in CARDS or (
        hint == "none" and op in ("add_scaffold", "enhance_feedback")
    ):
        raise ValueError(f"simplify operator not allowed: {op}")
    evidence = decision.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("simplify decision requires trace evidence")
    for item in evidence:
        name = item.get("attempt", "")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.startswith("attempt-")
        ):
            raise ValueError("evidence must name an attempt file")
        turn = item.get("turn")
        observation = item.get("observation")
        if (
            type(turn) is not int
            or not isinstance(observation, str)
            or not observation.strip()
        ):
            raise ValueError("evidence requires a turn and observation")
        records = [
            json.loads(line)
            for line in (pkg / "traces" / name).read_text().splitlines()
            if line.strip()
        ]
        if not any(record.get("turn") == turn for record in records):
            raise ValueError(f"evidence turn absent: {name}:{turn}")
    return decision
