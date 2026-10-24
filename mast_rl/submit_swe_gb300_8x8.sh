#!/bin/bash
# Submit the Qwen3-32B SWE-R2E "reward-rise" run to MAST GB300 at slime 8x8 scale.
#
# config rl_grpo_qwen3_32b_swe_r2e_gb300_8x8: 8 prompts x 8 samples = 64 rollouts/step,
# global_batch_size=64 (FSDP-4 -> gas=16), num_steps=10, num_generators=2
# = 1 controller + 1 trainer + 2 generator = 4 GB300 hosts.
#
# Uses the curated multi-task SOLVABLE dataset (mast_rl/swe_assets/r2e_solvable.jsonl,
# 7 orange3/aiohttp tasks) so reward has signal + within-group variance and can rise.
#
# Run FROM the aarch64 build host (needs the aarch64 env). Prereqs:
#   conda activate rlmast_arm
#   export DAYTONA_API_KEY=dtn_...
#   bash mast_rl/submit_swe_gb300_8x8.sh [--no-reinstall]
set -u

export RLMAST_ENV="${RLMAST_ENV:-rlmast_arm}"

if [[ -z "${DAYTONA_API_KEY:-}" ]]; then
    echo "ERROR: export DAYTONA_API_KEY (app.daytona.io key) before submitting." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

export SWE_RUN=1
export DAYTONA_API_KEY
export SWE_PROXY="${SWE_PROXY:-http://fwdproxy:8080}"
# Dataset (relative to repo root, where run.sh cd's). Default: the 47-task
# R2E-Gym-Lite set (diverse, unknown solvability). For a likelier *visible* reward
# rise over 10 steps, swap to r2e_solvable.jsonl (7 curated tasks, repeated).
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-mast_rl/swe_assets/r2e_train.jsonl}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-900}"
export SWE_EVAL_TIMEOUT_SEC="${SWE_EVAL_TIMEOUT_SEC:-400}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-20480}"
# 64 rollouts/step. Keep Daytona concurrency moderate -- the account showed
# provision retries even at 8-way; group_size=8 means a few failures still leave
# real attempts per task. Collected in waves of SWE_ROLLOUT_CONCURRENCY.
export SWE_BOOT_CONCURRENCY="${SWE_BOOT_CONCURRENCY:-6}"
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-16}"
export SHIM_PORT="${SHIM_PORT:-18031}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-2}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-5}"
SETTINGS_JSON="{\"permissions\":{\"defaultMode\":\"bypassPermissions\"},\"autoCompactEnabled\":true,\"autoCompactWindow\":16000}"
export SWE_CLAUDE_EXTRA_ARGS="${SWE_CLAUDE_EXTRA_ARGS:---settings '${SETTINGS_JSON}' --disable-slash-commands --disallowedTools WebFetch WebSearch Task Agent AskUserQuestion}"

export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"

exec bash "$(dirname "$0")/submit.sh" \
    --config rl_grpo_qwen3_32b_swe_r2e_gb300_8x8 --num_generators 2 "$@"
