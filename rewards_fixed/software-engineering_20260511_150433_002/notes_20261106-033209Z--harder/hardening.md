# Hardening rationale

## Attempt evidence

All 12 attempts earned reward 1. Their common strategy was to inspect the legacy source, add feature exposure or `<math.h>` for `M_PI`, add `-fPIC`, run `make shared && make install-shared`, call `ldconfig`, and compile and run `/app/test_numutils`. For example, `traces/attempt-01.jsonl` turns 10-17 and `traces/attempt-07.jsonl` turns 8-21 do exactly that. They verify function output and file presence, but none inspects the shared object's dynamic section, its SONAME, or the consumer's NEEDED entry. The successful rewards are consistent with the public task and are not reward hacks.

## Primary sources and pinned artifact

- https://sourceware.org/binutils/docs/ld/Options.html — the GNU linker manual says that `-soname=name` sets an ELF shared object's internal `DT_SONAME`, and that an executable linked against it causes the dynamic linker to load the object named by that field instead of the linker input filename.
- https://www.debian.org/doc/debian-policy/ch-sharedlibs.html — Debian's shared-library policy says a library is identified by its SONAME; linking records that SONAME in the binary's NEEDED section; and the SONAME path is normally a symlink to the fully versioned file.
- https://raw.githubusercontent.com/rui314/mold/v2.34.1/docs/mold.md — mold 2.34.1 documents `-h`/`--soname` as setting `DT_SONAME` and says this option is used when creating a shared object.
- https://api.github.com/repos/rui314/mold/releases/tags/v2.34.1 — the upstream release metadata lists `mold-2.34.1-x86_64-linux.tar.gz` for tag `v2.34.1`.

The Dockerfile will pin that release archive by URL and SHA-256 `dafd02fe19c51818aa22c9d569393616b1a85a2220d6fb32ba3728d351074e59`, install its linker, and use it for the legacy build.

## Reproduced behavior

In the seed container I applied the attempts' ordinary `M_PI` and `-fPIC` fixes, then ran:

```
make clean && make shared
readelf -d libnumutils.so.1.2.3 | grep -E 'SONAME|NEEDED'
make install-shared && ldconfig
cd /app && gcc test_numutils.c -lnumutils -lm -o test_numutils
readelf -d test_numutils | grep NEEDED
```

The library output had NEEDED entries for `libm.so.6` and `libc.so.6` but no SONAME. The executable consequently recorded `libnumutils.so` rather than a stable ABI name. The test program still returned 0, so this is wrong ABI metadata without an error announcing the fix.

## New decision

The student must still port, build, install, and test the same shared library, but must now make it a conventional versioned ELF ABI installation: select an ABI SONAME distinct from the full release filename, arrange the runtime and development symlinks correctly, and verify the consumer records that ABI name. This is one modest step beyond the demonstrated compiler-porting and shared-install workflow and requires inspecting ELF metadata rather than merely reacting to compilation diagnostics.

The build's lack of SONAME does not suggest a fix. The first fixes from the traces—adding `_GNU_SOURCE`/`<math.h>`, adding `-fPIC`, rebuilding, reinstalling, and running `ldconfig`—will be replayed and graded after the revised image is built. The other plausible generic response, upgrading or replacing the pinned linker, will also be tried and graded. Results will be appended below.

## Obvious-fix trials

After rebuilding the revised image, I applied the common trace fix (`_GNU_SOURCE`/`<math.h>`, `-fPIC`, `make shared`, `make install-shared`, `ldconfig`) without adding a SONAME. The program built and all 28 function checks passed, but `readelf` showed `SONAME: absent`, the consumer NEEDED entry was `libnumutils.so`, and grading scored **0.0**: six retained tests passed and the versioned-ELF installation check failed.

I then tried the other generic recovery path: replace the pinned mold invocation with the system GNU linker, clean, rebuild, reinstall, run `ldconfig`, and relink the test program. Again the program passed while `SONAME: absent`; grading scored **0.0**, with the same single versioned-ELF failure. Thus neither the diagnostic-driven compiler repair nor upgrading/replacing/regenerating the build product announces or accidentally supplies the intended ABI metadata fix.

## Successful-attempt replays and prediction

From a fresh reset I replayed the substantive commands from `traces/attempt-01.jsonl`: diagnose the initial failure, expose `M_PI`, add `-fPIC`, build/install, run `ldconfig`, and compile/run the consumer. It again reached `Passed: 28, Failed: 0`, but grading was **0.0**. The retained existence, header, link, functionality, version, and unmodified-test checks all passed; only the versioned ELF installation check failed because the object had no ABI SONAME/runtime link.

From another fresh reset I replayed the same path used by `traces/attempt-07.jsonl`, including `_GNU_SOURCE`, `<math.h>`, `-fPIC`, `make shared`, installation, `ldconfig`, and the `/app` consumer build/run. It also reached `Passed: 28, Failed: 0` and graded **0.0**, with the same six checks passing and only the versioned ELF installation check failing.

Expected next result: **6 of 12**. The familiar compiler fixes and functional test still provide strong progress, but success now depends on recognizing and validating ELF SONAME/symlink conventions, a decision neither replay made.
