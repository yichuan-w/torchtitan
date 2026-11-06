# Hardening evidence

## Observed strategy

All 12 successful attempts disassembled the supplied PE, recognized the arithmetic in the key-generation block, and eventually wrote the known arithmetic result directly to `/app/result.txt`. None changed or even queried `asm.syntax`. Attempts 01 and 12 spent many turns trying to initialize ESIL memory and registers, but both ultimately computed the answer outside a successful emulation and hard-coded it. Attempt 02 explicitly reduced the block to `(90 + 492)^2` and wrote that result. Thus the current task spares the student from diagnosing wrong ESIL semantics, and the seed verifier rewards a hard-coded answer despite the requested ESIL workflow.

## Primary sources and pinned artifacts

- https://github.com/radareorg/radare2/issues/26872 — the upstream report says that on a Capstone v6 build, x86 ESIL under `asm.syntax=att` reverses nonsymmetric operands and writes a shift result to `cl` instead of memory; it also lists affected `imul` cases.
- https://github.com/radareorg/radare2/pull/27048 — the merged upstream fix says Capstone 6 supplies x86 detail in AT&T order, causing source and destination to be swapped, and that Intel syntax produces the correct destination. It fixes this by requesting Intel operand-detail order.
- radare2 commit `7f585b38706dd4d3540d2904e28e7fad25a9522b`, fetched from https://github.com/radareorg/radare2/archive/7f585b38706dd4d3540d2904e28e7fad25a9522b.tar.gz with SHA-256 `8be536d0287637dbe11cd110b3c7f6587e710e25c180f119a8dd671128e6e3fa`. This is the exact commit and Capstone-next pin named in the issue, before the fix.
- The supplied crackme is fetched from https://raw.githubusercontent.com/poliva/IOLI-crackmes/main/IOLI-crackme/bin-win32/crackme0x02.exe with SHA-256 `adc5e56538368cf7813741c802a128a247191276cf8cfff9ada0aa06d83a7bfa`.

## Reproduction

I built the pinned radare2 commit with its pinned Capstone-next dependency and configured `asm.syntax=att`. Running the seed ESIL block produced `0x00000000`; adding only `e asm.syntax=intel` produced `0x00052b24`. The upstream reproducer also emitted operand detail in register-then-memory order and ESIL ending in `cl,=`, matching issue 26872:

```
r2 -N -a x86 -b 64 -e asm.syntax=att -e scr.color=0 -qc 'wx d323; pi 1; aoj~{[0].opex.operands}; ao~^esil:' malloc://16
```

Observed output included `[{"size":1,..."value":"cl"},{"size":4,..."type":"mem"...}]` and `...,cl,<<,cl,=,...`.

## Proposed change

Pin that real pre-fix radare2/Capstone combination and make AT&T syntax the configured default. The student must notice that emulation produces the wrong state, inspect the generated ESIL or compare syntax modes, and select a mode whose operand semantics are correct. This is one modest diagnostic step beyond the same ESIL setup, stepping, disassembly, and arithmetic checking shown in the successful traces. The environment contains the problem but no patch, fixed release, or note naming the fix.

The instruction will also make the already-required ESIL work checkable by requiring the reusable radare2 command script that generates the result. This repairs the seed reward hack demonstrated by every successful attempt; it is not the difficulty mechanism.

## Plausible first fixes and replay results

1. **Upgrade/reinstall first:** on a fresh reset, `apt-get install --reinstall radare2` failed with `Package 'radare2' has no installation candidate`. Leaving only the attempts' direct `echo 338724 > /app/result.txt` then graded **0**: the result-value checks passed, while the reusable-script check failed because `/app/solve.r2` did not exist.
2. **Regenerate/replay the prior script:** on another fresh reset, I replayed the seed ESIL setup without changing the configured syntax. It printed `0`; grading was **0**. The result contained no key, and rerunning `/app/solve.r2` also printed `0`, so both content checks failed.

For a positive control, the same script with `e asm.syntax=intel` printed `338724` and graded **1**. This confirms that the new decision, rather than a changed binary or arithmetic result, separates pass from fail.

## Attempt replays

- Attempt 01's successful path now stops at its final direct write. It still knows `338724`, but because it never creates `/app/solve.r2`, grading fails the reproducible ESIL-script requirement.
- Attempt 02's successful path likewise writes an arithmetic explanation and the known key, but never leaves a runnable `/app/solve.r2`; grading fails the same requirement. If its shown ESIL setup is saved under the configured default, the reproduced output is `0`, additionally failing key content.

Both paths therefore encounter the changed core workflow. Each fresh replay starts only from the reset image, with no service or setup started by hand.

## Expected solve rate

6 of 12. The student already checks disassembly and experiments extensively with ESIL, so roughly half should inspect a wrong emulation result deeply enough to compare generated operand semantics or syntax modes. The two replayed successful paths both fail unchanged, while the one-line semantic correction is reachable from their existing investigation style.
