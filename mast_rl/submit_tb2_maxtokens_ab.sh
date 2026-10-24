#!/bin/bash
# Eval-only A/B: does raising the per-turn generation cap recover the TB-2.0 trials
# that are truncated before they emit a tool call?
#
# Both arms score the SAME base Qwen3.5-9B on all 89 Terminal-Bench 2.0 tasks at k=5
# with the Harbor sampling defaults, and differ in ONE knob:
#
#   arm A (control): SWE_TB2_VAL_MAX_TOKENS unset -> 16384, today's value
#   arm B (probe)  : SWE_TB2_VAL_MAX_TOKENS=24576
#
# Why a control arm and not just B: the score alone cannot settle this. Three
# independent 16384 measurements of this same base model gave avg@5 16.85 / 19.10 /
# 18.20 (sd 1.13pp), so a single arm cannot resolve a ~2pp effect. The decisive
# measurement is the TRUNCATION RATE, and there is no DEBUG-logged baseline for it --
# hence TT_ROLLOUT_LOG_LEVEL=DEBUG on both arms, which emits per turn:
#   [anthropic_adapter] <id> turn=N: prompt=P max_tokens=M out=O finish=F
# A truncated turn is out == max_tokens with finish=length. Compare that rate, and the
# turns-per-episode distribution: a bigger per-turn cap buys fewer turns per episode,
# because preserve_all_thinking keeps every turn's reasoning in later prompts.
#
# 4 hosts per arm: 1 controller + 1 trainer + 1 generator + 1 eval_generator.
#
#   conda activate rlmast && export DAYTONA_API_KEY=dtn_...
#   bash mast_rl/submit_tb2_maxtokens_ab.sh a        # control (16384)
#   bash mast_rl/submit_tb2_maxtokens_ab.sh b        # probe   (24576)
set -u

ARM="${1:-}"
case "${ARM}" in
    a) MAX_TOKENS=""      ; TAG="mt16384" ;;
    b) MAX_TOKENS="24576" ; TAG="mt24576" ;;
    *) echo "usage: $0 {a|b}   (a = control 16384, b = probe 24576)" >&2; exit 2 ;;
esac
shift || true

export SWE_CONFIG=rl_grpo_qwen3_5_9b_tmax_tb2_eval
# The TB-2.0 task set, as both the eval set and the (unused) train stream.
export SWE_TB2_DATA="${SWE_TB2_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl}"
export SWE_TB2_VAL_DATA="${SWE_TB2_VAL_DATA:-${SWE_TB2_DATA}}"
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-${SWE_TB2_DATA}}"
# No SWE_TB2_CKPT: score the BASE model, so both arms are directly comparable to the
# three step-0 numbers already measured at 16384.
[[ -n "${MAX_TOKENS}" ]] && export SWE_TB2_VAL_MAX_TOKENS="${MAX_TOKENS}"

# Per-turn forensics: the whole point of the run.
export TT_ROLLOUT_LOG_LEVEL="${TT_ROLLOUT_LOG_LEVEL:-DEBUG}"

# One dedicated eval generator + its own worker processes, same shape as the training
# run's validation path. The idle training generator is harmless here: with 0 training
# steps nothing routes to it after setup, and an eval-generator failure now only
# disables validation rather than ending the job.
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-1}"
export SWE_NUM_EVAL_GENERATORS="${SWE_NUM_EVAL_GENERATORS:-1}"
export SWE_NUM_EVAL_ROLLOUT_WORKERS="${SWE_NUM_EVAL_ROLLOUT_WORKERS:-4}"
export SWE_EVAL_ROLLOUT_CONCURRENCY="${SWE_EVAL_ROLLOUT_CONCURRENCY:-128}"
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-128}"
export SWE_NUM_ROLLOUT_WORKERS="${SWE_NUM_ROLLOUT_WORKERS:-1}"

export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/tb2_ab_${TAG}}"

echo "TB-2.0 max_tokens A/B, arm ${ARM}: per-turn cap = ${MAX_TOKENS:-16384 (inherited)}"
echo "  dump: ${SWE_DUMP_DIR}"
exec bash "$(dirname "$0")/submit_swe_tmax_9b.sh" "$@"
