# Failure skills from run 1022's step-200 checkpoint: what to drill when rewriting tasks

For the task rewriter (teacher). Source: the 445 TB-2.1 trials of the run-1022 policy at step 200,
evaluated alone (avg@5 .353, 157 passes; pass@5 .596). This is the strongest policy measured so
far. 288 trials failed. **This study covers only the 258 that failed with the work finished: the
agent submitted, or its terminal ended.** The 29 trials that ran out of context, time or turns and
the one harness error are excluded, so every failure below is a wrong answer the policy chose to
stand behind.

Every failed trial was read and labelled against its task's passing trials where they exist (37 of
the 73 tasks with failures had 1-4 passes). It is a companion to `FAILURE_SKILLS_1026B.md`.

**Do not reproduce the benchmark tasks.** The task names in this document are evidence only.
A drill should exercise the same behavior in a different domain, with different files and
different tests; a task that resembles a TB-2.1 task trains on the test set.

## Where the failures are

| Primary cause | Trials | Share | Tasks | Near-miss trials |
|---|---|---|---|---|
| Wrong core algorithm or missing domain knowledge | 105 | 41% | 38 | 33 |
| Submitted while its own check was failing, or never checked | 40 | 16% | 15 | 7 |
| Verified through the wrong entry path or environment | 24 | 9% | 10 | 13 |
| Broke its own environment or terminal | 22 | 9% | 16 | 10 |
| Own checks passed; the verifier tested hidden or edge cases | 20 | 8% | 8 | 11 |
| Broke a constraint or the output contract the instruction states | 20 | 8% | 9 | 11 |
| Guessed or fabricated instead of decoding or measuring | 19 | 7% | 7 | 8 |
| Missed a performance target | 7 | 3% | 4 | 6 |
| Undetermined | 1 | 0% | 1 | 1 |

- **The headline is submitting on a red signal.** Counting it as a secondary cause too, 118 of
  258 failures (46%) were submitted while the agent's own screen showed the work failing: its check
  printed `False`, its metric was under the bar, its build had errored, or it had written "this is
  a guess" / "task cannot be completed" one turn earlier.
- **Limits are not what holds this policy back.** Only 29 of its 288 failures hit a context, time
  or turn limit (10%); for run 1026b at its best eval it was 136 of 303 (45%). The 1022 policy is
  concise; its failures are judgment and knowledge.
- **The agent damages its own grading.** 14 trials ended because the agent typed `exit`, `exit 1`
  or Ctrl-D into its only shell. Six more left the machine unable to run the verifier: a held or
  interrupted apt/dpkg, or a disk filled by `pip install torch`. All are agent actions, not
  infrastructure.

## Skills, in priority order

Every skill follows the teacher principle: the task leaves each decision to the student. The
drill makes the right habit necessary to pass; it does not tell the student the habit.

### 1. Do not submit while your own check is failing

**Failure (40 primary, 118 counting secondary, 42 tasks).**
- A distribution search printed `Tol 0.001 met? Fwd: False, Bwd: False`, then submitted.
- An OCR invoice task wrote six totals of 0.0, noted "many total_amounts are 0", and submitted.
- A hash-reconstruction task checked its digest against the prefix the instruction gives, saw
  `Starts with ...? False`, and submitted.
- A LaTeX task's own `pdflatex | grep -c Overfull` still showed 2-4 warnings in every trial.
- An interpreter task submitted with its one provided example failing, in all five trials; one
  trial submitted four times.
- Agents wrote "I'm going to provide a guess", `echo "Task seems incomplete"`, or
  `echo "about to fail"`, then submitted.

**Drill.** Tasks that ship a cheap self-check: a provided validator, a hint value, a known
input/output pair, an invariant such as totals that must add up. The first plausible attempt
fails it, and a reachable second approach passes. Score submitting with the check red the same as
any wrong answer. The habit: re-run every check the task gives right before submitting, and treat
a red result as "change the approach", not "finish".

### 2. Verify through the grader's entry path, from a clean environment

**Failure (24 primary; near-miss heavy, 13 of 24).**
- Four trials of one task pip-installed `cryptography` in their own shell and built the
  deliverable on it. The grader's fresh Python has no such package. The passing trial used only
  the standard library plus the `openssl` CLI.
- An HTML filter used `BeautifulSoup(..., 'lxml')` after `pip install lxml`; the clean grader
  crashed with `FeatureNotFound`.
- A cryptanalysis "attack" read the key from the Python module's globals. The grader passes a C
  function, so it returned nothing. A model-extraction task imported the hidden weight matrix
  instead of querying; the grader swaps that file.
- A renderer shelled out to a binary that exists in `/app` but not in the grader's empty chroot.
- A tool was put on PATH only through shell startup files; the grader's subprocess could not find
  it. A coverage build ran in the wrong directory, so the coverage files were not where the test
  looks.
- An ssh service was "verified" by typing `ssh` at the guest VM's login prompt, never from the host.
- Test pushes left state in a git server, so the grader's own pushes were rejected.

**Drill.** The deliverable is used by a different process than the student's shell: a clean
interpreter, a fresh subprocess with no shell rc, a chroot containing only the artifact, a swapped
black-box oracle, a real network client on the other side of a boundary. The instruction says how
it will be used; the tests use it exactly that way. Also have the grader start from the state the
student leaves, so leftover test data breaks it.

### 3. Test every requirement on hidden-style inputs, not the visible sample

**Failure (20 primary, plus sample-fitting labelled elsewhere).**
- A regex move generator was replaced by a lookup table of the 18 positions in the provided check
  script. The instruction says other positions are tested; the first unseen one failed.
- A resharding task enforced "at most 30 items per directory" on leaf directories but left 330 at
  the root. Every trial printed that 330 and missed it.
- A terminal-emulator wrapper was tested with `echo hello` only; the hidden test runs a background
  server. Five of five failed on it.
- A log counter matched the bare word `ERROR` instead of the `[ERROR]` severity tag, and was
  "verified" with an equally wrong `grep`.
- A secret scrubber missed a token on a line thousands of characters long, because the terminal
  showed only the line's tail. The passing trial used `grep -o` to print each match on its own line.

**Drill.** One worked example and a visible checker that covers it; hidden tests generated from
the same distribution. Requirements of the form "every X satisfies Y", where the obvious
construction breaks Y at one non-obvious spot (the root, the longest line, the boundary value).
Make a lookup table infeasible. The habit: build an independent checker (a reference library, a
fuzzer, an `os.walk` over everything) and run it on fresh inputs.

### 4. Decode and measure; never guess, and never copy the example

**Failure (19 primary, 21 more as secondary).**
- An ELF-extraction task added the `0x400000` base used in the instruction's example output to a
  position-independent binary that starts at 0, so no key matched. One trial had a correct answer
  and then changed it to match the example.
- A G-code task answered with the printer-model string from the file header instead of rendering
  the toolpath of the text object (five of five).
- A truncated-SQLite task read the byte after each key as its value, which happens to work for
  small integers and is wrong for doubles. Two trials invented values equal to the row index.
- A spectrum fit ran on an axis whose range does not contain the peak the task asks for. No trial
  checked that the expected quantity was even inside the data.
- A placeholder all-zero key, `FLAG{42}`, or a model name the agent said was a guess was written
  as the final answer.

**Drill.** Artifacts in a documented binary or encoded format whose true content is random per
task, with a decoy (a metadata string, an example value that differs from the real input in one
parameter, a pattern that fits the first few records). Tests compare exact recovered values. The
instruction says the format exists, not how to parse it.

### 5. Keep the machine gradeable: never kill your shell, never leave the package manager broken

**Failure (22 primary; 14 terminal exits; 6 verifier bootstrap failures).**
- `exit`, `exit 1`, `exit 0` or Ctrl-D sent to the only shell, usually after a hang, a dead mirror
  or "giving up". The episode ends with no deliverable.
- `apt-get install python3 | tail` hid the tzdata prompt, looked frozen, and was killed with
  Ctrl-C or `kill %1`. That left "dpkg was interrupted" or a held lock, and the verifier then
  could not install its own tools.
- `pip install torch` filled the disk; the verifier's install failed with "No space left on device".
- A forensic task opened the database with plain `sqlite3` at turn 2, which discarded the WAL it
  had to recover, in four of four trials.
- An orphaned VM process held the disk-image lock through dozens of relaunches. An out-of-memory
  `Killed` was diagnosed as a library bug.

**Drill.** Tasks that need an apt install with a debconf prompt, an interactive REPL or editor, a
VM, or a service restarted several times, and an episode that continues afterwards. Forensic tasks
where the first read destroys evidence unless it is copied first. Long jobs that are OOM-killed
unless memory use is reduced. The habits: `DEBIAN_FRONTEND=noninteractive`, `timeout`, heredoc
input, `dpkg --configure -a` after an interruption, copy evidence before touching it, kill your own
background processes, read `dmesg` after `Killed`, and never exit the shell.

### 6. Match the output contract and the stated constraints exactly

**Failure (20 primary).**
- A video-transcript task collected commands in a `set()` and printed them sorted, discarding
  order and duplicates.
- A text-editing task used global `sed` replacements without word boundaries, which changed words
  that had to stay identical. Another overwrote the protected input file with the expected output.
- A vulnerability report named the generic CWE when the prompt's own list had the specific one.
- An answer of 11 characters was submitted for a password the instruction says is 23 characters
  and ends in a given suffix.

**Drill.** As in the 1026b document, skill 3: a non-default or counter-intuitive format, protected
regions, stated length or format constraints, and strict tests. Tempt the natural default.

### 7. Learn the specific domain rule the instruction points at

**Failure (105 primary, the largest class; 33 near-miss trials).** These are not random gaps. In
the near-miss tasks a passing trial shows exactly the missing rule:
- Primer design ignored the restriction enzyme's clamp requirement that the instruction explicitly
  referenced (four of five trials), and the primers were placed with the wrong geometry.
- An embedding retrieval omitted the query/passage prompt type; the passing trials set it.
- A packed Q/K/V projection of shape 384x128 was read as "3 heads". The agent then spent 150 turns
  patching torch to get past the resulting assertion instead of questioning that reading.
- A MuJoCo speed-up task never tried the integrator and iteration settings the passing trials used.
- A NumPy-2 migration fixed most `np.int` uses but missed the one path the test runs.
- An asyncio task reproduced the exact cancellation gotcha the instruction warns about.
- Builds failed for want of `-msoft-float`, the distro Coq package, or the right Vim keys.

**Drill.** Tasks whose instruction names an external standard, vendor requirement or library
convention and ships its documentation in the image. Tests check the one rule that a generic
solution violates. A second family: an early, plausible inference (a shape, a unit, a version) is
wrong, and the wrong reading produces a persistent, tempting-to-patch error deep in a library.
The habit: when a library assertion blocks you, re-check your own assumption before patching the
library.

### 8. Meet performance thresholds with margin, and measure what the grader measures

**Failure (7 primary, plus near misses elsewhere).** Submitted at 1.4x against a 1.2x bar (the
grader measured 1.18x). An eigenvalue speed-up tied the reference and the agent submitted anyway.
A query rewrite was checked only for identical output and ran 0.7-0.8x as fast as the reference;
the passing trials filtered before the window function. A classifier reached 0.60 against 0.62,
and the final saved model was not even the tuned one.

**Drill.** As in the 1026b document, skill 8. Add tasks where the naive rewrite is correct but
slower, and one cheap structural change (a filter pushed down, a library routine) clears the bar.

### 9. Build large artifacts incrementally, on a small sample first

**Failure.** Nobody took `head -N` of a 1M-row file before iterating on a Vim macro; every attempt
ran on the full file and hung. Interpreters and emulators were rewritten whole through heredocs
again and again and broke on syntax. A segmentation script was submitted after only `py_compile`.

**Drill.** Tasks with a large input and a long full run, where a correct pipeline on a sample is
quick to check. Large programs with stage-wise tests (parse, then evaluate, then I/O). The habit:
sample, unit-check each stage, and run the real command end to end once before submitting.

## Compared with run 1026b

- **Shared:** checking through the wrong entry path, contract slips, guessing instead of decoding,
  and thin performance margins.
- **Larger here:** submitting on a red signal (skill 1) and self-inflicted machine damage
  (skill 5). The 1022 policy finishes its tasks, so its failures show up at submit time.
- **Smaller here:** context, turn and time exhaustion. In 1026b they were 45% of failures.

## Do not drill now

- **Likely beyond the 9B model for now:** a GPT-2 inference program in under 5 KB, a full MIPS
  interpreter in JS, an arithmetic gate netlist, regex-only chess move generation, linear
  cryptanalysis, a polyglot file valid in two languages.
- **Undetermined:** one trial wrote the expected answer and scored 0 with an empty verifier
  output, which is probably a grader miss.
