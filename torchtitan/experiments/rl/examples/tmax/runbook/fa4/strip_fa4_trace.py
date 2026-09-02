#!/usr/bin/env python3
"""Strip the debug tracing from the patched backward kernel, keep the fix.

Codex reached ALL PASS with `cute.printf` tracing still compiled in. Printing
from inside a kernel changes timing, and a deadlock that disappears once you add
tracing is the classic shape of a Heisenbug — so the pass only counts if it
survives the tracing being removed. This deletes each trace site (the
block/thread guard together with its printf, so no empty `if` body is left) and
leaves the structural change to the dO pipeline consumer untouched.
"""
from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

TARGET = Path("/scratch/gpfs/TRIDAO/al9080/titan-rl/lib/python3.12/"
              "site-packages/flash_attn/cute/flash_bwd_sm100.py")
BACKUP = Path("/scratch/gpfs/TRIDAO/al9080/fa4-fix/with-trace-flash_bwd_sm100.py")

GUARD = "cute.arch.block_idx()[0] == 0"
PRINT = 'cute.printf("TRACE'


def main() -> None:
    shutil.copy2(TARGET, BACKUP)
    lines = TARGET.read_text().splitlines(keepends=True)
    out, i, removed = [], 0, 0
    while i < len(lines):
        if GUARD in lines[i] and i + 1 < len(lines) and PRINT in lines[i + 1]:
            i += 2
            removed += 2
            continue
        if PRINT in lines[i]:
            i += 1
            removed += 1
            continue
        out.append(lines[i])
        i += 1
    text = "".join(out)
    try:
        ast.parse(text)
    except SyntaxError as e:
        sys.exit(f"stripping produced invalid Python at line {e.lineno}: {e.msg}")
    TARGET.write_text(text)
    print(f"removed {removed} trace lines; remaining TRACE mentions: "
          f"{text.count('TRACE')}")


if __name__ == "__main__":
    main()
