#!/bin/bash
# Eval-only MAST job: score one DCP checkpoint on TB2.1 (89 x k=5, temp 1.0) with the
# same eval generators / context / turn cap as the agentpick evolve runs, and a chosen
# context-notice style. Training does 0 steps (rl_grpo_qwen3_5_9b_tmax_tb2_eval).
#
#   NAME=r1021s160-della CKPT=/mnt/.../checkpoint/step-160 NOTICE=della \
#       bash mast_rl/run_tmax_eval_ckpt.sh
#   CKPT="" scores the base Qwen3.5-9B.
#
# Differences from the in-run eval, on purpose: no SWE_EVAL_AGENT_BUDGET_CAP_SEC /
# VERIFIER cap (1018 had none; the recipe floor is 5400 s), and the start burst is
# halved (SWE_EVAL_ROLLOUT_CONCURRENCY=225) to avoid the apt-get tmux stampede that
# zeroed up to 35 rollouts per pass in 1021.
set -eo pipefail
: "${NAME:?NAME=<short tag>}"
: "${NOTICE:?NOTICE=della|checked}"
CKPT=${CKPT-__unset__}
[[ "$CKPT" == __unset__ ]] && { echo "CKPT=<dcp dir> or CKPT= for base" >&2; exit 1; }

export TAG=q35-9b-evalckpt-${NAME}
export STEPS=0 EVAL=1
export SWE_CONFIG=rl_grpo_qwen3_5_9b_tmax_tb2_eval
export SWE_TB2_CKPT="$CKPT"
export TMAX_CONTEXT_NOTICE_STYLE=$NOTICE
VAL=${VAL_DATA:-mast_rl/swe_assets/tb2_1_envpatch_20261019/tb2_1_eval.jsonl}
export SWE_TB2_DATA=$VAL SWE_TB2_VAL_DATA=$VAL SWE_PROMPT_DATA=$VAL
export SWE_DUMP_DIR=/mnt/torchtrain_datasets/tree/yichuan/mast_runs/${TAG}-$(date +%m%d%H%M)

# Same eval shape as run_tmax_evolving_agentpick_both.sh (1018/1021).
export SWE_GROUP_SIZE=12 SWE_TMAX_CONTEXT=81920 SWE_MAX_CONTEXT_LEN=79872 SWE_LOSS_CHUNKS=40
export SWE_EVAL_MAX_MODEL_LEN=163840 SWE_EVAL_MAX_CONTEXT_LEN=161792 SWE_EVAL_MAX_TURNS=300
export SWE_EVAL_TIMEOUT_SEC=400 SWE_NUM_EVAL_GENERATORS=24 SWE_EVAL_GPU_MEMORY_LIMIT=0.8
export SWE_EVAL_ROLLOUT_CONCURRENCY=${SWE_EVAL_ROLLOUT_CONCURRENCY:-225}
unset SWE_EVAL_AGENT_BUDGET_CAP_SEC SWE_EVAL_VERIFIER_BUDGET_CAP_SEC

# Small footprint: 1 trainer host (weight push only), 1 packed training-generator host.
export SWE_DP_SHARD=8 SWE_DP_REPLICATE=1 SWE_NUM_GENERATORS=8
export SWE_CKPT_KEEP_EVERY=0

exec bash /home/yichuan/submit_tmax9b_agentpick_v7r2.sh "$@"
