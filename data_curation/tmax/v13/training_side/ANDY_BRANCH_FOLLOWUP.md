# `andy/proposer-drafts-checker` — follow-up readout (A1–A4 mapping, no-solution rows, harden trigger, entry seam, pin safety)

Ref: `origin/andy/proposer-drafts-checker`, head `ac7604b7`. Companion to
`ANDY_BRANCH_READOUT.md`; this file answers the five follow-up questions and
corrects that file's mapping of `oracle_reachable` / `expectation_revealed`.

Paths are relative to `torchtitan/experiments/rl/examples/tmax/` unless written
out. Every citation is on the ref.

Definitions used here (ours, not the branch's):

- **A1 `oracle_reachable`** — a *program* left in the image (reference binary,
  generator, solver script) the agent can call during rollout, or wrap inside
  its deliverable, to obtain the graded value.
- **A2 `expectation_revealed`** — the verifier takes its EXPECTED value from a
  file or program that already exists in the image during rollout.
- **A3 `value_derivable`** — the graded value is derivable during rollout from
  readable material without the core work.
- **A4 `expectation_movable`** — the verifier recomputes its expectation at
  grade time from a file the agent could rewrite during rollout.

---

## Q1 — Leak detectors: what they detect, on whom, gate or advice

### `evolution/synth_loop.py` gates 3 and 4

**Population: LLM-synthesized tasks only.** `run_one` builds the task dict from
`gen[...]` — a model's generated `instruction.md`, `environment/Dockerfile`,
`solution/solve.sh`, `tests/test_state.py` (`evolution/synth_loop.py:502-512`).
Nothing in this file is run over an unchanged TMAX seed, and nothing in the
online loop imports its gate sequence (`feedback_loop` imports `synth_loop as
sl` only for `audit`, `sh`, `build_image`, `oracle_check`, `shortcut_check`).

**Gate 3 = `audit()`, `evolution/synth_loop.py:217-244`.** Two lexical signals:

- `dark_paths` — quoted absolute paths in `test_state.py` that appear in
  neither the instruction nor the Dockerfile, minus `UNIVERSAL_PATHS`
  (`:240`).
- `leaks` — one and only one rule:
  `VERIFIER_PATH_RE.search(instruction)` → `"instruction names the verifier
  path"` (`:243`), where the regex is `tests/test.sh | tests/test_*.py |
  /oracle/ | test_state.py` (`:212-213`).

**This is not A2.** It detects the instruction naming the verifier *file*; it
says nothing about where the verifier gets its expected value. It is a GATE
here: `if rec["audit"]["dark_paths"] or rec["audit"]["leaks"]: return
status="audit_rejected"` (`:563-564`).

**Gate 4 = demonstrated shortcut, `:566-578`.** Only fires when the pre-build
LLM reading (`llm.diagnose_unsolved`, `:549`) named a command string; that
string is run in a fresh container with no solution staged and graded
(`shortcut_check`, `:389-406`); a pass → `status="rejected_hackable"` (`:577`).
GATE, but conditional on an LLM having guessed the exact command. The header is
explicit that the reading alone never rejects: *"The reading alone does not
reject; the demonstration does."* (`:571-572`).

Also in this file, and closest of anything on the ref to a structural A3/A1
check: `preflight` requires at least one test body that "rejects an answer that
was never produced" (`rejects_shortcut`, `:183-195`; enforced `:300-306`,
`"no check rejects an answer that was never produced"`). GATE, synthesized
tasks only, and it is a keyword/AST heuristic (`_NOSC_WORDS`, `_MUTATES` +
`_RERUNS`), not a demonstration.

### `evolution/verify_shortcuts.py`

Population: seed-corpus tasks that `audit_solvability.py` judged `hackable`,
read out of a task tar (`evolution/verify_shortcuts.py:1-26, 43-86`). It builds
the image, runs the auditor's claimed command sequence with **no solution
staged**, grades, and records `confirmed | refuted | unproven`. It is a
**measurement script**, not a gate: it writes a results JSONL and nothing in
the training or evolve path imports it. Its consumer is the offline
`evolution/repair_hackable.py` (`:1-27`), which repairs and re-proves offline.

### `evolution/scan_degenerate_graders.py`

Population: **`data/sources/tw-extract/tasks` only** (`:89`) — the TerminalWorld
half; the `tmax-extract` half is never scanned. It also only reads
`tests/*.py` (`scan`, `:60-62`), so a TMAX task whose verifier is
`tests/test.sh` returns `None` and is invisible to it. Three static patterns:
`fallback` (an `except` branch assigning the value the `try` was fetching),
`all_negative`, `verifier_network` (`:41-80`). ADVICE, explicitly:
*"A hit is not a verdict; it is a task worth booting."* (`:23`). Its own header
also records why the null probe is insufficient: *"The null-action sweep asked
what each task pays for `:` and found nothing… That probe is too weak."*
(`:4-6`).

### The instruction-only fast path's "leak / dark-path diff"

`evolution/feedback_loop.revalidate`, `:320`, block `:354-372`. When
`changed == ["instruction"]` and the job is not simplify / spec-repair /
calibration, the oracle and the null probe are **skipped** and the only check
is a before/after diff of `sl.audit()`:

```
before, after = sl.audit(orig), sl.audit(task)
new_leaks = [x for x in after["leaks"] if x not in before["leaks"]]
new_dark  = [p for p in after["dark_paths"] if p not in before["dark_paths"]]
if new_leaks or new_dark: return {"ok": False, "stage": "audit", ...}
```

GATE — the only gate on that path. It inherits gate 3's definition of "leak",
i.e. the instruction naming `tests/test.sh`; it is not A2.

### The structural path's two audits

`new_dark_paths` (`:265-286`) and `new_dark_literals` (`:299-318`,
implementation in `evolution/verifier_literals.py`) detect the **inverse** of
A2/A3: names the verifier depends on that the agent *cannot* read. Both are
explicitly ADVICE:

> "The two audits of what the verifier demands unseen are advice, not a verdict.
> Measured on wd-20260904a over 464 rewrites, the names audit flagged 130
> literals without its seed baseline and none with it, and every one of the 130
> was a false positive … A gate with no measured precision has no business
> discarding a session" — `evolution/feedback_loop.py:447-455`

The same pair is printed by the agent's own tool and is also advice there
(`evolution/agent_sandbox.py:521-526`).

### Other modules whose name/docstring names leak, shortcut, hackable, golden

| module | population | gate/advice |
|---|---|---|
| `evolution/audit_instruction_verifier.py` | a task tar (seed corpus), offline | ADVICE — *"Flagged tasks are candidates for review, not verdicts"* (`:17-18`); same lexical leak = instruction names the verifier |
| `evolution/audit_solvability.py` | seed tasks, one LLM read each | ADVICE; produces the `hackable` claim + shortcut string consumed by gate 4 / `verify_shortcuts` |
| `evolution/repair_hackable.py` | tasks with a *demonstrated* shortcut | offline repair tool, not a gate in any live path |
| `evolution/judge_honesty.py` | a `solve_eval --keep-trace` run | offline study; **nothing imports it** (only self-reference at `:21`) |
| `evolution/audit_time_bombs.py` | a task tar + mix, offline CLI | ADVICE; **nothing imports it** (only `evolution/check_urls.py:15` mentions it in prose) |
| `evolution/pool_diversity.py:14` | — | the only occurrence of "answer key" on the ref, and it is a metaphor |

No module on this ref mentions "oracle-in-image" or an "answer key" as a
detectable defect. "golden" occurs only inside `evolution/data/operator_cards*.json`
as task-authoring vocabulary.

### Direct answers for A1, A2, A3

**A1 — a solver/generator program left in the image.**
(a) Unchanged seed: **no check anywhere.** (b) Bred rewrite: **no check.** The
null probe grades an untouched workspace (`agent_sandbox.py:491-513`,
`feedback_loop.py:468-487`) — an image that *contains* a solver still fails it,
because nothing ran the solver. The independent `wrong-N.sh` controls
(`evolution/verifier_probes.py:40`) would only catch it if the probe author
happened to write a control that calls the program, and its prompt never
suggests looking for one. `synth_loop` gate 4 could catch it on a *synthesized*
task, and only if the LLM auditor guessed the invocation.

**A2 — verifier takes its expectation from something in the image.**
(a) Unchanged seed: **no check.** (b) Bred rewrite: **no check.** Nothing reads
the verifier looking for "where does the expected value come from"; the only
verifier-source analysis on the ref (`verifier_literals.py`) asks the opposite
question. The verifier-author prompt actually *pushes toward* the shape:

> "**Every name you depend on -- a key, a label, a file name, a column --
> appears, spelled the same, in `instruction.md` or in a file under
> `environment/` that the instruction points at.**"
> — `evolution/agents/verifier_author.md:52-54`

and the author prompt says *"put discoverable detail in the workspace itself"*
(`evolution/agents/task_evolution.md:152-153`).

**A3 — graded value derivable from readable material without the work.**
(a) Unchanged seed: **no check** in any live path. (b) Bred rewrite: **no
check**; the shortcut-claim machinery that used to cover it
(`verify_shortcuts.py`, `synth_loop` gate 4) is corpus-build only, and
`revalidate`'s docstring records that the loop deliberately removed its
LLM-guessed hackability gate — *"Of the 148 rewrites that probe rejected in one
week … 100 [were] the expected artifact printf'd into place by a reader who had
seen the solution and the verifier"* (`evolution/feedback_loop.py:334-342`).

### What the prompts say about answer-producing material / readable expectations

Nothing in any of the three prompts forbids leaving an answer-producing program
or a golden-output file in the image. The nearest sentences are all about A4,
and they are addressed to whoever writes the verifier:

> "Never use solver-writable replacement inputs as the authority for correctness;
> copying or hashing them at grading time does not recover the original data."
> — `evolution/agents/task_evolution.md:111-112`, and verbatim again at
> `evolution/agents/verifier_author.md:88-90`

> "For supplied-data tasks, derive expectations from the original fixture or
> expected values prepared before solver execution and supplied with the grader."
> — `evolution/agents/verifier_author.md:86-88`
> ("supplied with the grader" = shipped under `tests/`, which is uploaded after
> submission; the sentence permits, and does not warn about, deriving from a
> fixture the solver can also read.)

> "Before finishing, inspect whether a no-op, a hardcoded answer or fabricated
> evidence could still pass" — `evolution/agents/verifier_author.md:176-177`
> (self-inspection, unverified)

> "Do not infer a prohibition on copying or hardcoding."
> — `evolution/agents/verifier_author.md:69-70`

The only leak rule given to the author is the instruction→verifier direction:

> "**Never reveal the verifier in `instruction.md`.** No test file paths, no test
> or function names, no `pytest` or `test.sh` command."
> — `evolution/agents/task_evolution.md:307-309`

`evolution/agents/independent_verifier_probes.md` says only that the grading
tests and reference solution "are not available in its environment" (`:4-5`);
it gives no instruction about answer material in the image.

**Bottom line.** Every leak detector on this ref detects one of two things:
the instruction naming the verifier *file*, or the verifier needing a name the
agent cannot read. Nothing detects A1, A2 or A3 — not for an unchanged seed and
not for a bred rewrite — and the only machinery that ever demonstrated A3
(`verify_shortcuts` / `synth_loop` gate 4) runs on synthesized or seed corpora
offline and was deliberately removed from the online loop for low precision.
The prompts warn about A4 and about hidden vocabulary, never about
answer-producing material in the image.

---

## Q2 — Tasks with no reference solution

### Can a solutionless corpus task reach the training mix? Yes, on every path.

- `prepare_tmax_data.py` (the `allenai/tmax-15k-open-instruct` prep) never
  mentions `solution` or `solve.sh` at all; its documented per-task tree is
  `instruction.md`, `setup.sh` (ignored, baked into the image) and
  `tests/test.sh` (`prepare_tmax_data.py:7-18`). A row is built from those.
- `prepare_tmax_reaudit_data.py` lists `tasks/<task_id>/solution/solve.sh` in
  the expected layout (`:20`) but reads it **only if present**, and only to
  count oracle commands: `if os.path.exists(solve_path): … oracle_commands =
  _oracle_commands(f.read())` (`:455-459`). Absent ⇒ `oracle_commands = 0` and
  the row is written. The docstring's "WHAT IS REFUSED" list (`:39-50`) has no
  solution clause.
- `evolution/build_mix_v2.py` requires exactly one file to admit a package:
  `if not (src / "instruction.md").exists(): missing.append(tid); continue`
  (`:201-203`). `pack.to_row` never looks for a solution.
- `evolution/build_clean_dataset.py` is the one filter that is solution-derived
  — it keeps only task ids whose `oracle_validation.jsonl` verdict is `pass`
  (`:74, 82-84`) and marks verdict-flippers `flaky` (`:44-54, 100-103`). But
  that is the **TerminalWorld seed** dataset (`SRC = data/seed-dataset`, `:23`),
  not the TMAX half.
- `evolution/oracle_validate_seeds.py` runs `bash /oracle/solution/solve.sh`
  then `bash /oracle/tests/test.sh` and sets `status = "pass" if test_exit == 0`
  (`:114-124`). It only uploads members under `solution/` and `tests/`
  (`:109-111`); with no solution it still grades and can record `pass` on a
  verifier that is green on an untouched image.

And the branch states outright that the TMAX half has no oracle:

> "`oracle_*` is the reference solution's own peak, measured at the platform
> ceiling; it is empty for the TMax half, which ships no reference solution to
> run." — `evolution/export_measured_csv.py:11-13`

### Where seed solutions come from

For the TerminalWorld half: they ship with the corpus and are only *validated*,
never authored, by this tree (`oracle_validate_seeds.py`, then
`build_clean_dataset.py`). Counts are recorded:
`README_SEED_DATA.md:119` — `reward_verdict` **pass 861 / fail 446 / unknown 46**,
with *"The fail+unknown 36% have a reference solution that cannot even earn
reward 1, so every rollout on them scores 0"*; `evolution/README.md:158`
repeats "Of the 1,353, **861 have a reference solution that earns a passing
grade**". `evolution/count_partial_solutions.py` and
`add_reference_partial_column.py` further mark solutions carrying a
`# Partial:` comment, which "cannot score 1 by construction".

The only step on this ref where a **model writes `solve.sh`**, with an oracle
gate that it must score 1, is `evolution/synth_loop.py` — and it writes a
*new synthesized task*, not a solution for an existing seed
(`:502-512` builds the whole package from `gen`, `:557-560` rejects on
`oracle_check` failure). **There is no "author a missing solve.sh for a seed"
step anywhere on this ref.** Not found.

### What happens if a solutionless task reaches the evolve loop

1. `materialize_r0` (`evolution/evolve_ondella.py:416-464`) copies whatever the
   corpus carries, unchecked, minus `SEED_IGNORE` (`:97-99` — backups and
   provenance only). No solution requirement.
2. `handle` dispatches into `fb.process_one`, whose **first** package access is
   `task = ev.load(work)` (`evolution/feedback_loop.py:740`).
3. `ev.load` reads all four files unconditionally —
   `task = {key: (d / path).read_text(errors="replace") for key, path in fm.items()}`
   (`evolution/evolve.py:88-89`) with `fm["solve_sh"] = "solution/solve.sh"`
   (`:44`). **A package with no solution raises `FileNotFoundError` here**,
   which `process_one`'s blanket handler turns into
   `status="failed", stage="error"` (`evolution/feedback_loop.py:1029-1030`).

So the author session is **never started** for a solutionless task; it does not
get the chance to write one, and nothing in `agents/task_evolution.md` asks it
to (`solution/solve.sh` is described as "the reference solution, run to prove
the task is solvable", `:22`, and the editing rule is conditional: *"when the
intervention requires changing it"*, `:69`). The downstream `no_solution` gates
— `evolution/daytona_revalidate.py:268-273` and
`evolution/agent_sandbox.py:711-712` — are therefore unreachable from the corpus
path; they guard a package that *lost* its solution mid-session.

### Recorded counts of mix rows with / without a validated solution

For the TW half: 861 / 1,353 (above). For the TMax half: **not found.** No
doc, README, `LAYOUT.md`, `TASK_EVOLUTION.md` or comment on this ref counts mix
rows by presence of a validated solution; the only statement is the qualitative
`export_measured_csv.py:11-13` one.

**Bottom line.** Nothing between the corpus and the sampler requires a
reference solution, and the TMax half is documented as shipping none. Such a
task trains normally and every evolve signal drawn on it dies at
`ev.load` with `stage="error"` before any session runs — so it is never
hardened, never simplified, and never audited by the loop. No count of how many
mix rows are in that state exists on this ref.

---

## Q3 — Harden trigger and partial exploits

### Default and exact condition

`evolution_harder_ratio` default: **1.0** — field default
`rollouter.py:921` (`evolution_harder_ratio: float = 1.0`), env resolution
`config_registry.py:183-185` (`SWE_EVOLUTION_HARDER_RATIO`, default `"1.0"`),
validated `0 < x <= 1` at `rollouter.py:937-938`. Shipped launchers:
`runbook/launch_9b.sh:150` keeps `1.0`; `runbook/rltrain.env:163` sets `0.9`
(≥15 of 16).

`rollouter._maybe_emit_evolution_signal` (`rollouter.py:1219`), condition in
order:

- `run = _run_dir()`; `None` ⇒ return (`:1241-1243`)
- validation group (`group_id < 0`) ⇒ return (`:1244-1245`)
- `scored = [r for r in rollouts if is_scored(r)]`; `len(rewards) < 2` ⇒ return
  (`:1250-1254`)
- dense mode: `pstdev(rewards) != 0.0` ⇒ return (`:1257-1259`)
- sparse mode: **`if not all_failed and solved / len(rewards) <
  self._evolution_harder_ratio: return`** (`:1260-1262`)
- all-fail with zero turns ⇒ `infra_quarantine` advisory, no signal
  (`:1264-1289`)
- `SWE_EVOLUTION_SIGNALS != "1"` ⇒ return (`:1290-1291`)
- else write `{task, rev, run, group, direction: "harder" if passed else
  "easier", solved, total, created, attempts:[record paths]}` (`:1293-1314`)

### Is anything emitted for a partially solved group?

**No.** 5 of 16 solved, sparse: `all_failed` is False and `5/16 = 0.3125 <
1.0` ⇒ the function returns at `:1260-1262` with nothing written — no signal,
no advisory, no ledger row. The only two emissions in the whole function are
the k/k (or ≥ratio) harden signal, the 0/k simplify signal, and the
zero-turn `infra_quarantine` line. The class docstring says so:
*"Request easier all-fail tasks and harder high-success tasks"* (`:1222`), and
the config field comment adds *"Mixed groups still train"*
(`rollouter.py:922-924`).

Consequence: **a cheap reward hack found by a minority of rollouts surfaces
nowhere.** Its traces are written as rollout records like any other, but no
signal references them, `evolve_ondella.discover/choose` never sees the task,
the author session is never opened on it, and the group keeps training with its
spread intact. The traces are only ever read by the author once the group
crosses the ratio, i.e. essentially k/k.

### Does the author's prompt ask for reward-hack detection?

It asks for *difficulty* analysis of the winning strategy, not for whether that
strategy did the requested work. The opening of `_STUDENT_HARDER_GUIDANCE`
(`evolution/evolve_codex.py:1046-1049`):

> "Choose one change from the student's actual attempts in `traces/`. Start by
> identifying the successful strategy and a task-relevant judgment it currently
> bypasses."

and the framing of what a hardening is (`:1090-1093`):

> "What every hardening removes is an assumption the successful strategy relied
> on without checking: that the only CSV in the directory is the input, that the
> first match is the target, that a re-run reproduces what was recorded, that
> two records of different types are never equal."

The closest the prompt comes to reward-hack detection are two conditional
sentences, both subordinate to the hardening frame (`:1101-1105`):

> "If correctness depends on a method or source restriction, make it enforceable
> in the runnable environment or checkable by the grader; otherwise reformulate
> it as an observable task condition. When reviewing measured feedback, check
> successful attempts for violations of those restrictions."

and (`:1109-1111`):

> "Existing method requirements remain part of the original task; repair missing
> checks for validity, but do not count stricter enforcement of an unchanged
> requirement as the hardening mechanism."

So a trace showing "the policy printf'd the expected artifact" is, in the
prompt's vocabulary, a *strategy with an unchecked assumption* to be hardened
against — not a verifier defect to be reported. There is no "was this pass
real?" question, no place to record one, and `task_evolution.md`'s only exits
are `BLOCKED:` / `GIVE UP:` (`:283-303`), which return the task unchanged.

### Separate reward-hacking monitor over rollouts?

**None.** Greps for `hack|cheat|exploit|tamper` over `rollouter.py`,
`grading.py`, `integrity_baseline.py` return exactly one hit: the comment
"the same single seam the anti-tamper reset uses" at `grading.py:290`,
describing the reward-path nonce sentinel (`grading.py:275-286`: the harness
writes a nonce to `reward.txt` before grading and scores 0 if it cannot read it
back). That guards the reward file, not the strategy. In `evolution/`, the only
hit that reads transcripts is `evolution/judge_honesty.py` — *"For every passing
attempt, a judge reads the instruction and the commands and returns real |
shortcut | unclear"* (`:11-13`) — and it is a standalone CLI over a
`solve_eval` run that nothing imports.

**Bottom line.** The harden trigger is k/k by default (≥90 % in `rltrain.env`),
and a group at 5/16 emits nothing at all — no signal, no advisory, no record a
later reader could filter on. Minority reward hacks are invisible to the whole
evolve pipeline. When the author finally does read a k/k group's traces, its
prompt asks what judgment the winning strategy *bypasses*, which is a
difficulty question, not "did that pass actually do the work". No
reward-hacking monitor exists over training rollouts.

---

## Q4 — Entry point for external static findings

### What `_prepare_package` installs, and who reads it

`_prepare_package` — `evolution/evolve_codex.py:572-589`:

| file | written at | read by |
|---|---|---|
| `run/seed_literals.json` | `_write_seed_literals`, `:513-530` (`:520`) | `agent_sandbox._names_audit`, `:440-446` (advice print in `cmd_check`, `:521`) |
| `run/seed_size.json` | same helper, `:527-529` | `agent_sandbox._step_audit`, `:451-461` (the gate in `cmd_check`, `:522, 530`); quoted to the blind verifier in `_VERIFIER_JOB` and in `agents/verifier_author.md:30` |
| `run/resources.json` | `_write_resources`, `:532-547` (`:546`) | `agent_sandbox` box resolution, `:119-141`; referenced in the job prompts at `:1194, 1280` |
| `run/pretest.json` | `_write_pretest`, `:549-570` (`layout.write_pretest`, `:565`) | `agent_sandbox`, `:145-155` (hook) and `:634-636` (`Protected.from_pretest_file`) |
| `AGENTS.md` (= `agents/task_evolution.md`) | `:586` | the author session |
| `sandbox` | `:587-588` | the author session |

Written later into the same `run/`, by other functions: `run/checks.jsonl`
(appended by the tool, read by `_last_check`/`_require_checked`, `:632-664`),
`run/verdict.txt` (`_check_verdict`, `:665-677`), `run/sandbox.json` (`:720`),
`run/failure.txt` (`:901`, `:1938`, `:2103`, `:2178`; read by `_REPAIR_JOB`
`:1298-1310`, `_SPEC_REPAIR_JOB` `:1324-1330`, `_VERIFIER_REPAIR_JOB`
`:1388`), `run/student_feedback.json` (`:1996-1998`), `run/hardening.md`,
`run/operator.txt` (`:2071-2080`), `run/simplify.json`, `run/repair.json`
(`:2055`), `run/verifier-changes.md`, `run/verifier-probes/*`,
`run/independent-failures.jsonl` (`:1677-1679`).

### What `_blind_layout` installs

`_blind_layout` — `evolution/evolve_codex.py:1439-1463`. It copies the author's
package minus `HIDDEN_FROM_VERIFIER = ("solution", "traces", "AGENTS.md",
"sandbox")` (`:946-951`), and under `run/` keeps an **explicit allowlist of
four**:

```
return set(names) - {"seed_size.json", "resources.json",
                     "seed_literals.json", "pretest.json"}      # :1447-1452
```

then installs `AGENTS.md` = `agents/verifier_author.md` (`:1462`) and `sandbox`
(`:1463-1464`). `_restore_seed_tests` (`:1476-1485`) then overwrites `tests/`
with the previous revision's verifier. The independent probe author gets a
further-stripped copy: `_blind_layout` again, `tests/` replaced by an
always-exit-2 stub, and `seed_size.json` + `seed_literals.json` deleted
(`:1605-1616`).

### Existing channels carrying per-task notes into a session

| channel | into which session | origin |
|---|---|---|
| `run/student_feedback.json` | **author** (harder/easier, codex arm, no operator menu) | `evolution/evolve_ondella.py:611-628`, from the signal dict; prompt paragraph added at `evolve_codex.py:2000-2007` |
| `run/failure.txt` | **author** (`repair`, `repair_spec`) and **blind verifier** (`_blind_repair`, `:1778`) | the caller's own probe output |
| `run/independent-failures.jsonl` | **blind verifier** (probe-repair resume) | `verify_probes`' own miss log, `:1677-1679` |
| `run/seed_literals.json` / `seed_size.json` | both | the loop, from the input revision |
| `run/repair.json` | author, but the author *writes* it, not reads it | `:2054-2059` |

There is **no channel that carries externally supplied content** into either
session. `student_feedback` is the only one whose content the caller composes,
and it is composed from the signal's own numbers. The signal file is,
incidentally, the one place an outside writer could inject: `read_signal`
validates only that `SIGNAL_KEYS` are *present*
(`evolution/evolve_ondella.py:100-108, 344`), extra keys pass through, and the
loop fills `student_feedback` only `if d.get("student_feedback") is None`
(`:615`).

### Smallest change that lets a per-task JSON of static findings reach the right session

A static finding derived only from `instruction.md` + the environment + the
previous revision's `tests/` is inside the blind verifier's permitted view
(`agents/verifier_author.md:26-32` lists exactly those three plus `run/`), and
trivially inside the author's. So the seam is symmetric, and it is three lines
plus one prompt paragraph per side:

**Author side.** Write the file in `_prepare_package`, immediately after
`_write_pretest(pkg, task)` at **`evolution/evolve_codex.py:585`** — a new
`_write_static_findings(pkg, task)` next to the other three `_write_*` helpers
(`:513`, `:532`, `:549`), reading `task["_static_findings"]` which
`feedback_loop.process_one` would set beside `task["_pretest"]` /
`task["_protected"]` at **`evolution/feedback_loop.py:752-757`**, itself read
from a per-task file the loop already snapshots next to
`rewrite/pretest.json` (`evolution/evolve_ondella.py:540-551` is the existing
snapshot seam). Output path: `run/static_findings.json`. The prompt paragraph
that must name it is the same place `student_feedback` names its file —
**`evolution/evolve_codex.py:2000-2007`**, the `"MEASURED STUDENT FEEDBACK"`
block appended to `prompt`; an analogous `"EXTERNAL STATIC FINDINGS"` block
belongs there, or inside `_HARDER_JOB_BLIND` (`:1218`) / the `package` table in
`agents/task_evolution.md:18-25`.

**Blind-verifier side.** One name added to the `run/` allowlist in
`_blind_layout`, **`evolution/evolve_codex.py:1447-1452`** (the set currently
holding `seed_size.json`, `resources.json`, `seed_literals.json`,
`pretest.json`). The prompt paragraph is `_VERIFIER_JOB`
(**`evolution/evolve_codex.py:1359-1384`**) and the package table in
**`agents/verifier_author.md:26-32`**, which is the enumeration the verifier
author is told is its whole world.

**Blind-split constraint on content.** The file handed to the verifier session
must be derived from instruction + environment + previous `tests/` only. Any
finding that cites the reference solution, the author's draft or a rollout
trace must go to the author path only — the probe author's guard
(`_probe_hashes` before/after, `:1618-1645`) and the verifier-repair guard
(`:1700-1703`) both already refuse changes outside `run/`, so an extra `run/`
file is compatible with them, but nothing enforces the *provenance* of its
content; that has to be enforced by whoever writes the file.

**Bottom line.** `run/` is already the one-way tray both sessions read, with a
four-name allowlist for the blind side. Adding a per-task findings JSON is a
`_write_*` helper called at `evolve_codex.py:585`, one entry in the allowlist
set at `evolve_codex.py:1447-1452`, and a prompt paragraph in the same place
`student_feedback` gets one (`:2000-2007`) plus `_VERIFIER_JOB` (`:1359`). No
existing channel carries external content today.

---

## Q5 — Pin safety

### Is a listed path checked against what the instruction asks the solver to modify?

**No.** The only validation of `tests/protected_paths.json` is shape:
`pack_to_dataset.package_protected` (`evolution/pack_to_dataset.py:199-225`)
requires an object with only `paths` / `cmds`, each a list of non-empty
strings, and raises `ValueError` otherwise. `effective_protected`
(`:171-181`) only decides package-over-inherited precedence.
`integrity_baseline.py` digests whatever it is given
(`tmax_protected_fields`, `:262-276`; `capture_integrity_baseline`, `:279`).
Nothing anywhere compares a protected entry against `instruction.md`, against
`solve.sh`, or against anything else semantic. The author's prompt states the
intent but nothing enforces it:

> "If the public instruction requires files or command outputs to remain
> unchanged, list them in `tests/protected_paths.json` as `{"paths": [...],
> "cmds": [...]}`; the harness digests them before and after the episode and any
> change scores 0." — `evolution/agents/task_evolution.md:37-39`

### Is the oracle graded WITH the baseline active?

**`./sandbox check` — yes.** `evolution/agent_sandbox.py`:
- boot baseline for `grade` (the null probe): `boot_baseline = await
  dr.capture_baseline(sb, md["tmax"], …)` (`:700-702`), after
  `dr.seed_workspace` (`:694`).
- **oracle baseline**: `oracle()` re-reads the package into a row
  (`:706-709`), refuses a package with no `solution/solve.sh` (`:711-712`),
  writes `solution/` into `/solution` (`:713-718`), then
  `baseline = await dr.capture_baseline(sb, tmax, workdir=workdir, …)`
  (`:720-724`) — *"the state the reference solution starts from, taken after
  `solution/` is in and before it runs, with the lists as the package declares
  them now"* — runs `bash /solution/solve.sh` (`:726-728`) and grades with
  `grade_tmax(..., baseline_digests=baseline, ...)` (`:736-743`).
- The verdict then turns on it: `oracle_ok = reward >= 1.0 and
  r.get("solve_exit") == 0` (`:519`) and `ok = oracle_ok and not step`
  (`:527`), and `_require_checked` (`evolve_codex.py:654-664`) discards any
  rewrite with no passing record. So a pin the reference solution must violate
  ⇒ `grade_tmax` returns 0.0 at `grading.py:303-320` ⇒ `VERDICT: fail
  stage=oracle` ⇒ rewrite discarded.
- `grade` additionally refuses if the lists moved since boot: *"the protected
  lists changed since up; run ./sandbox reset to take a fresh baseline"*
  (`:777-783`).

**`feedback_loop.revalidate` / `daytona_revalidate.probe` — yes.**
`revalidate` passes `pretest_file=` into `daytona_probe`
(`evolution/feedback_loop.py:412-414, 468-473`); the file is the loop's own
snapshot the session cannot reach (`:748-760`). In `probe`
(`evolution/daytona_revalidate.py:221-252`) the row is built with
`pack.to_row(str(pkg), pretest=pretest, protected=protected)` (`:239-242`);
with protected entries present the hook is logged as
`{"mode": "baseline", …}` and *"A row with protected entries grades by the
integrity baseline (taken below, right before the run) and never consults the
pin hook."* (`:248-252`). Then `baseline = await capture_baseline(sb, tmax, …)`
(`:326-329`) — *"the state the run starts from -- solution/ in, nothing run yet
-- for the oracle and the shortcut probe alike; the verifier re-digests and a
difference grades 0"* — and `grade_tmax(..., baseline_digests=baseline, ...)`
(`:336-345`). Verdict `ok = reward >= 1.0 and code == 0 and
execution["submitted"]` (`:349`), so a mis-specified pin ⇒ oracle 0 ⇒
`stage="daytona_oracle"` rejection at `evolution/feedback_loop.py:421-444`.

**`oracle_validate_seeds.py` — NO.** It uploads `solution/` and `tests/` to
`/oracle/`, runs `bash /oracle/solution/solve.sh`, then `bash
/oracle/tests/test.sh`, and sets `status = "pass" if test.exit_code == 0`
(`evolution/oracle_validate_seeds.py:109-124`). It never imports
`integrity_baseline`, never captures a baseline, never reads
`protected_paths.json` and never runs `pre_test_sh` — a grep for
`protected|integrity|pre_test|pretest` over that file returns nothing. The same
is true of `evolution/synth_loop.py`'s `oracle_check` (`:368-386`) and
`shortcut_check` (`:389-406`). So a pin that is wrong for a **seed** is not
caught by seed oracle validation; it is caught only if and when that seed is
bred and the rewrite goes through `./sandbox check` or the Daytona probe.

### The same question for `pre_test_sh`

- In `./sandbox check` and in `daytona_revalidate.probe`, the hook reaches
  `grade_tmax` on the same row, so it runs exactly as training runs it —
  **but** `grading.py` gates it twice: `if pre_test and not _protected:`
  (`grading.py:325-326`), so **a row that has protected entries never runs its
  pin hook at all**, and then the environment-identity guard: it runs only when
  `_stamped and _episode and _stamped == _episode`, else it logs
  *"pre_test SKIPPED for %s: environment changed since capture"* and falls
  through (`grading.py:334-360`). The probe surfaces which branch it took as
  `hook["runs"]` and logs *"runs before the verifier" / "skipped: environment
  moved"* (`evolution/daytona_revalidate.py:262-266`).
- Consequence for pin safety: a mis-specified `pre_test_sh` **is** caught by
  the in-loop oracle, but only for a rewrite that did not touch the Dockerfile
  (otherwise the check is skipped for the oracle exactly as it will be skipped
  in training — so the pin silently stops protecting rather than failing loudly).
- `oracle_validate_seeds.py`: again, not run at all.
- The corpus-wide guard against *every* row silently skipping is prep-side:
  `prepare_tmax_data.selfcheck_env_identities` refuses the whole prep if no
  stamped row matches its episode identity (`prepare_tmax_data.py:370-400`;
  refusal clause restated at `prepare_tmax_reaudit_data.py:48-49`).

**Bottom line.** Nothing on this ref checks a pin against the instruction; the
only protection is behavioural — in the two in-loop graders the reference
solution runs under the baseline, so a pin the solution must violate zeroes the
oracle and the rewrite is rejected. That protection does not exist for seeds
(`oracle_validate_seeds.py` and `synth_loop.oracle_check` run no baseline and
no hook), and it is blind to a pin the *instruction* asks the solver to modify
but the reference solution happens not to touch. `pre_test_sh` is weaker still:
it is disabled outright on any row that also has protected entries, and skipped
on any environment change.

---

## Table: static-audit rule → does the training side detect it?

| static-audit rule | unchanged seed in the mix | bred rewrite | gate or advice |
|---|---|---|---|
| **A1 `oracle_reachable`** — answer-producing program left in the image | **No.** No detector on any path. `oracle_validate_seeds` only asks whether the reference passes | **No.** The null probe (`agent_sandbox.py:491-513`, `feedback_loop.py:468-487`) cannot see it; only an `wrong-N.sh` control that happened to call the program would, and no prompt asks for one | — (nothing to gate) |
| **A2 `expectation_revealed`** — verifier's expectation comes from a file/program in the image | **No.** The corpus-build "leak" rule is `instruction names the verifier path` only (`synth_loop.py:212-213, 243`) | **No.** Same rule on the instruction-only fast path (`feedback_loop.py:363-371`); `verifier_literals.py` asks the inverse question, and `verifier_author.md:52-54` requires the verifier's names to be agent-readable | GATE for the *wrong* thing (verifier-path leak); nothing for A2 |
| **A3 `value_derivable`** — value derivable from readable material without the work | **No** in any live path. `verify_shortcuts.py` + `synth_loop` gate 4 demonstrate it, but only offline on seed/synth corpora and only where an LLM named the exact commands | **No.** The loop deliberately removed its hackability gate — 148 rejections, 100 wrong (`feedback_loop.py:334-342`) | ADVICE at best (`audit_solvability`, `scan_degenerate_graders`); the offline demonstration path is a GATE only inside `synth_loop` |
| **A4 `expectation_movable`** — expectation recomputed from a solver-writable file | **No** automatic check. Only if the task ships `protected_paths` / `pre_test_sh`, which pins the fixture at grade time (`integrity_baseline.py:14-17`, `grading.py:303-320`) | **Partially.** Prompt rule `verifier_author.md:88-90`; a required input-replacement replay control `verifier_author.md:181-185`, replayed by the caller in fresh sandboxes (`verifier_probes.py:40`) | The replay controls are a GATE (miss ⇒ one repair, then discard, `evolve_codex.py:1655, 1671-1704`); the prompt rule and the pins are ADVICE/opt-in |
| **wrong hardcoded literal in the verifier** | **TW half only**, via `oracle_validate_seeds` → `reward_verdict` (861/1353 pass, `README_SEED_DATA.md:119`). **Not** for the TMax half, which ships no solution (`export_measured_csv.py:11-13`) | **Yes** — the reference solution fails, `ok = reward >= 1.0 …` (`daytona_revalidate.py:349`) ⇒ `stage="daytona_oracle"`; also `VERDICT: fail stage=oracle` in-session (`agent_sandbox.py:519, 530`) | GATE (oracle) |
| **source-text grep in the verifier** (grades the implementation text, not behaviour) | **No.** `scan_degenerate_graders.py` has no such pattern and reads only `tests/*.py` under `tw-extract` (`:60-62, 89`) | **Partially.** `verifier_author.md:69-70` ("Do not infer a prohibition on copying or hardcoding") and `:178-180` ("identify one concrete incorrect solution and the check that rejects it … distinguish code inspection from executed tests") are prompt rules; a differently-implemented `correct.sh` control would fail such a verifier and raise `SemanticProbeMisses` | ADVICE (prompt) + incidental GATE via the replayed `correct.sh` |
| **wall-clock threshold in the verifier** | **Advice only**, offline: `audit_time_bombs.py` `clock` category (`:24`, `CLOCK` regex `:122-129`, `clock_in_grade` summary `:390-393`). Nothing imports it | **No.** Not run on rewrites at all; every probe runs once, so a time-dependent verifier is never re-sampled (the only retries are infra-shaped, `feedback_loop.py:82-85`) | ADVICE, and unconsumed |
| **verifier imports a module the image lacks** | **TW half only**, as an oracle failure (`reward_verdict != pass`). Not for the TMax half | **Yes** — the verifier crashes in the real box, reward 0 ⇒ `stage="daytona_oracle"` / `VERDICT: fail stage=oracle`; the independent replay (`verifier_probes.verify_probes`) runs it again in fresh sandboxes | GATE (oracle + probe replay) |
