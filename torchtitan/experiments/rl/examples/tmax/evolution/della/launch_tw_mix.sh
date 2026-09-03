#!/bin/bash
# Max single-host tmax RL run on della-tridao. Shared box: pick free GPUs at
# launch via CUDA_VISIBLE_DEVICES below; logical 0-3 = FSDP-4 trainer, logical
# 4 = one TP-1 vLLM generator (the 2026-08-08-validated shape, scaled up).
set -euo pipefail
. /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/activate
. ~/.config/daytona/env
cd ~/torchtitan

STAMP=$(date +%Y%m%d-%H%M%S)
# RL_RESUME_DUMP reuses a previous run dir -> checkpoint auto-resume.
DUMP=${RL_RESUME_DUMP:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/runs/tw-mix-$STAMP}
mkdir -p "$DUMP"

export CUDA_VISIBLE_DEVICES=${RL_GPUS:-1,2,4,6,7}
export PYTHONPATH=$HOME/torchtitan
# The LIVE data path: the online evolution consumer atomically replaces this
# file and SWE_DATA_HOT_RELOAD picks it up mid-run - no restart.
export SWE_PROMPT_DATA=${RL_DATA:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl}
export SWE_DATA_HOT_RELOAD=1

# Online task evolution: every 0/k and k/k group drops a signal here; the
# data-side consumer re-tunes those tasks and folds them back into the live
# data path. This is the loop actually being online.
export SWE_TASK_EVOLUTION_DIR=/scratch/gpfs/TRIDAO/al9080/terminal-rl/evolution/signals
mkdir -p "$SWE_TASK_EVOLUTION_DIR"

# GRPO shape - maxed for one host + one generator engine
export SWE_NUM_GROUPS_PER_TRAIN_STEP=8
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0
export SWE_MAX_ACTIVE_GROUPS=128
export SWE_TRAIN_STEPS=150
export SWE_CKPT_INTERVAL=5

# throughput
export SWE_ROLLOUT_CONCURRENCY=1024
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

# inline eval off (decoupled per README)
export SWE_VAL_SAMPLES=0
export SWE_NUM_EVAL_GENERATORS=0

# trainer FSDP width: registry-native env override (base FSDP-8 needs 8 GPUs)
export SWE_DP_SHARD=2
# generator engines: rollout decode is the bottleneck (trainer idled 97% at
# FSDP-4 + 1 engine); 2 trainer GPUs + 3 engines rebalances the same 5 GPUs
export SWE_GEN_DP=3

# No W&B credentials on this box; record offline (syncable later), never prompt.
export WANDB_MODE=offline

echo "[launch] dump=$DUMP data=$SWE_PROMPT_DATA gpus=$CUDA_VISIBLE_DEVICES" | tee "$DUMP/launch.info"
env | grep -E "^(SWE_|TMAX_|TT_DAYTONA|CUDA_VISIBLE)" | sort >> "$DUMP/launch.info"

# Relative outputs/rl (checkpoints, traces) land under this run dir - fresh per
# run, so no cross-recipe checkpoint auto-resume.
cd "$DUMP"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 1 \
    --hf_assets_path /scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B 2>&1
