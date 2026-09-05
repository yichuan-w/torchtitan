#!/usr/bin/env bash
# Measure how hard a handful of tasks are for the policy, on the flow-matic
# eval host, before they are folded into a training mix.
#
#   ./difficulty_probe.sh <tasks.jsonl> <base|step-N> [k]
#
# The tasks file holds mix rows (one per line, as pack_to_dataset / a dev
# workdir's mix carries them). The recipe is the trainer's own TB-2.0 eval
# recipe at num_training_steps=0: one validation pass, k samples per task,
# and nothing else. What differs from run_tb2_eval.sh is deliberate:
#
#   * sampling is the TRAINING rollout's, not TB-2.0's published defaults, so
#     a pass rate here predicts the k/k or 0/k signal the trainer would emit;
#   * the time budget and per-sandbox fleet defaults are the trainer's;
#   * the probe's directory is exported as TRL_RUN_DIR, so whatever the
#     trainer records per rollout (LAYOUT.md: rollouts/, advisories/) lands
#     beside tasks.jsonl instead of wherever the environment pointed. The
#     pass itself is a validation pass, and validation groups write no
#     rollout records by contract; difficulty_probe_summary.py reads the
#     controller's validation trace report to say per task how many of k
#     passed and how the failures ended.
#
# Output: results/probe-<label>-<stamp>/summary.json and a table on stdout.
set -uo pipefail
W=/var/tmp/tw-eval
TASKS=${1:?tasks.jsonl}
MODEL=${2:?base|step-N}
K=${3:-8}
[ -s "$TASKS" ] || { echo "no tasks at $TASKS"; exit 1; }
N=$(grep -c . "$TASKS")

export HOME=$W/home HF_HOME=$W/cache/hf TRITON_CACHE_DIR=$W/cache/triton XDG_CACHE_HOME=$W/cache
export PYTHONPATH=$W/repo/torchtitan
set -a
# shellcheck disable=SC1091
. $W/secrets.env
set +a

if [ "$MODEL" = base ]; then
  export SWE_TB2_CKPT=""
else
  CKPT=$W/ckpt/$MODEL
  [ -f "$CKPT/.metadata" ] || { echo "no DCP at $CKPT (pull it first)"; exit 1; }
  export SWE_TB2_CKPT=$CKPT
fi
export SWE_TB2_DATA=$TASKS
export SWE_VAL_SAMPLES=$N
export SWE_TB2_VAL_K=$K
# The trainer's rollout sampling: rl_grpo_qwen3_5_9b_tmax leaves the
# generator's SamplingConfig defaults in place (temperature 0.8, top_p 0.95;
# actors/generator.py) and raises only max_tokens. Override only to answer a
# different question, and say so beside the number.
export SWE_TB2_VAL_TEMPERATURE=${SWE_TB2_VAL_TEMPERATURE:-0.8}
export SWE_TB2_VAL_TOP_P=${SWE_TB2_VAL_TOP_P:-0.95}
export SWE_GDN=1 SWE_GEN_BACKEND=vllm_native SWE_MAX_CONTEXT_LEN=63488
# The trainer's budget and scaffold (wd-20260903d launch env).
export SWE_TIME_BUDGET_SEC=${SWE_TIME_BUDGET_SEC:-3600}
export SWE_AGENT_TIMEOUT_FLOOR_SEC=${SWE_AGENT_TIMEOUT_FLOOR_SEC:-900}
export TMAX_AGENT=terminus TMAX_TURN_MAX_TOKENS=32768 TMAX_TERMINUS_MAX_TURNS=120 TMAX_EXEC_TIMEOUT_SEC=120
export TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2
export TT_DAYTONA_CREATE_CONCURRENCY=16 TT_DAYTONA_CREATE_RETRIES=8 TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_LABEL=difficulty_probe
export SWE_ROLLOUT_CONCURRENCY=$((N * K))
# GPU split: 2 trainer shards + 2 generator ranks on a contiguous window from
# RL_GPU_OFFSET (the allocator ignores CUDA_VISIBLE_DEVICES). Pick a window
# nobody else is on: nvidia-smi first.
export SWE_GEN_DP=${SWE_GEN_DP:-2} SWE_DP_SHARD=${SWE_DP_SHARD:-2}
export RL_GPU_OFFSET=${RL_GPU_OFFSET:-2}
export RL_GPUS=${RL_GPUS:-$RL_GPU_OFFSET,$((RL_GPU_OFFSET+1)),$((RL_GPU_OFFSET+2)),$((RL_GPU_OFFSET+3))}
export SWE_MAX_NUM_SEQS=${SWE_MAX_NUM_SEQS:-64} SWE_GPU_MEM_LIMIT=${SWE_GPU_MEM_LIMIT:-0.90}
export SWE_GEN_PREFIX_CACHE=1
export WANDB_MODE=offline WANDB_DIR=$W/results/wandb
mkdir -p "$WANDB_DIR"

TS=$(date +%Y%m%d-%H%M%S)
LABEL=$(basename "$TASKS" .jsonl)-$MODEL-k$K
RUN=$W/results/probe-$LABEL-$TS
mkdir -p "$RUN"
export TRL_RUN_DIR=$RUN
cp "$TASKS" "$RUN/tasks.jsonl"
LOG=$RUN/probe.log
{
  echo "tasks=$TASKS n=$N model=$MODEL k=$K run=$RUN"
  for v in SWE_TB2_CKPT SWE_TB2_VAL_K SWE_TB2_VAL_TEMPERATURE SWE_TB2_VAL_TOP_P \
           SWE_TIME_BUDGET_SEC SWE_AGENT_TIMEOUT_FLOOR_SEC TMAX_TERMINUS_MAX_TURNS \
           TT_DAYTONA_CPU TT_DAYTONA_MEM_GB TT_DAYTONA_DISK_GB RL_GPUS SWE_GEN_DP SWE_DP_SHARD; do
    printf '  %-28s %s\n' "$v" "${!v-}"
  done
  echo "  checkout $(git -C $W/repo/torchtitan log --oneline -1)"
} | tee "$LOG"

cd "$RUN" || exit 1
"$W/venv/bin/python" -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators 1 \
    --hf_assets_path "$W/models/Qwen3.5-9B" >> "$LOG" 2>&1
RC=$?
echo "exit=$RC" | tee -a "$LOG"
"$W/venv/bin/python" "$W/repo/torchtitan/torchtitan/experiments/rl/examples/tmax/evolution/eval_host/difficulty_probe_summary.py" "$RUN" | tee -a "$LOG"
exit $RC
