# Failure skills from run 1026b's best eval: what to drill when rewriting tasks

For the task rewriter (teacher). Source: the 445 TB-2.1 trials of run 1026b at its best eval,
policy step 75 (avg@5 .310, 138 passes; pass@5 .517), cross-checked against steps 50, 100, 125
and every other eval of the run. 303 trials failed for reasons that are the policy's (4 infra
failures excluded). Each skill below says what the policy does wrong, how often, and how to
build a training task that drills it.

**Do not reproduce the benchmark tasks.** The task names in this document are evidence only.
A drill should exercise the same behavior in a different domain, with different files and
different tests; a task that resembles a TB-2.1 task trains on the test set.

## Where the failures are

| Failure mode | Trials | Share | Tasks | In near-miss tasks (1-4/5) |
|---|---|---|---|---|
| Wrong core algorithm or missing domain knowledge | 98 | 32% | 28 | 20 |
| Ran out of context (220K) or the 300-turn cap | 86 | 28% | 32 | 20 |
| Ran out of the 5400 s time budget | 50 | 17% | 24 | 18 |
| Own checks passed; the verifier tested an edge or adversarial case | 33 | 11% | 12 | 18 |
| Broke a constraint the instruction states | 17 | 6% | 9 | 7 |
| Missed a performance target | 12 | 4% | 5 | 7 |
| Environment or service setup broke | 6 | 2% | 2 | 0 |

- **Near-miss tasks are the most trainable.** There the approach is right and the failure is in
  checking the result against the real contract: skills 1-3 and 8.
- **No "gave up" failures.** Short submissions are confident wrong answers, not abandonment.
- **The time-budget failures are partly infrastructure.** The eval generators' prefix cache
  thrashed in the second half of each pass, making every turn re-prefill the whole context.
  Re-judge the time-bound tasks after the eval runs on 32 generators at memory 0.9 before
  drilling them (see "Do not drill now").

## Skills, in priority order

Every skill follows the teacher principle: the task leaves each decision to the student. The
drill makes the right habit necessary to pass; it does not tell the student the habit.

### 1. Verify through the grader's entry path and environment

**Failure.** The policy checks its work in its own shell, with its own installs and its own
shortcut, and the grader runs a fresh process the other way.
- A check script imported a package the agent had just pip-installed in its own shell. The
  grader's Python does not have it; the script exited 1.
- A git deployment was tested by pushing to the local path. The grader clones over SSH, and
  that clone failed.
- A CLI was validated on the one sample image. The test compares predictions on 50 images.
- A tool was put on PATH with `export PATH=...`, which lives only in that shell. The grader's
  fresh shell found no binary. The passing trial symlinked it into `/usr/local/bin`.
- Distributed layers were "verified" at world size 1 only; the tests spawn several processes.

**Drill.** A task whose deliverable is consumed by a different process, user, transport or
interpreter than the student's shell: a service reached over its real protocol, a script run by a
clean interpreter, a command that must work from a new login shell, a component that must be
correct under N spawned processes. The instruction states how the deliverable will be used; the
tests use it exactly that way. A check done the convenient way passes; the real way fails.

### 2. Try to break the solution before submitting

**Failure (33 trials, 12 tasks).** The policy writes a checklist of the requirements, ticks each
one by reasoning, and submits. Sanitizers, filters, regexes, queries and primer designs pass its
own happy-path example and fail the verifier's edge and adversarial inputs.

**Drill.** One or two worked examples in the instruction; hidden tests full of edge cases:
encodings, nesting, empty and boundary inputs, dates at month and year edges, inputs crafted to
slip past a naive implementation. Ship the real checker in the image (the reference library, a
headless browser, the actual parser) so the student can test behavior instead of arguing about
it. The task should be unpassable without writing at least a few adversarial inputs.

### 3. Match the output contract exactly

**Failure (17 constraint violations, plus near-miss contract slips).**
- The spec said stdout is only the predicted digit; the tool printed
  `Prediction: 2 | Prediction written to ...`.
- The spec said save edges as `to,from`; the agent wrote parent-first rows under that header, and
  the test, reading columns by name, saw every edge reversed.
- Text the spec said must stay identical was edited; secrets in history were missed; a
  replacement token had the wrong format.

**Drill.** A spec with a non-default or counter-intuitive format (reversed column order, nothing
but the value on stdout, a fixed key order, a protected region, an exact placeholder token) and
tests that parse strictly by name or exact match. Tempt a "helpful" log line or a natural default
that breaks the contract. The habit to reward: print each deliverable raw and read it against the
spec's literal words before submitting.

### 4. Deliver early, then improve; keep long jobs bounded

**Failure (50 time-budget trials, 24 tasks; near-miss examples).**
- A model-recovery run kept starting long training in the foreground and killing it with C-c.
  It ran out of time without ever writing the required model file. The passing trial saved a
  model early and kept improving it.
- Compile and training tasks spend the budget on one long foreground command.

**Drill.** A task with a required artifact and a long-running process (training, a large build, a
big data job) where the full run does not fit in the budget but a reduced run, or a checkpoint
saved along the way, does. Tests check the artifact. The habit: write a valid deliverable early,
run long work in the background with `nohup`/`timeout` and poll it, try a small configuration
first, watch the remaining time.

### 5. Find the root cause before retrying; edit in place

**Failure (86 context and turn-cap trials, 32 tasks).**
- On a crash-debugging task the passing trial read the provided material once and made one fix in
  60 turns. A failing trial ran about 15 one-off debugger probes and rewrote the same source file
  about 15 times until its context ran out.
- In training, one rollout rewrote the same file in full 22 times in 74 turns; terminal echo of
  those rewrites took 74% of its terminal output.

**Drill.** Multi-stage debugging or porting tasks where blind rewrites cannot converge: the bug
needs a specific observation (a log line, a core dump, a minimal reproduction) and the code is
large enough that rewriting it whole costs the context. Tests per stage make progress visible. The
habit: form a hypothesis, test it with one observation, change the few lines involved (`sed`, a
patch), keep notes. The harness now collapses echoed heredoc lines; it does not stop full
rewrites.

### 6. Decode the evidence; never invent it

**Failure.** Missing database rows were reconstructed from a "plausible pattern" instead of decoding
the journal they were in. Values in a page-structured file were guessed from raw bytes instead of
parsing the record format.

**Drill.** A corrupted or encoded artifact in a documented binary format (a journal or
write-ahead log, a page-structured store, an object file, a custom container) whose true content is
generated at random for each task, so any guessed pattern fails. Put a tempting false pattern in the
visible data. Tests compare exact recovered values. The instruction says the format exists, not how
to parse it.

### 7. Check plausibility; change how you look at the data

**Failure.**
- A spectrum fit with a wrong axis scaling put a peak far outside its known band; the agent
  accepted it.
- An answer was read from a metadata string instead of rendering the toolpath that drew the text.
- Frames and board positions were picked by guess instead of reading the image with a tool.

**Drill.** Analysis tasks with a unit or scale trap in the input and tests that require values in
the physically correct range; the instruction names the quantity, not its expected value. Data whose
answer appears only after rendering or plotting (toolpaths, vector paths, image sequences), with a
decoy string in the metadata and the rendering tools installed.

### 8. Meet performance thresholds with margin

**Failure (12 trials, 5 tasks).** The policy submits when its own run is just above the bar (1.3-1.7x
against a required 1.2x; the grader measured 0.54x), or never measures.

**Drill.** Optimization tasks with a threshold that the tests check several times, under load, on the
same machine. The student has to benchmark the way the test does and leave margin.

### 9. Commit to the answer the evidence supports; make bulk edits precisely

**Failure.**
- A counting task had the correct number. At the "Are you sure?" prompt the agent recomputed
  three times with different methods and submitted a wrong one.
- A global `sed` replaced `n.float` with `np.float64` in a module that imports numpy as `n`; the
  same pattern would turn `np.int64` into `np.int6464`.

**Drill.** Tasks where a careful method gives one answer and quick alternatives give near misses, so
switching away from a verified result costs the reward. Refactoring tasks across many files where a
blind global substitution breaks code that the tests exercise.

### 10. Drive interactive programs from the terminal

**Failure.** Stuck inside an interactive admin shell or REPL, repeated C-d, the terminal exited.

**Drill.** Configure a service whose setup tool is interactive. Tests check the service state. Passing
needs non-interactive flags, here-strings or `expect`-style input, timeouts, and leaving the shell
usable.

### 11. Reason from the specification, not blind retries

**Failure.** Language-trick tasks (one source file valid in two languages) burned every turn on random
syntax variants.

**Drill.** Tasks that need one key insight about how two parsers or toolchains read the same input,
with the relevant specifications or man pages in the image and a tighter turn cap than usual.

## Do not drill now

- **Likely beyond the 9B model for now:** extreme code golf, chess move generation in regexes,
  porting a game to a new CPU emulator, logic-circuit arithmetic synthesis, linear cryptanalysis,
  installing a GUI operating system.
- **Re-check after the eval infrastructure fix** (32 eval generators, GPU memory 0.9) before
  blaming the model: tasks that fail by the 5400 s budget or the verifier's 900 s limit (protein
  assembly, fastText training, cell segmentation, embedding leaderboards, video move extraction,
  adaptive rejection sampling).

## Evidence

Traces: `validation_traces/step-75/` (and steps 50, 100, 125) of run
`agentpick-clean-evolve-both-1026b`. The per-trial failure labels, per-task tables and trace
quotes behind every number above were produced by three analysis passes over those traces.
They are in [`failure_skills_1026b/`](failure_skills_1026b/): [`taxonomy.md`](failure_skills_1026b/taxonomy.md)
(every failed trial labelled, per-task table), [`near_miss.md`](failure_skills_1026b/near_miss.md) (passing vs
failing trials of the same task) and [`zero_pass.md`](failure_skills_1026b/zero_pass.md) (the 43 tasks with 0/5).
