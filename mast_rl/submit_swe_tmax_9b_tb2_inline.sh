#!/bin/bash
# Qwen3.5-9B tmax RL with INLINE Terminal-Bench 2.0 validation on a dedicated node.
#
# Same training recipe as submit_swe_tmax_9b.sh, plus:
#   - one extra MAST host (`eval_generator` role) that serves ONLY validation, so
#     the eval sweep never takes generator capacity from rollout collection;
#   - the periodic validation pass reads the TB-2.0 JSONL instead of the tmax
#     holdout slice, at the Harbor Vanillux2Agent sampling defaults (k=5,
#     temperature 0.7, top_p 0.95) -- so validation/reward/mean IS avg@5 and
#     validation/pass_at_k IS pass@5, directly comparable to the standalone
#     mast/tb2 job and to the published tmax-9b numbers;
#   - the pass runs ASYNC: the eval generators pull weights inside the step-N
#     weight-sync window and then hold that version, so training continues while
#     the sweep scores a frozen policy;
#   - a browsable trace report per pass, next to the matching checkpoint:
#       <SWE_DUMP_DIR>/validation_traces/step-<N>/{INDEX.md,index.json,summary.json,traces/}
#
# Curve points at step 0 (pre-training), 20, and 40 with the defaults below.
# Async metrics are logged at the step they FINISH on; validation/policy_version
# and the step-<N> report directory carry the version actually scored.
#
# Hosts: 1 controller + 1 trainer + 6 generator + 1 eval_generator = 9.
#
# Prereqs (same as submit_swe_tmax_9b.sh) plus the TB-2.0 JSONL on the bucket:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...
#   python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_data \
#       --out mast_rl/swe_assets/tb2_eval.jsonl
#   manifold put mast_rl/swe_assets/tb2_eval.jsonl \
#       torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl
#
#   bash mast_rl/submit_swe_tmax_9b_tb2_inline.sh                 # first submit
#   bash mast_rl/submit_swe_tmax_9b_tb2_inline.sh --no-reinstall  # subsequent
set -u

# --- inline TB-2.0 validation -------------------------------------------------
export SWE_TB2_VAL_DATA="${SWE_TB2_VAL_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl}"
# 10, not 20: one TB-2.0 pass takes ~86 min (445 trials at concurrency 128), and
# at ~30 min/step an interval of 20 left the eval generator idle for ~9h between
# passes -- past the mesh idle timeouts below, so its next weight pull died on a
# gloo "connection closed by peer". 10 keeps the idle window well inside them and
# still leaves 86 min < 5h of headroom before the next pass is due.
export SWE_VAL_INTERVAL="${SWE_VAL_INTERVAL:-10}"
# One dedicated generator host (DP-8 x TP-1, same shape as a training generator).
export SWE_NUM_EVAL_GENERATORS="${SWE_NUM_EVAL_GENERATORS:-1}"
# Eval rollouts run in their own CPU worker processes, off both the controller
# GIL and the training pool. 445 trials at 128 concurrent is ~4 waves; a bigger
# burst mostly buys Daytona 429s (the create rate limit is ~10/s account-wide).
export SWE_NUM_EVAL_ROLLOUT_WORKERS="${SWE_NUM_EVAL_ROLLOUT_WORKERS:-4}"
export SWE_EVAL_ROLLOUT_CONCURRENCY="${SWE_EVAL_ROLLOUT_CONCURRENCY:-128}"

# --- training schedule: BS32 / SPP8, no zero-std drop -------------------------
# 32 prompt groups x 8 siblings per optimizer step. drop_zero_std OFF keeps
# all-pass / all-fail groups in the batch (the historical BS32/SPP8 schedule).
export SWE_NUM_GROUPS_PER_TRAIN_STEP="${SWE_NUM_GROUPS_PER_TRAIN_STEP:-32}"
export SWE_GROUP_SIZE="${SWE_GROUP_SIZE:-8}"
export SWE_DROP_ZERO_STD="${SWE_DROP_ZERO_STD:-0}"
# Buffer capacity must cover (max_offpolicy + 1) * num_groups = 5 * 32; the
# recipe's 40-group default would reject the worker split at this group count.
export SWE_MAX_ACTIVE_GROUPS="${SWE_MAX_ACTIVE_GROUPS:-160}"
export SWE_TRAIN_STEPS="${SWE_TRAIN_STEPS:-40}"

export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax_tb2_inline}"

# An idle generator mesh loses its worker processes: the next call fails with a
# gloo "connection closed by peer". Seen twice -- on ...-5bf595 the six training
# generators died during an 82-minute blocking pre-training sweep, and on
# ...-e5e9cc the eval generator died in the ~46-minute gap between its step-0 pass
# and step 20 (these timeouts were already 6h then, so Monarch's orphan reaper is
# NOT the mechanism; the exact cause is unconfirmed). The controller now keeps the
# eval generators warm with a per-step ping + weight pull and treats any
# eval-generator failure as "disable validation", so training survives either way.
# These stay raised as one less thing in the way.
# 24h, not 6h: 6h was chosen for faster steps. A 30 min/step run with validation
# every 10 steps idles the eval mesh for ~5h, and any slowdown crosses 6h.
export MONARCH_MESH_ORPHAN_TIMEOUT="${MONARCH_MESH_ORPHAN_TIMEOUT:-24h}"
export MONARCH_MESH_PROC_SPAWN_MAX_IDLE="${MONARCH_MESH_PROC_SPAWN_MAX_IDLE:-24h}"

exec bash "$(dirname "$0")/submit_swe_tmax_9b.sh" "$@"
