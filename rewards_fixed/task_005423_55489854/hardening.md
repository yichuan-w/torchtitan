# Hardening rationale

## Attempt evidence and repair
All 12 successful traces inspect the small C/Python project, add `utils.c` to the extension, add the requested assertion, rebuild, and generate `report.json`. The commonly attempted numerical fix is a clamp: attempts 01, 08, and 09 finish with a reported standard deviation of `0.0`; other attempts eventually replace the implementation with Welford's algorithm after observing that zero is inaccurate. The seed verifier checks only that the value is numeric, non-NaN, and nonnegative, so those inaccurate clamp-only runs earned reward despite the instruction requiring correct floating-point values. The scratch verifier will be repaired to compare both statistics with independently computed population statistics for the original supplied data.

## Upstream evidence
- https://raw.githubusercontent.com/pypa/setuptools/v59.6.0/setuptools/_distutils/command/build_ext.py — `build_extension` forms `depends = sources + ext.depends` and returns without rebuilding when `newer_group(depends, ext_path, 'newer')` is false; the command's `--force` option is documented in the same source as forcing a build while ignoring timestamps.
- https://files.pythonhosted.org/packages/source/s/setuptools/setuptools-59.6.0.tar.gz — fetched source artifact has SHA-256 `22c7348c6d2976a52632c67f7ab0cdf40147db7789f9aed18734643fe9cf3373`; the environment will explicitly pin `setuptools==59.6.0`.

## Reproduction
In the current sandbox I first repaired `setup.py` and built once, then restored the broken `setup.py`, gave the native outputs/object files future timestamps, and ran `python3 setup.py build_ext --inplace` again. It printed only `running build_ext` plus a copy of the already-built library, did not compile either C file, and the aggregator still produced `{"mean": 999999999.9999965, "stddev": NaN}`. This reproduces the documented timestamp-based skip behavior with the pinned implementation in this task's Python 3.10 environment.

## Change and decision
The revised image ships the same broken source tree and goal but also contains stale native build outputs whose timestamps make the ordinary rebuild path reuse bad code. The student must distinguish source correctness from the loaded native artifact and choose a clean or forced rebuild after checking the generated report. This is real extension-build debugging, and it is one modest step beyond the successful attempts' existing inspect/edit/build/run loop. It is not announced by an error: the build succeeds and only validation reveals that the result is still wrong.

## Shortcut trials and replay
- **Error-message / obvious local fix:** I added the missing source, changed `utils.c` to clamp negative variance, added the assertion, then ran the instruction's ordinary `build_ext --inplace`. The command reused the stale library, the assertion stopped execution, and `./sandbox grade` scored **0.0** because `report.json` was absent.
- **First recovery seen in attempts:** From a fresh reset I upgraded/reinstalled setuptools, then ran the ordinary build and aggregator. The newer setuptools used a different build directory and did compile, but the unchanged unstable implementation still tripped the assertion; `./sandbox grade` scored **0.0** because `report.json` was absent. Thus neither the easy clamp nor generic dependency upgrade satisfies the accurate-statistics contract.

## Successful-trace replays
- **attempt-01:** Replayed its joined keystrokes from a fresh revised-image reset. It edits only the helper to clamp negative variance and invokes the ordinary rebuild. The stale extension remains loaded, the new assertion aborts before output, and grading fails `test_report_exists_and_valid` because `report.json` is absent (the remaining checks are not reached under fail-fast grading).
- **attempt-12:** Replayed its joined keystrokes from another fresh reset. Although it writes Welford's algorithm, its ordinary rebuild also reuses the stale extension; the assertion aborts, and grading again fails `test_report_exists_and_valid` because `report.json` is absent (remaining checks not reached).

Both previously successful paths now stop at the build/run boundary, so the intervention touches the measured strategy. Given that several traces already clean build artifacts while others do not, and only the clean-build group is likely to combine that recovery with an accurate algorithm, I expect **6 of 12** in the next same-size group.

## Verifier controls
Executed a concrete incorrect deliverable with the old rewarded `stddev: 0.0`; `test_report_values` rejected it as inaccurate. Executed a legal alternative Python implementation using `math.fsum` and `statistics.pstdev`; all three checks passed, confirming that the scratch verifier does not require the reference implementation's C algorithm.
