#!/bin/bash
# Submit the Qwen3.5-9B (Gated DeltaNet hybrid) Search-R1 retrieval-QA RL job to MAST.
#
# This is the Search-R1 launcher (submit.sh -> run.sh's retriever branch) plus the
# GDN toolchain switch. SWE_GDN=1 makes run.sh point CUDA_HOME at the pip
# nvidia/cu13 ptxas 13.2 for the FLA/triton GDN kernels (trainer backward +
# generator prefill); it is forwarded to every role by mast.py's env allowlist.
# SWE_RUN is deliberately NOT set, so run.sh takes the Search-R1 retriever branch
# (dense-retrieval server on the controller) rather than the Daytona/Claude-Code
# branch (submit_swe*.sh sets SWE_RUN=1 for that).
#
# Goal: validate the qwen3_5 linear-attention (GatedDeltaNet) implementation by
# watching the held-out NQ exact-match (validation_reward/_mean) rise, using the
# dense Search-R1 recipe unchanged.
#
# Prereq: the rlmast conda env must be GDN-capable (tilelang 0.1.11 + apache-tvm-ffi
# + torchvision + the CCCL header patch) so the conda-pack ships them; and the
# Qwen3.5-9B weights + Search-R1 index/parquet must be staged on the bucket. See
# reference_rlmast_gdn_env and the Search-R1 staging in run.sh.
#
# Usage:
#   conda activate rlmast
#   bash mast_rl/submit_search_r1_gdn.sh                     # 1 generator (default)
#   bash mast_rl/submit_search_r1_gdn.sh --num_generators 2
#   bash mast_rl/submit_search_r1_gdn.sh --no-reinstall      # skip torchtitan reinstall
set -u
export SWE_GDN=1
CONFIG="${SEARCH_R1_CONFIG:-rl_grpo_qwen3_5_9b_search_r1}"
NUM_GENERATORS="${NUM_GENERATORS:-1}"
exec bash "$(dirname "$0")/submit.sh" \
    --config "${CONFIG}" --num_generators "${NUM_GENERATORS}" "$@"
