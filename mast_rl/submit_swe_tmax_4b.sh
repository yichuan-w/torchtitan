#!/bin/bash
# Submit the DENSE Qwen3-4B tmax terminal-agent RL job to MAST in BATCH-INVARIANT
# mode -- a numerics control for the Qwen3.5-9B GDN run (submit_swe_tmax_9b.sh).
#
# Same tmax recipe/topology as the 9B (trainer FSDP-8 + generator DP-8 x TP-1 +
# controller, host_loop, group_size 32) but the model is the DENSE Qwen3-4B (no
# Gated DeltaNet) and both actors run debug.batch_invariant + deterministic. The
# generator runs the SAME torchtitan model inside vLLM (backend=torchtitan_wrapper)
# with matched varlen attention (num_splits=1 / FA3) + fp32 lm_head, so the
# trainer/generator per-token logprobs are bitwise-identical (tests/test_bitwise_
# parity.py) and bit_wise/logprob_diff/{mean,abs_mean,max} should collapse to ~0.
#
# SWE_GDN=1 is kept ONLY for the toolchain: it points CUDA_HOME at the pip
# nvidia/cu13 ptxas 13.2 (newest, safest to JIT the triton batch_invariant_ops
# kernels + FA3) and switches vLLM to native torch sampling. It does NOT enable any
# GDN model path here -- the 4B is dense and the recipe sets vllm_additional_config
# to {} (no gdn_prefill_backend).
#
# Prereqs:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...
#   # dense Qwen3-4B weights staged on the bucket (done via manifold putr):
#   #   /mnt/torchtrain_datasets/tree/yichuan/qwen/Qwen3-4B
#   # 2-step batch-invariant debug run (matches the cb0c79 logdiff debug run):
#   SWE_TRAIN_STEPS=2 SWE_OFFPOLICY_STEPS=2 SWE_VAL_SAMPLES=0 SWE_DEBUG_MAX_LOGDIFF=1 \
#     bash mast_rl/submit_swe_tmax_4b.sh
set -u
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_4b_tmax}"
# Keep the cu13 toolchain (newest ptxas for the triton batch_invariant kernels +
# FA3, native torch sampling); harmless for the dense (prebuilt) flash-attn .so.
export SWE_GDN=1
export SWE_DISABLE_CUSTOM_ALL_REDUCE="${SWE_DISABLE_CUSTOM_ALL_REDUCE:-1}"
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_train.jsonl}"
export TMAX_EXEC_TIMEOUT_SEC="${TMAX_EXEC_TIMEOUT_SEC:-120}"
export TMAX_EVAL_TIMEOUT_SEC="${TMAX_EVAL_TIMEOUT_SEC:-600}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-2400}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-63488}"
# Fresh dump dir for the batch-invariant 4B control (distinct from q35_9b_tmax*).
export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_4b_tmax_bi}"
export TT_DAYTONA_CPU="${TT_DAYTONA_CPU:-2}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-4}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-5}"
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-16}"
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-6}"
# Dense Qwen3-4B HF weights on the bucket (batch invariance needs the trainer and
# generator to load the SAME weights; the recipe runs both from these).
GDN_ASSETS="${GDN_HF_ASSETS:-/mnt/torchtrain_datasets/tree/yichuan/qwen/Qwen3-4B}"
exec bash "$(dirname "$0")/submit_swe.sh" --hf_assets_path "${GDN_ASSETS}" "$@"
