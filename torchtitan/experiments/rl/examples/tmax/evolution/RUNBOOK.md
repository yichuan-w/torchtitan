# Runbook — terminal-agent RL on della-tridao

Everything runs on **della-tridao**, reachable over the tailnet without the Princeton
VPN: `ssh della-ts`, then `ssh della-tridao`.

Two things run continuously. **Training** is a systemd user service that restarts itself
from the latest checkpoint if it dies. **Online evolution** is a systemd user unit that
rewrites tasks the model finds too hard or too easy. They never talk directly: training
writes one signal per zero-variance group into its own run directory, evolution rewrites
the task and publishes a new version of the mix, training hot-reloads it. Which file is
where, and what is in it, is [`../LAYOUT.md`](../LAYOUT.md); this document is where to
look and what to do.

Paths, once:

```bash
export TRL_PROFILE=andy                  # whose checkout and root: ../runbook/profiles/<name>.env
TT=~/torchtitan                          # the checkout profile andy names: yichuan-w/torchtitan,
                                         # branch yichuan/qwen35-port-cotrain (the single
                                         # canonical line; andylizf/torchtitan is frozen,
                                         # PR staging only)
set -a; . $TT/torchtitan/experiments/rl/examples/tmax/runbook/profiles/$TRL_PROFILE.env; set +a   # TRL_BASE, TRL_TT
EVO=$TT/torchtitan/experiments/rl/examples/tmax/evolution
PY=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
cd $TRL_BASE                             # the experiment root: one mix, one loop, a sequence of runs
```

Every script here runs from the checkout the profile names; nothing is copied out of it,
and a profile's tree is pulled by its owner (see [Changing the loop while a run depends on
it](#online-evolution)).

## Is it healthy?

The run that is up is `runs/latest`, and everything the trainer wrote is inside it:

```bash
bash $EVO/train_vitals.sh                  # the readout: unit, step cadence, solve rate, sandboxes
systemctl --user is-active rltrain.service
jq -c '{run, started, resumed_from, checkpoint_step, mix_version, gpus}' runs/latest/launch.json
grep -a 'trainer_loop\] step' runs/latest/stdout.log | tail -1    # no line yet = warmup
ls runs/latest/checkpoints/                                        # step-* on the local disk
jq -c . evolution/status.json                                      # the loop, as of its last round
tail -3 evolution/loop.log
```

No `[trainer_loop] step` line for the first ~45 min after a restart is normal; see
[Restarts cost an hour](#restarts-cost-an-hour). `status.json` carries `pending`,
`handled`, `deferred`, `junk`, `superseded`, `rewrites_running`, `accepted`, `rejected`
(by stage), `blocked`, `failed`, `kept` and `mix_version`; it is rebuilt from the ledger
at the end of every round, so a stale `updated` means the loop is not rounding.

The same numbers reach W&B (project `terminal-agent-rl`): the training run carries
`evolution/pending_signals`, `evolution/handled_total`, `evolution/accepted_total`,
`evolution/rejected_total`, `evolution/blocked_total`, `evolution/kept_total` and
`evolution/mix_version`, which the trainer reads out of `evolution/status.json`, so the
loop's health sits on the same dashboard as the loss.

Two more, when you want detail:

| command | what it tells you |
|---|---|
| `bash ~/check_effect.sh` | the eval series on the frozen holdout, raw and infra-excluded, with submit precision. |
| `$PY ~/submit_precision.py <index.json>` | one eval pass broken down by how each rollout ended. Eager-submission shows up here and nowhere else. |

`train_vitals.sh [run dir]` reads only the run directory (`stdout.log` and
`trainer/structured_logs/`), `runs/latest` unless one is named; `train-vitals.timer`
appends a snapshot every 15 min. Every autotune decision starts and ends with that
readout: diff two runs of it.

A log holds one boot. Each process lifetime is its own run directory, so
`runs/latest/stdout.log` starts at the current boot; the boot before it is the previous
directory under `runs/`, and each directory's `launch.json` says what it resumed
(`resumed_from`, `checkpoint_step`), which mix version it loaded and the whole
`SWE_*` / `TMAX_*` / `TT_DAYTONA_*` environment it had. The loop's log is
`evolution/loop.log`. A one-shot tool logs to `logs/<tool>--<stamp>.log`, and one with
more than a log to say writes a directory `logs/<tool>--<stamp>/`.

## Changing something

**Every knob lives in one file**: `runbook/rltrain.env` in the checkout. The launcher reads
it from its own directory, the systemd unit `EnvironmentFile=`s it together with the
profile, and anything exported before the launcher wins over both.

```bash
RL_GPUS=2,0,6,1,4                    # trainer 2,0 | generators 6,1,4; positional, see below
RL_RESUME_FROM=tmax-9b--20260904-181500Z   # resume that run's checkpoints, in a new run directory
SWE_MAX_NUM_SEQS=256                 # per-engine concurrency
TT_DAYTONA_CREATE_CONCURRENCY=128    # sandbox creates in flight (32 left a restart's
                                     # create queue tens of minutes deep)
TT_DAYTONA_CREATE_RETRIES=8          # platform floor is ~1.8% create failures
SWE_INITIAL_ACTIVE_GROUPS=64         # cold-start admission; drives assembly time
SWE_WRONG_SUBMIT_PENALTY=0.3         # graded-wrong submit scores -0.3
SWE_CKPT_KEEP=24                     # checkpoints kept (they are 102 GiB each)
SWE_TB2_VAL_DATA=$TRL_BASE/data/evalsets/tb2_eval.jsonl   # what the eval runs against
SWE_VAL_SAMPLES=89                   # 0 skips the blocking boot eval entirely
SWE_LR=3e-6                          # AdamW lr; the recipe default is 1e-6
```

**`RL_RESUME_FROM` is the difference between continuing and starting over.** Set to a run
name (or a run directory), the launcher makes a new run directory whose `checkpoints` link
points at that run's checkpoint directory, and the trainer resumes from the newest `step-*`
in it; `launch.json` records `resumed_from` and `checkpoint_step`, and the launcher refuses
to start when the named run has no checkpoint to resume. Every run in a chain of restarts
links the same checkpoint directory, so the value does not need updating between restarts.
**Delete the line** and the launcher makes a fresh run whose checkpoint directory is empty,
so training starts from the base weights. (`RL_RESUME_DUMP` in the environment is refused
outright, with a message naming `RL_RESUME_FROM`.) Confirm which you got before walking
away:

```bash
jq '{resumed_from, checkpoint_step}' runs/latest/launch.json    # null, null = from scratch
TRL_PROFILE=andy ./launch_9b.sh --dry-run                       # the same, without starting a trainer
```

The previous run's directory and checkpoints are untouched either way and are the only way
to compare afterwards.

**`RL_GPUS` is positional, not a set to be sorted.** The allocator gives each mesh its slice
of the list by position, overwriting `CUDA_VISIBLE_DEVICES` inside the spawned process
before CUDA starts: the trainer takes the first `SWE_DP_SHARD` entries, then one entry per
generator, so put the quietest GPUs where the generators land. The launcher refuses a list
that names a device twice or whose length is not `SWE_DP_SHARD + SWE_GEN_DP`. This box is
shared with two other users, so check what is free before choosing:

```bash
for i in 0 1 2 3 4 5 6 7; do
  echo -n "GPU$i: "
  nvidia-smi -i $i --query-compute-apps=pid,used_memory --format=csv,noheader |
    while IFS=, read -r p m; do echo -n "$(ps -o user= -p ${p// /}) $m  "; done
  echo
done
```

A knob only takes effect on restart. The **mix** is the exception: a version the loop
publishes is picked up within a minute, no restart: watch for `TMaxDataset: hot reload` in
`stdout.log`, and `runs/latest/trainer/mix_versions.jsonl` gets one line per boot or reload
with the version and sha256 the trainer is now serving. A hand change to the mix goes the
same way; see [Anything that writes to the mix](#anything-that-writes-to-the-mix).

```bash
systemctl --user restart rltrain.service     # applies the env file
systemctl --user stop rltrain.service        # and it stays stopped
journalctl --user -u rltrain.service -n 50   # why it died, if it did
```

### Restarts cost an hour

A restart is closer to **two hours** before the first training step lands, and most of
that is not what the name suggests. Weight loading is ~15 min and assembly ~45, but ahead
of both the loop runs a **blocking pre-training validation** — the full 445-rollout eval,
announced as `Running pre-training validation (blocking); then N steps`. Measured on the
2026-08-28 restart: 14:01 start, 445/445 at ~16:05, steps immediately after.

Two ways this reads as a hang and is not. The rollouts it completes all carry a
**negative group id** (`group=-84/...`), which is how the log marks a validation sample
that is never trained on, so `DONE` climbs while `STEP` stays `warmup`. And the rate
collapses near the end — 2 rollouts in 5 minutes at 443/445 — because what is left is the
slowest episodes running against a 2-hour budget. Check `val=N/445` before concluding
anything; assembly proper is the sandbox count climbing toward 768 and then **draining**.

Every crash pays this again. At 14 self-heal restarts so far that is 14 blocking evals,
which is the sharpest argument for moving evaluation to a separate host.

So: **bundle changes into one restart**, and do it right after a checkpoint lands so you
lose the least work. One exception — never change more than one concurrency-shaped knob
(`SWE_ROLLOUT_CONCURRENCY`, `SWE_INITIAL_ACTIVE_GROUPS`, `TT_DAYTONA_CREATE_CONCURRENCY`)
per restart. They interact through the sibling-gate arithmetic, and a bad combination
wedges generation with no error in the log.

### Checkpoints live on the local disk

`runs/<run>/checkpoints` is a symlink to `/scratch/al9080/terminal-rl/ckpt/<run>`, on a
28 TB node-local NVMe array. At 102 GiB per checkpoint they filled the shared 50 TB GPFS
pool and took training down twice with `OSError: [Errno 122] Disk quota exceeded`. The
local disk is faster too, which matters because a checkpoint save blocks training.

It is node-local: it does not survive a node rebuild and is invisible from other hosts.
Logs, eval artifacts and data stay on GPFS. A resumed run's link points at the directory
of the run it resumed, so a chain of restarts shares one checkpoint directory and the run
directory alone still says where its checkpoints are.

## Training

```bash
systemctl --user start rltrain.service
```

The unit runs the launcher with `rltrain.env` and the profile, restarts unconditionally
after 30 s (`Restart=always` — a trainer NCCL abort exits 0, which `on-failure` treated as
a clean stop and left the run down; stop the unit explicitly when a run is meant to end),
and refuses to start on any host but della-tridao — the home directory is shared NFS, so
without that guard the unit would also fire on della-gpu and clobber the run. Every
restart is a new directory under `runs/` resuming the same checkpoints
(`RL_RESUME_FROM` in the unit's environment).

Shape of the run: 32 groups x 16 rollouts = 512 rollouts per step, terminus agent,
63488-token context, 5 GPUs (2 trainer + 3 generator engines), `vllm_native` backend
(`torchtitan_wrapper` asserts on B300 — FA4-cute paged+varlen gap).

Rollouts execute in Daytona sandboxes. Concurrency there is not the constraint people
assume: measured create-failure rate is ~1.8% at concurrency 8 and ~1.9% at 32, so it is
platform noise, not throttling. The binding limit is the LLM side, not the sandbox side:
`SWE_ROLLOUT_CONCURRENCY=1536`, sized against the ~768 decode slots (3 engines x
`SWE_MAX_NUM_SEQS=256`). Doubling it to 3072 added only queueing — queued first turns
overran the agent budget after every restart — which is why it was halved.

## Online evolution

The loop runs `evolve_ondella.py` from the checkout its profile names, as a systemd
user unit called `evolve-<root>` (the basename of `TRL_BASE`). A user unit rather than a
nohup'd process: nohup'd processes are SIGKILLed with the ssh session on della-tridao.
Which checkout that is depends on whose run it is -- `runbook/profiles/<name>.env` -- and
a loop another profile owns is restarted by its owner, not from here.

Everything the loop reads and writes hangs off `TRL_PROFILE` and `TRL_BASE`; it takes no
path arguments. It reads `runs/*/signals/` and the rollout records they reference, keeps
its own state under `evolution/` (`loop.log`, `loop.lock`, `loop.env`, `ledger.jsonl`,
`status.json`, `tasks/`) and publishes new mix versions under `data/mix/`.

One script starts it and restarts it:

```bash
TRL_PROFILE=andy TRL_BASE=/scratch/gpfs/TRIDAO/al9080/terminal-rl \
TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
  bash $EVO/restart_evolve.sh [workers] [interval]      # defaults 16, 120
```

It sources `della/evolveloop_env.sh`, which turns the profile and root into the loop's
whole environment; snapshots that environment into `evolution/loop.env`, the unit's
`EnvironmentFile=`; finds the loop alive over this root through `evolution/loop.lock`
(the loop writes its host and pid there) and, when there is one on this host, stops its
whole process group and marks the session and rewrite records it interrupted; then starts
the unit, logging to `evolution/loop.log`. With no loop alive it is simply the launcher.
The three `TT_DAYTONA_*` are the trainer's fleet defaults and have to match its launch
env: a row declaring no `daytona_*` of its own is verified at this size, and
`evolveloop_env.sh` refuses to start without them. It sources `~/.config/daytona/env`
(without it structural revalidation silently fails `no_docker`), points `SYNTH_ENV_FILE`
at the OpenAI key (without it the loop dies at startup with `no OPENAI_API_KEY`), and sets
`SWE_RETUNE_AGENT=codex` (agentic retune with the full rollout records as files, no chat
fallback: a failed session leaves the task as it was and logs `agent_failed`),
`SWE_SIMPLIFY_HINT=vague` (`specific` writes where-to-look hints into the task text, and
the policy learns to follow hints rather than to solve) and `SWE_EVOLVE_SIMPLIFY=0` (0/k
signals are ledgered as `deferred` and replay when it is switched on). The codex arm runs
`$TRL_BASE/bin/codex`, with `jq` beside it on the agent's PATH for reading the records.
The worker count is not a throughput knob: the loop is signal-starved (89% of rounds carry
≤8 signals) and it only drains rare bursts faster.

After changing a prompt or a script, pull the checkout and run the same command: job
prompts are module constants and need the restart; `AGENTS.md` is copied from disk at
every session and does not.

**Changing the loop while a run depends on it.** The loop that feeds a training
run is the run's, and every restart of it interrupts Codex sessions and changes
the behaviour the run is being measured under (four restarts in one afternoon on
wd-20260903b, each for one change). So a change is tried first, dry, on signals
the loop has already handled.

The checkout to try it in is **your profile's own**, the same one your training
runs use. Two people share this account and the profile is what keeps their
trees apart; another person's checkout is theirs, and a third checkout invented
for development is a tree nobody's profile names and nobody will keep current.

The path never says whose checkout it is -- the account is `al9080`, so every
directory on the box has an andy-shaped name, including the one Yichuan runs
from. Read `runbook/profiles/<name>.env`:

| profile | `TRL_TT` | whose |
|---|---|---|
| `andy` | `/home/al9080/torchtitan` | ours |
| `yichuan` | `/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/torchtitan` | Yichuan's |

```bash
# push the branch, check it out in your checkout
cd /home/al9080/torchtitan && git fetch origin && git checkout <branch>

# replay the newest 3 harder signals the loop has handled, dry: each rewrite
# directory is written in full and marked dry, and no ledger line, lineage
# line or mix version is. The loop's singleton lock applies, so this runs
# while the loop is stopped (an agreed moment), or over a forked root.
TRL_PROFILE=andy TRL_BASE=$TRL_BASE TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
  bash $EVO/della/replay_signals.sh 3 harder
```

`replay_signals.sh [n] [direction]` takes the ids from the ledger's newest `handled`
lines of that direction and runs `evolve_ondella.py --signal <run>/<task>--g<N>` for
each, which implies `--dry`; it first prints the checkout's commit and how many tracked
files differ from HEAD, and that count has to be zero. Each harder replay costs one Codex
session and a few sandboxes, so keep n small. The results are the rewrite directories
under `evolution/tasks/*/rewrites/` whose `rewrite.json` says `"dry": true`; read the
verdicts and the sessions there. A root to try folds in as well is a fork:
`new_root.py --base <dir> --fork-from $TRL_BASE` copies the mix history and every
task's accepted revisions and records `forked_from`.

The loop's own flags are for the same purpose: `--once` runs one round and exits,
`--only <task>` handles that task's pending signals alone, `--limit N` caps tasks handled
per round, `--dry` handles and publishes nothing, `--signal <id>` replays one handled
signal (dry), and `--workers` and `--interval` (seconds between rounds; 120 in production)
are what the unit runs with.

**Check the branch out; do not `rsync` files in.** A tree whose HEAD names one
commit and whose files are another cannot be traced to anything: the round's
log will name a commit that did not produce it, and the next `git pull`
silently reverts the copy. That happened here -- a dev round ran without a
change it was meant to be testing, because a branch switch upstream had quietly
removed it from the copied tree.

Another person's checkout is fast-forwarded, and their loop restarted, only by them.

**`TRL_PROFILE` decides which checkout runs, not the directory a script sits
in.** `evolveloop_env.sh` and `tb2_eval_local.sh` use their own location only to
find `runbook/profiles/<name>.env`, and take `TRL_TT` from that file; a copy of
one invoked from somewhere else still runs the tree its profile names, and says
so. Give every launch a profile.

**Restarting is not the only way code reaches a running loop.** Most of the
loop is imported once and frozen into the process, which is why it feels safe
to update the checkout under it. These are not: they are read from the checkout
at the moment they are used, so a `git pull` in that directory changes the
behaviour of a run already in flight, with nothing in its log to say so.

| read at use time | when |
|---|---|
| `agents/task_evolution.md` | copied into the agent's package at every session |
| `agent_sandbox.sh` | copied in as `./sandbox` at every session |
| `agent_sandbox.py` | executed afresh on every `./sandbox` call |
| `task_size.py`, `verifier_literals.py` | imported by `agent_sandbox.py`, so also per call |
| `daytona_revalidate.py` | spawned as a subprocess for every probe |

So the checkout a run reads is part of that run, and updating it is a change
to the experiment. Let whoever owns the run pull when they choose.

What it does: training emits a signal for every group that came back all-solved or
all-failed, because those produce no gradient. All-failed means the task is too hard, so
the instruction gets rewritten with more guidance; all-solved means too easy, so the
guidance is stripped back out. **The verifier is never touched** — difficulty comes off
the instruction, or the task is worth less. Rewrites are audited (no verifier path leaked,
no necessary information deleted) before a new mix version is published.

Where it leaves things, all under `$TRL_BASE` and all specified in
[`../LAYOUT.md`](../LAYOUT.md):

- **Pending signals** are the files under `runs/*/signals/` whose id has no line in
  `evolution/ledger.jsonl`. What the current run has produced so far:
  `jq -r .direction runs/latest/signals/*.json | sort | uniq -c`; the loop's own count is
  `pending` in `status.json`.
- **The ledger**, `evolution/ledger.jsonl`, is one line per signal seen: `handled`, with
  the rewrite it produced; `deferred`, its direction switched off, replayed when it is
  switched on; `superseded`, a signal about a revision the task has moved past, or a
  sibling of the one signal per task a round takes; or `junk`, unreadable or an unknown
  task. Handling a deferred signal later appends a new line; the old one stays.
  `jq -r .outcome evolution/ledger.jsonl | sort | uniq -c` is the loop's history in one
  line.
- **One handled signal is one rewrite**, `evolution/tasks/<task>/rewrites/<stamp>--<job>/`
  with `job` `harder` or `easier`. `rewrite.json` says which signal, which input revision,
  the status (`accepted`, `rejected`, `blocked`, `failed`, `kept`), the verdicts (oracle,
  `dark_paths`, `dark_literals`, `step`), the measured resources and the result revision.
  `package/` is the agent's working copy, with the run's rollout records hardlinked in as
  `traces/attempt-NN.jsonl` and `run/sandbox.log` and `run/checks.jsonl` holding the
  container's log and the agent's own verdicts; on `accepted` it is renamed to `r<N+1>/`
  with the harness files removed, so an accepted rewrite has no `package/` and a rejected
  or failed one keeps it. `sessions/<stamp>--<kind>/` is one codex invocation each
  (`agent`, `repair`, `verifier`, `oracle`): `session.json`, `prompt.md`, `stdout.txt`,
  `stderr.txt`, and the CLI's own session jsonl under `codex/`.
- **A task's whole history** is its directory: `lineage.jsonl` (`rewrite` and `fold`
  events; the `fold` line is the only record of a revision entering the mix, keyed by
  `mix_version`) and the accepted revisions `r0/` (the seed, copied from
  `data/sources/<corpus>/tasks/<task>` on the task's first signal), `r1/`, ...
- **The mix** is `data/mix/live.jsonl`, a hardlink to the newest
  `data/mix/history/v<N>--<stamp>.jsonl`; every version ever served stays there with its
  manifest (`parent_version`, `sha256`, `rows`).
- **The audit repository**: every round ends with a commit to a git whose metadata sits in
  `evolution/.git` and whose work tree is the root, of the ledger, `status.json`, every
  task's `lineage.jsonl` and `rewrite.json` files and the mix manifests, named one by one
  and nothing else. `git --git-dir=evolution/.git --work-tree=. log` is the loop's history
  round by round; packages, sessions and traces are never in it.

To read one rollout record: `head -n1 <record> | jq '{reward, finish_reason, turns, secs}'`
is the outcome, and `jq -c 'select(.turn) | {turn, keystrokes, task_complete}' <record>`
is what the agent typed, turn by turn.

A session that fails (timeout, no axis declared, nothing changed) leaves the task as it
was and logs `agent_failed` with the reason; its `session.json` carries the exit code and
error. There is no chat fallback on the codex arm. Revalidation of a structural rewrite is
the oracle on a fresh build plus a null probe (the verifier alone on an untouched
workspace, which must fail, or the rewrite is rejected as `null_pass`).

`evolution/tasks/` holds every task's verifier and reference solution and the full
rollout transcripts the agent worked from. Treat it as private experiment data.

**Stopping it: `restart_evolve.sh`, or `systemctl --user stop evolve-<root>`, not Ctrl-C.**
Ctrl-C mid-round gets absorbed and you end up with two instances. The loop holds
`evolution/loop.lock` for its lifetime -- flock on its own node, a 30 s heartbeat on the
file's mtime for other nodes -- so a second start over the same root exits 1 naming the
holder instead of running beside it. Nothing stale needs clearing: the kernel drops the
flock when the holder dies, and a holder on another node is treated as dead once its
heartbeat is 90 s old. Afterwards check the process is genuinely new:

```bash
systemctl --user show evolve-<root> -p MainPID -p ActiveEnterTimestamp
pat=evolve_ondella; pgrep -cf "${pat}\.py"   # must be 1; a plain pgrep -f counts
                                            # your own ssh command line too
```

## Measuring whether it is learning

The trap: the task pool rewrites itself toward easier, so a rising score on the training
mix can be the tasks getting easier rather than the model getting better. Two yardsticks
that cannot move under you, both under `data/evalsets/`, which the loop never touches:

**TB-2.0** (`data/evalsets/tb2_eval.jsonl`, 89 tasks): external, fixed. The blocking
validation at boot evaluates the checkpoint being resumed, so a restart buys a clean data
point for free. Off the training GPUs, every new checkpoint of a run is scored by

```bash
TRL_BASE=$TRL_BASE TRL_TT=$TT TRL_MODEL=/scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B \
  bash $EVO/della/eval_watcher.sh [run dir]        # runs/latest by default; exits after 24 h
```

which stages each completed local `step-*` to the run's `checkpoints-staged/` on GPFS
(the pli eval nodes read only GPFS) and submits `della/tb2_eval.sbatch` for it; the job
writes `evals/<stamp>--<run>-step<N>/` (its own `launch.json`, `stdout.log` and
`trainer/`) and its SLURM log is `logs/tb2_eval--<jobid>.log`. The same eval on this
box, on two GPUs, is `TRL_PROFILE=andy bash $EVO/della/tb2_eval_local.sh
<checkpoints/step-N|base> <gpu-offset>`, with the same `evals/` naming.

**The frozen holdout** (`data/evalsets/holdout_eval.jsonl`, 64 tasks): the last 64 rows
of every mix version, which the training harness excludes from rotation (`holdout_n=64`)
and which evolution never rewrites, because it only rewrites tasks that were trained on.

To evaluate the base model on either, for a baseline to compare against:

```bash
tmux new-session -d -s baseeval
tmux send-keys -t baseeval \
  "RL_GPUS=1,2,3,4,7 SWE_TRAIN_STEPS=0 SWE_VAL_SAMPLES=89 TRL_PROFILE=andy \
   bash $TT/torchtitan/experiments/rl/examples/tmax/runbook/launch_9b.sh" Enter
```

`SWE_TRAIN_STEPS=0` evaluates and exits. With no `RL_RESUME_FROM` the run is fresh, so it
runs the base weights; `launch.json` says so (`resumed_from: null`).

Two things that will mislead you if you skip them:

- **`avg@k` is not comparable across the wrong-submit penalty.** It is a mean over rewards,
  and those now include `-0.3` values, so it drops for reasons unrelated to capability.
  Use `num_pass` from the eval's `summary.json`.
- **Validation scores an infra failure 0.0** (training correctly uses NaN and drops it).
  Infra rates swing between passes, so report the infra-excluded number too;
  `check_effect.sh` prints both.

## Anything that writes to the mix

The last 64 rows are the eval instrument. Appending a row pushes one task out of the
holdout and pulls another in, which invalidates every before/after comparison anchored on
it. The loop's own fold is safe because it only ever replaces existing rows.

Only the loop writes `data/mix/`. Anything else that has to change a row goes through
`layout.MixDir.publish`, which writes the next `history/v<N>--<stamp>.jsonl` with its
manifest and relinks `live.jsonl` in one rename; `live.jsonl` is the same inode as the
newest history file, so editing it in place rewrites a served version and is never the
way. Insert new rows before the holdout window, and check afterwards that the window is
unchanged:

```bash
diff <(tail -n 64 data/mix/history/v<N-1>--*.jsonl | jq -r .label) \
     <(tail -n 64 data/mix/live.jsonl | jq -r .label) && echo holdout unchanged
```

## One-shot tools

```bash
. ~/.config/daytona/env   # every one of these needs it; each logs to $TRL_BASE/logs/<tool>--<stamp>.log

# pass@k on Daytona, resumable, skips ids already graded in --out
SYNTH_ENV_FILE=$TRL_BASE/.synth_env $PY $EVO/solve_daytona.py \
  --ids ids.txt --out results.jsonl --attempts 5 --concurrency 200 [--agent chat|codex]

# build + oracle + cheat-probe one task package
$PY $EVO/daytona_revalidate.py <package_dir> [--shortcut "CMD"]

# find Dockerfiles that can never build (a comment inside a RUN continuation)
$PY $EVO/fix_inrun_comments.py           # dry run
$PY $EVO/fix_inrun_comments.py --apply   # fixes the package; the mix row follows through a publish
```

Two systemd timers run unattended: `daytona-sweep.timer` deletes our failed sandboxes
every 10 min (saving each one's error text under `$TRL_BASE/logs/daytona-sweep--<stamp>/`
first, because `BUILD_FAILED` never reaches `Stopped` and so no auto-delete timer ever
covers it), and `train-vitals.timer` appends a vitals snapshot every 15 min.

## Things that have bitten us

- **Never check GPUs or processes through a tailnet alias.** Aliases have resolved to
  della-gpu, a different machine with one GPU. Two "training died" panics came from
  reading the wrong host. Go through `ssh della-ts` then `ssh della-tridao`.
- **A knob that looks set may not be.** A launcher here once hardcoded its exports,
  which silently beat the env file; and `preserve_all_thinking` was dropped by the
  renderer config layer for two months. If a setting matters, read it back out of
  `runs/latest/launch.json` (`.env`), `/proc/<pid>/environ` or the startup log, do not
  trust that you set it.
- **The Bash tool's default timeout is 120 s.** A slow `ssh` gets killed mid-command.
  Pass an explicit timeout. A killed `ssh` does not abort a remote `systemctl restart` —
  systemd finishes it daemon-side.
- **`env $(cat saved_env)` loses any value containing a space.** Restoring a process's
  environment this way put the second word of `SSH_CONNECTION` where the command name
  belonged and the evolution loop stayed down until someone looked. Write the snapshot
  as quoted `KEY="value"` lines and hand it to systemd as an `EnvironmentFile=` instead;
  `restart_evolve.sh` does, keeping only the prefixes the loop needs, so `BASH_FUNC_*`,
  whose exported shell functions span several lines and parse as garbage after the
  first, never get in.
- **Do not gate a commit on a piped test command.** `pytest ... | tail && git commit`
  commits on a failing test, because the exit status is `tail`'s.
