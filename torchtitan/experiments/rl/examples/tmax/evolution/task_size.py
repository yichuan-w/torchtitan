#!/usr/bin/env python3
"""How much a task asks, in two numbers, and how far a rewrite may raise them.

The size of the reference solution (non-empty, non-comment lines of
solve.sh) is a proxy for how much work the policy has to reproduce, and the
number of assertions in the verifier for how many things it has to get
exactly right. Neither says how hard one task is. What they say is where a
task sits in the seed corpus's measured outcomes, and that is enough to keep
a rewrite where the training signal is most often mixed.

Measured on wd-20260903b (663 seeds, labels from ~14 h of training signals;
"easy" = 16/16, "hard" = 0/16, "in band" = no signal):

    solution lines   easy  in band  hard   hard share
    < 6                68       17     7      8%
    6 - 10            106       59    19     10%
    10 - 14            67       53    15     11%
    14 - 20            49       46    12     11%
    20 - 30            21       30    18     26%
    >= 30              13       39    24     32%

Below 14 lines a task is still mostly easy; 14-20 has the largest in-band
share with the hard share still one in nine; past 20 the hard share doubles.
The agentic harder arm's rewrites had a median of 125 lines (seeds: 9), far
outside the table, and 85% of the ones training sampled again came back
0/16. Seed verifiers have a median of 6 assertions in 3 test functions, so
one requirement is two or three assertions.

So a rewrite is one rung up: at least MIN_ADDED lines more than the seed
(it did get harder), at most MAX_ADDED more (one requirement), and at most
MAX_ADDED_ASSERTS more assertions. The band is relative to the seed, with no
absolute ceiling: the seed at its size scored 16/16, which is the proof the
policy handles that size, and one rung above it is what is asked. (An
absolute ceiling of 20 was tried first; with a 17-line seed it left one
admissible size and with an 18-line seed none.) The policy's own effort on
the seed (turns, commands typed) was tried as a predictor of the rewrite's
outcome and carried nothing (AUC 0.47), so it is not used.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

MIN_ADDED = 3
MAX_ADDED = 8
MAX_ADDED_ASSERTS = 5


def solution_lines(src: str) -> int:
    return sum(1 for l in src.splitlines() if l.strip() and not l.strip().startswith("#"))


def verifier_asserts(src: str, kind: str = "python") -> int:
    if kind == "python":
        try:
            tree = ast.parse(src)
        except SyntaxError:
            tree = None
        if tree is not None:
            return sum(1 for n in ast.walk(tree) if isinstance(n, (ast.Assert, ast.Raise)))
    return len(re.findall(r"^\s*(assert|raise|exit 1|return 1)\b", src, re.M))


def size_of(solve_sh: str, verifier: str, kind: str = "python") -> dict:
    return {"solution_lines": solution_lines(solve_sh),
            "verifier_asserts": verifier_asserts(verifier, kind)}


def size_of_package(pkg: Path, verifier_rel: str) -> dict:
    kind = "python" if verifier_rel.endswith(".py") else "shell"
    return size_of(_read(pkg / "solution" / "solve.sh"), _read(pkg / verifier_rel), kind)


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def violations(seed: dict, new: dict) -> list[str]:
    """Why `new` is not one rung above `seed`, in the words the agent reads;
    empty when it is."""
    out = []
    s, n = seed["solution_lines"], new["solution_lines"]
    if n < s + MIN_ADDED:
        out.append(f"the reference solution has {n} lines against the seed's {s}; a harder task "
                   f"needs at least {MIN_ADDED} more, or the policy has nothing new to do")
    if n > s + MAX_ADDED:
        out.append(f"the reference solution has {n} lines against the seed's {s}; one rung is at "
                   f"most {MAX_ADDED} more (in this corpus the 0/16 share doubles once a task "
                   f"outgrows the 14-20 line band, and rewrites that grew to 125 lines scored "
                   f"0/16 five times in six)")
    sa, na = seed["verifier_asserts"], new["verifier_asserts"]
    if na > sa + MAX_ADDED_ASSERTS:
        out.append(f"the verifier has {na} assertions against the seed's {sa}; one requirement is "
                   f"two or three, so at most {MAX_ADDED_ASSERTS} more")
    return out


def why(vs: list[str]) -> str:
    return ("The rewrite is more than one rung above the seed: " + "; ".join(vs)
            + ". Keep the seed's deliverable and add one requirement; take the rest back out.")
