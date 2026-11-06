# Hardening rationale

## Observed successful strategy

All 12 attempts clone the repository, run `make TOOLPREFIX=i586-elf-`, and encounter the native `mkfs2` failure after the cross-compiled kernel and boot block have already been produced. They then edit `kernel/include/types.h` and/or `mkfs2.c`, often struggle with the host utility, but ultimately rely on the two early binaries and create `/app/lain/text`. For example, attempt 01 suppresses the `readdir_r` warning and accepts the later failed `make` (turns 18–24); attempt 06 rewrites the loop but only checks `kernel/kernel` and `kernel/bootblock` (turns 20–26). The seed verifier rewards this partial build. No rewarded attempt produces or checks `fs.img` or `xv6.img`.

## Primary sources and reproduced behavior

- https://sourceware.org/git/?p=glibc.git;a=blob_plain;f=NEWS;hb=glibc-2.24 — glibc 2.24 states: “The readdir_r and readdir64_r functions have been deprecated. It is recommended to use readdir and readdir64 instead.”
- https://sourceware.org/git/?p=glibc.git;a=blob_plain;f=manual/filesys.texi;hb=glibc-2.24 — the glibc manual says `readdir_r` is deprecated, recommends `readdir`, and documents portability problems including entries that can be truncated or not NUL-terminated.
- https://raw.githubusercontent.com/lain-dono/lainos/2019519a15e7e6b9aadabeade89385cf181fa13e/mkfs2.c — the pinned LainOS source calls `readdir_r(cur_dir, &dir_buf, &entry)` in `add_dir`.
- https://api.github.com/repos/lain-dono/lainos/commits?per_page=5 — the repository API reports `2019519a15e7e6b9aadabeade89385cf181fa13e` as the tip commit used by the default `develop` branch.
- https://api.github.com/repos/mit-pdos/xv6-public/commits/f043ac66be4b7523b4c99d2becf288368f1d3a73 — upstream xv6 commit `f043ac6` is titled “Remove BYTE directives from kernel linker script” and removes the two `BYTE(0)` directives from the `.stab` and `.stabstr` output sections.

Pinned artifacts are the LainOS source commit `2019519a15e7e6b9aadabeade89385cf181fa13e`, binutils 2.36.1, and GCC 10.3.0. The latter two were already pinned in the image recipe; the rewrite pins the source revision there as well.

On the seed image, I ran:

```
rm -rf /root/lainos
git clone -q https://github.com/lain-dono/lainos.git /root/lainos
cd /root/lainos
git rev-parse HEAD
make TOOLPREFIX=i586-elf-
```

The revision was `2019519a15e7e6b9aadabeade89385cf181fa13e`. Host GCC 11.4.0 against glibc 2.35 rejected conflicting `int8_t`, `int64_t`, and `intptr_t` declarations and then reported `readdir_r` as deprecated under `-Werror`; `make` exited 2. `kernel/kernel` and `kernel/bootblock` existed, but `fs.img` and `xv6.img` did not.

## Proposed change and new decision

Keep the original build goal and wording, but make “build the operating system” observable as a completed top-level build: require both filesystem and bootable disk images, not merely intermediate kernel binaries. The student must correctly separate host-only types from kernel types and replace the deprecated directory iteration while preserving end-of-directory versus error handling, then rerun the build. This is real C/build-system repair that the successful attempts approached but the old goal spared them from completing. It is one modest step beyond their demonstrated source inspection, compiler-error diagnosis, and local patching.

The planted diagnostics identify conflicting typedefs and deprecated `readdir_r`, but do not dictate a safe integration. Warning suppression alone leaves the hard type errors; replacing `readdir_r` naively can leave an unused buffer under `-Werror`, mishandle `errno`, or corrupt the recursive directory-image walk. More importantly, an apparently successful image still triple-faulted under QEMU because binutils 2.36.1 assigned the data `PT_LOAD` physical address as `0x80108c5b`; QEMU's `-d guest_errors,cpu_reset` output showed rejected writes followed by `Triple fault`. Applying the upstream xv6 linker-script change made the data segment load at physical `0x00109000`; booting with `xv6.img` as disk 0 and `fs.img` as disk 1 then printed `xv6...`, `cpu0: starting`, `init: starting sh`, and the `$` prompt.

## Shortcut trials and replay results

I applied the build output's first apparent suggestion, suppressing the deprecated-declaration error with `-Wno-error=deprecated-declarations`; the build still stopped at the conflicting host/kernel typedefs and grade was 0. I also replayed the common attempt strategy of introducing host-safe typedefs and replacing `readdir_r`, then rerunning `make`; this produced both images, but QEMU stopped after “Booting from Hard Disk” and grade was 0 because the disk image did not reach the shell. Thus neither the suggested warning suppression nor the attempts' first repair passes.

From fresh resets I replayed two successful traces. Attempt 01's path (host-safe `types2.h`, suppress the deprecation warning, rerun `make`, create the text file) now produces both images but stops at the QEMU boot check; the sole failed check is that `xv6.img` does not boot to the LainOS shell. Attempt 11's path (drop the kernel type include, replace `readdir_r` naively, rerun `make`, create the text file) stops in `mkfs2` with `add_dir: Success`; it fails the completed-image/boot check because `xv6.img` is absent. The repository and `/app/lain/text` checks pass in both replays.

Expected next result: **6 of 12**. Every prior attempt must now finish its host-utility repair and investigate a silent boot failure; none inspected the linker program headers or ran QEMU, though several showed enough C and build debugging ability to reach the new decision.

## Verifier controls

Executed negative control: after moving the oracle repository aside, I replaced the candidate `xv6.img` with the text `not a boot image`; grade was 0 at the boot-image check. Executed legal alternative: a repository at another searched location containing the same source and artifacts, with equivalent binaries copied rather than produced by the reference's exact edits, graded 1. This demonstrates the scratch checker rejects a concrete incorrect result while allowing an alternative legal implementation/path.
