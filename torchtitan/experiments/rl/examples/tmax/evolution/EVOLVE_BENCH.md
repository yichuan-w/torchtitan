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
  `legacy-2400` recipe, so the bands in its manifest are legacy bands: an
  eval of its rewrites under `train-2026-10-25` pairs with a re-measurement
  of its original rows under that recipe (`tally --before`), never with them.

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
# a set measured under another recipe: its original rows again, under this one
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
