#!/bin/bash
# Validation-ONLY (step=0) run to test whether a given generator backend still
# emits malformed tool calls ("format errors") on the base Qwen3.5-9B model.
#
# WHY this exists: the torchtitan_wrapper (unified GDN) generator produced
# malformed tool calls (format_errors=4 stops), while vllm_native produced zero.
# This launches ONLY the pre-training validation pass -- no optimizer steps, no
# training rollouts -- so a backend can be format-error-tested cheaply and fast.
#
# HOW it works:
#   SWE_TRAIN_STEPS=0 -> Controller.run() does the unconditional pre-training
#   validation (controller.py:764 `_validate_and_log(step=start_step)`) and then
#   the trainer loop runs 0 steps (range(1, 1) is empty) -> the job exits right
#   after validation. The tmax config builds on rl_grpo_qwen3_5_9b_swe_r2e, whose
#   generator backend is env-driven, so SWE_GEN_BACKEND selects the backend.
#
# WHAT to look at (in the run's controller_console.log on the bucket):
#   grep -c 'format_errors=[1-9]'    # non-zero format errors (the signal)
#   grep -c 'format_errors=4'        # episodes that hit the 4-strike stop
#   grep -c 'anthropic_adapter] group'   # tool-calling turns exercised
# vllm_native was 0; torchtitan_wrapper was >0.
#
# CAVEAT: validation decodes GREEDY (num_samples n=1), whereas the format errors
# were first seen in sampled (temperature 1.0) training rollouts. The tool-call
# format path is identical, but the greedy rate may differ from the sampled rate.
# For a sampled test, run a few real steps instead (drop SWE_TRAIN_STEPS=0).
#
# Usage:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...
#   bash mast_rl/submit_swe_tmax_9b_fmttest.sh                 # torchtitan_wrapper (default here)
#   SWE_GEN_BACKEND=vllm_native bash mast_rl/submit_swe_tmax_9b_fmttest.sh   # control
#   bash mast_rl/submit_swe_tmax_9b_fmttest.sh --no-reinstall  # skip torchtitan repack
set -u

# Base tmax 9B config; SWE_TRAIN_STEPS=0 turns it validation-only.
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_9b_tmax}"
export SWE_TRAIN_STEPS=0

# Backend under test. Default torchtitan_wrapper (the one that showed format
# errors); flip to vllm_native for the zero-error control.
export SWE_GEN_BACKEND="${SWE_GEN_BACKEND:-torchtitan_wrapper}"
# Unified path defaults: eager decode + no prefix cache (unvalidated cache-copy).
export SWE_GEN_CUDAGRAPH="${SWE_GEN_CUDAGRAPH:-0}"
export SWE_GEN_PREFIX_CACHE="${SWE_GEN_PREFIX_CACHE:-0}"

# Only 32 greedy validation episodes (val_samples), so a small footprint is enough.
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-64}"
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-2}"

# Distinct dump so it never collides with the training run's dir/checkpoint.
export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax_fmttest}"

exec bash "$(dirname "$0")/submit_swe_tmax_9b.sh" "$@"
