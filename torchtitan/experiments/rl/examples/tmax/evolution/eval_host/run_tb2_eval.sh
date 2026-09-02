#!/usr/bin/env bash
# Run one TB-2.0 evaluation on the flow-matic eval host.
#
# This is the trainer's own `rl_grpo_qwen3_5_9b_tmax_tb2_eval` recipe at
# num_training_steps=0, against a model-only DCP shipped from della. Same code
# (the checkout is pinned to the SHA the trainer runs), same package versions,
# same native-DCP load path -- so the number it produces belongs on the training
# curve rather than beside it.
#
#   ./run_tb2_eval.sh <step>
set -uo pipefail
W=/var/tmp/tw-eval
STEP=${1:?usage: run_tb2_eval.sh <step>}

export HOME=$W/home
export HF_HOME=$W/cache/hf
export TRITON_CACHE_DIR=$W/cache/triton
export XDG_CACHE_HOME=$W/cache
export PYTHONPATH=$W/repo/torchtitan
# `set -a` matters: secrets.env holds bare KEY=value lines, and sourcing those
# sets shell variables, not environment ones -- the rollout workers are child
# processes and saw no DAYTONA_API_KEY at all, so every sandbox creation failed
# with "not set" while the key sitting in the file was valid.
set -a
# shellcheck disable=SC1091
. $W/secrets.env
set +a

CKPT=$W/ckpt/step-$STEP
[ -f "$CKPT/.metadata" ] || { echo "no DCP at $CKPT (pull it first)"; exit 1; }

# The eval settings the trainer's inline validation uses, so the two are the
# same measurement. SWE_VAL_SAMPLES=89 is the whole TB-2.0 slice.
export SWE_TB2_CKPT=$CKPT
# SWE_TB2_DATA, not SWE_TB2_VAL_DATA -- the latter belongs to the training
# recipe's inline validation and this recipe never reads it, so setting it
# leaves data_path empty and the dataset refuses to build.
export SWE_TB2_DATA=$W/data/tb2_eval.jsonl
export SWE_VAL_SAMPLES=${SWE_VAL_SAMPLES:-89}
# Pinned rather than left to defaults. The eval samples (temperature 0.7,
# top_p 0.95, k=5 -> 89 x 5 = 445 rollouts); it is not greedy, so two runs of
# the same checkpoint differ by sampling noise -- about +/-8 rollouts at the
# rates seen so far. A number is comparable to the training curve only if these
# three match what the trainer's own validation used.
export SWE_TB2_VAL_TEMPERATURE=${SWE_TB2_VAL_TEMPERATURE:-0.7}
export SWE_TB2_VAL_TOP_P=${SWE_TB2_VAL_TOP_P:-0.95}
export SWE_TB2_VAL_K=${SWE_TB2_VAL_K:-5}
export SWE_GDN=1
export SWE_GEN_BACKEND=vllm_native
export SWE_MAX_CONTEXT_LEN=63488
export SWE_TIME_BUDGET_SEC=2400
# Validation shares the global rollout semaphore, so this has to clear the whole
# slice or the tasks queue behind each other and the wall clock stops meaning
# anything.
export SWE_ROLLOUT_CONCURRENCY=${SWE_ROLLOUT_CONCURRENCY:-768}
export TT_DAYTONA_CREATE_CONCURRENCY=${TT_DAYTONA_CREATE_CONCURRENCY:-32}
export TT_DAYTONA_CREATE_RETRIES=8
export TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CPU=2
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_LABEL=tw_eval_flowmatic

# The rollout scaffold. These are not defaults -- they are what the trainer's own
# process has set, and each one changes what a rollout does (tokens per turn, how
# many turns, how long a command may run). A number produced with different values
# is not on the same curve.
export TMAX_AGENT=${TMAX_AGENT:-terminus}
export TMAX_TURN_MAX_TOKENS=${TMAX_TURN_MAX_TOKENS:-32768}
export TMAX_TERMINUS_MAX_TURNS=${TMAX_TERMINUS_MAX_TURNS:-120}
export TMAX_EXEC_TIMEOUT_SEC=${TMAX_EXEC_TIMEOUT_SEC:-120}

# GPU split. `--num-generators` counts generator MESHES, not GPUs; each mesh is
# SWE_GEN_DP wide, and the trainer is SWE_DP_SHARD wide, so the framework asks for
# num_generators*SWE_GEN_DP + SWE_DP_SHARD devices and fails on NVML if that
# exceeds what the box has. The base recipe bakes DP-8 + trainer-16 = 72.
#
# della runs 3 + 2 on 275GB B300s. An H100 holds 80GB, so the same split would not
# fit: shard wider instead. 4 + 4 = 8, which is every GPU on this host.
export SWE_GEN_DP=${SWE_GEN_DP:-4}
export SWE_DP_SHARD=${SWE_DP_SHARD:-4}
export RL_GPUS=${RL_GPUS:-0,1,2,3,4,5,6,7}
export RL_GPU_OFFSET=0
GENERATORS=1

# Decode batch cap per engine. della sets 256 against 275GB; 80GB leaves roughly a
# third of the KV budget once the 18GB of bf16 weights are in. This is throughput
# only -- sequences decode independently, so the cap does not change any output.
export SWE_MAX_NUM_SEQS=${SWE_MAX_NUM_SEQS:-64}
export SWE_GPU_MEM_LIMIT=${SWE_GPU_MEM_LIMIT:-0.90}

# Prefix caching, which the trainer leaves to vLLM (off for hybrid GDN) because its
# weights move every step and a cached prefix would be stale. Here they never move:
# one checkpoint, zero training steps. The measured ~2x is on PREFILL, which is what
# a 120-turn episode spends its generation time on and is not part of the ~2% of the
# clock that decode accounts for.
export SWE_GEN_PREFIX_CACHE=${SWE_GEN_PREFIX_CACHE:-1}

# How long each task may run. Two settings, and they answer different questions:
#
#   7200 (default) -- what the training loop uses. TB-2.0 declares 900s for 49 of its
#       89 tasks, sized for a fast local runner; a 120-turn Terminus-2 episode in a
#       2 vCPU sandbox spends ~98% of its clock on in-sandbox commands, so the floor
#       raises 87 of 89 tasks. Every datapoint on the training curve was measured
#       this way, so this is the only setting comparable to it.
#   0 -- each task gets exactly the timeout TB-2.0 declares. Comparable to published
#       TB-2.0 results, not to our curve, and faster (41 task-hours against 92).
#
# Neither is "the right one" -- record which was used beside the number.
export SWE_AGENT_TIMEOUT_FLOOR_SEC=${SWE_AGENT_TIMEOUT_FLOOR_SEC:-7200}

# The recipe logs to wandb, which the trainer has a key for and this host does
# not. `offline` rather than `disabled`: the same code path runs, the run is
# written locally, and nothing is uploaded -- so the eval is not silently taking
# a different branch from the one the training-side validation takes.
export WANDB_MODE=${WANDB_MODE:-offline}
export WANDB_DIR=$W/results/wandb
mkdir -p "$WANDB_DIR"

TS=$(date +%Y%m%d-%H%M%S)
RUN=$W/results/tb2-step$STEP-floor${SWE_AGENT_TIMEOUT_FLOOR_SEC}-$TS
mkdir -p "$RUN"
LOG=$W/logs/tb2-step$STEP-floor${SWE_AGENT_TIMEOUT_FLOOR_SEC}-$TS.log
{
  echo "step=$STEP ckpt=$CKPT generators=$GENERATORS run=$RUN"
  echo "--- settings this number depends on ---"
  for v in SWE_TB2_CKPT SWE_TB2_DATA SWE_VAL_SAMPLES SWE_TB2_VAL_K \
           SWE_TB2_VAL_TEMPERATURE SWE_TB2_VAL_TOP_P SWE_MAX_CONTEXT_LEN \
           TMAX_TURN_MAX_TOKENS TMAX_TERMINUS_MAX_TURNS TMAX_EXEC_TIMEOUT_SEC \
           SWE_GEN_DP SWE_DP_SHARD SWE_MAX_NUM_SEQS SWE_ROLLOUT_CONCURRENCY \
           SWE_GEN_PREFIX_CACHE SWE_GEN_CUDAGRAPH SWE_AGENT_TIMEOUT_FLOOR_SEC; do
    printf '  %-28s %s\n' "$v" "${!v-}"
  done
} | tee "$LOG"

cd "$RUN" || exit 1
"$W/venv/bin/python" -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators "$GENERATORS" \
    --hf_assets_path "$W/models/Qwen3.5-9B" >> "$LOG" 2>&1
RC=$?
echo "exit=$RC  log=$LOG  run=$RUN" | tee -a "$LOG"
exit $RC
