#!/bin/bash
# Overfit bench (torchtitan/experiments/rl/examples/tmax/overfit_bench) on MAST:
# one job dir (bench.py new) trained with the bench's fixed recipe.env; the
# rewriter runs on the devvm against the same root through the mount
# (mast_rl/run_overfit_rewriter.sh).
#
#   JOB=tb21/torch-tensor-parallelism--gpt56-ref bash mast_rl/submit_overfit_bench.sh
#
# Topology: one host (trainer FSDP-6 + 1 generator + 1 eval generator).
# A resubmit with the same RUN_NAME resumes from the run's newest checkpoint.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BENCH=$HERE/../torchtitan/experiments/rl/examples/tmax/overfit_bench
: "${JOB:?set JOB=<bench>/<task>--<rewriter> under overfit_bench/jobs}"
LOCAL_JOB=$HOME/rl/.torchtrain_datasets/tree/yichuan/overfit_bench/jobs/$JOB
[ -f "$LOCAL_JOB/job.json" ] || { echo "no job.json in $LOCAL_JOB (bench.py new)" >&2; exit 2; }

# OFB_REWRITER_MODE=ladder: the rewriter is ladder_rewriter.py on the bench
# scheduler (SWE_BENCH_SCHEDULER=1, which never waits for rewrites, so not
# synchronous); start it with OFB_REWRITER_MODE=ladder run_overfit_rewriter.sh.
# OFB_RECIPE=bench there takes recipe.env as is, without the deviations below.
OFB_REWRITER_MODE=${OFB_REWRITER_MODE:-reference}
if [[ "$OFB_REWRITER_MODE" == ladder ]]; then
    export SWE_SYNC_WAIT_FOR_REWRITES=0
fi
if [[ "${OFB_RECIPE:-ours}" != bench ]]; then
# Deviation from recipe.env (user, 2026-10-18): one active group, so a step's 8
# rollouts run as one wave instead of two groups sharing the 8 slots.
export SWE_MAX_ACTIVE_GROUPS=${SWE_MAX_ACTIVE_GROUPS:-1}
export SWE_INITIAL_ACTIVE_GROUPS=${SWE_INITIAL_ACTIVE_GROUPS:-1}
export SWE_ALLOW_IDLE_ROLLOUT_SLOTS=1
# G12 (user, 2026-10-18; G32 had too long a tail): the generator idles through
# each group's tail, so 12 attempts cost about the wall time of 8.
export SWE_GROUP_SIZE=${SWE_GROUP_SIZE:-12}
export SWE_ROLLOUT_CONCURRENCY=${SWE_ROLLOUT_CONCURRENCY:-12}
# Readout k=5 (user, 2026-10-18; recipe.env says 16): pass@5 from 5 attempts is
# simply "any of the 5 passed".
export SWE_TB2_VAL_K=${SWE_TB2_VAL_K:-5}
# Readout after every step (user, 2026-10-18; recipe.env says 5). It runs on the
# eval GPU on step N's weights while the training generator rolls out step N+1's
# group on the same weights, so it costs no training time; a pass still running
# when the next one is due is skipped (validation/skipped), not queued.
export SWE_VAL_INTERVAL=${SWE_VAL_INTERVAL:-1}
# ...but only once a step has had a nonzero gradient: before that the weights are
# the base model's and every readout would re-measure it.
export SWE_VAL_SKIP_UNCHANGED=${SWE_VAL_SKIP_UNCHANGED:-1}
# Fully synchronous (user, 2026-10-18): slots release only after the weight pull,
# so with one active group every rollout is on the newest policy; on top of that
# the next group is drawn only once the rewriter has finished every rewrite it
# was signalled for (rewriting overlaps training, not the next rollout).
export SWE_SYNC_WAIT_FOR_REWRITES=${SWE_SYNC_WAIT_FOR_REWRITES:-1}
fi
# The bench recipe first; never clobber what the caller exported.
while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue ;; esac
    [ -n "${!k-}" ] || export "$k=$v"
done < "$BENCH/recipe.env"
if [[ "$OFB_REWRITER_MODE" != ladder ]]; then
    # The file-based loop, as every run before the ladder: these were never
    # forwarded to MAST until mast.py learned them for the ladder.
    export SWE_BENCH_SCHEDULER=0
    unset SWE_BENCH_MAX_TURNS SWE_BENCH_SANDBOX_CPU SWE_SKIP_ZERO_GRAD_STEP
fi
STEPS=$SWE_TRAIN_STEPS
# Every attempt gets 2400 s (recipe.env): a TB2.1 row declares its own budget
# (900 s here) and the rollouter takes max(declared, floor), so the floor is the
# knob that makes the budget 2400 for training and readout alike.
export SWE_AGENT_TIMEOUT_FLOOR_SEC=${SWE_AGENT_TIMEOUT_FLOOR_SEC:-$SWE_TIME_BUDGET_SEC}

# 120K context for training and readout (user, 2026-10-18): 24% of the first 25
# rollouts ended at the 63.5K wall. SWE_TMAX_CONTEXT sets vLLM max_model_len and
# the trainer's packing width (eval generators follow it); the agent budget keeps
# the usual 2048 headroom.
export SWE_TMAX_CONTEXT=${SWE_TMAX_CONTEXT:-122880}
export SWE_MAX_CONTEXT_LEN=${SWE_MAX_CONTEXT_LEN:-$((SWE_TMAX_CONTEXT - 2048))}

# Paths: the job root on the shared mount, one run dir per RUN_NAME.
RUN_NAME=${RUN_NAME:-tmax-9b--$(date -u +%Y%m%d-%H%M%SZ)}
export TRL_BASE=/mnt/torchtrain_datasets/tree/yichuan/overfit_bench/jobs/$JOB
export TRL_RUN_DIR=$TRL_BASE/runs/$RUN_NAME
export SWE_PROMPT_DATA=$TRL_BASE/data/mix/live.jsonl
export SWE_TB2_VAL_DATA=$TRL_BASE/data/evalsets/original.jsonl
export SWE_DUMP_DIR=$TRL_RUN_DIR/trainer
export SWE_HOLDOUT_N=0
# Signals for the reference rewriter: harder at >= 15/16-style solved, easier at 0
# (rltrain.env's value; the code default is 1.0).
export SWE_EVOLUTION_HARDER_RATIO=${SWE_EVOLUTION_HARDER_RATIO:-0.9}

# Topology. OFB_LAYOUT=collocate: one 8-GPU host, trainer FSDP-6 + 1 generator +
# 1 eval generator (default; loss chunks 128). OFB_LAYOUT=trainer_host: the
# trainer takes the whole controller host (FSDP-8) and the generator and eval
# generator get a host each, 3 hosts. FSDP-6 OOMed in forward_backward at the
# 120K context (needed 3.4 GiB more); FSDP-8 frees ~6 GiB of optimizer state per
# rank and 64 loss chunks halve the logits chunk.
OFB_LAYOUT=${OFB_LAYOUT:-collocate}
if [[ "$OFB_LAYOUT" == trainer_host ]]; then
    export SWE_DP_SHARD=${SWE_DP_SHARD:-8} SWE_LOSS_CHUNKS=${SWE_LOSS_CHUNKS:-64}
    LAYOUT_ARG=--launcher.trainer-on-controller
else
    export SWE_DP_SHARD=${SWE_DP_SHARD:-6} SWE_LOSS_CHUNKS=${SWE_LOSS_CHUNKS:-128}
    LAYOUT_ARG=--launcher.collocate-on-controller
fi
export SWE_DP_REPLICATE=1
export SWE_GEN_DP=1 SWE_NUM_GENERATORS=${SWE_NUM_GENERATORS:-1} SWE_PACK_GENERATORS=0
export SWE_NUM_EVAL_GENERATORS=${SWE_NUM_EVAL_GENERATORS:-1} SWE_EVAL_GEN_DP=1
export SWE_NUM_EVAL_ROLLOUT_WORKERS=${SWE_NUM_EVAL_ROLLOUT_WORKERS:-4}
export SWE_EVAL_ROLLOUT_CONCURRENCY=${SWE_EVAL_ROLLOUT_CONCURRENCY:-$SWE_TB2_VAL_K}
# One active group needs one rollout worker (8 lanes) to keep its gate supplied.
export SWE_NUM_ROLLOUT_WORKERS=${SWE_NUM_ROLLOUT_WORKERS:-1}
# An eval pass is 16 attempts every 5 steps; pull the eval weights only for it.
export SWE_EVAL_PULL_ONLY_BEFORE_VALIDATION=1

task=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["task"])' "$LOCAL_JOB/job.json")
TAG=${TAG:-ofb-${task}-$(basename "$JOB" | sed 's/.*--//')}
echo "[overfit] job=$TRL_BASE run=$RUN_NAME steps=$STEPS tag=$TAG"
mkdir -p "$LOCAL_JOB/runs/$RUN_NAME"
# Trainer NCCL timeout 1800 s (default 300): at 120K a new batch shape makes the
# regionally compiled backward recompile on one rank for more than 5 minutes,
# and the other ranks' reduce-scatter hit the watchdog (step 2, FSDP-6 and -8).
STEPS=$STEPS EVAL=1 TAG=$TAG MAX_RETRIES=${MAX_RETRIES:-3} \
    exec bash /home/yichuan/submit_tmax9b_agentpick_v7r2.sh \
    "$LAYOUT_ARG" --trainer.comm.init-timeout-seconds "${OFB_NCCL_TIMEOUT:-1800}" \
    --trainer.comm.train-timeout-seconds "${OFB_NCCL_TIMEOUT:-1800}" "$@"
