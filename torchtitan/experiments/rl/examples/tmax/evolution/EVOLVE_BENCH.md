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

- 400 rows is one base-student eval of 2,000 attempts. The base student left
  257 of the 342 repair rows of the `repair342` set all-solved or none-solved;
  clean rows carry signal more often, so an arm rewrites fewer than 400 tasks,
  still several times the 100 of `r100`.
- All of repair, because those are the rows with known defects.
  Each arm then also reports whether it repairs them. The 76 repair rows
  (and 1 calibforge_repair row) that the audit excluded from the training
  release stay in on purpose. They are the rows whose defects are known
  independently of the rewriter, and `data_evolve_bench_defects.tsv` lists
  them as `audit_20261024_broken`, with four classes checked by hand.
- `skip_unpackable` is off: the fixture rows are excluded by id, so a new
  unpackable row fails the build instead of disappearing.

First-round signals come from a base-student eval of the whole set. `freeze --eval` makes a harder signal for every task
solved on every graded attempt and an easier one for every task solved on
none. Mixed tasks need no rewrite; they are recorded as usable as they are and
counted in the set's yield (mixed as-is plus rewritten to mixed, over the
set's rows). A task whose package grader differs from its row's
`test_sh` is left out and listed (`package_not_row`), since the loop would
rewrite it from a grader the student never met.

## Sets already frozen

Two sets are frozen on della under
`/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolve-bench/sets/`, from runs that
predate the bench:

- `r100`: 100 harder signals from `runs/tmax-9b--20260911-000700Z`, each
  task's first training group, all solved (`offline_select.py`'s pick); the
  selection the r100 rewriter arms used.
- `repair342`: the 342 repair and calibforge_repair rows of the 2026-10-24
  agentpick release (`data/prepared/releases/c02f5864…`), with signals made
  by `freeze --eval` from its base-student eval: 237 easier, 19 harder, 81
  mixed as-is, 4 ungraded, and 1 left out because its package grader is not
  its row's.

## Running an arm

```bash
EVO=<checkout>/torchtitan/experiments/rl/examples/tmax/evolution
PY=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
BENCH=/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolve-bench

# once per set: build the release, score every row with the base student, freeze
$PY $EVO/data_release.py build --config $EVO/data_evolve_bench.json --out <data>/prepared
TRL_PROFILE=andy $PY $EVO/evolve_bench.py eval --evalset <release>/mix.jsonl --harness terminus \
    --gpus 4,4 --base $BENCH                       # through gpu-pool, on della-tridao
$PY $EVO/evolve_bench.py freeze --bench $BENCH --name agentpick400 \
    --eval $BENCH/evals/<stamp>--base-step0-mix --release <release>

# per arm: a root from the set and offline_drive.sh as a user unit (login node)
$PY $EVO/evolve_bench.py run --bench $BENCH --set agentpick400 --arm arms/sol-medium.json
# when the drive's log ends in "finished"
$PY $EVO/evolve_bench.py evalset --root $BENCH/arms/agentpick400--sol-medium
TRL_PROFILE=andy $PY $EVO/evolve_bench.py eval --harness terminus --gpus 4,4 \
    --evalset $BENCH/arms/agentpick400--sol-medium/data/evalsets/bench-accepted.jsonl
$PY $EVO/evolve_bench.py tally --root $BENCH/arms/agentpick400--sol-medium \
    --eval $BENCH/arms/agentpick400--sol-medium/evals/<stamp>--base-step0-bench-accepted \
    --before $BENCH/evals/<stamp>--base-step0-mix --labels $EVO/data_evolve_bench_defects.tsv \
    --out $BENCH/results/sol-medium.json
$PY $EVO/evolve_bench.py compare sol=$BENCH/results/sol-medium.json opus=$BENCH/results/opus-medium.json \
    --out $BENCH/results/sol-vs-opus
```

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

The student eval (`eval`, through `della/tb2_eval_local.sh`):

| pin | value | why |
|---|---|---|
| attempts per task | `SWE_TB2_VAL_K=5` | three counts need a spread; the `r100` and `repair342` arms were scored at k=5 |
| sampling | temperature 1.0, top_p 1.0 | `tb2_eval_local.sh`'s defaults, which the `repair342` base eval ran at; the r100 evals of earlier arms ran 0.8 / 0.95 and do not compare with bench evals |
| budget of a row that declares none | `SWE_EVAL_TIME_BUDGET_SEC=2400` | the workspace's fixed rollout budget |
| budget of a row that declares one | max(declared, `SWE_EVAL_BUDGET_FLOOR_SEC=7200`) | the floor the `r100` and `repair342` evals used; the eval config reads these two, not `SWE_TIME_BUDGET_SEC` |
| concurrency | `SWE_ROLLOUT_CONCURRENCY` = `SWE_MAX_NUM_SEQS` (256) x 1 generator | past it, attempts wait for a decode slot while their wall clock runs and read as time-budget failures: one eval run at a concurrency of 445 on one 256-sequence generator had 356 of its last 400 attempts end at the budget |
| harness | `--harness terminus` or `miniswe`, recorded | the student's harness changes what it can do; a set's signals were measured under one, and `eval` refuses another |
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
