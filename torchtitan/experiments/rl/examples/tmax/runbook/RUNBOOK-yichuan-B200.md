# Qwen3.5-9B RL on B200: current best practice

This is the operational runbook for the B200 host used by the `andy` profile.
It describes the configuration that has completed real rollout and training
steps on the TerminalWorld + SWE-Smith mix. It is deliberately separate from
the older B300 notes; values from the B300 runbook must not be copied here
without re-measuring them.

The reference code is the `yichuan/qwen35-port-cotrain` branch. The launcher
records the exact commit, environment, data hash, GPU list, and checkpoint
location in `runs/<run>/launch.json`; use that file when reproducing a run.

## Recommended layout

With eight visible GPUs, use two trainer ranks, five independent rollout
engines, and one dedicated evaluation engine:

```text
RL_GPUS=0,2,4,5,6,7,1,3
                 └─ trainer: physical 0,2
                    rollout: physical 4,5,6,7,1
                    eval:    physical 3
SWE_DP_SHARD=2
SWE_GEN_DP=1
SWE_NUM_GENERATORS=5
SWE_NUM_EVAL_GENERATORS=1
SWE_EVAL_GEN_DP=1
```

The GPU list is positional. `SWE_DP_SHARD + SWE_GEN_DP` describes the training
and rollout mesh width, while `SWE_NUM_GENERATORS` creates independent
replicas. Keep `SWE_GEN_DP=1` for this layout: each rollout replica then has a
single local vLLM engine and does not introduce an additional intra-engine DP
router.

## Known-good environment

This is the starting point for a new run. Paths and credentials are machine
specific and should remain outside the repository.

```bash
RL_GPUS=0,2,4,5,6,7,1,3
SWE_DP_SHARD=2
SWE_GEN_DP=1
SWE_NUM_GENERATORS=5
SWE_NUM_EVAL_GENERATORS=1
SWE_EVAL_GEN_DP=1

SWE_GEN_BACKEND=vllm_native
SWE_GEN_PREFIX_CACHE=1
SWE_GEN_VLLM_DEFAULT_COMPILE=1
SWE_GPU_MEM_LIMIT=0.88
# Leave SWE_MAX_NUM_SEQS unset. The controller derives the engine ceiling.
SWE_ROLLOUT_CONCURRENCY=600
SWE_NUM_ROLLOUT_WORKERS=16

SWE_GROUP_SIZE=12
SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_INITIAL_ACTIVE_GROUPS=128
SWE_MAX_ACTIVE_GROUPS=160
SWE_DROP_ZERO_STD=0
SWE_TRAIN_STEPS=150
SWE_LR=3e-6

SWE_MAX_CONTEXT_LEN=63488
TMAX_AGENT=terminus
TMAX_TERMINUS_MAX_TURNS=120
TMAX_TURN_MAX_TOKENS=32768
TMAX_EXEC_TIMEOUT_SEC=120
SWE_TIME_BUDGET_SEC=2400
SWE_AGENT_TIMEOUT_FLOOR_SEC=7200

SWE_AC=full
SWE_LOSS_CHUNKS=32
SWE_LMHEAD_TF32=1
SWE_DISABLE_CUSTOM_ALL_REDUCE=1

SWE_DP_FALLBACK_ROUTER=leastloaded
SWE_DP_STICKY_REBALANCE=0
SWE_DATA_HOT_RELOAD=1
SWE_VAL_SAMPLES=89
SWE_VAL_INTERVAL=20
SWE_TB2_VAL_K=5
```

`SWE_LMHEAD_TF32=1` is now also the 9B launcher's default. It enables TF32
inputs for the fp32 lm_head matmuls while retaining fp32 accumulation and
logit outputs. Set it explicitly to `0` only for a precision comparison.

Do not set `SWE_MAX_NUM_SEQS` in this profile. A fixed per-engine value makes
the admission limit disagree with the number of independent replicas. The
controller derives the limit from the active rollout pool; the boot log is the
source of truth.

## Launch

Use a fresh checkpoint directory for a fresh step-0 experiment. Keep Daytona,
Hugging Face, and W&B credentials in a protected environment file; never add
them to `rltrain.env` or a committed runbook.

```bash
cd /path/to/torchtitan-qwen35-port-cotrain
set -a
. /path/to/credentials.env
. /path/to/b200-tw-swe.env
set +a
bash torchtitan/experiments/rl/examples/tmax/runbook/launch_9b.sh
```

For a user systemd service, use `KillMode=control-group` and a stop timeout of
120 seconds so all Monarch and vLLM children leave with the trainer. Every
restart should create a new run directory. Resume only by setting
`RL_RESUME_FROM` to a run with a complete `step-*` checkpoint; do not point a
fresh step-0 run at an old checkpoint directory by accident.

## Startup checks

Before judging throughput, wait for vLLM CUDA graph capture and the first weight
sync. These lines must appear in `stdout.log`:

```text
[trainer] TF32 enabled for fp32 matmuls (SWE_LMHEAD_TF32=1)
Spawned 16 RolloutWorker(s) ... (global target 600)
5 generator actors and 1 eval generator
```

Each rollout engine should then report `Waiting: 0 reqs` during normal admission.
The first few minutes can have lower KV use while prefixes warm; a healthy
steady state on this host has roughly 45--65% KV use and 95%+ prefix-cache hit
rate, with no persistent waiting queue. A single snapshot is not enough to
diagnose imbalance because task lengths differ.

Useful checks:

```bash
RUN=/path/to/experiment/runs/latest
systemctl --user status <unit> --no-pager
rg 'Avg generation throughput|TF32 enabled|ERROR|Traceback|trainer_loop' "$RUN/stdout.log" | tail -80
nvidia-smi
```

The vLLM line gives running requests, waiting requests, KV-cache use, and prefix
hit rate for every engine. Compare several consecutive samples. Healthy routing
means the request counts may differ briefly, but waiting should drain and no
engine should remain idle while siblings are saturated.

## Why these values are conservative

Rollout concurrency is limited by decode latency and agent wall-clock budgets,
not by the number of Daytona sandboxes. Raising admission too far increases
prefill and queueing, slows every sequence, and causes otherwise valid agents
to hit the 2,400-second budget. On this host, 550 was stable and 600 is the
current trial value; move in increments of 50 only after checking per-sequence
throughput and timeout rate.

Keep `SWE_MAX_NUM_SEQS` unset and do not compensate for a waiting queue by
doubling it. A queue that persists with low KV use usually indicates routing,
sandbox creation, or a failed result receiver rather than insufficient KV
capacity.

`SWE_GROUP_SIZE=12` and 32 groups per training step keep the first batch from
being dominated by long-tail 16-way groups. `SWE_DROP_ZERO_STD=0` lets the
buffer fill with full-solve/all-fail groups; their zero-advantage samples are
filtered from the forward pass while the group remains available for buffer
accounting and evolution signals.

## Routing and failure diagnosis

The generation path has sticky routing for an existing session and a
least-loaded fallback for a cold session. Equal least-load candidates are
rotated, and `SWE_DP_STICKY_REBALANCE=0` keeps an established session pinned.
`reserved_load` counts admitted work, not generated tokens, so equal request
counts do not imply equal KV use when prompt lengths differ.

If all engines stop together, inspect the first exception in `stdout.log` and
the systemd journal. A cancelled request arriving after a peer result used to
raise `KeyError`/`InvalidStateError` and kill the shared result receiver; the
late-result and cancellation race fixes are part of the current branch. A
`tmux: command not found` message is a sandbox image problem and yields a
zero-turn rollout; it is separate from vLLM routing.

Do not infer a trainer failure from a quiet GPU during rollout collection. The
trainer waits until a complete trainable group batch exists. Once a step starts,
look for `forward_backward`, `optim done`, `weights pushed`, and `weights pulled`.
W&B logs the step after the complete optimizer/weight-sync cycle, so it can lag
behind the microbatch progress in the local log.

## Data and evaluation

The current training mix is the versioned TerminalWorld + SWE-Smith JSONL. Keep
resource fields from the HF SWE-Smith rows (`peak_ram_mb`, `peak_disk_mb`,
`req_cpus`, and `validated_test_timeout_s`) when constructing the mix; do not
replace them with a single global sandbox size. TB 2.1 is a read-only eval set
and must not be concatenated into training data.

Every run must retain `launch.json`, `inputs/mix.jsonl`, `stdout.log`, rollout
records, trainer lineage, and W&B run ID. Those files are required to tell a
slow task, a failed sandbox, a routing imbalance, and a trainer starvation
episode apart.
