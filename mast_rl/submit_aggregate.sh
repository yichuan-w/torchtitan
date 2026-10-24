#!/bin/bash
# Aggregate a finished wash on the MOUNTED bucket into the curated band JSONL.
# Runs aggregate_passrate.py on shard 0 (devvm bucket access is rate-limited, so we
# aggregate where the bucket is a local mount). Writes band JSONL + instance_summary
# + good_sample_ids back to the bucket; fetch the single summary file afterwards.
set -u
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

export SWE_AGGREGATE=1
# The wash wrote to curate_runs/<WANDB_RUN_NAME>/results; the wash used the base
# config name as the run name.
export SWE_CURATE_RUN="${SWE_CURATE_RUN:-torchtitan-rl-rl_grpo_qwen3_32b_swe_r2e}"
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/swe_data/r2e_subset_4p5k.jsonl}"
export SWE_BAND_OUT="${SWE_BAND_OUT:-/mnt/torchtrain_datasets/tree/yichuan/swe_data/r2e_band.jsonl}"
export SWE_BAND_MIN="${SWE_BAND_MIN:-0.2}"
export SWE_BAND_MAX="${SWE_BAND_MAX:-0.7}"
# K=4 wash -> require >=2 graded samples (default 8 would reject everything).
export SWE_BAND_MIN_SAMPLES="${SWE_BAND_MIN_SAMPLES:-2}"
# DAYTONA key not needed for aggregation, but submit.sh/run.sh tolerate its absence.
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"

# Base recipe + 0 generators -> controller (shard 0, aggregates) + trainer (no-op).
ARGS=(--config "${SWE_CONFIG:-rl_grpo_qwen3_32b_swe_r2e}")
ARGS+=(--num_generators "${SWE_NUM_GENERATORS:-0}")
exec bash "$(dirname "$0")/submit.sh" "${ARGS[@]}" "$@"
