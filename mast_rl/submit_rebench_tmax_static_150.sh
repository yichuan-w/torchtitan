#!/usr/bin/env bash
# Reproduce the static SWE-ReBench + TMax Qwen3.5-9B baseline on H100 MAST.
#
# Training data is the 1,769-row subset of the release documented at commit
# a3113ebca: 1,317 SWE-ReBench rows plus 452 TMax rows. No synthesis or data
# evolution process is launched. TB 2.1 replaces the older TB 2.0 eval input so
# the evaluation path includes the current fixes.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
TMAX_ENV=${TMAX_ENV:-/home/shuhuay/.conda/envs/tmax}
MODE=baseline
if [[ "${1:-}" == "--smoke" ]]; then
    MODE=smoke
    shift
fi
RUN_TAG=${RUN_TAG:-rebench-tmax-static-s150-a3113-$(date -u +%Y%m%d-%H%M%S)}

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

# Exact published release subset from README_SEED_DATA.md at a3113ebca.
export SWE_PROMPT_DATA=mast_rl/swe_assets/rebench-tmax-a3113ebc.jsonl
export SWE_TB2_VAL_DATA=mast_rl/swe_assets/runbook_20260909/tb2_1_eval.jsonl
export SWE_HOLDOUT_N=0
export SWE_DATA_HOT_RELOAD=0
export SWE_EVOLUTION_SIGNALS=0

export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768
export TMAX_EXEC_TIMEOUT_SEC=120
export TMAX_EVAL_TIMEOUT_SEC=600
export SWE_TIME_BUDGET_SEC=2400
export SWE_AGENT_TIMEOUT_FLOOR_SEC=7200

# README topology: four HSDP trainer hosts, 12 independent single-GPU rollout
# generators, one single-GPU eval generator, and one controller host.
export SWE_DP_SHARD="${SWE_DP_SHARD:-8}"
export SWE_DP_REPLICATE="${SWE_DP_REPLICATE:-4}"
export SWE_GEN_DP="${SWE_GEN_DP:-1}"
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-12}"
export SWE_NUM_EVAL_GENERATORS="${SWE_NUM_EVAL_GENERATORS:-1}"
export SWE_EVAL_GEN_DP="${SWE_EVAL_GEN_DP:-1}"

export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0
export SWE_OFFPOLICY_STEPS=5
export SWE_MAX_ACTIVE_GROUPS=160
export SWE_INITIAL_ACTIVE_GROUPS=160
# Empty means take-any selection. The successful recorded run used this mode.
export SWE_SELECTION_WINDOW_GROUPS=""
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-2048}"
export SWE_NUM_ROLLOUT_WORKERS=16
export SWE_MAX_NUM_SEQS=32

export SWE_GEN_BACKEND=torchtitan_wrapper
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export SWE_TRAIN_STEPS=150
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
export TT_DAYTONA_CPU=2
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_CREATE_CONCURRENCY=8
export TT_DAYTONA_CREATE_RETRIES=8
export TT_DAYTONA_HEARTBEAT_SEC=180
export TT_DAYTONA_AUTO_DELETE_MIN=15
export SWE_PREFLIGHT_FANOUT=4

export NCCL_TRANSPORT=IB
export MONARCH_SUPERVISION_WATCHDOG_TIMEOUT=1000s
export MONARCH_HOST_SPAWN_READY_TIMEOUT=180s
export MONARCH_MESSAGE_DELIVERY_TIMEOUT=300s
export MONARCH_MESH_ORPHAN_TIMEOUT=24h
export MONARCH_MESH_PROC_SPAWN_MAX_IDLE=24h

MAX_RETRIES=10
LAUNCHER_ARGS=(
    --launcher.job-name-prefix "q35-9b-${RUN_TAG}"
    --launcher.host-type grandteton_80g_roce
    --launcher.wandb-team shuhuay
    --launcher.wandb-project torchtitan
    --launcher.max-retries "$MAX_RETRIES"
)

if [[ "$MODE" == smoke ]]; then
    export SWE_PROMPT_DATA=mast_rl/swe_assets/rebench-tmax-a3113ebc-smoke.jsonl
    export SWE_DP_SHARD=4
    export SWE_DP_REPLICATE=1
    export SWE_GEN_DP=1
    export SWE_NUM_GENERATORS=1
    export SWE_NUM_EVAL_GENERATORS=0
    export SWE_EVAL_GEN_DP=1
    export SWE_TRAIN_STEPS=1
    export SWE_NUM_GROUPS_PER_TRAIN_STEP=1
    export SWE_GROUP_SIZE=4
    export SWE_DROP_ZERO_STD=0
    export SWE_OFFPOLICY_STEPS=0
    export SWE_MAX_ACTIVE_GROUPS=4
    export SWE_INITIAL_ACTIVE_GROUPS=1
    export SWE_SELECTION_WINDOW_GROUPS=""
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
    LAUNCHER_ARGS=(
        --launcher.job-name-prefix "q35-9b-${RUN_TAG}"
        --launcher.host-type grandteton_80g_roce
        --launcher.wandb-team shuhuay
        --launcher.wandb-project torchtitan
        --launcher.max-retries "$MAX_RETRIES"
        --launcher.collocate-on-controller
    )
fi

export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/shuhuay/mast_runs/q35_9b_${RUN_TAG}}"

cd "$REPO_ROOT"
exec bash mast_rl/submit_swe_tmax_9b.sh \
    "${LAUNCHER_ARGS[@]}" \
    "$@"
