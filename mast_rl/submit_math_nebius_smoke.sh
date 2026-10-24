#!/bin/bash
# One-host DAPO-Math smoke on Nebius GB300 (msl_infra_pytorch_dct, region ofn):
# Qwen3-4B-Base, TP-2 trainer + two TP-1 generators collocated on one 4-GPU host,
# three steps. Exercises the aarch64 env, the Manifold mount, and fwdproxy egress
# (the dataset loads from HF) before a TMax run spends hosts on them.
#
#   bash mast_rl/submit_math_nebius_smoke.sh [--no-reinstall]
# conda activate hooks reference unset vars, so source before `set -u`.
source "$(dirname "$0")/nebius_env.sh"
set -u

export DAPO_MATH_RUN=1
export DAPO_DUMP_DIR="${DAPO_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/dapo4b_nebius_smoke_$(date +%m%d_%H%M)}"
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"

exec bash "$(dirname "$0")/submit.sh" \
    --config rl_dapo_qwen3_4b_base_math_8k_nebius_smoke --num_generators 2 "$@"
