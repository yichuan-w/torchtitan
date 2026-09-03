#!/bin/bash
# take8: README_TERMINALWORLD-aligned run on della-tridao (single host, 5 GPUs).
# Deviations from the README, all host-capacity driven, none semantic:
#   - 1 generator host w/ SWE_GEN_DP=3 engines (README: 12 generator hosts)
#   - no dedicated eval generator (README: 1 host); SWE_VAL_SAMPLES=89 +
#     SWE_TB2_VAL_DATA still run the periodic TB-2.0 pass on the training
#     generators every SWE_VAL_INTERVAL steps
#   - trainer FSDP-2 (README: HSDP multi-host)
# Data: mix_live.jsonl = mix_v3 (TW filtered to solvable = pass@5 != 0).
set -euo pipefail
. /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/activate
. ~/.config/daytona/env
cd ~/torchtitan

STAMP=$(date +%Y%m%d-%H%M%S)
DUMP=${RL_RESUME_DUMP:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/runs/tw-mix-take8-$STAMP}
mkdir -p "$DUMP"

# GPU placement. Setting CUDA_VISIBLE_DEVICES here does NOT choose the devices.
# train.py's allocator hands each mesh an absolute range starting at 0 and
# overwrites CUDA_VISIBLE_DEVICES inside the spawned process before CUDA starts,
# so whatever is set here is discarded -- RL_GPUS=1,2,4,6,7 was observed running
# on physical 0-4. RL_GPU_OFFSET is the knob that moves that range, and it can
# only express a CONTIGUOUS window, so a set like 1,2,4,6,7 cannot be asked for
# at all. Keep RL_GPUS as the count and starting index, and refuse a value the
# allocator cannot honour rather than pretending to place devices it will ignore.
export RL_GPUS=${RL_GPUS:-0,1,2,3,4}
_gpu_first=${RL_GPUS%%,*}
_gpu_n=$(awk -F, '{print NF}' <<< "$RL_GPUS")
_gpu_expected=$(seq -s, "$_gpu_first" $((_gpu_first + _gpu_n - 1)))
export RL_GPU_OFFSET=${RL_GPU_OFFSET:-$_gpu_first}
if [ "$RL_GPUS" != "$_gpu_expected" ]; then
    echo "[launch] RL_GPUS=$RL_GPUS is not contiguous. The allocator takes" >&2
    echo "[launch] $_gpu_n consecutive GPUs from RL_GPU_OFFSET, so it would run" >&2
    echo "[launch] on $_gpu_expected regardless. Set RL_GPUS to a contiguous" >&2
    echo "[launch] window or pick a different one." >&2
    exit 2
fi
if [ "$RL_GPU_OFFSET" != "$_gpu_first" ]; then
    echo "[launch] RL_GPU_OFFSET=$RL_GPU_OFFSET disagrees with RL_GPUS starting" >&2
    echo "[launch] at $_gpu_first. The offset is what actually places the run." >&2
    exit 2
fi
export CUDA_VISIBLE_DEVICES=$RL_GPUS
export PYTHONPATH=$HOME/torchtitan
export SWE_PROMPT_DATA=${RL_DATA:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl}
export SWE_DATA_HOT_RELOAD=${SWE_DATA_HOT_RELOAD:-1}
export SWE_TASK_EVOLUTION_DIR=${SWE_TASK_EVOLUTION_DIR:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolution/signals}
mkdir -p "$SWE_TASK_EVOLUTION_DIR"

# GRPO shape - README values
export SWE_NUM_GROUPS_PER_TRAIN_STEP=${SWE_NUM_GROUPS_PER_TRAIN_STEP:-32}
export SWE_GROUP_SIZE=${SWE_GROUP_SIZE:-16}
export SWE_DROP_ZERO_STD=${SWE_DROP_ZERO_STD:-0}
export SWE_MAX_ACTIVE_GROUPS=${SWE_MAX_ACTIVE_GROUPS:-512}
export SWE_SELECTION_WINDOW_GROUPS=${SWE_SELECTION_WINDOW_GROUPS:-64}
export SWE_TRAIN_STEPS=${SWE_TRAIN_STEPS:-150}
export SWE_CKPT_INTERVAL=${SWE_CKPT_INTERVAL:-5}

# agent + context - README values
export TMAX_AGENT=${TMAX_AGENT:-terminus}
export TMAX_TERMINUS_MAX_TURNS=${TMAX_TERMINUS_MAX_TURNS:-120}
export SWE_MAX_CONTEXT_LEN=${SWE_MAX_CONTEXT_LEN:-63488}
export TMAX_TURN_MAX_TOKENS=${TMAX_TURN_MAX_TOKENS:-32768}
# README uses torchtitan_wrapper; on this box FA4-cute asserts "page_table is
# not supported with cu_seqlens_k" inside the wrapper varlen path -> vllm_native.
export SWE_GEN_BACKEND=${SWE_GEN_BACKEND:-vllm_native}

# throughput
export SWE_ROLLOUT_CONCURRENCY=${SWE_ROLLOUT_CONCURRENCY:-768}
export SWE_NUM_ROLLOUT_WORKERS=${SWE_NUM_ROLLOUT_WORKERS:-16}
export SWE_GDN=${SWE_GDN:-1}
export SWE_DISABLE_CUSTOM_ALL_REDUCE=${SWE_DISABLE_CUSTOM_ALL_REDUCE:-1}
export SWE_TIME_BUDGET_SEC=${SWE_TIME_BUDGET_SEC:-2400}
export TMAX_EXEC_TIMEOUT_SEC=${TMAX_EXEC_TIMEOUT_SEC:-120}
# Engine concurrency cap. 32 left engines with ~216 reqs queued and the state
# pool at 7% (GDN state is constant-size) -> raised. Override via rltrain.env.
export SWE_MAX_NUM_SEQS=${SWE_MAX_NUM_SEQS:-128}

# daytona, current platform behavior
export TT_DAYTONA_EPHEMERAL=${TT_DAYTONA_EPHEMERAL:-1}
export TT_DAYTONA_CPU=${TT_DAYTONA_CPU:-2}
export TT_DAYTONA_MEM_GB=${TT_DAYTONA_MEM_GB:-4}
export TT_DAYTONA_DISK_GB=${TT_DAYTONA_DISK_GB:-10}
export TT_DAYTONA_CREATE_CONCURRENCY=${TT_DAYTONA_CREATE_CONCURRENCY:-32}
export TT_DAYTONA_HEARTBEAT_SEC=${TT_DAYTONA_HEARTBEAT_SEC:-180}

# inline TB-2.0 eval - README values (minus the dedicated eval host)
export SWE_TB2_VAL_DATA=${SWE_TB2_VAL_DATA:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/tb2_eval.jsonl}
export SWE_VAL_INTERVAL=${SWE_VAL_INTERVAL:-20}
export SWE_VAL_SAMPLES=${SWE_VAL_SAMPLES:-89}
export SWE_NUM_EVAL_GENERATORS=${SWE_NUM_EVAL_GENERATORS:-0}

# 5-GPU split: 2 trainer + 3 generator engines
export SWE_DP_SHARD=${SWE_DP_SHARD:-2}
export SWE_GEN_DP=${SWE_GEN_DP:-3}

# W&B online: key in ~/.netrc (della-tridao has direct internet)
export WANDB_PROJECT=${WANDB_PROJECT:-terminal-agent-rl}
unset WANDB_MODE

echo "[launch] dump=$DUMP data=$SWE_PROMPT_DATA gpus=$CUDA_VISIBLE_DEVICES" | tee "$DUMP/launch.info"
env | grep -E "^(SWE_|TMAX_|TT_DAYTONA|CUDA_VISIBLE|WANDB_PROJECT)" | sort >> "$DUMP/launch.info"

cd "$DUMP"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 1 \
    --hf_assets_path /scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B 2>&1
