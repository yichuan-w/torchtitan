# Runbook — terminal-agent RL on della-tridao

Everything runs on **della-tridao**, reachable over the tailnet without the Princeton
VPN: `ssh della-ts`, then `ssh della-tridao`.

Two things run continuously. **Training** is a systemd user service that restarts itself
from the latest checkpoint if it dies. **Online evolution** is a tmux loop that rewrites
tasks the model finds too hard or too easy. They never talk directly: training drops
signal files in a directory, evolution rewrites the task file, training hot-reloads it.

Paths, once:

```bash
ROOT=/scratch/gpfs/TRIDAO/al9080/terminal-rl     # data, logs, scripts, run dirs
PY=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
TT=~/torchtitan                          # training code: yichuan-w/torchtitan,
                                                 # branch yichuan/qwen35-port-cotrain (the
                                                 # single canonical line; andylizf/torchtitan
                                                 # is frozen, PR staging only)
```

Data-side scripts live in this directory and are deployed to `$ROOT/evolve-onhost/scripts/`.
Edit here, `scp` there, and commit — in the same session, or the two drift.

## Is it healthy?

```bash
bash ~/check_all.sh
```

One line for training, one for the last eval, one for any codex review in flight:

```
ST=active STEP=71 ROT=705 DONE=4310 CK=4
EVAL=policy_version=60): avg@k=0.4431 pass@k=0.4045
CODEX run=0 log=review5.log verdict=VERDICT: SHIP
```

`ST` is the systemd unit. `STEP=warmup` for the first ~45 min after a restart is normal —
see [Restarts cost an hour](#restarts-cost-an-hour). `ROT` is how many tasks are in
training rotation. `CK` is how many checkpoints are on disk.

Three more, when you want detail:

| command | what it tells you |
|---|---|
| `bash ~/train_vitals.sh` | step cadence, windowed solve rate, turn distribution, engine load, sandbox errors. A snapshot also lands in `$ROOT/logs/vitals_history.log` every 15 min. |
| `bash ~/check_effect.sh` | the eval series on the frozen holdout, raw and infra-excluded, with submit precision. |
| `$PY ~/submit_precision.py <index.json>` | one eval pass broken down by how each rollout ended. Eager-submission shows up here and nowhere else. |

Raw logs: `$ROOT/logs/rltrain_take8.log` (training), `$ROOT/logs/evolve_ondella.log`
(evolution), `$ROOT/logs/daytona_sweep.log` (sandbox cleanup). W&B project
`terminal-agent-rl`.

**When you grep the training log, cut it at the current boot first.** It is append-only
across restarts, so a line from three restarts ago reads as current:

```bash
L=$ROOT/logs/rltrain_take8.log
B=$(grep -abo "launch] dump" $L | tail -1 | cut -d: -f1)
tail -c +$B $L | grep ...
```

## Changing something

**Every knob lives in one file**: `$ROOT/scripts/rltrain.env`. The launch script reads it
and every `export` in it is `${VAR:-default}`, so the env file always wins.

```bash
RL_GPUS=0,1,2,3,4                    # 2 trainer + 3 generator, and see the note below
RL_RESUME_DUMP=$ROOT/runs/tw-mix-take8-...   # resume from the newest checkpoint in here
SWE_MAX_NUM_SEQS=256                 # per-engine concurrency
TT_DAYTONA_CREATE_CONCURRENCY=128    # sandbox creates in flight (32 left a restart's
                                     # create queue tens of minutes deep)
TT_DAYTONA_CREATE_RETRIES=8          # platform floor is ~1.8% create failures
SWE_INITIAL_ACTIVE_GROUPS=64         # cold-start admission; drives assembly time
SWE_WRONG_SUBMIT_PENALTY=0.3         # graded-wrong submit scores -0.3
SWE_CKPT_KEEP=24                     # checkpoints kept (they are 102 GiB each)
SWE_TB2_VAL_DATA=$ROOT/data/mix/tb2_eval.jsonl   # what the eval runs against
SWE_VAL_SAMPLES=89                   # 0 skips the blocking boot eval entirely
SWE_LR=3e-6                          # AdamW lr; the recipe default is 1e-6
```

**`RL_RESUME_DUMP` is the difference between continuing and starting over.** Set, the
launcher reuses that dump directory and the trainer resumes from the newest checkpoint in
it. **Delete the line** and the launcher makes a fresh timestamped directory with no
`checkpoint/`, so training starts from the base weights. Confirm which you got before
walking away: the launch log names the dump, and `ls $DUMP/checkpoint` is empty on a real
restart-from-scratch. Back up `rltrain.env` and the run log first — the old run's
checkpoints stay on the local disk and are the only way to compare afterwards.

**`RL_GPUS` names a contiguous window, not a set.** `train.py`'s allocator hands each
mesh an absolute range starting at 0 and overwrites `CUDA_VISIBLE_DEVICES` inside the
spawned process before CUDA initialises, so whatever the launch script exports is
discarded — `RL_GPUS=1,2,4,6,7` was found running on physical GPUs 0-4. `RL_GPU_OFFSET`
is what actually moves the range, and only a contiguous window can be expressed. The
launch script now refuses a non-contiguous value instead of appearing to honour it. This
box is shared with two other users, so check what is free before moving the window:

```bash
for i in 0 1 2 3 4 5 6 7; do
  echo -n "GPU$i: "
  nvidia-smi -i $i --query-compute-apps=pid,used_memory --format=csv,noheader |
    while IFS=, read -r p m; do echo -n "$(ps -o user= -p ${p// /}) $m  "; done
  echo
done
```

A knob only takes effect on restart. The **task file** is the exception: edit
`$ROOT/data/mix/mix_live.jsonl` and training picks it up within a minute, no restart —
watch for `TMaxDataset: hot reload` in the log.

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

`outputs/rl/checkpoint` in the run dir is a symlink to `/scratch/al9080/terminal-rl-ckpt/`,
a 28 TB node-local NVMe array. At 102 GiB per checkpoint they filled the shared 50 TB GPFS
pool and took training down twice with `OSError: [Errno 122] Disk quota exceeded`. The
local disk is faster too, which matters because a checkpoint save blocks training.

It is node-local: it does not survive a node rebuild and is invisible from other hosts.
Logs, eval artifacts and data stay on GPFS.

## Training

```bash
systemctl --user start rltrain.service
```

The unit runs the launcher with `$ROOT/scripts/rltrain.env`, restarts unconditionally after 30 s
(`Restart=always` — a trainer NCCL abort exits 0, which `on-failure` treated as a clean
stop and left the run down; stop the unit explicitly when a run is meant to end), and refuses to
start on any host but della-tridao — the home directory is shared NFS, so without that
guard the unit would also fire on della-gpu and clobber the run.

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
user unit called `evolve-<workdir name>`. A user unit rather than a nohup'd process:
nohup'd processes are SIGKILLed with the ssh session on della-tridao. Which checkout
that is depends on whose run it is -- `runbook/profiles/<name>.env` -- and a loop
another profile owns is restarted by its owner, not from here.

To restart our own after changing a prompt or a script, pull that checkout and run
`restart_evolve.sh <old loop pid>` from its `evolution/` directory. The loop holds its
credentials only in its own environment — there is no env file it reads at startup — so
the script reads them from `/proc/<pid>/environ` (into `<workdir>/meta/evolve.env`, kept
out of the evolution root because the lineage snapshot commits that directory), stops the
old process group, marks the Codex traces it interrupted, and starts the replacement unit
with the same log, worker and interval arguments. Job prompts are module constants and
need the restart; `AGENTS.md` is copied from disk at every session and does not.

**Changing the loop while a run depends on it.** The loop that feeds a training
run is the run's, and every restart of it interrupts Codex sessions and changes
the behaviour the run is being measured under (four restarts in one afternoon on
wd-20260903b, each for one change). So a change is tried somewhere else first:
a dev workdir with the run's shape and no trainer.

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
# once: a dev workdir (a copy of the mix, the pools, empty queues)
D=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-evolve-dev
mkdir -p $D/data/mix $D/evolution/signals $D/logs
cp $W/data/mix/mix_live.jsonl $D/data/mix/ && ln -s $W/data/tw-extract $D/data/tw-extract

# per change: push the branch, check it out in your checkout, replay a few of
# the run's consumed signals, run one round
cd /home/al9080/torchtitan && git fetch origin && git checkout <branch>
EVO=/home/al9080/torchtitan/torchtitan/experiments/rl/examples/tmax/evolution
bash $EVO/della/replay_signals.sh $W/evolution $D 3 harder
TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 bash $EVO/della/evolve_dev_round.sh $D 3
```

**Check the branch out; do not `rsync` files in.** A tree whose HEAD names one
commit and whose files are another cannot be traced to anything: the round's
log will name a commit that did not produce it, and the next `git pull`
silently reverts the copy. That happened here -- a dev round ran without a
change it was meant to be testing, because a branch switch upstream had quietly
removed it from the copied tree.

Folds land in the dev copy of the mix; `$D/evolution/{retuned,consumed,lineage}`
and the Codex traces under `$D/evolution/signals/codex_traces/` are the
evidence. Another person's checkout is fast-forwarded, and their loop restarted,
only by them.

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
to the experiment. Deploy to the dev checkout; let whoever owns the run pull
when they choose.

To start it from nothing, for one training workdir:

```bash
TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
  bash della/launch_evolveloop.sh /scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/<wd> 16
```

The three `TT_DAYTONA_*` are the trainer's fleet defaults and have to match its launch
env: a row declaring no `daytona_*` of its own is verified at this size, and the script
refuses to start without them. It sources `~/.config/daytona/env` (without it structural
revalidation silently fails `no_docker`), points `SYNTH_ENV_FILE` at the OpenAI key
(without it the loop dies at startup with `no OPENAI_API_KEY`), and sets
`SWE_RETUNE_AGENT=codex` (agentic retune with the full failure traces as files, no chat
fallback: a failed session leaves the task as it was and logs `agent_failed`),
`SWE_SIMPLIFY_HINT=vague` (`specific` writes where-to-look hints into the task text, and
the policy learns to follow hints rather than to solve) and `SWE_EVOLVE_SIMPLIFY=0` (0/k
signals defer to `evolution/deferred_easier`). The worker count is not a throughput knob:
the loop is signal-starved (89% of rounds carry ≤8 signals) and it only drains rare
bursts faster.

What it does: training emits a signal for every group that came back all-solved or
all-failed, because those produce no gradient. All-failed means the task is too hard, so
the instruction gets rewritten with more guidance; all-solved means too easy, so the
guidance is stripped back out. **The verifier is never touched** — difficulty comes off
the instruction, or the task is worth less. Rewrites are audited (no verifier path leaked,
no necessary information deleted) before folding back into `mix_live.jsonl`.

`SWE_TASK_EVOLUTION_DIR` names the signal queue. Its direct `*.json` children are pending
signals, and Codex traces default to `${SWE_TASK_EVOLUTION_DIR}/codex_traces/`. The consumer
does not scan that subdirectory. The signal queue's parent remains the artifact root for
the evolution run: `consumed/`, `retuned/`, `deferred_easier/`, `junk/`,
`evolution_stats.json`, `evolution_lineage.jsonl`, and `evolve_ondella.log` are written
there. Each Codex retune, evolve, or oracle-repair attempt keeps one trace directory;
`trace.json` records the task ID and invocation status (and, for a resumed oracle repair,
a `repairs` list), `harness/` holds the prompt, the pre-agent archive of the package and
the process output, `pkg/` is the agent's working directory (the package plus `run/` and
`traces/`; `run/sandbox.log` and `run/checks.jsonl` are the container's log and the
agent's own verdicts), and `.cxhome/sessions/` contains the Codex session JSONL when the
CLI created one. Final retune and fold outcomes remain in `evolution_lineage.jsonl`. A
session that fails (timeout, no axis declared, nothing changed) leaves the task as it was
and logs `agent_failed` with the reason; there is no chat fallback on the codex arm.
Revalidation of a structural rewrite is the oracle on a fresh build plus a null probe (the
verifier alone on an untouched workspace, which must fail, or the rewrite is rejected as
`null_pass`). After stopping the old loop's process group, the restart script
backs up each trace record still in the `running` state as `trace.pre-finalize.json`, then
writes `"status": "interrupted"` before starting the replacement loop. Set
`SWE_EVOLUTION_TRACE_DIR` to move Codex traces elsewhere.

`codex_traces/` is excluded from evolution Git snapshots. Treat it as private experiment
data because the archived workspace includes the verifier, reference solution, and full
rollout transcripts.

**Stopping it: `restart_evolve.sh`, or `systemctl --user stop evolve-<wd>`, not Ctrl-C.**
Ctrl-C mid-round gets absorbed and you end up with two instances. The loop holds
`<evolution root>/evolve_ondella.lock` for its lifetime -- flock on its own node, a 30 s
heartbeat on the file's mtime for other nodes -- so a second start over the same signals
directory exits 1 naming the holder instead of running beside it. Nothing stale needs
clearing: the kernel drops the flock when the holder dies, and a holder on another node is
treated as dead once its heartbeat is 90 s old. Afterwards check the process is genuinely
new:

```bash
systemctl --user show evolve-<wd> -p MainPID -p ActiveEnterTimestamp
pat=evolve_ondella; pgrep -cf "${pat}\.py"   # must be 1; a plain pgrep -f counts
                                            # your own ssh command line too
```

## Measuring whether it is learning

The trap: the task pool rewrites itself toward easier, so a rising score on the training
mix can be the tasks getting easier rather than the model getting better. Two yardsticks
that cannot move under you:

**TB-2.0** (`$ROOT/data/mix/tb2_eval.jsonl`, 89 tasks) — external, fixed. The blocking
validation at boot evaluates the checkpoint being resumed, so a restart buys a clean data
point for free.

**The frozen holdout** (`$ROOT/data/mix/holdout_eval.jsonl`, 64 tasks) — the last 64 rows
of the mix, which the training harness excludes from rotation (`holdout_n=64`) and which
evolution never rewrites, because it only rewrites tasks that were trained on.

To evaluate the base model on either, for a baseline to compare against:

```bash
tmux new-session -d -s baseeval
tmux send-keys -t baseeval \
  "RL_GPUS=1,2,3,4,7 RL_RESUME_DUMP=$ROOT/runs/base-eval-$(date +%s) SWE_TRAIN_STEPS=0 \
   SWE_TB2_VAL_DATA=$ROOT/data/mix/tb2_eval.jsonl TRL_PROFILE=andy bash runbook/launch_9b.sh" Enter
```

`SWE_TRAIN_STEPS=0` evaluates and exits. A fresh `RL_RESUME_DUMP` means no checkpoint to
resume, so it runs the base weights.

Two things that will mislead you if you skip them:

- **`avg@k` is not comparable across the wrong-submit penalty.** It is a mean over rewards,
  and those now include `-0.3` values, so it drops for reasons unrelated to capability.
  Use `num_pass` from `summary.json`.
- **Validation scores an infra failure 0.0** (training correctly uses NaN and drops it).
  Infra rates swing between passes, so report the infra-excluded number too;
  `check_effect.sh` prints both.

## Anything that writes to the mix

The last 64 rows are the eval instrument. Appending a row pushes one task out of the
holdout and pulls another in, which invalidates every before/after comparison anchored on
it. Use the script that knows this:

```bash
$PY $ROOT/evolve-onhost/scripts/refold_repaired.py <task_id> ...          # dry run
$PY $ROOT/evolve-onhost/scripts/refold_repaired.py --apply <task_id> ...  # backs up first
```

It inserts new rows *before* the holdout window and asserts afterwards that the window is
unchanged. The evolution loop's own fold is safe because it only ever replaces existing
rows.

## One-shot tools

```bash
. ~/.config/daytona/env   # every one of these needs it

# pass@k on Daytona, resumable, skips ids already graded in --out
SYNTH_ENV_FILE=$ROOT/.synth_env $PY $ROOT/evolve-onhost/scripts/solve_daytona.py \
  --ids ids.txt --out results.jsonl --attempts 5 --concurrency 200 [--agent chat|codex]

# build + oracle + cheat-probe one task package
$PY $ROOT/evolve-onhost/scripts/daytona_revalidate.py <package_dir> [--shortcut "CMD"]

# find Dockerfiles that can never build (a comment inside a RUN continuation)
$PY $ROOT/evolve-onhost/scripts/fix_inrun_comments.py           # dry run
$PY $ROOT/evolve-onhost/scripts/fix_inrun_comments.py --apply   # fixes package + mix copy
```

Two systemd timers run unattended: `daytona-sweep.timer` deletes our failed sandboxes
every 10 min (saving each one's error text to `$ROOT/logs/sandbox-failures/` first,
because `BUILD_FAILED` never reaches `Stopped` and so no auto-delete timer ever covers
it), and `train-vitals.timer` appends a vitals snapshot every 15 min.

## Things that have bitten us

- **Never check GPUs or processes through a tailnet alias.** Aliases have resolved to
  della-gpu, a different machine with one GPU. Two "training died" panics came from
  reading the wrong host. Go through `ssh della-ts` then `ssh della-tridao`.
- **A knob that looks set may not be.** A launcher here once hardcoded its exports,
  which silently beat the env file; and `preserve_all_thinking` was dropped by the
  renderer config layer for two months. If a setting matters, read it back out of
  `/proc/<pid>/environ` or the startup log, do not trust that you set it.
- **The Bash tool's default timeout is 120 s.** A slow `ssh` gets killed mid-command.
  Pass an explicit timeout. A killed `ssh` does not abort a remote `systemctl restart` —
  systemd finishes it daemon-side.
- **`env $(cat saved_env)` loses any value containing a space.** Restoring a process's
  environment this way put the second word of `SSH_CONNECTION` where the command name
  belonged and the evolution loop stayed down until someone looked. Read the snapshot
  line by line instead — `restart_evolve.sh` does, and skips `BASH_FUNC_*`,
  whose exported shell functions span several lines and parse as garbage after the first.
- **Do not gate a commit on a piped test command.** `pytest ... | tail && git commit`
  commits on a failing test, because the exit status is `tail`'s.
