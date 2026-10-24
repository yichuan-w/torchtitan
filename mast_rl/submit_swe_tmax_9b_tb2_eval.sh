#!/bin/bash
# Eval-only MAST job: score a trained Qwen3.5-9B tmax checkpoint on the full
# Terminal-Bench 2.0 benchmark (89 tasks), greedy pass@1, via the same Daytona
# rollout + grade path as training.
#
# Reuses submit_swe_tmax_9b.sh verbatim (9B GDN generator, vanillux agent loop,
# renderer, timeouts, context) and only overrides:
#   - SWE_CONFIG      -> rl_grpo_qwen3_5_9b_tmax_tb2_eval (num_training_steps=0,
#                        validation over all 89 TB-2.0 tasks; see config_registry).
#   - SWE_TB2_DATA    -> the TB-2.0 JSONL (prepare_tb2_data.py output, tmax schema).
#   - SWE_TB2_CKPT    -> the trained DCP checkpoint dir to load as INITIAL weights
#                        (e.g. the run's checkpoint/step-100). Loaded model-only,
#                        native DCP (initial_load_in_hf=False), NOT a resume.
#   - SWE_DUMP_DIR    -> a FRESH dir (no checkpoint/ -> CheckpointManager falls to
#                        initial_load_path = SWE_TB2_CKPT).
#   - SWE_ROLLOUT_CONCURRENCY -> 89 so all tasks run at once (validation shares the
#                        global rollout semaphore).
#
# Prereqs (same as the training submit):
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...
#   # TB-2.0 JSONL staged on the bucket (see prepare_tb2_data.py):
#   #   python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_data \
#   #       --out mast_rl/swe_assets/tb2_eval.jsonl
#   #   manifold put mast_rl/swe_assets/tb2_eval.jsonl \
#   #       torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl
#   bash mast_rl/submit_swe_tmax_9b_tb2_eval.sh                 # first submit (reinstalls)
#   bash mast_rl/submit_swe_tmax_9b_tb2_eval.sh --no-reinstall  # subsequent submits
set -u

export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_9b_tmax_tb2_eval}"
# TB-2.0 tasks + trained checkpoint to score (bucket paths, mounted at /mnt by run.sh).
export SWE_TB2_DATA="${SWE_TB2_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl}"
# Trained DCP checkpoint to score. Use `-` (not `:-`) so an explicitly EMPTY value passes
# through unchanged: SWE_TB2_CKPT="" means "score the BASE model" (the recipe's
# `if _TB2_CKPT:` is then skipped, so the model loads from --hf_assets_path = the base
# Qwen3.5-9B) -- this is how the step-0 baseline (policy_version 0) is evaluated. Only a
# truly UNSET var falls back to the default checkpoint.
export SWE_TB2_CKPT="${SWE_TB2_CKPT-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax/checkpoint/step-100}"
# The base config reads _DEFAULT_DATA (SWE_PROMPT_DATA) at import; point it at the
# TB-2.0 file too so the eval config's tb2_data fallback is well-defined.
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-${SWE_TB2_DATA}}"
# Run all 89 tasks concurrently (validation goes through the same rollout semaphore).
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-89}"
# Fresh dump dir: no checkpoint/ here, so the trainer loads SWE_TB2_CKPT via
# initial_load_path instead of resuming.
export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax_tb2eval}"

exec bash "$(dirname "$0")/submit_swe_tmax_9b.sh" "$@"
