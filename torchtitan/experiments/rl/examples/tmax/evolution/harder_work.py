# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_HARDER_WORK=<X> and EVOLVE_HARDER_ESCALATE=1 (both default off): size
a harder rewrite by the work the student's own solved attempts measured.

In run 111e a harder rewrite's first group tracked how much more work it made
the student do on the same task, and nothing the author could see or claim
predicted it: the authors wrote "6 of 12" 78% of the time (r = 0.07 against the
measured count) and said both replays were blocked in 92% of the rewrites
that then came back 12/12. Measured as W = the next group's median turns over
the parent's (drift-corrected), rewrites near W = 1.2 stayed 12/12 about 6 times
in 10 and landed 3-8/12 about 1 in 30; at W >= 2 about 1 in 3 stayed 12/12 and
1 in 6 landed 3-8/12. A lineage whose harder rewrite already came back 12/12
did worse on the next round (3-8/12 in 4%), and its authors did not take a
bigger step on their own (median W 1.17 against 1.24).

EVOLVE_HARDER_WORK gives the author the parent's measured work as numbers
(run/parent_work.json and a prompt block), asks for a change that makes the
median solver need at least X times its turns, drops the "modest step" wording,
scales task_size's caps by X, and requires a ``work: N turns`` line in
run/hardening.md, recorded as ``harder_work`` in rewrite.json beside the parent
stats so W can be measured on the next group.

EVOLVE_HARDER_ESCALATE: when the lineage's previous harder rewrite came back
all-solved (traces/history/index.jsonl), the factor is multiplied by
ESCALATE_STEPS (1.5 after one such miss, 2 after two or more) and two stacked
changes are allowed. Without EVOLVE_HARDER_WORK the base factor is 1, so only
escalated rewrites get a work target.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from pathlib import Path

import task_size as ts

from torchtitan.experiments.rl.examples.tmax import rollout_record

# Rough characters per token of an attempt's transcript; records carry no
# token counts, so context use is an estimate and is labelled as one.
CHARS_PER_TOKEN = 3.5
# A target never asks for more of the turn budget than this.
MAX_BUDGET_SHARE = 0.85
# Escalation multipliers by the number of consecutive all-solved harder
# rewrites at the end of the lineage (1 -> 1.5, 2 or more -> 2.0).
ESCALATE_STEPS = (1.5, 2.0)


def work_factor() -> float | None:
    """EVOLVE_HARDER_WORK as a float >= 1; None when unset."""
    spec = os.environ.get("EVOLVE_HARDER_WORK", "").strip()
    if not spec:
        return None
    try:
        x = float(spec)
    except ValueError:
        x = float("nan")
    if not x >= 1.0 or x > 10:
        raise ValueError(f"EVOLVE_HARDER_WORK must be a number in [1, 10], got {spec!r}")
    return x


def escalate_enabled() -> bool:
    return os.environ.get("EVOLVE_HARDER_ESCALATE", "0") == "1"


def enabled() -> bool:
    return work_factor() is not None or escalate_enabled()


def _keys_text(keys) -> str:
    if isinstance(keys, list):
        return "".join(str(k) for k in keys)
    return str(keys or "")


def _spread(values: list[float]) -> dict:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def parent_stats(
    traces: Path,
    *,
    skip_finish: frozenset = frozenset(),
    turn_budget: int,
    secs_budget: int,
    context_tokens: int,
) -> dict | None:
    """The solved attempts' work in traces/attempt-*.jsonl: turns, seconds and
    transcript characters (fastest/median/slowest), with the slack the
    slowest left against each budget. None when no attempt solved."""
    solved = []
    attempts = 0
    for path in sorted(traces.glob("attempt-*.jsonl")):
        try:
            head, turns = rollout_record.read_record(path)
        except (OSError, ValueError):
            continue
        reward = head.get("reward")
        if (
            "turn" in head
            or head.get("infra_failed")
            or head.get("finish_reason") in skip_finish
            or not isinstance(reward, (int, float))
            or reward != reward
        ):
            continue
        attempts += 1
        if reward <= 0:
            continue
        chars = 0
        n_turns = 0
        for t in turns:
            if "turn" not in t:
                continue
            n_turns += 1
            chars += sum(len(t.get(k) or "") for k in ("think", "analysis", "plan", "output"))
            chars += len(_keys_text(t.get("keystrokes")))
        solved.append({
            "attempt": path.name,
            "turns": int(head.get("turns") or n_turns),
            "secs": float(head.get("secs") or 0.0),
            "chars": chars,
            "budget_sec": head.get("budget_sec"),
        })
    if not solved:
        return None
    secs_budget = int(solved[0]["budget_sec"] or secs_budget)
    turns = _spread([s["turns"] for s in solved])
    secs = _spread([s["secs"] for s in solved])
    chars = _spread([s["chars"] for s in solved])
    ctx_chars = context_tokens * CHARS_PER_TOKEN
    return {
        "solved": len(solved),
        "attempts": attempts,
        "turns": turns,
        "secs": {k: round(v, 1) for k, v in secs.items()},
        "chars": chars,
        "budgets": {"turns": turn_budget, "secs": secs_budget, "context_tokens": context_tokens},
        "slack": {
            "turns": round(1 - turns["max"] / turn_budget, 3),
            "secs": round(1 - secs["max"] / secs_budget, 3),
            "context_est": round(1 - chars["max"] / ctx_chars, 3),
        },
        "chars_per_token": CHARS_PER_TOKEN,
        "per_attempt": [{k: s[k] for k in ("attempt", "turns", "secs", "chars")} for s in solved],
    }


def lineage_misses(traces: Path) -> int:
    """Consecutive accepted harder rewrites at the end of this task's history
    whose next measured group was all-solved; 0 without a history."""
    index = traces / "history" / "index.jsonl"
    try:
        entries = [json.loads(l) for l in index.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return 0
    misses = 0
    for entry in reversed(entries):
        if entry.get("status") != "accepted":
            continue
        measured = entry.get("measured_next") or {}
        if (
            entry.get("job") == "harder"
            and isinstance(measured.get("solved"), int)
            and measured.get("total")
            and measured["solved"] >= measured["total"]
        ):
            misses += 1
            continue
        break
    return misses


def target(stats: dict | None, misses: int) -> dict | None:
    """The work target for this rewrite, or None when neither flag applies
    (or the parent has no solved attempt to measure)."""
    base = work_factor()
    escalated = escalate_enabled() and misses > 0
    if stats is None or (base is None and not escalated):
        return None
    factor = base if base is not None else 1.0
    step = 1.0
    if escalated:
        step = ESCALATE_STEPS[min(misses, len(ESCALATE_STEPS)) - 1]
        factor *= step
    median = stats["turns"]["median"]
    budget = stats["budgets"]["turns"]
    need = math.ceil(factor * median)
    ceiling = int(MAX_BUDGET_SHARE * budget)
    return {
        "base_factor": base,
        "factor": round(factor, 3),
        "escalated": escalated,
        "escalation_step": step,
        "misses": misses,
        "stacked_allowed": escalated,
        "target_turns": min(need, ceiling),
        "capped_by_budget": need > ceiling,
        "caps": size_caps(factor),
    }


def size_caps(factor: float) -> dict:
    """task_size's per-rewrite caps scaled by the work factor."""
    return {
        "max_added": math.ceil(ts.MAX_ADDED * factor),
        "max_added_asserts": math.ceil(ts.MAX_ADDED_ASSERTS * factor),
    }


def _pct(x: float) -> str:
    return f"{max(0.0, x) * 100:.0f}%"


def prompt_block(stats: dict, tgt: dict, attempts_n: int) -> str:
    t, s, c = stats["turns"], stats["secs"], stats["chars"]
    b, sl = stats["budgets"], stats["slack"]
    tok = lambda chars: int(chars / CHARS_PER_TOKEN)  # noqa: E731
    lines = [
        "",
        "",
        "THE WORK TARGET",
        f"The {stats['solved']} solved attempts of {stats['attempts']} in traces/ measured "
        "(fastest / median / slowest; per attempt in run/parent_work.json):",
        f"  turns       {t['min']} / {t['median']:g} / {t['max']}   "
        f"(budget {b['turns']}; the slowest left {_pct(sl['turns'])} unused)",
        f"  seconds     {s['min']:.0f} / {s['median']:.0f} / {s['max']:.0f}   "
        f"(budget {b['secs']}; the slowest left {_pct(sl['secs'])} unused)",
        f"  transcript  {c['min']:,} / {c['median']:,.0f} / {c['max']:,} characters, about "
        f"{tok(c['min']):,} / {tok(c['median']):,} / {tok(c['max']):,} tokens at "
        f"{CHARS_PER_TOKEN:g} characters a token (context {b['context_tokens']:,} tokens; "
        f"the largest left about {_pct(sl['context_est'])} unused)",
        "",
        "In run 111e a harder rewrite's next group tracked how much more work it made",
        "the student do on this same task, not how blocked the replayed attempts looked:",
        "the student reads the revised task and takes another path. Rewrites whose next",
        "group needed about 1.2 times the parent's turns stayed solved by every attempt",
        "6 times in 10 and landed 3 to 8 of 12 about once in 30; at 2 times or more,",
        "about 1 in 3 stayed all-solved and 1 in 6 landed 3 to 8 of 12. Most attempts",
        "that failed there submitted a wrong answer; few ran out of budget.",
        "",
        f"This rewrite's target: the median successful attempt needs at least "
        f"{tgt['factor']:g} times its current turns, that is at least "
        f"{tgt['target_turns']} turns against the median {t['median']:g} now, within the "
        f"{b['turns']}-turn budget."
        + (" (The target is capped at "
           f"{int(MAX_BUDGET_SHARE * 100)}% of the turn budget.)" if tgt["capped_by_budget"] else ""),
        "The added turns come from work the student cannot skip on the revised task:",
        "reading, the failure it meets, diagnosis, the fix, rebuilding and re-checking.",
        "Count them at the pace these attempts work in traces/, including the steps an",
        "attempt has not reached yet, not as the reference solution's commands: a change",
        "the reference solution makes in one line can still cost the student many",
        "turns, and one it needs many lines for can cost it few. Size the change so",
        "the median attempt clears the target; a change the student routes around in",
        "a few turns does not count however many replayed paths it stops.",
        f"The size limits above are already scaled for this target: the reference "
        f"solution may grow by at most {tgt['caps']['max_added']} non-comment lines and "
        f"the verifier by at most {tgt['caps']['max_added_asserts']} assertions.",
    ]
    if tgt["stacked_allowed"]:
        n = tgt["misses"]
        what = "harder rewrite" if n == 1 else f"{n} harder rewrites"
        lines += [
            "",
            f"This task's last {what} came back solved by every attempt",
            "(traces/history): that step size was too small for this student, so the",
            f"target above is already {tgt['escalation_step']:g} times what it would otherwise be.",
            "This rewrite may stack two independent difficulties, where the guidance",
            "above says one, as long as each holds to every rule above and both lie on",
            "the path the median attempt takes.",
        ]
    lines += [
        "",
        "In `run/hardening.md`, put your estimate on a line of its own, exactly",
        "`work: N turns` with N the turns you expect the median successful attempt to",
        "need on the revised task; the caller reads that line, records it beside these",
        "measurements, and returns a hardening.md without it to you.",
    ]
    return "\n".join(lines) + "\n"


# Each replacement drops the "modest step" sizing from a harder job; the
# phrases are asserted against the guidance texts in evolve_codex.
MODEST_REPLACEMENTS = (
    (
        "and why that is one modest step from the skills\nthose attempts show.",
        "and how many turns the median successful attempt will now need, against\n"
        "the work target below.",
    ),
    (
        "while remaining a modest step from those skills;",
        "while remaining reachable from those skills;",
    ),
    (
        "Aim for a modest reduction in this student's solve rate toward mixed success,\n"
        "not universal failure.",
        "Aim for mixed success, not universal failure, and size the change by the\n"
        "work target below.",
    ),
)


def drop_modest(text: str) -> str:
    for old, new in MODEST_REPLACEMENTS:
        text = text.replace(old, new)
    return text


WORK_LINE = re.compile(
    r'^[ \t>*`-]*work:[ \t]*"?[ \t]*(\d+)[ \t]+turns?[ \t]*"?`?[ \t]*$',
    re.MULTILINE | re.IGNORECASE,
)


def declared_work(text: str) -> int | None:
    """The ``work: N turns`` line of a hardening.md as N; None when absent."""
    m = WORK_LINE.search(text)
    if m is None or int(m[1]) <= 0:
        return None
    return int(m[1])


def summary(stats: dict) -> dict:
    """The stats as recorded in rewrite.json (per-attempt rows left out)."""
    return {k: v for k, v in stats.items() if k != "per_attempt"}
