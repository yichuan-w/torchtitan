#!/usr/bin/bash
# Launch the README TerminalWorld + SWE-Smith Qwen3.5-9B Terminus-2 run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMAX_ENV="${TMAX_ENV:-/home/shuhuay/.conda/envs/tmax}"
RUN_TAG="${RUN_TAG:-readme-v1-20260820}"

export PATH="${TMAX_ENV}/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export CONDA_PREFIX="${TMAX_ENV}"
export CONDA_DEFAULT_ENV=tmax
export RLMAST_ENV=tmax
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

set -a
source /home/shuhuay/.daytona_env
set +a
: "${DAYTONA_API_KEY:?DAYTONA_API_KEY must be set in /home/shuhuay/.daytona_env}"

export https_proxy="${https_proxy:-http://fwdproxy:8080}"
export HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy}}"
export MONARCH_SUPERVISION_WATCHDOG_TIMEOUT=1000s
export MONARCH_HOST_SPAWN_READY_TIMEOUT=180s
export MONARCH_MESSAGE_DELIVERY_TIMEOUT=300s
export LITELLM_LOG=ERROR
export LITELLM_SUPPRESS_DEBUG_INFO=1

export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768
export SWE_GEN_BACKEND=torchtitan_wrapper

# Latest oracle-filtered mix: 853 TerminalWorld + 1,244 SWE-Smith tasks.
export SWE_PROMPT_DATA=/mnt/torchtrain_datasets/tree/yichuan/rts_data/mix_tw859_swe_v3clean.jsonl

export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0
export SWE_MAX_ACTIVE_GROUPS=512
export SWE_TRAIN_STEPS=150
# The committed README uses take-any selection rather than a bounded window.
export SWE_SELECTION_WINDOW_GROUPS=""

export SWE_DP_REPLICATE=2
export SWE_ROLLOUT_CONCURRENCY=1280
export SWE_NUM_ROLLOUT_WORKERS=16
export SWE_NUM_GENERATORS=12
export SWE_CKPT_INTERVAL=5

export TT_DAYTONA_CPU=2
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_HEARTBEAT_SEC=180
export TT_DAYTONA_CREATE_CONCURRENCY=8

# Keep validation off the training controller. Submit TB-2.0 eval jobs separately.
export SWE_VAL_SAMPLES=0
export SWE_NUM_EVAL_GENERATORS=0

export SWE_DUMP_DIR="/mnt/torchtrain_datasets/tree/shuhuay/mast_runs/q35_9b_mix_tw_swe_terminus_${RUN_TAG}"

cd "${REPO_ROOT}"
exec bash mast_rl/submit_swe_tmax_9b.sh \
  --launcher.job-name-prefix "q35-9b-tw-swe-terminus-${RUN_TAG}" \
  --launcher.wandb-team shuhuay \
  --launcher.wandb-project torchtitan \
  --launcher.max-retries 100 \
  "$@"
