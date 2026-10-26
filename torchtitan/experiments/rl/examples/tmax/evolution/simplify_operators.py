# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""How a simplification is chosen from the attempts, and what it must declare.

The change comes from the attempts (and the task's earlier rewrites in
traces/history), as hardening's does; there is no menu of intervention kinds
to pick from, and the declaration names no operator. The rules every kind of
change is held to -- no final answer or recipe, correctness checks kept for
the retained goals -- are stated once, below.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

CALIBRATION_GUIDANCE = """Compare two intervention strengths by the observed decisions
they remove and the decisions left along the complete path to submission.
Historical progress under another policy does not estimate the current
student's success. Work absent from a trace is unknown; use the latest
measured behavior rather than an obstacle newer traces already solve.

When the latest variant is all-solved, undo its help within the original
requirements, sized by the attempts: what none of them uses or what they
reproduce unaided is withdrawn whole; what they visibly rely on is reduced to
a strict subset. Compare decisions
automated, not code size or command count: splitting a helper into extra calls
does not restore a decision if its checks still force the correct outcome.
Identify a decision the student will now make and a plausible incorrect choice
the remaining aid permits. For an all-solved starter, restore one coherent
part of the supplied implementation while retaining help with another observed
obstacle. A placeholder is insufficient if every student fills it correctly.

When the latest variant is all-failed, target the remaining observed obstacle
without repeating advice the student already followed. If history contains
both extremes, state what help the intervention adds to the all-failed variant
and what decision it leaves unaided compared with the all-solved variant.
Consider feedback at the mistaken decision when a warning in the instruction
does not change behavior. Expose the unmet condition while leaving the student
to choose and implement the correction; do not complete the task for them.
Check the diagnostic against the attempts' reactions. An artifact's existence
may become true while the actual requirement remains unmet. Replace such a
misleading cue or choose a different intervention. If students follow the cue
but stop after local success, target the next missing required transition.
Preserve the successful tool output contract; additional diagnostics must not
contaminate it. Keep the hint limits.

For supplied state, identify how the student discovers and uses the actual
target resource. Check that the retained verifier distinguishes success there
from the same message produced elsewhere, while allowing other correct
implementations. Report grading defects separately without repairing them or
treating false passes as useful difficulty. Predictions remain hypotheses;
oracle and untouched-state checks establish only candidate validity.
"""


def prompt(hint: str) -> str:
    if hint not in ("none", "vague", "specific"):
        raise ValueError(f"unknown simplify hint level: {hint}")
    return f"""Hint level: {hint}. It limits what the instruction and any text you supply
say about how to solve: none permits structural changes only; vague permits a
direction or subgoal, not concrete solution steps; specific permits one
trace-supported step. Structural help -- a starter, a completed stage, a
prepared input -- is allowed at every level for the work around the retained
skill, never for the retained skill itself. No level permits the final answer,
an exact patch, the output the retained skill should compute, or private
verifier details.

## Goal

Every attempt of the current student failed this task. Change it so the next
group of the same size lands in the learnable band: some attempts succeed and
some fail. A task that stays at 0/k gives no training signal and wastes every
rollout spent on it; a task that overshoots to k/k gives no signal either,
until a later hardening withdraws the help. Neither miss is the safe one: size
the change to what the attempts show.

## Diagnose from the attempts

Read every attempt in traces/ and find where each one first goes wrong; count
how many attempts stop at each point and cite them. Classify the dominant
cause, then act on that cause:
- A task defect: the instruction and the verifier disagree, the environment
  lacks what the task needs, or grading rejects a reasonable result. Repairing
  it is part of this rewrite: state the requirement, fix the setup, or correct
  the check, and record every repair in run/repair.json as a list of objects
  with nonempty "defect", "evidence" (attempt file and turn, or the conflicting
  file locations), "change" and "kind"; each repair is reported for review.
  "kind" is "reward_hack" for a shortcut an attempt was rewarded for,
  "hidden_requirement" for a check the instruction never states,
  "broken_setup" for an environment missing what the task needs, and "other"
  for anything else. The attempts measured the task with its defect, so they
  cannot show what the repaired task needs: the repair is this rewrite's only change, and the
  repaired task is measured before any help is added.
- Mechanics around the task: tool use, writing files through the terminal,
  exhausted time or context. Help with the mechanics and leave the task's
  decisions to the student.
- A missing capability at a decision the task requires: reduce what that
  decision demands.
An attempt the harness marks infra_failed (a sandbox or provider failure), or
one with no trace, is not a measurement: leave it out. An attempt whose
grading failed on the state it left behind -- a disk it filled, a package lock
its own command still held -- was scored and failed like any other; read it as
a failure of that attempt, often a mechanics bottleneck. Diagnose from the
scored attempts, and write BLOCKED: invalid_signal: <reason> to run/verdict.txt
and stop without edits only when no scored attempt with its trace remains. Base every claim about the
environment, the verifier or the student on what you observed in the traces
or the sandbox, not on what a file name or a success message suggests.

## Learn from earlier rewrites

traces/history/summary.md lists this task's earlier rewrites with what training
measured before and after each, and their authors' notes, including their
predictions. When an earlier simplification predicted successes and the
student still failed every attempt, find out from the current traces why
before choosing anything: if the attempts now get past the obstacle that
rewrite targeted and fail later, target the new obstacle; if they still fail
at the same point, its help did not reach them or was too weak, so give
stronger help there; if they fail at a defect, repair it. Write this
comparison in previous. A change that already failed, repeated, is not a new
attempt. If an earlier rewrite made this task harder, what it changed is the
first candidate to restore in part.

## Keep the retained skill with the student

retained_skill is the decision the task exists to train: the algorithm, the
bug, the technique its name points at. A version is trained so that the
student can later do the original task, so the student makes that decision in
every version, however much help surrounds it. A version in which the student
only completes what the help left open -- a line or a boundary check in an
implementation supplied around it, a skeleton the verifier now requires, the
original source or reference solution anywhere it can reach -- is solved
without the skill and teaches submitting a half-done task instead.

Judge the help as a whole, not this rewrite's increment:
traces/history/since-original.diff is every change since the original task
(absent when this is the original). Help that earlier simplifications already added counts toward what the
student is given, and if it already supplies part of the retained skill,
withdrawing that part is this rewrite's first change.

## Choose where the help lives

Choose the change from the attempts. The student will meet the original task
with none of this help, so what it learns here has to be triggered by
something the original task also shows it. Prefer help in this order:

1. The original task's own signals, made easier to reach. The student reacts
   to the error, test output or state the original also produces -- a
   ModuleNotFoundError, a crash under the original's input, a wrong value it
   can compare against a stated requirement. Change how soon or how plainly it
   meets that signal, not the signal: a smaller instance that reaches the
   failing step early, a starting state that removes unrelated steps before
   it, an instruction to run the program on a stated input and read what it
   reports. The cue it learns to act on is still there in the original.
2. Words in the instruction: a direction for the failing decision, or the goal
   decomposed into checked subgoals. The student still does the work, but on a
   cue the original lacks, so a later hardening must be able to withdraw it:
   write each hint as its own sentences, removable without rewriting the rest.
3. Work supplied in the environment: only for work outside the retained skill
   that the attempts already do reliably, so the student spends its budget on
   the skill, or that the original environment did not let it do (record that
   as a repair). The student never does supplied work, so it learns nothing
   from it. What the student could have obtained in the original environment
   itself -- a package or tool it can install, a file it can fetch, a setting
   it can change -- is part of the work: help with it by saying what to obtain
   and how, not by obtaining it for the student.

Help comes in increasing strength within each place, up to a worked instance
of the hard step on an input other than the graded one. Use the earliest place
and the weakest help the evidence says will produce successes. A failed
prediction at one strength needs a reason, from the traces, to stay at that
strength. When no attempt has a foothold, reduce what the retained decision
demands -- a smaller instance, fewer cases, feedback that exposes the mistake
where it is made -- and leave the decision itself to the student; when the
student cannot make it in any form the task can still grade, write GIVE UP
with that reason rather than hand it over. Say in change which place the help
is in and which signal of the original the student learns to act on.

## Invariants

- Never hand over the final answer, the exact output, or a command recipe that
  completes the task; the student must still do the remaining work.
- Keep the verifier's correctness and shortcut checks for every requirement
  that remains. Remove checks only for a requirement you removed, and edit
  every sentence of instruction.md that states or implies it; a separate
  author writes the verifier from the instruction.
- Guidance names the operation the reference solution actually performs. Apply
  your guidance literally to the task's own data in the sandbox and confirm it
  reproduces the reference's intermediate result.
- Update instruction, environment, reference solution and verifier together.
  The untouched environment must fail and the reference solution must pass:
  run ./sandbox check before finishing.

## Declare

Before editing, write run/simplify.json with retained_skill, bottleneck,
change, restore, prediction, expected and evidence, plus previous when the
history shows an earlier accepted simplification. expected is the solved
count you predict for the next group of the same size, written as "k of n".
prediction names the failing action the change addresses, why the change
enables a different next action, and an observable result that would refute
it. evidence is a nonempty list of objects with attempt, turn and observation:
attempt is the file's name exactly as traces/ lists it, with its .jsonl suffix
and no directory (attempt-03.jsonl); turn is an integer: a turn that file
records, or 0 for its first line, the attempt's summary with any verifier
diagnostics. When you run an attempt's implementation in the sandbox to test a
suspected mismatch, record the input, observed output and grading result in
the observation of the evidence item for the attempt whose implementation you
ran. Each other field is a nonempty string. The caller checks the
declaration's form and evidence locations, not the truth of your diagnosis.

Every attempt failing is itself the evidence that help is needed: a teacher
whose student keeps failing gives a hint, not up. When the traces are thin,
start from the weakest help at the first point where the attempts go wrong.
Write GIVE UP with the reason only when the task cannot be made valid here --
a defect this environment does not let you repair -- and keep it unchanged.
""" + extra_guidance()


def extra_guidance() -> str:
    """Rules a deployment adds to every simplify prompt and to every calibration
    (the hardening that undoes a simplification), read from the file
    EVOLVE_SIMPLIFY_EXTRA_GUIDANCE_FILE names; nothing when it is unset, so the
    evolve loop's prompts are unchanged. A named file that is missing raises."""
    path = os.environ.get("EVOLVE_SIMPLIFY_EXTRA_GUIDANCE_FILE", "")
    if not path:
        return ""
    return "\n" + Path(path).read_text()


EXPECTED = re.compile(r"^\s*\d+\s+of\s+\d+\s*$")


def read_decision(pkg: Path, hint: str) -> dict:
    decision = json.loads((pkg / "run/simplify.json").read_text())
    for field in (
        "retained_skill",
        "bottleneck",
        "change",
        "restore",
        "prediction",
        "expected",
    ):
        if not isinstance(decision.get(field), str) or not decision[field].strip():
            raise ValueError(f"simplify decision requires {field}")
    if not EXPECTED.match(decision["expected"]):
        raise ValueError('simplify decision expected must read "k of n"')
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
        path = pkg / "traces" / name
        if not path.is_file() and (pkg / "traces" / f"{name}.jsonl").is_file():
            # "attempt-01" names attempt-01.jsonl and nothing else.
            path = pkg / "traces" / f"{name}.jsonl"
            item["attempt"] = path.name
        if not path.is_file():
            raise ValueError(f"evidence names no file in traces/: {name}")
        with path.open() as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        # Turn 0 is the first line: the attempt's summary, where the verifier's
        # diagnostics are, and evidence worth citing.
        if turn == 0:
            found = bool(records) and "turn" not in records[0]
        else:
            found = any(record.get("turn") == turn for record in records)
        if not found:
            raise ValueError(f"evidence turn absent: {path.name}:{turn}")
    return decision
