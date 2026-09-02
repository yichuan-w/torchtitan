#!/usr/bin/env python3
"""Make FA4 backward refuse to run when the DSL underneath it is the wrong one.

The rollout environment has flash-attn-4 4.0.0b26 against nvidia-cutlass-dsl
4.6.0, which is not the version the release pins. Forward works there and vllm
can use it; backward hangs, with no output and no error, until something kills
it. That silent hang cost this investigation twenty-one rounds, and it will cost
whoever calls backward there next the same way.

This inserts an assertion, not a workaround. It compares the installed DSL
against what flash-attn-4 requires and raises only when they disagree, so it
disappears on its own the day the environment is corrected, and it names the
environment that does work.

The insertion point is the module-level `_flash_attn_bwd*` functions, which is
where the kernel is built. The autograd Function's `backward` is not on the path
SDPA takes — patching it looks right, changes nothing, and the call still hangs.

Usage: fa4_guard_backward_wrong_dsl.py <path to flash_attn/cute/interface.py>
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

MARKER = "_pvpn_check_dsl_matches_release"

GUARD = '''

def _pvpn_check_dsl_matches_release() -> None:
    """Refuse backward when the DSL is not the one flash-attn-4 pins.

    Against a mismatched DSL the backward kernel does not return at all, so
    without this the failure looks like a hung job rather than a bad install.
    """
    from importlib.metadata import requires, version

    try:
        installed = version("nvidia-cutlass-dsl")
        wanted = next(
            req.split("==", 1)[1].split(";", 1)[0].strip()
            for req in (requires("flash-attn-4") or [])
            if req.startswith("nvidia-cutlass-dsl==")
        )
    except Exception:  # metadata unavailable: say nothing rather than guess
        return
    if installed == wanted:
        return
    raise RuntimeError(
        f"FA4 backward needs nvidia-cutlass-dsl {wanted}, but {installed} is "
        f"installed. Against a mismatched build the backward kernel never "
        f"returns -- it hangs rather than failing. Use the environment built "
        f"for it: /scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl/venv/bin/python. "
        f"Forward is unaffected and still works here."
    )
'''


def body_start_line(fn: ast.FunctionDef) -> int:
    """1-based line to insert before: the first real statement of the body."""
    first = fn.body[0]
    # A docstring has to stay the first statement, so step past it.
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        if len(fn.body) == 1:
            return first.end_lineno + 1
        return fn.body[1].lineno
    return first.lineno


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    backup = target.with_suffix(".py.pre_dsl_guard")

    # Re-applying has to start from the unpatched file, or a failed earlier
    # attempt gets layered under the new one.
    if MARKER in target.read_text():
        if not backup.exists():
            sys.exit("guard present but no backup to restore from")
        shutil.copy2(backup, target)
        print("restored the unpatched file before re-applying")

    text = target.read_text()
    tree = ast.parse(text)
    targets = [n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("_flash_attn_bwd")]
    if not targets:
        sys.exit("no _flash_attn_bwd* functions found")

    lines = text.splitlines(keepends=True)
    # Insert from the bottom up so earlier line numbers stay valid.
    for fn in sorted(targets, key=lambda n: n.lineno, reverse=True):
        at = body_start_line(fn) - 1
        indent = " " * (len(lines[at]) - len(lines[at].lstrip()))
        lines.insert(at, f"{indent}{MARKER}()\n")

    new = "".join(lines) + GUARD
    ast.parse(new)
    if not backup.exists():
        shutil.copy2(target, backup)
    target.write_text(new)
    print(f"guard added to {len(targets)} entry point(s): "
          + ", ".join(n.name for n in targets))


if __name__ == "__main__":
    main()
