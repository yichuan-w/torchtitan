#!/usr/bin/env python3
"""Turn rollout outcomes into a diagnosis, not just a difficulty score.

RST validates with the oracle and discards what the oracle cannot pass; it uses
pass@k only to report difficulty after the fact. Feeding rollout outcomes back
into synthesis is an extension, and doing it on pass@k alone is a trap:

  * 0/8 reads as "too hard", but it is equally consistent with the task never
    telling the agent something the verifier requires. That is unfairness, not
    difficulty, and simplifying the task hides it instead of fixing it.
  * 8/8 reads as "too easy", but it is equally consistent with the instruction
    handing over what the verifier checks. Making the task harder leaves the
    leak in place, and the next round inherits it.

So the outcome is only half the evidence. Crossed with the audit — does the
verifier assert things the instruction and workspace never reveal, does the
instruction name the verifier — it separates four different situations that
want four different repairs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Diagnosis:
    verdict: str          # what actually happened
    action: str           # what to do about it
    why: str              # one line, for the manifest


# Above this share of attempts, a task is treated as carrying no gradient even
# when one attempt failed — measured, not chosen: see diagnose().
EASY_RATE = 0.75


def diagnose(rewards: list[str | None], audit: dict,
             attempts: int) -> Diagnosis:
    """Cross pass@k with the audit flags.

    `audit` carries `dark_paths` (verifier asserts what nothing reveals) and
    `leaks` (instruction names the verifier).
    """
    solved = sum(1 for r in rewards if r == "1")
    graded = sum(1 for r in rewards if r in ("0", "1"))
    dark, leaks = audit.get("dark_paths") or [], audit.get("leaks") or []

    if graded == 0:
        return Diagnosis("ungraded", "requeue",
                         "no rollout produced a reward file; harness fault "
                         "rather than a task verdict")

    if solved == 0:
        if dark:
            return Diagnosis(
                "unfair", "repair_discoverability",
                f"nothing solved it and the verifier asserts {len(dark)} path(s) "
                f"the instruction and workspace never reveal — publish them or "
                f"make them discoverable, do not simplify the task")
        return Diagnosis(
            "too_hard", "simplify_or_tier",
            "nothing solved it and the verifier only checks what the task "
            "reveals; genuinely hard, so either simplify or keep it as a "
            "hard-tier task rather than a training seed")

    # Not `solved == graded`. A task accepted at 3 of 4 re-measures at 1.0 in
    # seven of ten cases, because a task whose true rate is around 0.9 drops one
    # attempt often enough to look banded — it leaks in over the top edge. At 2
    # of 4 the same re-measurement holds six in ten. So the gate treats a rate at
    # or above three quarters as no gradient, which costs yield and buys most of
    # the drift back.
    if graded and solved / graded >= EASY_RATE:
        if leaks:
            return Diagnosis(
                "leaking", "tighten_instruction",
                "every attempt solved it and the instruction names the "
                "verifier; tighten the instruction before concluding anything "
                "about difficulty")
        return Diagnosis(
            "too_easy", "apply_another_operator",
            "every attempt solved it with no leak signal; genuinely easy, so "
            "recurse with another operator instead of discarding")

    return Diagnosis("usable", "accept",
                     f"solved {solved}/{graded}: discriminating, which is what "
                     f"an RL seed has to be")


REPAIR_PROMPTS = {
    # The paper's repair policy bounds what a repair may touch and forbids it
    # from buying success by weakening the task. Both directions below obey it:
    # neither removes a check, and neither publishes a private requirement.
    "repair_discoverability": """The task was attempted {attempts} times and \
never solved. Its verifier asserts these paths, which appear in neither the \
instruction nor the workspace:

{dark}

Make each one fair. Prefer making the evidence discoverable — a fixture, config \
file, README or command output the instruction already points at — over listing \
paths in the instruction. Publish in the instruction only what cannot be made \
discoverable.

You may not remove or weaken the check, relax a semantic assertion, or drop \
shortcut protection. Return only the files you change, with full contents.

Return schema: {{"status":"ok","rationale":"...","files":{{}}}}

Task contract:
{contract}

Current task:
{task_context}""",

    "tighten_instruction": """The task was solved on every one of {attempts} \
attempts, and the instruction names the verifier or restates its checks:

{leaks}

Rewrite instruction.md so it states the goal and the deliverable without \
handing over the acceptance criteria. Keep it under the path budget: about \
three absolute paths at most, no schema dumps, no numbered step checklists. \
Anything the agent legitimately needs must stay discoverable from the \
workspace.

You may not weaken the verifier or change the solution. Return instruction.md \
only.

Return schema: {{"status":"ok","rationale":"...","files":{{"instruction.md":"..."}}}}

Task contract:
{contract}

Current task:
{task_context}""",
}
