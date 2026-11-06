# EvolveBench: one round of the rewriter over a frozen signal set

EvolveBench measures a rewriter configuration ("arm": code commit, model,
effort, prompt flags) on many tasks at once. Every arm rewrites the same
frozen signals, its accepted revisions are scored by the same student under
one pinned recipe, and the result is a JSON that names every input it came
from. Two arms are compared task by task.

It complements `overfit_bench/`. The overfit bench
asks whether a rewriter, given a whole training run on one task, brings the
policy to solve that task: slow, one task per job, measured by training.
EvolveBench asks what one round of rewriting does to the training signal of a
few hundred tasks: whether each rewrite lands in the band where a group
carries gradient, at what cost, and whether the rewrite is sound. No training
happens, and the rewriter never sees the measurement.

`evolve_bench.py` is the one entry point; `evolve_bench.py <cmd> -h` lists
every argument.

## Standard (2026-11-06)

This section is the standard EvolveBench results are judged by. Where any
other part of this file, or an earlier comment in yichuan-w/torchtitan#319,
differs from it, this section holds. The sections after it describe the tool
and the sets frozen before this standard; where they name a single student, a
single recipe, k=16 or McNemar, this section's two students, their two
recipes, k=12 and the sign test apply.

### Principles

Work done under this standard needs no further approval, provided it keeps
these five principles, which Zhifei set and canonical `evolution/CLAUDE.md` on
`yichuan/qwen35-port-cotrain` also records:

1. The loop spends no extra student rollouts to measure a rewrite; a task's
   difficulty is read only from rollouts training already runs (2026-10-19).
   EvolveBench's own evaluations sit outside training and do not break this.
2. A change to what the rewriter is told (a prompt edit, a prompt flag) enters
   training only after it beats the mainline arm A0 on EvolveBench as specified
   here. EvolveBench is the only measure that decides it (2026-11-05).
3. When the rewriter does something wrong, first find the sentence of its
   prompt that pushed it there and change the prompt. A check is a backstop: a
   check that does not change a prompt is only recorded until it is measured the
   same way (2026-11-05).
4. A task that several rewrites in a row did not move stays in training as
   usual. Training compute spent on tasks without a signal is not what is being
   optimized (2026-11-05).
5. Mechanism details are decided by whether they raise EvolveBench and help the
   policy progressively solve Terminal-Bench 2 tasks (2026-11-05).

Execution details inside these principles are decided by whoever runs the
measurement and reported in yichuan-w/torchtitan#319. A change to the principles
or to this standard goes through Zhifei's side first.

### Task selection

Candidates: the 47 tasks of the 122B harder slice `evb122b-h50`, the 55 of
dev55, the rest of agentpick400, and tasks from runs 111e and 114. Each
candidate's original is measured once at k=12 on base 9B and once on base 122B
(122B under `train-111c-122b`; 9B under the recipe the dev55 re-measurement on
the eval cluster records in its `launch.json`). Then:

- easier: originals base 122B solves in none of 12 attempts;
- harder: originals base 9B solves in all 12 attempts;
- a candidate in neither group is left out.

The pool is the candidates that pass this selection. A task's direction is set
here and holds for both students, including a student whose own 12 attempts
on the original are mixed.

The known-defect repair rows of agentpick400 (`repair` and `calibforge_repair`,
labelled in `data_evolve_bench_defects.tsv`, release config
`data_evolve_bench.json`) are candidates like the rest and also report a repair
rate, as described below. An earlier k=12 measurement of an original on the
same student can stand in for its selection measurement; a k=16 measurement
contributes its first 12 graded attempts.

### Rewriting and measurement

Every task in the pool is rewritten once per student, 9B and 122B. The
rewriter's traces are that student's own 12 attempts on the original. The
rewrite is then measured on the same student at k=12, the group size of the
122B training runs. One rep is the whole pool rewritten and measured once per student; every
arm runs 3 reps. An existing rewrite counts toward a rep only when its traces
came from the student it is measured on.

### Reported numbers

The main number is clean_mixed: tasks whose rewrite, measured at k=12, is solved
in 1 to 11 of 12 attempts and has no confirmed defect in the Opus blind audit,
whose audit step is followed by a verify step. It is the same for both directions and both students and
is reported per direction and student and in total; the denominator is the
tasks of that direction in the pool, and a rewrite the pipeline did not accept
counts as not clean_mixed.

Reported beside it:

- tasks still at 12/12 and tasks at 0/12 after the rewrite;
- attempts that ended at the time limit, as an infrastructure finding with the
  per-turn generation wait, never as difficulty;
- confirmed audit defects by class; LEAKY is the leak measure;
- repair rate by defect class, from `tally --labels data_evolve_bench_defects.tsv`
  (`repair` in its output), with a hand label in the `fixed` column taking
  precedence.

Not reported: the count of rewrites landing in 3 to 8 of 12, which is the
solved-count band `EVOLVE_HARDER_TARGET` gives harder rewrites in training and
not what EvolveBench judges by; and the count of new "input X gives output Y"
sentences, which is measured only on the 7-task leak slice that tests
`EVOLVE_NO_WORKED_ANSWERS` (the parents of the seven 111e harder rewrites that
added such a sentence). dev55's earlier k=16 clean_mixed numbers serve only to
compare with the three earlier replicates of the p8 rewriter (3d60dfdd7).

### Comparison with A0

Each arm is paired with A0 task by task, rep 1 with rep 1 and so on, with an
exact sign test on clean_mixed. The difference between A0's own reps is the
noise floor. An arm wins only when the whole pool is measured and every rep is
audited, its clean_mixed lead over A0 is larger than A0's own rep-to-rep
difference, and the paired sign test gives p < 0.05.

### Chain mode

Chain mode takes about 50 tasks, and each task runs its own chain of up to 3
hops. A hop is one rewrite and one k=12 measurement of it on the chain's
student; the next hop goes harder after 12/12, easier after
0/12, and the chain stops at a mixed result. The next hop's traces are the
attempts of the previous hop's measurement (`eval_signal_run.py`, branch
`evolve-second-hop-stage`). The tasks include deflate
(`software-engineering_20260611_205750_010`), Forth
(`software-engineering_20260604_144210_015`), 111e tasks still at 12/12 after
two or more harder rewrites, and the 48 tasks proposed as chain seeds in
`candidates/doc-cases-20261027`, whose labelled failure class is reported
beside each hop; with these the set may exceed 50. Chain mode reports, per hop,
the counts above, plus:

- stubborn rate: tasks whose result did not change over two or more
  consecutive same-direction hops, an easier task staying at 0/12 or a harder
  one at 12/12;
- oscillation rate: harder overshooting to 0/12 and easier bringing it back to
  12/12, or the reverse.

Stubborn tasks stay in training as usual; the chain only reports them.

### Judging agent

A separate agent judges whether a rewrite is worth anything; Yichuan's side
implements it. It does not ask that question directly. From the tested
student's attempts on the original and on the rewrite it answers:

- what new judgement the rewriter claims the change requires;
- whether the failed attempts fail on exactly that judgement;
- whether most successful attempts actually make that judgement;
- whether failures submitted a wrong answer or ran out of time or context;
- whether the rewrite leaks the answer, only lengthens the work, lets the
  difficulty be bypassed, requires something the instruction does not state,
  or breaks the environment.

For easier rewrites it also reports the kind of help: repairs the task,
supplies missing information, gives the method, or does the core work for the
student. Its output is one verdict per rewrite with the reason and quoted
evidence.

It enters the report only after its agreement is measured on a calibration set,
above all how often it passes a rewrite that is not worth anything. The
calibration set, with the verdict each should get, uses rewrites named in
yichuan-w/torchtitan#319 and #324. The TLS, LZ4, HAProxy, Bubblewrap and
encoding rewrites are from run 1030c, whose samples are on branch
`yichuan/evolve-samples-1030c`:

- `task_005436_d605aece`, the TLS rewrite (r0 to r1, harder): not worth it. It
  claims the student must make a self-signed certificate pass default hostname
  verification for `localhost`; most failures did the check the instruction
  asks for (`curl --cacert`), which passes because OpenSSL falls back to the
  CN, and failed under the grader's pinned urllib3 2.0.0, which does not fall
  back; the instruction does not state the pin.
- `software-engineering_20260508_045740_001`, the LZ4 rewrite (harder): not
  worth it. It claims the student must tell link-time library lookup from
  the runtime loader's, so that the installed extension loads the LZ4 1.10.0
  the task provides and not the system 1.9.4; most failures already loaded
  1.10.0 at runtime, most of them from a copy they compiled themselves, and
  failed on not using the copy provided under `/opt`.
- `task_001320` rev2 and rev3: leak.
- `system-administration_20260509_065529_005`, the HAProxy rewrite (r1 to r2,
  harder): not worth it; the rewriter meant to keep a check on the running
  503 response, and the blind verifier narrowed it.
- the rewrites of arm A4 (`EVOLVE_HARDER_WORK` alone) on the 122B harder slice
  whose failures hit the time limit, three of them (mathematics,
  model-training, sci_154924): lengthened work.
- the 24 rewrites still at 12/12 that Yichuan read in #319, listed with their
  rewrite directories there (comment 6671575981): not worth it.

Cases that should be judged worth it come from 13 candidates: the 11
audit-clean rewrites at 3 to 8 of 12 in #319, the Bubblewrap rewrite
(`task_000575_10c82d13`) and the encoding rewrite (`task_002307_b945c419`)
from #324 item 5, each with evidence on the questions above; Zhifei's side
assigns their labels. If fewer than 10 of them are worth it, more come from
the first rep over this standard's pool: audit-clean rewrites whose k=12
measurement is mixed.

### Arms under this standard

A0, `EVOLVE_HARDER_TARGET`, `EVOLVE_PEER_CONTEXT`, AQ2 (`EVOLVE_HARDER_QUIET`),
AN (`EVOLVE_NO_WORKED_ANSWERS`) and ASR (`EVOLVE_HARDER_STATE_RESULT`) are all
concluded again under this standard. Results measured before it are no longer
grounds for a decision.

#### AK: `EVOLVE_EASIER_KEEP_CHECKS`

Branch `evolve-easier-keep-checks` (b6675e62, cut from 1ee4fd7c). The setting
rewrites three sentences the blind verifier of an easier rewrite is given;
harder rewrites get the same prompts as A0. The defect it targets: in 1030c, 35
of 2,407 accepted rewrites (28 of them easier) had a verifier that accepts a
wrong answer the instruction still rules out (#324, `full_regress/` on
`yichuan/evolve-samples-1030c`).

- Settings: A0's, that is `EVOLVE_HARDER_QUIET=0 EVOLVE_PEER_CONTEXT=0`, plus
  `EVOLVE_EASIER_KEEP_CHECKS=1`. A0 runs with the same two set to 0 and the
  new one unset.
- Pool: the easier tasks of the pool, both students, 3 reps. The harder half
  is not rewritten again; its prompts are A0's.
- Regression slice: the input revisions of the 28 confirmed easier regressions
  of 1030c, each rewritten easier once per rep by A0 and by AK from the same
  1030c signal traces. This slice is audited only, not measured at k=12,
  because those traces come from the 1030c student; like the 7-task leak
  slice for AN, it only counts the defect.
- Counted, per arm and rep: clean_mixed on the easier tasks (the decision
  number, sign test against A0 as above); grader regressions, using #324's
  rule: an Opus judge, blind to the arm, must write a concrete wrong solution
  that the input revision's verifier rejects, the rewrite's verifier accepts,
  and the rewrite's instruction does not permit. Candidates come from #324's
  extractor (deleted test functions, deleted assertions, deleted shell check
  lines). The count is also split into supplied work, anti-shortcut checks
  and reported defects, read from `run/verifier-changes.md`. Also counted:
  LEAKY and UNFAIR from the standard audit, easier rewrites still at 0/12, and
  the acceptance rate. Keeping more inherited checks can raise UNFAIR or 0/12,
  so AK does not win if either rises past A0's rep-to-rep difference.
- Expected size: at 1030c's easier rate (28 of 1,378, 2%), the pool alone
  yields a handful of regressions per arm, too few for a sign test. The slice
  carries the regression comparison and clean_mixed carries the decision.

## The task set

`data_evolve_bench.json` is a `data_release.py` config over
`Fzz1/tb-training-mix-agentpick` at 7dee55fe (the revision
`data_agentpick_fast_audited.json` pins). It selects 400 rows:

| source | rows | excluded first |
|---|---|---|
| clean_fast | 100 of 336 | the 4 rows the 2026-10-24 audit found broken |
| calibforge_clean_fast | 100 of 395 | the 2 rows the audit found broken |
| repair | all 171 | none |
| calibforge_repair | 29 of 176 | the 49 TB2.1 siblings (`cf_tb21_sibling` in `metadata/split_assignment_calibforge.tsv`) and the 6 rows whose verifier needs a fixture the loader refuses |

So 200 rows are clean and 200 are repair. The release's `config.json`, with
every exclude list, is copied into the frozen set's manifest.

- All of repair, because those are the rows with known defects.
  Each arm then also reports whether it repairs them. The 76 repair rows
  (and 1 calibforge_repair row) that the audit excluded from the training
  release stay in on purpose. They are the rows whose defects are known
  independently of the rewriter, and `data_evolve_bench_defects.tsv` lists
  them as `audit_20261024_broken`, with four classes checked by hand. For
  the same reason this release is not training data as it stands.
- `skip_unpackable` is off: the fixture rows are excluded by id, so a new
  unpackable row fails the build instead of disappearing.

A set holds only zero-signal rows. A row solved on every graded attempt of
its before-measurement gets a harder signal, one solved on none an easier
signal; a mixed row already carries gradient and is an ordinary training
task, so it is not in the set. `freeze` takes each row's before-measurement
from existing results, the first source that measured it, in the order
given: an eval (`--eval`, the task's attempts) or a training run
(`--first-group`, the task's first rev-0 group, banded on its first k
graded attempts, k the `--recipe`'s attempts per task, so the band means what
an eval's does; the
whole group is staged as the signal's attempts). A row is dropped, with the
reason in the manifest's `dropped`, when it is mixed, ungraded, not
measured, measured on a `test_sh` other than the release row's, or when its
package grader is not the row's `test_sh` (the loop would rewrite it from a
grader the student never met). The manifest's `measured` names each kept
row's source, group, commit and k, and the tally reads the before-bands from
it. All sources of one set must have run one student harness, which becomes
the set's: evals of its rewrites have to use that harness too.

## Sets already frozen

Four sets are frozen on della under
`/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolve-bench/sets/`:

- `agentpick400-measured`: the zero-signal rows of the 400-row release
  above, 234 of them (191 easier, 43 harder), all measured under Terminus.
  The 163 repair rows come from the base eval of the repair342 rows
  (`evals/20261024-100842Z--base-step0-repair342-20261024`, commit
  ba31d0ca, k=5). The 71 clean rows come from the first groups of the
  September agentpick training run (`runs/tmax-9b--20260927-084446Z` of the
  `yichuan` profile's root, commit 9403e73c), so their before-bands come
  from older code than the evals of their rewrites. Both sources ran the
  `legacy-2400` recipe, so the bands in its manifest are legacy bands: an
  eval of its rewrites under `train-2026-10-25` pairs with a re-measurement
  of its original rows under that recipe (`tally --before`), never with them.

  | corpus | easier | harder | dropped |
  |---|---|---|---|
  | repair (171) | 133 | 11 | mixed 23, ungraded 3, package not the row 1 |
  | calibforge_repair (29) | 18 | 1 | mixed 10 |
  | clean_fast (100) | 7 | 28 | mixed 39, measured on another test_sh 16, not measured 9, ungraded 1 |
  | calibforge_clean_fast (100) | 33 | 3 | not measured 50, mixed 14 |

- `agentpick400-train1025`: the 234 rows of `agentpick400-measured`
  measured again under `train-2026-10-25` (k=16) and frozen from those evals.
  191 stay zero-signal (166 easier, 25 harder); 42 came out mixed and 1
  ungraded, and are dropped; the release's other 166 rows were not measured.
  Its manifest names the evals. Their before-bands are the ones an eval of a
  rewrite of these rows pairs with.

  | corpus | easier | harder | dropped |
  |---|---|---|---|
  | repair | 121 | 4 | mixed 19 |
  | calibforge_repair | 15 | 0 | mixed 4 |
  | clean_fast | 4 | 19 | mixed 11, ungraded 1 |
  | calibforge_clean_fast | 26 | 2 | mixed 8 |

- `r100`: 100 harder signals from `runs/tmax-9b--20260911-000700Z`, each
  task's first training group, all solved (`offline_select.py`'s pick); the
  selection the r100 rewriter arms used.
- `repair342`: the zero-signal repair and calibforge_repair rows of the
  2026-10-24 agentpick release (`data/prepared/releases/c02f5864…`), from
  its base-student eval: 237 easier and 19 harder, frozen before the
  manifest listed dropped rows.

## Running an arm

```bash
EVO=<checkout>/torchtitan/experiments/rl/examples/tmax/evolution
PY=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
BENCH=/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolve-bench

# once per set: build the release, freeze it from existing measurements of its rows
$PY $EVO/data_release.py build --config $EVO/data_evolve_bench.json --out <data>/prepared
$PY $EVO/evolve_bench.py freeze --bench $BENCH --name agentpick400-measured --release <release> \
    --eval <evals of the rows> --first-group <training runs that sampled them>

# choose one per arm: automatic scoring starts the drive and its wave watcher as systemd user units on the login node
$PY $EVO/evolve_bench.py run --bench $BENCH --set agentpick400-measured --arm arms/sol-medium.json \
    --before $BENCH/evals/<stamp>--base-step0-original \
    --every 40 --minutes 60 --eval-host della-tridao --eval-gpus auto
# the watcher writes $BENCH/results/sol-medium.json after the drive and every wave finish and tally completes

# or choose the manual workflow when automatic scoring is not wanted
$PY $EVO/evolve_bench.py run --bench $BENCH --set agentpick400-measured --arm arms/sol-medium.json \
    --before $BENCH/evals/<stamp>--base-step0-original --no-auto-eval
# manual path: cut the final evalset, score it, and tally it
$PY $EVO/evolve_bench.py evalset --root $BENCH/arms/agentpick400-measured--sol-medium
TRL_PROFILE=andy $PY $EVO/evolve_bench.py eval --harness terminus --gpus 4,4 \
    --evalset $BENCH/arms/agentpick400-measured--sol-medium/data/evalsets/bench-accepted.jsonl
# for a set measured under another recipe, score its original rows under the
# train-2026-10-25 recipe and pass that eval to --before
TRL_PROFILE=andy $PY $EVO/evolve_bench.py eval --harness terminus --gpus 5,5 \
    --evalset $BENCH/sets/agentpick400-measured/original.jsonl --base $BENCH
$PY $EVO/evolve_bench.py tally --root $BENCH/arms/agentpick400-measured--sol-medium \
    --eval $BENCH/arms/agentpick400-measured--sol-medium/evals/<stamp>--base-step0-bench-accepted \
    --before $BENCH/evals/<stamp>--base-step0-original \
    --labels $EVO/data_evolve_bench_defects.tsv \
    --out $BENCH/results/sol-medium.json
$PY $EVO/evolve_bench.py compare sol=$BENCH/results/sol-medium.json opus=$BENCH/results/opus-medium.json \
    --out $BENCH/results/sol-vs-opus
```

### Scoring rewrites while the arm runs

The default `run` starts a second systemd user unit,
`bench-watch-<set>--<arm>`, beside the offline drive. The watcher repeats
these steps until the arm is complete:

1. It cuts the next accepted-revision wave into
   `data/evalsets/bench-wave-NN.jsonl` when `--every` rows accumulate, when
   `--minutes` elapse after the previous wave, or when the drive finishes.
2. It assigns one eval to each card in `--eval-gpus`. With `--eval-gpus auto`,
   it asks `~/.claude/skills/gpu-pool/tool/pool.py pick` for a free card. Each
   eval starts through `pool.py label` and `pool.py run <eval-host> <card>`;
   multiple supplied cards let it evaluate multiple waves in parallel;
   the pool's della target follows the existing `della-ts` to
   `della-tridao` hop.
3. It records the eval marker and launch directory under the arm's `logs/`
   and `watcher.json`. A restart discovers completed `launch.json` records and
   does not rescore them.
4. If an eval exits without its index, it runs `salvage`, keeps the salvaged
   index, and queues the `.rest.jsonl` rows for retry. `tally` merges the
   salvaged and retried evals. The existing `rest` and `salvage` layouts
   therefore remain resumable.
5. After the drive finishes and every wave is complete, it runs `tally` with
   the evals and the paths recorded by `run --before`, then writes
   `results/<arm>.json`.

The watcher logs each check, wave, card, recovery, and tally step with a UTC
timestamp in `arms/<set>--<arm>/logs/evolve_bench-watcher--*.log`. Wave evals populate tally
data only; they do not affect acceptance or make the drive wait.

```bash
# manual mode only: run from the machine that launches GPU work
$PY $EVO/evolve_bench.py wave --root $BENCH/arms/agentpick400-measured--sol-medium --every 40 --minutes 60
# for each WAVE it prints: eval that wave's evalset, as above
```

### Package-install metrics in `tally`

For each accepted rewrite, `rewrite_stats.<direction>.package_install` lists
the task ids whose instruction diff adds install or obtain guidance and the
task ids whose Dockerfile diff removes an installed package or changes its
version pin. The classifier reads only added instruction lines and the
Dockerfile diff.

When `tally` receives both accepted-row evals and the original-row evals from
`--before`, `install_metrics` reports before, after, and after-minus-before
values. It reports the share of attempts with an install command, the median
number of install commands per attempt, the share of attempts whose output
contains `No space left on device` or `Errno 28`, and pass rates for attempts
with and without installs.
The same table is grouped by rewrite direction and by whether the rewrite
added install guidance. Commands come from each student's rollout record:
`keystrokes`, or the vanillux tool command extracted by `rollout_record.py`.
Harness-issued tmux commands do not enter the metric.

When a set has no measurement yet and needs a base eval, `pipeline` also
overlaps rewriting with that eval:

```bash
$PY $EVO/evolve_bench.py pipeline --bench $BENCH --name <set> \
    --eval $BENCH/evals/<stamp>--base-step0-mix --release <release> --arm arms/sol-medium.json
```

It starts two systemd user units. The stager (`stage`) reads the base eval's
per-attempt records as they land and stages a task's signal once all k of its
attempts are graded, growing the stream's `selected.json`. The drive
(`offline_drive.sh` with `STREAM_DONE`) runs rounds over whatever has been
staged and waits, rather than ending, while the stream is still open. When the
eval writes its own `index.json`, the stager freezes the set from that index,
checks every streamed decision against it (`stream.json`'s `final_check`), and
writes the done marker that lets the drive finish.

An arm is one JSON file with exactly these keys:

```json
{"name": "sol-medium", "profile": "andy", "commit": "1f3a1c039",
 "bin": "/scratch/gpfs/TRIDAO/al9080/terminal-rl/bin", "workers": 64,
 "env": {"SYNTH_MODEL": "gpt-5.6-sol", "SYNTH_EFFORT": "medium", "EVOLVE_AGENT_TIMEOUT": "7200",
         "SWE_EVOLVE_SIMPLIFY": "1",
         "LOOP_ENV": "/scratch/gpfs/TRIDAO/al9080/terminal-rl/.codex-forward.env"}}
```

`run` refuses an arm whose profile's checkout is not at `commit` or has
uncommitted changes, and an `env` that sets anything the bench pins or
derives. It records the arm, the checkout's full commit, `codex --version`
and the set manifest's sha256 in the root's `bench_arm.json`. Running the
same arm again resumes it; a changed config needs a new name.

## Pinned values and their reasons

The student eval (`eval`, through `della/tb2_eval_local.sh`) runs one named
recipe, `train-2026-10-25`, which `launch.json` records as
`SWE_BENCH_RECIPE` with every value it pins. It rolls a validation attempt
out the way a training rollout is, so a task's band says whether a training
group on it carries gradient. The values come from `runbook/rltrain.env` and
the training defaults of `config_registry.py` and Terminus; each `SWE_EVAL_*`
switches a validation-only behaviour back to training's.

| pin | value | as in training |
|---|---|---|
| attempts per task | `SWE_TB2_VAL_K=16` | `SWE_GROUP_SIZE=16` |
| turns | `TMAX_TERMINUS_MAX_TURNS=120` | same |
| context | `SWE_TMAX_CONTEXT=81920`, agent stops at `SWE_MAX_CONTEXT_LEN=79872` | same: 2048 tokens of headroom below vLLM's `max_model_len` |
| per-turn generation cap | `TMAX_TURN_MAX_TOKENS=16384` | same |
| wall clock per attempt | 7200 s (`SWE_EVAL_TIME_BUDGET_SEC`, floor `SWE_EVAL_BUDGET_FLOOR_SEC`) | `SWE_TIME_BUDGET_SEC` / `SWE_AGENT_TIMEOUT_FLOOR_SEC` = 7200 |
| validation-only caps | `SWE_EVAL_MAX_TURNS`, `SWE_EVAL_MAX_CONTEXT_LEN`, `SWE_EVAL_*_BUDGET_CAP_SEC` = 0 | training has none |
| context notice | `SWE_EVAL_CONTEXT_NOTICE=1`, style `checked` at 24576 / 8192 tokens left, status line each turn | training always shows it; validation hides it by default |
| an attempt that never submitted | scores 0 (`SWE_EVAL_GRADE_UNSUBMITTED=0`); no shadow verdict enters the score | training scores it 0; validation would grade it at the verifier |
| opening turn that breaks the format | ends the attempt at 0 (`SWE_EVAL_STRICT_OPENING=1`) | `SWE_STRICT_OPENING` defaults to 1 |
| tmux | rows load as they are (`SWE_TB2_BAKE_TMUX=0`) | the training dataset does not bake tmux in |
| sampling | temperature 1.0, top_p 1.0 | same |
| concurrency | `SWE_ROLLOUT_CONCURRENCY` = 128 per generator, 1 generator | not training's: above what the generator decodes at once, attempts wait for a slot while their wall clock runs and read as time-budget failures. The `repair342` base eval at 600 on one generator had 356 of its last 400 attempts end at the budget; at 128 it had 36 time-budget failures over all 1,710 attempts. A card whose KV cache runs fewer sequences at once queues the rest the same way, so `eval --concurrency N` lowers it there (never above 128): on a B300 shared with another job, 1.58M KV tokens ran about 48 sequences with 75 more waiting at 128. Concurrency changes timing, not the task, so `compare` records it and does not refuse a difference |
| harness | `--harness vanillux`, `terminus` or `miniswe`, recorded | a set's signals were measured under one, and `eval` refuses another |
| action cap | `TMAX_CALL_LIMIT=120` | vanillux's cap, whose default is 64; 120 matches the turns Terminus gets |
| per-sandbox fallback | `TT_DAYTONA_*` 1 / 2 / 2 | the fleet default, for rows that declare nothing |
| signals | `SWE_EVOLUTION_SIGNALS=0` | the measurement never reaches a loop |

`legacy-2400` is the recipe every measurement before it ran: the eval path's
own behaviour (no context notice, unsubmitted attempts graded, no strict
opening, tmux baked in), k=5, a 65536 context with the agent stopping at
63488, 2400 s for rows that declare nothing, a 32768-token turn cap. The
`agentpick400-measured`, `repair342` and `r100` sets were measured under it,
and so were both arms' existing results. It is kept so those results can be
read; no new eval runs it.

Any pinned value already set to another value in the caller's environment is
refused, not overridden. `launch.json` records the recipe, the checkout's
commit, the host, the GPU names, the generator count and the evalset's
sha256. `tally` reads them back and refuses to pair a before and an after of
different recipes: an eval of a set measured under `legacy-2400` reports its
transitions only against `--before` evals of the original rows under the same
recipe. `compare` refuses to pair two measurements that differ on the recipe or
on checkpoint, code commit, GPU type, generator count or harness
(`--allow-mismatch` pairs them anyway, marked NOT COMPARABLE).

### One evalset on several cards

`eval --shard i/n` scores rows i, i+n, i+2n, ... of the evalset, written as an
evalset of their own beside it (or under `<base>/evalsets/` when the evalset
sits in a frozen set's directory). Each shard is one eval on one card, all of
one GPU type; `tally --eval` and `--before` take every shard's eval and merge
them, refusing shards that ran different recipes.

An eval stopped before the controller wrote its index (a generator that
thrashed, a lost card) is not rerun whole: `salvage --eval <dir>` keeps every
task whose k attempts all have records as `salvaged-index.json` in the eval
directory, and writes the evalset's other rows as `<evalset>.rest.jsonl`.
Score that with a new eval; `tally` and `freeze` read a salvaged eval's index
where it has none of its own, count only the tasks it kept, and mark it
`salvaged` in their inputs. Every eval merged into one tally must still come
from one code commit.

When fewer cards turn up than an evalset has shards, `rest --evalset <x>
--done <evals>` writes the rows none of the finished evals scored as
`<x>.rest.jsonl`, so what is left runs as one eval and pays one slow tail
instead of one per shard.

The drive (`run`): `TT_DAYTONA_*` at the trainer's fleet default (1 / 2 / 2,
since a row that declares nothing is verified at that size), offline_drive's
own `MAX_ROUNDS=6` and `MAX_ATTEMPTS=2`, and `SWE_POLICY_CANARY_K=0`: the loop
spends no student rollouts on a rewrite.

## Tally output

Infra-failed attempts stay dropped from the existing bands and remain counted
apart. `tally` classifies each one as `agent_caused` or `platform` from the
rollout header and the last recorded student turn. A disk exhaustion at the
agent or verifier stage, a final `pkill`/`kill`/`tmux kill-*`/tmux removal
command, or a recorded control-plane PID signal is `agent_caused`; other
infra failures are `platform`. The JSON field `infra` reports both classes for
the arm, before measurement, and each accepted `harder`/`easier` direction.

The existing bands remain the primary result. `after_agent_caused_as_failures`
and `before_agent_caused_as_failures` show alternate bands that score
`agent_caused` attempts as failures while still dropping `platform` attempts;
`before_after_agent_caused_as_failures` gives the paired alternate transition
table when a before measurement is present. The original `after`, `before`, and
transition fields do not change. A rewrite lost to infrastructure (429s,
provider outages, session timeouts, a stopped loop) counts in neither
acceptance nor per-accepted cost; its spend is reported as `usd_infra`.

Mechanical, computed for every rewrite from the root:

- Three counts (mixed / all solved / none solved, plus ungraded and
  unattempted) with the finish-reason split of all and of failed attempts.
  These come from an eval or from a training run's first group on each new
  revision (`tally --run`), so the same report reads off a live run.
- Band by direction: harder -> mixed, -> none solved (overshoot), ->
  still all solved; easier -> mixed, -> all solved (overshoot), -> still none
  solved.
- Before -> after on the original rows, with exact McNemar on the mixed
  share; arm against arm on the tasks both accepted, and over the whole set
  with an unaccepted task counting as not mixed.
- Where each task ended: accepted, give-up / blocked, each gate
  (`gate:<stage>`, the blind verifier's assertion cap, the semantic probe),
  provider content filter, each infra class.
- Diff statistics of every accepted rewrite against its input revision:
  tests/ size ratio, assertion count delta, instruction word delta,
  Dockerfile changed, a new check that runs the submitted program on inputs
  the grader writes, and the rewrite saying the verifier is unchanged while
  tests/ differs.
- Grader fetches: hosts other than package indexes that a grader clones
  or downloads from, before and after, with which rewrites introduced, kept
  or removed one.
- Cost: list price per session from `offline_cost.py`, per accepted
  rewrite and per mixed task. An arm with sessions of a model `offline_cost`
  has no price for, or a Claude Code arm (no Codex token records), reports
  its cost as unknown.
- Repair rows by known defect: the `verdict` column of each repair source's
  `metadata.parquet` (the HF dataset's judge), a missing reference solution, the defects file, and the grader-fetch check on every row. For
  each class: where the rows ended, the accepted revisions' band, how many
  declared a repair, the fix rate where it is mechanical (a missing solution
  is fixed when the accepted revision ships one; a grader fetch when the
  accepted grader has none), and agreement with hand labels that carry a
  `fixed` column.
- The checked-in `data_evolve_bench_defects.tsv` also carries the 2026-10-26
  audit classes `audit_20261026_exclude`, `audit_20261026_resource`,
  `audit_20261026_never_solved`, and `audit_20261026_near_always`. Rows that
  are not in the current set stay in the file and do not enter a tally. For a
  resource row, the mechanical fix is true only when the accepted row's
  declared `daytona_cpu`, `daytona_mem_gb`, and `daytona_disk_gb` meet every
  numeric `suggested=` value in its note; `repair.<class>.resource_rows`
  records the declared size and whether it is raised for every row.

LLM-judged, optional: `audit-bundle --union` writes blinded bundles of every
task accepted by at least one listed arm (each existing arm revision as V1..Vn
in a per-task random order, diffs against r0, the author's closing summary,
root paths and diff mtimes scrubbed), with `agents/bench_audit.md` as the
auditors' rubric and `agents/bench_audit_verify.md` for one adversarial
verifier per defect finding. `audit-run` invokes `claude -p --bare` against
Amazon Bedrock with a fresh empty home, no settings, skills, plugins, hooks,
memory or MCP servers, the selected rubric as the system prompt, and only
Read, Grep and Glob; it passes only the AWS credentials named in `--env-file`,
resumes completed verdicts, retries failures, and records the model, CLI,
rubric digest, argv, usage and cost. The key goes to a path outside the bundle.
`audit-tally` unblinds verdicts into per-arm label rates, confirmed defects,
defects inside the mixed band (they feed wrong rewards) and $ per clean mixed
task.

## Known failure modes of the mechanical metrics

- The band of one eval is noisy per task (k=16 under `train-2026-10-25`, k=5
  under `legacy-2400`); compare arms paired, on one recipe, and read the
  McNemar p rather than a difference of counts.
- The claimed-unchanged check reads the author's notes and closing message.
  Under the blind verifier (the default) tests/ is written by another
  session, so an author who truthfully left tests/ alone still reads as a
  mismatch.
- The re-run check is a regex over added test lines (a process call plus a
  file write); it misses a re-run through a helper defined elsewhere and
  counts a process call that only inspects the environment.
- The grader-fetch check flags a URL the image redirects to a local mirror
  (`git config url.<local>.insteadOf`) as a dependency, and misses a URL built
  at run time.
- Assertion counts are `assert*` tokens: a shell grader's checks are not
  counted.
- The auditors and verifiers are models: rates move with the auditor model and
  are comparable only between arms audited in one batch with one rubric.
