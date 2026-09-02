#!/usr/bin/env python3
"""Let the varlen trainer attention use FA4 on Blackwell, behind a switch.

The backend choice here is per architecture: Hopper activates FA3, and Blackwell
currently falls through to whatever PyTorch picks by default, because FA3 is
Hopper-only. FA4 is the Blackwell path, and its backward is what the last
several days went into getting to run.

Gated on SWE_ATTN_FA4 rather than made unconditional. This is a shared
checkout, the FA4 backward has one accuracy configuration still outside the
acceptance bar, and a silent backend change to someone else's training runs is
the kind of thing that surfaces as an unexplained loss curve weeks later.

Usage: rl_enable_fa4_varlen.py <path to torchtitan/models/common/attention.py>
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sys

OLD = """        if has_cuda_capability(9, 0) and not has_cuda_capability(10, 0):
            if current_flash_attention_impl() != "FA3":
                activate_flash_attention_impl("FA3")
"""

NEW = '''        if has_cuda_capability(9, 0) and not has_cuda_capability(10, 0):
            if current_flash_attention_impl() != "FA3":
                activate_flash_attention_impl("FA3")
        # LOCAL (terminal-rl): Blackwell's flash path is FA4. Opt-in, because
        # this checkout is shared and the choice of attention backend is not
        # something another run should inherit without asking for it.
        elif has_cuda_capability(10, 0) and os.environ.get("SWE_ATTN_FA4") == "1":
            if current_flash_attention_impl() != "FA4":
                activate_flash_attention_impl("FA4")
'''


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    text = target.read_text()
    if "SWE_ATTN_FA4" in text:
        print("already wired")
        return
    if OLD not in text:
        sys.exit("the architecture gate is not in its expected form")
    text = text.replace(OLD, NEW, 1)

    if not any(l.strip() == "import os" for l in text.splitlines()):
        lines = text.splitlines(keepends=True)
        first_import = min(i for i, l in enumerate(lines)
                           if l.startswith(("import ", "from ")))
        lines.insert(first_import, "import os\n")
        text = "".join(lines)

    ast.parse(text)
    shutil.copy2(target, target.with_suffix(".py.pre_fa4"))
    target.write_text(text)
    print("FA4 wired behind SWE_ATTN_FA4=1")


if __name__ == "__main__":
    main()
