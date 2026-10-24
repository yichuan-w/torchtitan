#!/usr/bin/env bash
# Qwen3.5-9B static ReBench-only baseline. Use --smoke for a one-step,
# one-host task-path check before submitting the 200-step run.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
TMAX_ENV=${TMAX_ENV:-/home/shuhuay/.conda/envs/tmax}
MODE=baseline
if [[ "${1:-}" == "--smoke" ]]; then
    MODE=smoke
    shift
fi
RUN_TAG=${RUN_TAG:-rebench-only-s200-$(date -u +%Y%m%d-%H%M%S)}

export PATH="$TMAX_ENV/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export CONDA_PREFIX="$TMAX_ENV"
export CONDA_DEFAULT_ENV=tmax
export RLMAST_ENV=tmax
export TORCHTITAN_SRC="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -z "${DAYTONA_API_KEY:-}" && -s /home/shuhuay/.daytona_env ]]; then
    set -a
    # shellcheck disable=SC1091
    . /home/shuhuay/.daytona_env
    set +a
fi
: "${DAYTONA_API_KEY:?set DAYTONA_API_KEY before submitting}"

export SWE_PROMPT_DATA=mast_rl/swe_assets/rebench-only-a6d8f8ea.jsonl
export SWE_TB2_VAL_DATA=mast_rl/swe_assets/runbook_20260909/tb2_1_eval.jsonl
export SWE_HOLDOUT_N=0
export SWE_DATA_HOT_RELOAD=0

export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768
export TMAX_EXEC_TIMEOUT_SEC=120
export TMAX_EVAL_TIMEOUT_SEC=600
export SWE_TIME_BUDGET_SEC=2400
export SWE_AGENT_TIMEOUT_FLOOR_SEC=7200

# Match the committed ReBench baseline except for the H100 topology: one FSDP-8
# trainer host, six DP-8 generator hosts, and one DP-8 evaluation host.
export SWE_DP_SHARD=8
export SWE_GEN_DP=8
export SWE_NUM_GENERATORS=6
export SWE_NUM_EVAL_GENERATORS=1
export SWE_EVAL_GEN_DP=8
export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=12
export SWE_DROP_ZERO_STD=1
export SWE_OFFPOLICY_STEPS=4
export SWE_MAX_ACTIVE_GROUPS=160
export SWE_INITIAL_ACTIVE_GROUPS=128
# Set (rather than unset) the empty value so the older submit wrapper does not
# replace it with its historical 20-group default. Empty means take-any.
export SWE_SELECTION_WINDOW_GROUPS=""
export SWE_ROLLOUT_CONCURRENCY=1000
export SWE_NUM_ROLLOUT_WORKERS=16
export SWE_MAX_NUM_SEQS=32

export SWE_LOSS=dppo
export SWE_LR=3e-6
export SWE_AC=full
export SWE_LOSS_CHUNKS=32
export SWE_LMHEAD_TF32=1
export SWE_GEN_BACKEND=vllm_native
export SWE_GEN_PREFIX_CACHE=1
export SWE_GEN_VLLM_DEFAULT_COMPILE=1
export SWE_GPU_MEM_LIMIT=0.88
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export VLLM_ALLREDUCE_USE_FLASHINFER=0
export VLLM_USE_FLASHINFER_SAMPLER=0

export SWE_TRAIN_STEPS=200
export SWE_CKPT_INTERVAL=5
export SWE_CKPT_KEEP=2
export SWE_CKPT_ASYNC=async
export SWE_VAL_INTERVAL=20
export SWE_VAL_SAMPLES=89
export SWE_TB2_VAL_K=5
export SWE_NUM_EVAL_ROLLOUT_WORKERS=4
export SWE_EVAL_ROLLOUT_CONCURRENCY=64
export SWE_VAL_TRACES=1

export TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CPU=1
export TT_DAYTONA_MEM_GB=2
export TT_DAYTONA_MAX_MEM_GB=8
export TT_DAYTONA_DISK_GB=2
export TT_DAYTONA_CREATE_CONCURRENCY=8
export TT_DAYTONA_CREATE_RETRIES=8
export TT_DAYTONA_HEARTBEAT_SEC=180
export TT_DAYTONA_AUTO_DELETE_MIN=15
export TT_DAYTONA_TTL_MIN=480
export SWE_PREFLIGHT_FANOUT=4

export MONARCH_SUPERVISION_WATCHDOG_TIMEOUT=1000s
export MONARCH_HOST_SPAWN_READY_TIMEOUT=180s
export MONARCH_MESSAGE_DELIVERY_TIMEOUT=300s
export MONARCH_MESH_ORPHAN_TIMEOUT=24h
export MONARCH_MESH_PROC_SPAWN_MAX_IDLE=24h

MAX_RETRIES=10
if [[ "$MODE" == smoke ]]; then
    export SWE_INCLUDE_PROMPTS=mast_rl/swe_assets/rebench-only-smoke-ids.txt
    export SWE_DP_SHARD=4
    export SWE_GEN_DP=1
    export SWE_NUM_GENERATORS=1
    export SWE_NUM_EVAL_GENERATORS=0
    export SWE_EVAL_GEN_DP=1
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
    export SWE_VAL_SAMPLES=0
    export TMAX_TERMINUS_MAX_TURNS=4
    export SWE_MAX_CONTEXT_LEN=8192
    export TMAX_TURN_MAX_TOKENS=2048
    export SWE_TIME_BUDGET_SEC=300
    export SWE_AGENT_TIMEOUT_FLOOR_SEC=300
    export SWE_CKPT_INTERVAL=999
    export SWE_PREFLIGHT_FANOUT=1
    RUN_TAG="${RUN_TAG}-smoke"
    MAX_RETRIES=0
fi

export SWE_DUMP_DIR="/mnt/torchtrain_datasets/tree/shuhuay/mast_runs/q35_9b_${RUN_TAG}"
LAUNCHER_ARGS=(
    --launcher.job-name-prefix "q35-9b-${RUN_TAG}"
    --launcher.host-type grandteton_80g_roce
    --launcher.wandb-team shuhuay
    --launcher.wandb-project torchtitan
    --launcher.max-retries "$MAX_RETRIES"
)
if [[ "$MODE" == smoke ]]; then
    LAUNCHER_ARGS+=(--launcher.collocate-on-controller)
fi

cd "$REPO_ROOT"
exec bash mast_rl/submit_swe_tmax_9b.sh \
    "${LAUNCHER_ARGS[@]}" \
    "$@"
