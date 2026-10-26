# Overfit bench: does the rewriter bring the policy to solve the task?

One job trains the policy on one benchmark task and nothing else, for a fixed
number of steps. The rewriter may change what the policy trains on at any time;
the readout is always the original task, graded by its own unchanged verifier.
The policy, the recipe and the readout are the same for every job, so what
differs between two runs of a task is the rewriter, and that is what is
measured. Learning the task by heart counts: the question is only whether the
original task gets solved.

Tasks: every Terminal-Bench 2.1 task (89) and every Terminal-Bench 4 task (66).

## What the bench fixes

- **Policy and recipe**: `recipe.env` (group size 12, LR 3e-6, 200 steps of
  one group each, 8 groups in flight, groups up to 8 steps old still trained).
  Hardware placement is the runner's.
- **A training attempt** has the main training run's caps: 120 Terminus turns
  (`SWE_BENCH_MAX_TURNS`), 2 h of wall clock (`SWE_BENCH_TRAIN_BUDGET_SEC`)
  and an 80K context (`SWE_TMAX_CONTEXT=81920`, agent budget 79872), the same
  for every task whatever it declares; no budget floor applies. Grading keeps
  the task's own verifier timeout. Jobs before 2026-10-25 trained at 60 turns
  with no wall clock and the 63488-token context `runbook/rltrain.env` fills in.
- **Sandbox**: every sandbox, training and readout, and a separate verifier's,
  gets 4 vCPU (`SWE_BENCH_SANDBOX_CPU`), whatever the row declares; memory and
  disk are the row's, clamped to Daytona's per-sandbox caps (8 GiB, 10 GiB;
  with a GPU 192 GiB, 512 GiB). A GPU task that declares 16 vCPU therefore
  runs on 4.
- **Start**: the job's training mix is the original task alone (mix v1),
  unless the rewriter seeds the pool before training starts (see Running a job).
- **Readout**: the original task row, held in `data/evalsets/original.jsonl`
  and never served from the mix, 16 attempts every 5 steps. A readout attempt
  has the TB2.1 eval's budget, not training's (`SWE_BENCH_READOUT_EVAL_BUDGET`):
  300 turns, 5400 s whatever the task declares (`SWE_AGENT_TIMEOUT_FLOOR_SEC`
  and `SWE_EVAL_AGENT_BUDGET_CAP_SEC` both 5400), a 160K context and no
  context notice, as the current TB2.1 eval runs (run 1022's 160K validation
  context; the RUNBOOK's 300-turn cap for a 160K eval). An attempt that ends without submitting
  is graded and scored at the verifier's verdict, as Harbor does
  (`SWE_EVAL_GRADE_UNSUBMITTED`); `validation/strict_reward_mean` keeps the
  submit-only score. Jobs before 2026-10-25 read the original under the
  training budget (60 turns, submit-only).
  Reported as
  pass@5 per validation, the first step with any pass, and the first step from
  which pass@5 stays at or above 0.5 for three validations in a row.
- **Mix format**: a published mix whose rows the trainer cannot parse is
  ignored and training keeps the previous version. A row whose environment
  fails at rollout time is unscored (excluded from the advantage), and
  training continues.

Everything else belongs to the rewriter.

## What the rewriter decides

When to rewrite, which versions the pool holds and at what weight, whether
the original stays in, which version each group trains on, what a rewritten
task contains, which model and prompts produce it, and whether and how it is
checked before publishing. There is no trigger from the bench: the trainer's
`signals/` (written for groups whose solved fraction crosses a threshold) and
its events exist for the rewriter and may be ignored.

Two things it may not do, whatever else it decides:

- **Read the readout**: `data/evalsets/`, `runs/*/validation_rollouts/` and
  `runs/*/trainer/validation_traces/` are the measurement, not an input.
- **Run extra student rollouts**: the policy is sampled only by training (and
  the readout). A rewriter learns how the policy does from the training
  attempts the bench already records, not by running the policy itself; the
  policy canary controller is refused in bench mode for this reason.

Training never waits for the rewriter. A draw asks it at most 1 s for a
version and otherwise draws from the pool; a rewrite in progress, a slow hook
or a dead rewriter changes what is drawn, never when.

## Interface

What training draws from is a **version pool**. A version is one task row,
identified by its content sha (the row's `content_revision`, also the
lineage's `sample_revision`; `task@r<rev>` is only a label), with a weight, a
parent version, when it was added, and whether it is active. Each group draws
one active version, by default with probability proportional to its weight.
The pool lives in the controller (`scheduler.py`), records every change in
`runs/<run>/scheduler/pool.jsonl`, keeps its whole state in
`data/mix/pool.json`, and republishes its active rows as the next mix version
whenever the active set changes, so `data/mix/` always holds what training
draws from. A rewriter talks to it through either interface below, or both.

### Socket

`<job>/scheduler.json` names a unix socket (under `/tmp`, node-local: the
rewriter runs on the trainer's host). JSON lines; `rewriter_client.py` is the
client, `example_rewriter.py` a minimal rewriter on it, and `scheduler.py`'s
docstring the wire format. The scheduler starts at the trainer's first draw;
`RewriterClient.connect()` waits for it (30 min by default, logging while it
waits), past a `scheduler.json` a killed earlier run left behind.

| call | |
|---|---|
| `add(row, weight=1, parent=None)` | a row, checked as the trainer parses it, becomes an active version |
| `reweight(version, weight)` | weight >= 0; 0 keeps it active but undrawn |
| `retire(version)` | no longer drawn |
| `get_state()` | every version: id, task, rev, weight, parent, added_at, status |
| hook `which_version(candidates)` | asked at every draw; answer an active version id, or null; no valid answer within 1 s means the weighted draw |

Events, training groups only (never the readout), each also in
`runs/<run>/scheduler/events.jsonl`:

| event | fields |
|---|---|
| `rollout_done` | version, task, rev, group, idx, reward (None = unscored), finish_reason, record_path, policy_versions |
| `group_done` | version, task, rev, group, passed, scored, policy_version |
| `step_done` | step, policy_version (after the step's weights reach the generator) |
| `pool_drained` | task (its active weight just reached 0) |

A rewriter that stops reading loses its oldest events (counted), never stalls
training; `events.jsonl` has them all. With every weight at 0 the pool draws
uniformly among active versions, and with none active it reuses the last one
retired, so a drained pool still trains.

### Files

The rewriter is any process run with the job root; it reads and writes only these:

| | path | |
|---|---|---|
| read | `job.json` | bench, task, rewriter name |
| read | `data/sources/<corpus>/tasks/<task>/` | the original task package (instruction, environment, solution, tests) |
| read | `data/mix/live.jsonl`, `data/mix/history/` | the mix being trained on, and every version published |
| read | `runs/*/rollouts/<task>/g<group>-r<idx>.jsonl` | every training attempt as it completes: reward, finish reason, every turn (format in LAYOUT.md) |
| write | `evolution/` | the rewriter's own state and logs |
| read | `runs/*/scheduler/events.jsonl`, `data/mix/pool.json` | the socket's events and the pool, as files |
| publish | `publish_mix.py --job <job> --rows <file>` | the next mix version; refused whole unless every row parses |

A file publish names the whole active set: the scheduler imports it within
2 s, adding each new row as a version (its parent is the version of the same
task the publish drops, whose weight it inherits) and retiring every active
version the file leaves out.

A row is the trainer's task schema (`../data.py`, `_parse_sample_row`): the
prompt, and under `metadata` the instance id, an image or Dockerfile, and the
`tmax` grading block (`test_sh`, `fixtures`, `reward_path`).

## Running a job

```
# once per benchmark: one single-row file per task
bench.py split --evalset <bench eval jsonl> --bench tb21 --out <tasks dir>

# one job per (task, rewriter)
bench.py new --tasks <tasks dir> --task <id> --sources <bench task tree> \
    --rewriter <name> --jobs-root <dir> [--bin <codex/jq dir>] \
    [--seed-rows <rows.jsonl>]

# optional, before training starts: the versions the pool starts from
bench.py seed <job> --rows <rows.jsonl> [--weight W]

# two processes, side by side
TRL_PROFILE=<p> RUNNER_ENV=<hardware env> ./start_training.sh <job>
<your rewriter> <job>        # or: ./start_reference_rewriter.sh <job>
                             # or: ./example_rewriter.py <job>

# the reference solution through the job's own row, on Daytona (a check that
# the task runs as training serves it; nothing of the bench depends on it)
evolution/daytona_revalidate.py <task package> --row <tasks dir>/<id>.jsonl

# results, one line per job
bench.py readout <job> [<job> ...]
```

For TB2.1 the eval JSONL is `data/evalsets/tb2_1_tmux_f1640f428d48.jsonl`
(built by `../prepare_tb2_1_data.py`, tmux preinstalled) and the task tree is
`terminal-bench-2-1/`. TB4: `data/evalsets/tb4_tmux_493ef156d.jsonl` (66 tasks,
the same builder pointed at the TB4 release tree -- `terminal-bench-prebuilt-v4.0.0`
from the v4.0.0 GitHub release, where every image is digest-pinned -- with
`--expect-tasks 66 --verify-runtime <dir> --keep-going`). Every TB4 task grades in
a separate verifier environment (`tmax.separate_verifier`), which the rollouter
boots after the agent submits and fills with the task's declared artifacts. A
task that declares more carries it in its row (see `../harbor_env.py`): an H100
in region earth (`--gpu-target`) for fp8-rmsnorm-gemm, jax-speedrun-gpu and
math-eval-grader; its docker-compose project, run on one docker-in-docker
sandbox (11 tasks); the image's own non-root user for the agent (3); a
healthcheck, collect hooks, verifier env and no-internet. That eval file
predates these fields; rebuild it to get them. A sandbox request above Daytona's
per-sandbox limits (4 vCPU, 8 GiB, 10 GiB disk; with a GPU 16 vCPU, 192 GiB,
512 GiB) is clamped to them; 20 tasks have one. For a compose task that clamps
the docker-in-docker host only: the compose project keeps the task's declared
cpu/memory reservations, which can then exceed the host's.

Training starts drawing immediately (with 8 groups in flight, the first 8
draws happen at startup), so versions a rewriter adds over the socket miss the
first round. To have them in it, seed the pool: `bench.py seed` (or `new
--seed-rows`) makes the pool start from exactly the given rows, each its own
active version at `--weight` (default 1), several rows of one task included.
The original stays in only if the file holds it (`<tasks dir>/<id>.jsonl`).
Every row must parse as the trainer parses it, or nothing changes. Seeding
writes `data/mix/pool.json`, which the scheduler loads before its first draw,
and publishes the rows as the next mix version; it is refused while a
scheduler serves the job.

The rewriter can be started before the trainer: `example_rewriter.py` and any
rewriter on `RewriterClient.connect()` wait for the scheduler, and the
reference rewriter runs file-based until it can connect.

The reference rewriter (`start_reference_rewriter.sh`) is the evolve loop run
through `rewriter_client.py reference`: it wakes on `group_done` rather than
polling for signal files, and gives each fold to the pool as add(new) +
retire(parent) as well as publishing it.

Both reference rewriters reject an easier rewrite whose grader fails a
solution the parent's grader passed (stage `grader_stricter`): the parent
version's passing training finals, replayed from their recorded keystrokes,
and the parent's and the original's reference solutions, each on a fresh
Daytona sandbox (`../evolution/grader_replay.py`; `EVOLVE_EASIER_GRADER_REPLAY=0`
turns it off). The evolve loop outside the bench leaves it off.

`EVOLVE_SIMPLIFY_EXTRA_GUIDANCE_FILE`, set in a job's `evolution/rewriter.env`,
appends a file to every easier-rewrite prompt (`../evolution/simplify_operators.py`).
`simplify_extra.md` here holds the bench's rules: the original task is the exam, so an
easier version does not preinstall what the student can install, supply a checker for
the retained skill, or record an inferable convention as a repair. Each rule cites the
readout it came from (torch-tensor-parallelism, v6, 2026-10-23).

`SYNC_OVERFIT.md` describes a strictly synchronous variant of a job (one group
in flight, the next group waiting for the rewrites it triggered) and how it is run.

## The ladder rewriter

`REWRITER_MODE=ladder ./start_reference_rewriter.sh <job>` runs
`ladder_rewriter.py`, a second reference rewriter. Each rewrite is the evolve
loop's own (`evolve_ondella.handle`: the same sessions, oracle, probes and
verifier, and the same rewrite.json, ledger, lineage and loop.log); what
differs is which rewrites it asks for and what it does with the result. This
is the rewriter's policy, not a bench rule:

- **Versions side by side.** A trainer signal names the version its group
  trained on, and the ladder rewrites that version. A child that passes
  validation becomes the task's next free revision `r<max+1>` and a new
  version whose parent is the version it came from. A fold retires nothing
  unless the task is at its cap (below).
- **Easier: up to K in parallel.** A group that solved nothing (the trainer's
  easier signal, held to `EVOLVE_EASIER_MIN_SCORED_ATTEMPTS` as in the loop)
  starts one easier rewrite per help level, concurrently: `none`, `vague`,
  `specific` (`--help-levels`), the simplify prompt's own levels from small
  help to large. It starts all of them only while the task has no learnable
  version and fewer than half of `--max-active` active versions; otherwise
  it starts one, at the middle level. Each rewrite carries its level in its
  signal, where it takes the place of `SWE_SIMPLIFY_HINT`.
- **Exploit before explore.** A version is learnable while one of its last 3
  groups (`--history`) solved some but not all of its scored attempts. While
  the task has a learnable version, an all-failed group starts no easier
  rewrite (the signal is closed `superseded`), and a version with no group
  yet weighs half the lowest learnable weight (`--explore-vs-learnable`),
  never more than `--explore`.
- **A cap per task.** At most 8 active versions per task (`--max-active`). A
  child that would exceed it replaces the worst active version: never the
  original, never one that solved anything in its last 3 groups
  (`--protect-solved`); of the rest, tested before untested, the longest
  all-failed streak first, then the lowest `p(1-p)`. A signal whose task is
  at the cap with nothing replaceable starts nothing.
- **Harder: one.** A group at or above the trainer's
  `SWE_EVOLUTION_HARDER_RATIO` starts one harder rewrite, the loop's
  withdraw-help hardening, with the parent version as its previous revision.
- **One request per version.** A version with a request running, or with an
  active child in the signal's direction, starts nothing new (the signal is
  closed `superseded`, with the reason); so does a signal older than the
  version's latest group. Different versions of one task are rewritten at
  once, up to `--workers` (16) rewrites in all.
- **Weights**, recomputed at every `group_done` and pushed with `reweight`:
  `p(1-p)` for `p` the solve rate of the version's last 3 groups
  (`--history`; `(passed + 0.5) / (scored + 1)`), 0.25 for a version with no
  group yet (`--explore`, lowered as above while a learnable version
  exists), halved while the version has an active harder
  child (`--harder-parent-factor`), and never below 0.05 for the original,
  rev 0 (`--original-floor`), which is never retired.
- **Retirement.** A version other than the original is retired once it has an
  active harder child and its last 2 groups (`--retire-after`) since that
  child was folded each solved at least 0.9 of their scored attempts
  (`--mastered`, default `SWE_EVOLUTION_HARDER_RATIO`). The group that asked
  for the child does not count, or every parent would be retired the moment
  its harder child arrived. A version other than the original that has a
  child is also retired once its last 2 groups (`--stale-after`) each solved
  nothing.
- **Decisions in loop.log.** Every spawn, retirement, reweight and sandbox
  teardown is one `policy ...` line with its reason; a signal that starts
  nothing is one `superseded` line with its reason.
- **Sandbox teardown.** A sandbox server runs in its own session and outlives
  the command that started it. A session's cleanup downs only its package's
  own container, and `./sandbox probes` downs each case's container in a
  `finally` that never runs when the process is killed, as an agent's command
  cut off mid-grade is; that case's server then holds a Daytona sandbox
  until someone kills it. So every rewrite the ladder starts has the servers
  under its directory downed when it ends, whatever its outcome, along with
  any server of the job whose package is gone or whose rewrite is closed.
  Starting the ladder downs every server of the job (none can be in use: the
  loop lock is the ladder's), and SIGTERM, SIGINT or SIGHUP downs them all
  before the process exits. A SIGKILL leaves them for the next start.

It needs the scheduler (it waits for it), reads training records, signals,
the scheduler's events and the pool, and never the readout: the record the
rewrite's author reads indexes training attempts only. It takes the evolve
loop's lock, so it never runs beside the reference rewriter on one job. A
seeded version it did not make has no package under `evolution/tasks/` past
r0, so a signal about it is closed as junk.

To start the next rung as soon as a version gives a gradient rather than once
it is mastered, set `SWE_EVOLUTION_HARDER_RATIO=0.01` in the ladder job's
`runner.env` (the trainer then sends a harder signal at a version's first
partial solve) and `EVOLVE_HARDER_FROM_PARTIAL=1` in its `evolution/rewriter.env`
(that rewrite restores one part toward the original instead of adding a new
difficulty). The version keeps training beside its child, and the original is
never hardened. Measured reason: on v7 (2026-10-23), r3 was learnable from step
33 but never solved a whole group, so at the default ratio 1.0 the ladder never
built the next rung; at 0.75 it would have done so only at step 177.
