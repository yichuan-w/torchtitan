#!/bin/bash
# take7: README_TERMINALWORLD-aligned run on della-tridao (single host, 5 GPUs).
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
DUMP=${RL_RESUME_DUMP:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/runs/tw-mix-take7-$STAMP}
mkdir -p "$DUMP"

export CUDA_VISIBLE_DEVICES=${RL_GPUS:-1,2,4,6,7}
# The checkout this script sits in, not a fixed path: $HOME/torchtitan is a
# stale tree on della (13 PRs behind the canonical branch as of 2026-09-04)
# and nothing running reads it, so a run launched against it would train old
# code with nothing in the log to say so.
export PYTHONPATH=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
export SWE_PROMPT_DATA=${RL_DATA:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl}
export SWE_DATA_HOT_RELOAD=1
export SWE_TASK_EVOLUTION_DIR=/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolution/signals
mkdir -p "$SWE_TASK_EVOLUTION_DIR"

# GRPO shape - README values
export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0
export SWE_MAX_ACTIVE_GROUPS=512
export SWE_SELECTION_WINDOW_GROUPS=64
export SWE_TRAIN_STEPS=150
export SWE_CKPT_INTERVAL=5

# agent + context - README values
export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768
# README uses torchtitan_wrapper; on this box FA4-cute asserts "page_table is
# not supported with cu_seqlens_k" inside the wrapper varlen path -> vllm_native.
export SWE_GEN_BACKEND=${SWE_GEN_BACKEND:-vllm_native}

# throughput
export SWE_ROLLOUT_CONCURRENCY=768
export SWE_NUM_ROLLOUT_WORKERS=16
export SWE_GDN=1
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export SWE_TIME_BUDGET_SEC=2400
export TMAX_EXEC_TIMEOUT_SEC=120
export SWE_MAX_NUM_SEQS=32

# daytona, current platform behavior
export TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CPU=2
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_CREATE_CONCURRENCY=8
export TT_DAYTONA_HEARTBEAT_SEC=180

# inline TB-2.0 eval - README values (minus the dedicated eval host)
export SWE_TB2_VAL_DATA=/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/tb2_eval.jsonl
export SWE_VAL_INTERVAL=20
export SWE_VAL_SAMPLES=89
export SWE_NUM_EVAL_GENERATORS=0

# 5-GPU split: 2 trainer + 3 generator engines
export SWE_DP_SHARD=2
export SWE_GEN_DP=3

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
