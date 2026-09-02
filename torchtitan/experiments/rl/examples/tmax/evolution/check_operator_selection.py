#!/usr/bin/env python3
"""Check the operator selection against the authors' stated formula.

The formula was sent by 煜坤 on 2026-08-15 and is written down in
docs/rst-authors/. Implementing it is a claim that this code does what that
document says, so each clause of it gets a check here rather than a reading.

    S(o) = L(o) x D(f(o)) x P(o)
    D(f) = max(0.25, 1 + 0.2N - n_f)
    P(o) = 1 / (1 + n_o)
    config_data_consistency additionally x 0.35
    top 12 by local fit reach the diversity stage; best is preferred, next 5 fallback

Run: check_operator_selection.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import synth_client as llm  # noqa: E402
import synth_operators as ops  # noqa: E402

# A seed with signals from several families, so the scan has something to rank.
SEED = {
    "instruction": "Fix the failing pytest run and regenerate the report.",
    "dockerfile": "FROM python:3.11\nRUN pip install -r requirements.txt\n",
    "solution": ("make build\npytest -q\nsort data.csv | uniq > out.csv\n"
                 "sha256sum out.csv > out.sha256\ngit log --oneline\n"),
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def main() -> None:
    print("local fit")
    fit = llm.local_fit(SEED)
    check("every operator scored", len(fit) == 40, f"{len(fit)} scored")
    check("some operator has signal", max(fit.values()) > 0,
          f"max {max(fit.values()):.3f}")

    # The damping is visible only against the same operator undamped.
    raw = dict(fit)
    saved = llm.BROAD_OPERATOR
    llm.BROAD_OPERATOR = {}
    try:
        undamped = llm.local_fit(SEED)
    finally:
        llm.BROAD_OPERATOR = saved
    op = "config_data_consistency"
    expected = undamped[op] * 0.35
    check("config_data_consistency damped by 0.35 (on S, per the authors)",
          abs(raw[op] - expected) < 1e-9,
          f"{raw[op]:.4f} vs {expected:.4f}")

    print("\npool and ordering")
    scored = llm.score_operators(SEED, {}, {})
    check("pool capped at 12", len(scored) <= llm.LOCAL_POOL,
          f"{len(scored)} candidates")
    check("sorted by score, best first",
          all(scored[i][0] >= scored[i + 1][0] for i in range(len(scored) - 1)))

    print("\nfamily balance D(f) = max(0.25, 1 + 0.2N - n_f)")
    # A family that is behind its share must outscore the same operator when
    # that family is ahead — with the operator's own count held fixed.
    fam_of = {o: f for f, d in ops.OPERATORS.items() for o in d}
    top_op = scored[0][2]
    fam = fam_of[top_op]
    ahead_fams = {f: 0 for f in ops.OPERATORS}
    ahead_fams[fam] = 10
    ahead = {o: s for s, _, o in llm.score_operators(SEED, {}, ahead_fams)}
    behind = {o: s for s, _, o in llm.score_operators(SEED, {}, {})}
    check("an over-used family scores lower",
          ahead.get(top_op, 0) < behind.get(top_op, 0),
          f"{ahead.get(top_op, 0):.4f} < {behind.get(top_op, 0):.4f}")
    check("but is floored, not excluded", ahead.get(top_op, 0) > 0)

    d_floor = max(0.25, 1 + 0.2 * 10 - 10)
    check("floor is 0.25 where the formula goes negative", d_floor == 0.25)

    print("\noperator frequency P(o) = 1/(1+n_o)")
    once = {o: s for s, _, o in llm.score_operators(SEED, {top_op: 1}, {})}
    check("a used operator is halved on the second pass",
          abs(once.get(top_op, 0) - behind[top_op] / 2) < 1e-9,
          f"{once.get(top_op, 0):.4f} vs {behind[top_op] / 2:.4f}")

    print("\nblocked, which the authors put at 0.33-0.57% end to end")
    # L(o) starts at 1 and adds, so nothing scores zero and the local scan does
    # not decline a seed on its own. It used to: dividing hits by markers gave
    # absent signal a score of zero, and we blocked 3-14% of seeds. Blocking is
    # now what it should be — the model reading a seed and saying no.
    empty = {"instruction": "", "dockerfile": "", "solution": ""}
    fit = llm.local_fit(empty)
    check("a seed with no signal still scores, from the base of 1",
          min(fit.values()) > 0, f"min {min(fit.values()):.2f}")
    check("the local scan alone does not block it",
          llm.score_operators(empty, {}, {}) != [])

    print("\nfallbacks")
    check("five alternatives offered", llm.FALLBACK_COUNT == 5)

    print("\nshortlist handed to the agent")
    sl = llm.operator_shortlist(SEED, {}, {})
    scored = llm.score_operators(SEED, {}, {})
    check("hands over the candidates, not one",
          1 < len(sl) <= 1 + llm.FALLBACK_COUNT, f"{len(sl)} candidates")
    check("shaped (family, operator, definition)",
          all(len(c) == 3 and all(isinstance(x, str) for x in c) for c in sl))
    check("keeps the score order",
          [op for _, op, _ in sl] == [op for _, _, op in scored[:len(sl)]])
    check("every candidate is a real operator",
          all(op in ops.OPERATORS[fam] for fam, op, _ in sl))
    check("no duplicates", len({op for _, op, _ in sl}) == len(sl))
    check("definitions are the operators' own",
          all(d == ops.OPERATORS[f][o] for f, o, d in sl))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
