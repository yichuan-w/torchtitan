#!/bin/bash
# Submit the Qwen3.6-27B (Gated DeltaNet hybrid) SWE-R2E RL job to MAST.
#
# Wraps submit_swe.sh with the GDN config + the GDN toolchain switch (SWE_GDN=1
# makes run.sh point CUDA_HOME at the pip nvidia/cu13 ptxas 13.2 for the FLA/triton
# kernels) + the 27B GDN weights on the bucket. host_loop agent, off-policy 2,
# drop_zero_std False, 8x8 (all baked into rl_grpo_qwen3_5_27b_swe_r2e).
#
# Prereq: Qwen3.6-27B uploaded to torchtrain_datasets/tree/qwen3_5/Qwen3.6-27B and
# rlmast made GDN-capable (tilelang 0.1.11 + apache-tvm-ffi + torchvision) so the
# conda-pack ships them. See reference_rlmast_gdn_env.
set -u
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_27b_swe_r2e}"
export SWE_GDN=1
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
# GDN generation needs more per-turn room than dense (thinking model); keep the
# 24576 model ceiling but let the adapter use most of it.
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-22528}"
GDN_ASSETS="${GDN_HF_ASSETS:-/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.6-27B}"
exec bash "$(dirname "$0")/submit_swe.sh" --hf_assets_path "${GDN_ASSETS}" "$@"
