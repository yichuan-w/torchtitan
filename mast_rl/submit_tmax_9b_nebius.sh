#!/bin/bash
# Qwen3.5-9B TMax terminal-agent RL on Nebius GB300 (msl_infra_pytorch_dct, ofn).
#
# Topology (default 5 hosts = 20 GB300):
#   controller host = controller + rollout workers + trainer FSDP-4
#   SWE_GEN_HOSTS (default 4) generator hosts, each packing four DP-1 vLLM engines
# SWE_GEN_HOSTS=3 gives the 4-host shape.
#
# The generator settings are the 8x B300 ones (runbook/RUNBOOK-yichuan-B300.md):
# independent DP-1 engines, vllm_native + FLASH_ATTN (no FlashInfer JIT on SM10x),
# and a high per-engine decode cap -- one GB300 engine has ~200 GiB of KV, so
# concurrency is bounded by rollout admission, not by the engines.
#
# The batch is shaped for a ~5 minute step rather than for the paper's 8x32:
# 16 groups x 12 rollouts with zero-std groups kept (their samples are skipped in
# the forward pass), so a step waits on 16-rollout group tails instead of on
# replacement groups. Override any knob by exporting it first.
#
#   bash mast_rl/submit_tmax_9b_nebius.sh [--no-reinstall]
# conda activate hooks reference unset vars, so source before `set -u`.
source "$(dirname "$0")/nebius_env.sh"
set -u

if [[ -z "${DAYTONA_API_KEY:-}" ]]; then
    echo "ERROR: put the Daytona key in ~/.daytona_key or export DAYTONA_API_KEY." >&2
    exit 1
fi

SWE_GEN_HOSTS="${SWE_GEN_HOSTS:-4}"
export SWE_CONFIG=rl_grpo_qwen3_5_9b_tmax_nebius

# --- topology ---
export SWE_DP_SHARD="${SWE_DP_SHARD:-4}"
export SWE_GEN_DP=1
export SWE_PACK_GENERATORS=1
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-$((SWE_GEN_HOSTS * 4))}"
# Four single-GPU engines share a host: keep each on its physical GPU ordinal and
# stage the weight pull through CPU.
export SWE_PACKED_GPU_ORDINAL_FIX=1
export SWE_CPU_STAGED_WEIGHT_PULL="${SWE_CPU_STAGED_WEIGHT_PULL:-1}"

# --- generator (B300 settings) ---
export SWE_GDN=1
export SWE_GEN_BACKEND=vllm_native
export SWE_GEN_VLLM_ATTENTION="${SWE_GEN_VLLM_ATTENTION:-FLASH_ATTN}"
export VLLM_ALLREDUCE_USE_FLASHINFER=0
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export SWE_GPU_MEM_LIMIT="${SWE_GPU_MEM_LIMIT:-0.88}"
export SWE_MAX_NUM_SEQS="${SWE_MAX_NUM_SEQS:-256}"
export SWE_GEN_PREFIX_CACHE="${SWE_GEN_PREFIX_CACHE:-1}"
export SWE_DP_FALLBACK_ROUTER="${SWE_DP_FALLBACK_ROUTER:-leastloaded}"

# --- trainer speed (B300 runbook section 7) ---
export SWE_AC="${SWE_AC:-selective}"
export SWE_LOSS_CHUNKS="${SWE_LOSS_CHUNKS:-8}"
export SWE_LMHEAD_TF32="${SWE_LMHEAD_TF32:-1}"

# --- batch shape and admission ---
export SWE_NUM_GROUPS_PER_TRAIN_STEP="${SWE_NUM_GROUPS_PER_TRAIN_STEP:-16}"
export SWE_GROUP_SIZE="${SWE_GROUP_SIZE:-12}"
export SWE_DROP_ZERO_STD="${SWE_DROP_ZERO_STD:-0}"
export SWE_OFFPOLICY_STEPS="${SWE_OFFPOLICY_STEPS:-4}"
# Collection is bound by group tails, not the engines (KV ~10% at this load), so
# admission sits at the staleness cap: (off + 1) x groups = 80 groups. 840 / 10
# workers = 84 each = 7 groups + 1 queued per worker = 80, which the recipe's
# work-conserving check requires.
export SWE_MAX_ACTIVE_GROUPS="${SWE_MAX_ACTIVE_GROUPS:-80}"
export SWE_INITIAL_ACTIVE_GROUPS="${SWE_INITIAL_ACTIVE_GROUPS:-80}"
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-840}"
export SWE_NUM_ROLLOUT_WORKERS="${SWE_NUM_ROLLOUT_WORKERS:-10}"
# With off + 1 steps in flight, a step lasts about a group tail / (off + 1), and
# the tail is bounded by the rollout budget: 1200 s / 5 keeps a step near 4 min.
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-1200}"
export SWE_TRAIN_STEPS="${SWE_TRAIN_STEPS:-20}"
export SWE_LR="${SWE_LR:-3e-6}"

# --- agent / data ---
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
# The agentpick TB training mix (run_tmax_evolving_agentpick_both.sh's default):
# every row bakes tmux into its Dockerfile, which the terminus agent needs.
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/agentpick_clean_cf_58d825a3.jsonl}"
export TMAX_CONTEXT_NOTICE_STYLE="${TMAX_CONTEXT_NOTICE_STYLE:-della}"
export TMAX_EXEC_TIMEOUT_SEC="${TMAX_EXEC_TIMEOUT_SEC:-120}"
export TMAX_EVAL_TIMEOUT_SEC="${TMAX_EVAL_TIMEOUT_SEC:-600}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-63488}"

# --- no validation, evolution, or checkpoints: a step-time run ---
export SWE_VAL_SAMPLES=0
export SWE_HOLDOUT_N=0
export SWE_DATA_HOT_RELOAD=0
export SWE_EVOLUTION_SIGNALS=0
export SWE_CKPT_INTERVAL="${SWE_CKPT_INTERVAL:-1000}"
export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax_nebius_$(date +%m%d_%H%M)}"

# --- Daytona ---
export TT_DAYTONA_EPHEMERAL="${TT_DAYTONA_EPHEMERAL:-1}"
export TT_DAYTONA_CREATE_CONCURRENCY="${TT_DAYTONA_CREATE_CONCURRENCY:-32}"
export TT_DAYTONA_CREATE_RETRIES="${TT_DAYTONA_CREATE_RETRIES:-8}"
export TT_DAYTONA_CPU="${TT_DAYTONA_CPU:-2}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-4}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-10}"
export TT_DAYTONA_HEARTBEAT_SEC="${TT_DAYTONA_HEARTBEAT_SEC:-180}"
export TT_DAYTONA_AUTO_DELETE_MIN="${TT_DAYTONA_AUTO_DELETE_MIN:-15}"
export TT_DAYTONA_RPC_RETRIES="${TT_DAYTONA_RPC_RETRIES:-2}"

# Weight sync on aarch64 falls back to gloo (run.sh); NCCL only carries the
# single-host trainer FSDP, so Socket is enough.
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"
export MONARCH_HOST_SPAWN_READY_TIMEOUT="${MONARCH_HOST_SPAWN_READY_TIMEOUT:-300s}"
export MONARCH_MESSAGE_DELIVERY_TIMEOUT="${MONARCH_MESSAGE_DELIVERY_TIMEOUT:-600s}"

echo "nebius 9B tmax: $((1 + SWE_GEN_HOSTS)) hosts, trainer FSDP-${SWE_DP_SHARD}," \
    "${SWE_NUM_GENERATORS} DP-1 engines, ${SWE_NUM_GROUPS_PER_TRAIN_STEP}x${SWE_GROUP_SIZE}," \
    "concurrency ${SWE_ROLLOUT_CONCURRENCY}, dump ${SWE_DUMP_DIR}"
exec bash "$(dirname "$0")/submit_swe.sh" --hf_assets_path /mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.5-9B "$@"
