#!/usr/bin/env bash
# One-step MAST smoke for the TerminalWorld + TMax runbook data and TB 2.1 eval.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
TMAX_ENV=${TMAX_ENV:-/home/shuhuay/.conda/envs/tmax}
RUN_TAG=${RUN_TAG:-runbook-smoke-$(date -u +%Y%m%d-%H%M%S)}

export PATH="$TMAX_ENV/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export CONDA_PREFIX=$TMAX_ENV
export CONDA_DEFAULT_ENV=tmax
export RLMAST_ENV=tmax
export TORCHTITAN_SRC=${TORCHTITAN_SRC:-/home/shuhuay/rl/torchtitan-runbook-audit}
export PYTHONPATH="$TORCHTITAN_SRC${PYTHONPATH:+:$PYTHONPATH}"

if [ -z "${DAYTONA_API_KEY:-}" ] && [ -s /home/shuhuay/.daytona_env ]; then
    set -a
    # shellcheck disable=SC1091
    . /home/shuhuay/.daytona_env
    set +a
fi
: "${DAYTONA_API_KEY:?set DAYTONA_API_KEY before submitting}"

export SWE_PROMPT_DATA=mast_rl/swe_assets/runbook_20260909/terminalworld-tmax-reaudit-seed.jsonl
export SWE_INCLUDE_PROMPTS=mast_rl/swe_assets/runbook_20260909/smoke-train-ids.txt
export SWE_TB2_VAL_DATA=mast_rl/swe_assets/runbook_20260909/tb2_1_eval.jsonl

# One H100 host: four trainer ranks, one rollout engine, and one eval engine.
export SWE_DP_SHARD=4
export SWE_GEN_DP=1
export SWE_NUM_GENERATORS=1
export SWE_NUM_EVAL_GENERATORS=1
export SWE_EVAL_GEN_DP=1

# Functional smoke only: one small optimizer step and one TB 2.1 task.
export SWE_TRAIN_STEPS=1
export SWE_NUM_GROUPS_PER_TRAIN_STEP=1
export SWE_GROUP_SIZE=4
export SWE_DROP_ZERO_STD=0
export SWE_MAX_ACTIVE_GROUPS=8
export SWE_INITIAL_ACTIVE_GROUPS=1
export SWE_SELECTION_WINDOW_GROUPS=4
export SWE_ROLLOUT_CONCURRENCY=4
export SWE_NUM_ROLLOUT_WORKERS=1
export SWE_MAX_NUM_SEQS=4
export SWE_VAL_SAMPLES=1
export SWE_TB2_VAL_K=1
export SWE_VAL_INTERVAL=1
export SWE_NUM_EVAL_ROLLOUT_WORKERS=1
export SWE_EVAL_ROLLOUT_CONCURRENCY=1

export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=4
export SWE_MAX_CONTEXT_LEN=8192
export TMAX_TURN_MAX_TOKENS=2048
export SWE_TB2_VAL_MAX_TOKENS=2048
export SWE_TIME_BUDGET_SEC=300
export TMAX_EXEC_TIMEOUT_SEC=120
export TMAX_EVAL_TIMEOUT_SEC=300
export SWE_GEN_BACKEND=vllm_native
export SWE_GEN_VLLM_DEFAULT_COMPILE=1
export SWE_GEN_PREFIX_CACHE=1
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export SWE_CKPT_INTERVAL=999

export TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CPU=1
export TT_DAYTONA_MEM_GB=2
export TT_DAYTONA_MAX_MEM_GB=8
export TT_DAYTONA_DISK_GB=2
export TT_DAYTONA_CREATE_CONCURRENCY=1
export TT_DAYTONA_CREATE_RETRIES=8
export TT_DAYTONA_HEARTBEAT_SEC=180
export TT_DAYTONA_AUTO_DELETE_MIN=15
export SWE_PREFLIGHT_FANOUT=1

export SWE_DUMP_DIR="/mnt/torchtrain_datasets/tree/shuhuay/mast_runs/q35_9b_tw_tmax_tb21_${RUN_TAG}"
export MONARCH_SUPERVISION_WATCHDOG_TIMEOUT=1000s
export MONARCH_HOST_SPAWN_READY_TIMEOUT=180s
export MONARCH_MESSAGE_DELIVERY_TIMEOUT=300s

cd "$REPO_ROOT"
exec bash mast_rl/submit_swe_tmax_9b.sh \
    --launcher.job-name-prefix "q35-9b-tw-tmax-tb21-${RUN_TAG}" \
    --launcher.collocate-on-controller \
    --launcher.wandb-team shuhuay \
    --launcher.wandb-project torchtitan \
    --launcher.max-retries 0 \
    "$@"
