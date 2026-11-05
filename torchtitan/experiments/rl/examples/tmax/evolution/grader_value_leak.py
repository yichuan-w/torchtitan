"""EVOLVE_GRADER_VALUE_LEAK=record|reject (default off): a rewrite that writes
the grader's concrete test values into the instruction.

In 111e a harder rewrite of task_001320 (rev2, rev3) replaced the grader's
recomputation with fixed inputs and expected outputs and stated the same pair
in instruction.md ("must print N"). Every gate passed it, because the
instruction now licensed the hard-coded answer, and the student found it.

The check is static and language-blind, so a Rust or shell grader is covered
as well as a Python one: a token that this rewrite added to tests/ and also
added to instruction.md. Tokens are numbers of MIN_DIGITS digits or more, and
quoted strings in tests/ of MIN_CHARS characters or more that hold a digit (a
file name, a key, a path or a command the instruction must state is left out:
measured on exp-canon-harder-r100, the one string without a digit flagged was
the `vm-audit ./simple_vm tests/*.asm` command the task requires). A value
both versions already shared is the task's own and is never flagged.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import verifier_literals as vl

MIN_DIGITS = 6
MIN_CHARS = 8
FILE_MAX = 1024 * 1024
_NUMBER = re.compile(r"(?<![\w.])\d{%d,}(?![\w])" % MIN_DIGITS)
_QUOTED = re.compile(r"\"([^\"\n]{%d,200})\"|'([^'\n]{%d,200})'" % (MIN_CHARS, MIN_CHARS))


def mode() -> str:
    """"record", "reject", or "" (off)."""
    value = os.environ.get("EVOLVE_GRADER_VALUE_LEAK", "").strip()
    if value not in ("", "0", "record", "reject"):
        raise ValueError(f"EVOLVE_GRADER_VALUE_LEAK must be record or reject, got {value!r}")
    return "" if value == "0" else value


def _tests_text(pkg: Path) -> str:
    tests = pkg / "tests"
    if not tests.is_dir():
        return ""
    parts = []
    for path in sorted(tests.rglob("*")):
        if path.is_file() and path.stat().st_size <= FILE_MAX:
            data = path.read_bytes()
            if b"\0" not in data:
                parts.append(data.decode(errors="replace"))
    return "\n".join(parts)


def _instruction(pkg: Path) -> str:
    path = pkg / "instruction.md"
    return path.read_text(errors="replace") if path.is_file() else ""


def _candidates(text: str) -> set[str]:
    found = set(_NUMBER.findall(text))
    for m in _QUOTED.finditer(text):
        s = (m[1] or m[2]).strip()
        if len(s) >= MIN_CHARS and re.search(r"\d", s) and not vl.SHAPE.match(s):
            found.add(s)
    return found


def _in(token: str, text: str) -> bool:
    if token.isdigit():
        return re.search(r"(?<![\w.])" + token + r"(?![\w])", text) is not None
    return token in text


def leaked(parent: Path, new: Path) -> list[str]:
    """Tokens new in both tests/ and instruction.md of ``new`` against ``parent``."""
    old_tests, new_tests = _tests_text(parent), _tests_text(new)
    old_instr, new_instr = _instruction(parent), _instruction(new)
    added = _candidates(new_tests) - _candidates(old_tests)
    return sorted(
        t for t in added
        if not _in(t, old_tests) and _in(t, new_instr) and not _in(t, old_instr)
    )


def check(parent: Path, new: Path) -> dict:
    tokens = leaked(parent, new)
    if not tokens:
        return {"ok": True}
    shown = ", ".join(repr(t) for t in tokens[:5])
    return {
        "ok": False,
        "tokens": tokens[:20],
        "why": f"the instruction states test values this rewrite added to tests/: {shown}",
    }


PROMPT_NOTE = """

Do not write the grader's concrete test inputs or expected outputs into the
instruction. A requirement the instruction states as "for input X print N" is
met by a program that prints N, so it can be hard-coded. State the rule the
program must follow, and have the grader check it on inputs the instruction
does not name (hidden, generated or randomized ones)."""
