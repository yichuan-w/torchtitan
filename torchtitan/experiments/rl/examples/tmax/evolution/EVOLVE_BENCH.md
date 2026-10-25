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
(`--first-group`, the task's first rev-0 group, banded on its first five
graded attempts so the band means what a five-attempt eval's does; the
whole group is staged as the signal's attempts). A row is dropped, with the
reason in the manifest's `dropped`, when it is mixed, ungraded, not
measured, measured on a `test_sh` other than the release row's, or when its
package grader is not the row's `test_sh` (the loop would rewrite it from a
grader the student never met). The manifest's `measured` names each kept
row's source, group, commit and k, and the tally reads the before-bands from
it. All sources of one set must have run one student harness, which becomes
the set's: evals of its rewrites have to use that harness too.

## Sets already frozen

Three sets are frozen on della under
`/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolve-bench/sets/`:

- `agentpick400-measured`: the zero-signal rows of the 400-row release
  above, 234 of them (191 easier, 43 harder), all measured under Terminus.
  The 163 repair rows come from the base eval of the repair342 rows
  (`evals/20261024-100842Z--base-step0-repair342-20261024`, commit
  ba31d0ca, k=5). The 71 clean rows come from the first groups of the
  September agentpick training run (`runs/tmax-9b--20260927-084446Z` of the
  `yichuan` profile's root, commit 9403e73c), so their before-bands come
  from older code than the evals of their rewrites. Both sources ran the
  `legacy-2400` recipe, so the set is a legacy-recipe set: its rewrites are
  evaluated under `legacy-2400`, and its results compare only with other
  sets measured that way.

  | corpus | easier | harder | dropped |
  |---|---|---|---|
  | repair (171) | 133 | 11 | mixed 23, ungraded 3, package not the row 1 |
  | calibforge_repair (29) | 18 | 1 | mixed 10 |
  | clean_fast (100) | 7 | 28 | mixed 39, measured on another test_sh 16, not measured 9, ungraded 1 |
  | calibforge_clean_fast (100) | 33 | 3 | not measured 50, mixed 14 |

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

# per arm: a root from the set and offline_drive.sh as a user unit (login node)
$PY $EVO/evolve_bench.py run --bench $BENCH --set agentpick400-measured --arm arms/sol-medium.json
# when the drive's log ends in "finished"
$PY $EVO/evolve_bench.py evalset --root $BENCH/arms/agentpick400-measured--sol-medium
TRL_PROFILE=andy $PY $EVO/evolve_bench.py eval --harness terminus --gpus 4,4 \
    --evalset $BENCH/arms/agentpick400-measured--sol-medium/data/evalsets/bench-accepted.jsonl
$PY $EVO/evolve_bench.py tally --root $BENCH/arms/agentpick400-measured--sol-medium \
    --eval $BENCH/arms/agentpick400-measured--sol-medium/evals/<stamp>--base-step0-bench-accepted \
    --labels $EVO/data_evolve_bench_defects.tsv \
    --out $BENCH/results/sol-medium.json
$PY $EVO/evolve_bench.py compare sol=$BENCH/results/sol-medium.json opus=$BENCH/results/opus-medium.json \
    --out $BENCH/results/sol-vs-opus
```

### Scoring rewrites while the arm runs

`wave` scores accepted revisions while the drive is still rewriting: it cuts
the revisions no earlier wave holds into `data/evalsets/bench-wave-NN.jsonl`
once enough have accumulated, enough time has passed since the previous
wave, or the drive is done, and each wave is evaluated like any evalset. The
tally takes every wave's eval as `--eval`. Wave evals feed only the tally: no
signal is made from them and no acceptance waits on them.

```bash
# every few minutes, from the machine that launches GPU work
$PY $EVO/evolve_bench.py wave --root $BENCH/arms/agentpick400-measured--sol-medium --every 40 --minutes 60
# for each WAVE it prints: eval that wave's evalset, as above
```

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

The student eval (`eval`, through `della/tb2_eval_local.sh`) runs one of two
named recipes, which `launch.json` records as `SWE_BENCH_RECIPE`. A set
records the recipe its before-measurements were taken under
(`student_recipe` in its manifest; a set frozen before manifests recorded one
counts as legacy), and every eval of its rewrites runs that recipe, so before
and after stay comparable. `compare` refuses to pair the two recipes.

| | `training-2026-10` (default) | `legacy-2400` |
|---|---|---|
| turns | 120 | 120 |
| context: generator `max_model_len` (`SWE_TMAX_CONTEXT`) | 81920 | 65536 (the code's default) |
| context: the agent stops at (`SWE_MAX_CONTEXT_LEN`) | 79872 | 63488 |
| budget of a row that declares none (`SWE_EVAL_TIME_BUDGET_SEC`) | 7200 s | 2400 s |
| budget of a row that declares one | max(declared, 7200 s) | max(declared, 7200 s) |
| sets | new sets | `agentpick400-measured`, `repair342`, `r100` |

`training-2026-10` holds the student to the caps training runs at now: 120
turns, an 81920-token context, two hours per attempt. The agent stops 2048
tokens short of the generator's `max_model_len` (config_registry's headroom),
so a long episode ends before vLLM would reject it. `legacy-2400` is what the
repair342 and r100 evals ran; it stays for the sets measured under it, whose
before-measurements are not taken again.

Both recipes share the rest:

| pin | value | why |
|---|---|---|
| attempts per task | `SWE_TB2_VAL_K=5` | three counts need a spread; the `r100` and `repair342` arms were scored at k=5 |
| sampling | temperature 1.0, top_p 1.0 | `tb2_eval_local.sh`'s defaults, which the `repair342` base eval ran at; the r100 evals of earlier arms ran 0.8 / 0.95 and do not compare with bench evals |
| concurrency | `SWE_ROLLOUT_CONCURRENCY` = 128 per generator, 1 generator | above what the generator decodes at once, attempts wait for a slot while their wall clock runs and read as time-budget failures: the `repair342` base eval at 600 on one generator had 356 of its last 400 attempts end at the budget, and at 128 it had 36 time-budget failures over all 1,710 attempts. 128 is the measured value, under `legacy-2400`; 256 (`SWE_MAX_NUM_SEQS`) has not been measured, and neither has 128 at the longer context |
| harness | `--harness vanillux`, `terminus` or `miniswe`, recorded | the student's harness changes what it can do; a set's signals were measured under one, and `eval` refuses another |
| action cap | `TMAX_CALL_LIMIT=120` | vanillux's action cap, whose default is 64; at 120 it matches the 120 turns `TMAX_TERMINUS_MAX_TURNS` gives Terminus |
| per-sandbox fallback | `TT_DAYTONA_*` 1 / 2 / 2 | applies only to rows that declare nothing |
| signals | `SWE_EVOLUTION_SIGNALS=0` | the measurement never reaches a loop |

Any of these already set to another value in the caller's environment is
refused, not overridden. `launch.json` records them, with the checkout's
commit, the GPU names, the generator count and the evalset's sha256. `tally`
reads them back, and `compare` refuses to pair two measurements that differ
on any of them: checkpoint, code commit, GPU type, generator count, harness or
a pinned value (`--allow-mismatch` pairs them anyway, marked NOT COMPARABLE).

The drive (`run`): `TT_DAYTONA_*` at the trainer's fleet default (1 / 2 / 2,
since a row that declares nothing is verified at that size), offline_drive's
own `MAX_ROUNDS=6` and `MAX_ATTEMPTS=2`, and `SWE_POLICY_CANARY_K=0`: the loop
spends no student rollouts on a rewrite.

## Tally output

Infra-failed attempts are dropped from every band and counted apart. A
rewrite lost to infrastructure (429s, provider outages, session timeouts, a
stopped loop) counts in neither acceptance nor per-accepted cost; its spend is
reported as `usd_infra`.

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

LLM-judged, optional: `audit-bundle` writes blinded bundles of the tasks every
listed arm accepted (each arm's revision as V1..Vn in a per-task random order,
diffs against r0, the author's closing summary, root paths and diff mtimes
scrubbed), with `agents/bench_audit.md` as the auditors' rubric (EFFECTIVE,
WEAK, LEAKY, UNFAIR, BROKEN, GRADER-TIGHTENING-ONLY,
UNSTATED-ENVIRONMENT-TRAP) and `agents/bench_audit_verify.md` for one
adversarial verifier per finding. The key goes to a path outside the bundle.
`audit-tally` unblinds verdicts into per-arm label rates, confirmed defects,
defects inside the mixed band (they feed wrong rewards) and $ per clean mixed
task.

## Known failure modes of the mechanical metrics

- The band of one k=5 eval is noisy per task; compare arms paired, on one
  recipe, and read the McNemar p rather than a difference of counts.
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
