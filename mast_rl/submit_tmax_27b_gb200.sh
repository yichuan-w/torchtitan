#!/bin/bash
# Qwen3.5-27B tmax terminal-agent RL on MAST GB200. Run FROM the aarch64 host.
#
# Mirrors the H100 9B run (rts_work/submit_terminus_conc2048.sh): Terminus-2, full
# RTS pool, 64x8 = 512 rollouts/step, drop_zero_std, TB-2.0 inline validation every
# 10 steps. The recipe itself is rl_grpo_qwen3_5_9b_tmax with the 27B model + flex
# attention, so fp32 master params / fp32 AdamW / DPPO chunked-32 loss are inherited.
#
# 12 hosts: 1 controller + 4 trainer (FSDP-8, 2 GPU/host) + 7 generator (TP-1).
# The 9B H100 run uses 12 generators; only 7 fit here alongside the trainer.
set -u
export RLMAST_ENV="${RLMAST_ENV:-rlmast_arm}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

export SWE_RUN=1
export SWE_GDN=1
export DAYTONA_API_KEY="${DAYTONA_API_KEY:-$(cat ~/.daytona_key 2>/dev/null)}"
export SWE_PROXY="${SWE_PROXY:-http://fwdproxy:8080}"
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"
# The UNIFIED torchtitan model inside vLLM, not vLLM's own Qwen3.5. Two reasons
# beyond train/infer parity: the wrapper path PINS the vLLM attention backend
# from the ModelSpec (FlexAttention.Config -> FLEX_ATTENTION), which is what
# keeps FlashInfer -- whose sm_100a trtllm FMHA kernel fails to JIT in this
# container ("CUDA compiler and CUDA toolkit headers are incompatible") -- out
# of the picture; and it builds the model from ModelSpec instead of reading
# config.json, so the checkpoint's vision tower is never instantiated.
export SWE_GEN_BACKEND="${SWE_GEN_BACKEND:-torchtitan_wrapper}"
# vLLM's flex backend sizes its Triton tiles from GPU shared memory alone and
# ignores head_dim, so Qwen3.5 (head_dim 256) asks 552KB against sm_100's 227KB
# and inductor dies with "out of resource: triton_tem_fused_flex_attention".
# TRITON_ATTN does not have that heuristic.
export SWE_GEN_VLLM_ATTENTION="${SWE_GEN_VLLM_ATTENTION:-TRITON_ATTN}"

# harness / context -- same as the H100 run
export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768
export TMAX_EVAL_TIMEOUT_SEC="${TMAX_EVAL_TIMEOUT_SEC:-600}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-2400}"

# data -- full RTS pool (36130)
export SWE_PROMPT_DATA=/mnt/torchtrain_datasets/tree/yichuan/rts_data/rts_v2_all.jsonl

# batch / schedule -- same as the H100 run
export SWE_NUM_GROUPS_PER_TRAIN_STEP="${SWE_NUM_GROUPS_PER_TRAIN_STEP:-32}"
export SWE_GROUP_SIZE="${SWE_GROUP_SIZE:-8}"
export SWE_TRAIN_STEPS="${SWE_TRAIN_STEPS:-100}"
export SWE_MAX_ACTIVE_GROUPS="${SWE_MAX_ACTIVE_GROUPS:-512}"
export SWE_DROP_ZERO_STD="${SWE_DROP_ZERO_STD:-1}"

# concurrency. NOT the H100 run's 2048: GB200 is in nao, far from Daytona, and the
# controller polls every sandbox on one asyncio loop. The GB300 run measured 16 as
# saturating; 512 is a deliberate middle ground to be measured, not trusted.
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-1024}"
export SWE_NUM_ROLLOUT_WORKERS="${SWE_NUM_ROLLOUT_WORKERS:-16}"
# 16 workers x 4 = 64 concurrent sandbox creates. Daytona's create limit is
# ~600/60s, and at 8 per worker (128 concurrent) a conc-1024 start floods it:
# 20x HTTP 429 plus a retry storm, and the first completed group came back with
# all 8 siblings status=error / turns=0 -- no sandbox, not a model failure.
export TT_DAYTONA_CREATE_CONCURRENCY="${TT_DAYTONA_CREATE_CONCURRENCY:-4}"
export TT_DAYTONA_CPU="${TT_DAYTONA_CPU:-2}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-4}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-10}"
export SHIM_PORT="${SHIM_PORT:-18041}"
export SWE_PREFLIGHT_TIMEOUT="${SWE_PREFLIGHT_TIMEOUT:-900}"

# TB-2.0 inline validation every 10 steps on its own generator host.
export SWE_TB2_VAL_DATA="${SWE_TB2_VAL_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl}"
export SWE_VAL_INTERVAL="${SWE_VAL_INTERVAL:-10}"
export SWE_NUM_EVAL_GENERATORS="${SWE_NUM_EVAL_GENERATORS:-1}"
export SWE_NUM_EVAL_ROLLOUT_WORKERS="${SWE_NUM_EVAL_ROLLOUT_WORKERS:-4}"
export SWE_EVAL_ROLLOUT_CONCURRENCY="${SWE_EVAL_ROLLOUT_CONCURRENCY:-64}"

export MONARCH_MESH_ORPHAN_TIMEOUT="${MONARCH_MESH_ORPHAN_TIMEOUT:-24h}"
export MONARCH_MESH_PROC_SPAWN_MAX_IDLE="${MONARCH_MESH_PROC_SPAWN_MAX_IDLE:-24h}"

export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_27b_gb200_t100}"

echo "config=rl_grpo_qwen3_5_27b_tmax_gb200 steps=$SWE_TRAIN_STEPS val_interval=$SWE_VAL_INTERVAL"
echo "groups=$SWE_NUM_GROUPS_PER_TRAIN_STEP x $SWE_GROUP_SIZE conc=$SWE_ROLLOUT_CONCURRENCY gens=6+1eval"
exec bash "$(dirname "$0")/submit.sh" \
    --config rl_grpo_qwen3_5_27b_tmax_gb200 --num_generators 6 "$@"
