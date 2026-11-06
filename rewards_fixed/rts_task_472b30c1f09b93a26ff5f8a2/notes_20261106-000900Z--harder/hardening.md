# Hardening rationale

## Observed successful strategy

All 12 attempts earned reward, and all converged on the same shortcut: inspect printable strings or alphanumeric runs in `Crackme-Monologix-Linux`, notice the literal `6571965`, execute the binary once with that candidate, then write `solution.txt` and remove the two starting files. Examples are `traces/attempt-02.jsonl` turns 5-11 and `traces/attempt-09.jsonl` turns 4-9. Attempt 12 tried GDB first but ultimately extracted printable sequences with Perl at turn 23 and entered the exposed number at turn 25. The task currently spares the student any need to handle a packed executable; its password is a plaintext string in the on-disk ELF.

No successful attempt violated the public requirements. The seed verifier does impose one hidden requirement by asserting that GDB is installed even though the instruction permits any analysis method. That check will be removed from the scratch verifier and reported in `run/repair.json`; GDB remains installed and available to students.

## Primary-source research

- https://raw.githubusercontent.com/upx/upx/v4.2.4/doc/upx-doc.html — UPX documents that it supports Linux executables, maintains checksums of compressed and uncompressed data, and that supported formats can be unpacked with the `-d` switch.
- https://api.github.com/repos/upx/upx/releases/tags/v4.2.4 — the upstream release record identifies tag `v4.2.4`, published 2024-05-09, and its `upx-4.2.4-amd64_linux.tar.xz` release asset.
- https://api.github.com/repos/Monologix/Monologix-Crackmes/commits/master — the upstream crackme repository currently resolves `master` to commit `07f50bf1dbb6495136fd9f13e83762745d7953a3`, which will pin the existing task input rather than continuing to fetch a moving branch.

Pinned artifacts:

- UPX 4.2.4 Linux amd64 release archive: `https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-amd64_linux.tar.xz`, SHA-256 `75cab4e57ab72fb4585ee45ff36388d280c7afd72aa03e8d4b9c3cbddb474193`.
- Original crackme at commit `07f50bf1dbb6495136fd9f13e83762745d7953a3`, SHA-256 `6dd7f2b64a18d015ed60f45d77d7c9c62acb00d1aa632da0eb23448566880fd8`.

Before packing, the build changes the original seven-byte password literal to another seven-byte value. This preserves the crackme's behavior while ensuring that memorizing the answer exposed in every trace is not a legal shortcut around the new executable analysis.

## Reproduction in this environment

On a fresh seed sandbox I downloaded and checksum-verified the UPX 4.2.4 release, installed only the build prerequisite `xz-utils`, and ran:

```
/tmp/upx-4.2.4-amd64_linux/upx --lzma /data/Crackme-Monologix-Linux
strings /data/Crackme-Monologix-Linux | grep 6571965
gdb -batch -ex 'set breakpoint pending on' -ex 'break strcmp' -ex 'run < /tmp/dummy_input' -ex 'info registers rsi' -ex 'x/s $rsi' /data/Crackme-Monologix-Linux
printf '6571965\n' | /data/Crackme-Monologix-Linux
```

UPX reported `16816 -> 8960`, format `linux/amd64`, and `Packed 1 file`. The packed image no longer exposed the password through printable inspection. The seed GDB strategy did not hit `strcmp`; the process printed the incorrect-password message and exited, followed by `The program has no registers now.` The packed executable nevertheless accepted the correct password and printed `Bravo, tu as reussi !`. Its embedded printable metadata identifies it as packed with UPX 4.24, giving investigation a concrete upstream name without dictating whether to unpack it or debug its decompressed memory image.

## Proposed change and new decision

The Docker build will checksum-pin both upstream artifacts, replace the original same-width password literal before compression, compress the crackme with UPX 4.2.4 `--lzma`, and remove UPX and its archive from the final image. The public goal and wording remain unchanged. The reference solution will recognize and unpack the executable with the matching pinned upstream tool before applying its existing GDB analysis.

The student must now diagnose that ordinary static inspection is seeing the packer stub and choose a packer-aware route (obtain and use the matching unpacker, or debug/dump the decompressed process) before recovering and verifying the password. This is one modest, real reverse-engineering step beyond the attempts' demonstrated use of strings, ELF tools, GDB, and execution tests. The program's UPX marker is evidence to investigate, while neither its runtime messages nor a failed breakpoint command announces the unpack command.

## Required shortcut trials and replay

I performed each trial on a fresh revised sandbox and graded the resulting state.

1. **Follow the tool/runtime output.** A pending `strcmp` breakpoint followed by `run` produced the crackme's ordinary incorrect-password output, then GDB's `The program has no registers now` / `No registers`. There is no prescribed repair in either message. I followed the only actionable program request by trying a plausible password and then the memorized old password, completed the requested cleanup, and graded. Reward was 0. The password-content checks `test_check_02_intermediate_artifact` and `test_check_04_no_shortcut` failed; cleanup and final-state checks passed.
2. **First-failure response used in the attempts.** On a fresh reset I reinstalled `binutils` and `gdb`, then reran `strings` and the attempts' alphanumeric extraction. The output still contained only packer-stub tokens (`UPX`, `packed`, `packer`, `PROT_EXEC`, and random runs), not the password. Completing with the old traced password and cleanup received reward 0, failing the same two password-content checks while passing cleanup/final-state checks. Reinstalling or regenerating the failed analysis therefore does not announce or perform the needed packer-aware decision.

### Successful-attempt replays

- **Attempt 02 path**, from a fresh reset: listed `/data`, read `leftover.log`, ran the binary, used `strings`, `objdump`, and alphanumeric extraction, tried the old exposed password, wrote it, and performed the same cleanup. It now stops at the static-analysis stage: output identifies UPX but does not expose the new password, and the old candidate prints the incorrect-password message. Grade reward was 0. `test_check_02_intermediate_artifact` and `test_check_04_no_shortcut` failed; evidence and final cleanup semantics passed.
- **Attempt 09 path**, from another fresh reset: searched strings for pass/key/success terms, disassembled, extracted long alphanumeric and numeric runs, tried the old exposed password, wrote it, and performed the same cleanup. It now stops when extraction yields only UPX metadata and the old candidate is rejected. Grade reward was 0 with the same two password-content failures; evidence and final cleanup semantics passed.

Both paths are directly touched: their common plaintext-extraction shortcut no longer succeeds, while the binary, UPX marker, debugger, network, and normal inspection tools leave a capable student a bounded unpack-and-retry route. Given that none of 12 attempts recognized or handled packing, but several used GDB and all validated candidates interactively, I expect **6 of 12** in the next group: roughly half should make the extra UPX diagnosis and fetch/unpack decision within the generous episode budget, while the replayed fast paths fail.
