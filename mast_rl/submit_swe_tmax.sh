#!/bin/bash
# Submit the Qwen3.6-27B (Gated DeltaNet hybrid) AI2 tmax terminal-agent RL job to
# MAST.
#
# Copy of submit_swe_gdn.sh repointed at the tmax config + tmax data: it wraps
# submit_swe.sh with the tmax GDN config (rl_grpo_qwen3_5_27b_tmax) and the GDN
# toolchain switch (SWE_GDN=1 makes run.sh point CUDA_HOME at the pip nvidia/cu13
# ptxas 13.2 for the FLA/triton kernels) + the 27B GDN weights on the bucket. The
# only difference from the SWE launch is the config + SWE_PROMPT_DATA (the tmax
# JSONL): the controller boots a Daytona sandbox per rollout, runs the host_loop
# agent as ROOT against the on-box adapter, then grades in place with
# `bash /tests/test.sh` -> /logs/verifier/reward.txt. All MAST plumbing (roles,
# fwdproxy egress, torchstore weight sync, host counts) is inherited from
# submit_swe.sh unchanged.
#
# Prereqs:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...     # app.daytona.io key
#   # tmax JSONL staged on the bucket (see prepare_tmax_data.py):
#   #   /mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_train.jsonl
#   bash mast_rl/submit_swe_tmax.sh                 # first submit (reinstalls torchtitan)
#   bash mast_rl/submit_swe_tmax.sh --no-reinstall  # subsequent submits
set -u
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_27b_tmax}"
export SWE_GDN=1
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
# tmax terminal-agent JSONL (produced by prepare_tmax_data.py) on the bucket.
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_train.jsonl}"
# tmax verifier can be slow (some tasks fuzz thousands of inputs); give it room.
export TMAX_EVAL_TIMEOUT_SEC="${TMAX_EVAL_TIMEOUT_SEC:-900}"
# GDN generation needs more per-turn room than dense (thinking model); keep the
# 24576 model ceiling but let the adapter use most of it.
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-22528}"
GDN_ASSETS="${GDN_HF_ASSETS:-/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.6-27B}"
exec bash "$(dirname "$0")/submit_swe.sh" --hf_assets_path "${GDN_ASSETS}" "$@"
