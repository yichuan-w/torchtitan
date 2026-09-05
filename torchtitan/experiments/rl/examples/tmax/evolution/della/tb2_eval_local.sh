#!/bin/bash
# TB2 eval run LOCALLY on della-tridao B300s (bypasses the pli-lc queue).
#   TRL_PROFILE=andy tb2_eval_local.sh <ckpt-dir|base> <gpu-offset>
# gpu-offset picks a contiguous 2-GPU window (0 -> GPUs 0,1; 2 -> GPUs 2,3).
# The train.py allocator ignores CUDA_VISIBLE_DEVICES and places meshes from
# RL_GPU_OFFSET, so that is the knob used here.
#
# Output: $TRL_BASE/evals/<stamp>--<run>-step<N>/ (LAYOUT.md: an evaluation is
# neither a run nor the loop). <run> and <N> are read off the checkpoint path,
# runs/<run>/checkpoints/step-<N> or the host-local ckpt/<run>/step-<N>; `base`
# evaluates as base-step0 and any other bare model directory as its basename
# and step0. Inside: launch.json (what was evaluated, with what), stdout.log,
# and the trainer's own output under trainer/ (validation_traces/ is in there).
set -u
CKPT=$1; OFF=$2
[ "$CKPT" = base ] && CKPT=""

# The profile says which checkout to run and which root the eval belongs to;
# the script's own location is used only to find it. Two people launch from
# one account here and every path carries the same name, so neither a
# hardcoded path nor "wherever this file sits" answers the question. As in
# launch_9b.sh, a TRL_TT or TRL_BASE already in the environment wins.
: "${TRL_PROFILE:?name the profile whose checkout to run; see runbook/profiles/}"
_PROFILE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../runbook/profiles/$TRL_PROFILE.env
[ -f "$_PROFILE" ] || { echo "no profile $TRL_PROFILE at $_PROFILE" >&2; exit 2; }
export TRL_TT=${TRL_TT:-$(sed -n 's/^TRL_TT=//p' "$_PROFILE")}
[ -d "$TRL_TT" ] || { echo "profile $TRL_PROFILE names TRL_TT=$TRL_TT, not a directory" >&2; exit 2; }
export TRL_BASE=${TRL_BASE:-$(sed -n 's/^TRL_BASE=//p' "$_PROFILE")}
[ -d "$TRL_BASE" ] || { echo "profile $TRL_PROFILE names TRL_BASE=$TRL_BASE, not a directory" >&2; exit 2; }

# Name the eval after what it evaluates. Walking up past checkpoints/ (the
# run's link), checkpoint/ and outputs/rl/ (the pre-layout run shape still on
# disk under ckpt/) lands on the run the checkpoint came from.
if [ -z "$CKPT" ]; then
    RUN_NAME=base; STEP=0
else
    [ -d "$CKPT" ] || { echo "no checkpoint directory at $CKPT" >&2; exit 2; }
    case "$(basename "$CKPT")" in
        step-*)
            STEP=${CKPT##*/step-}
            _run=$(dirname "$CKPT")
            while :; do
                case "$(basename "$_run")" in
                    checkpoints|checkpoint|rl|outputs) _run=$(dirname "$_run") ;;
                    *) break ;;
                esac
            done
            RUN_NAME=$(basename "$_run") ;;
        *)  RUN_NAME=$(basename "$CKPT"); STEP=0 ;;
    esac
fi
STAMP=$(date -u +%Y%m%d-%H%M%SZ)
EVAL=$TRL_BASE/evals/$STAMP--$RUN_NAME-step$STEP
mkdir -p "$EVAL" || exit 2
exec > >(tee -a "$EVAL/stdout.log") 2>&1

set -a; . ~/.config/daytona/env; set +a
# The TB-2.0 set is the fixed benchmark both people score against, read-only,
# so one copy (rltrain.env names the same file as SWE_TB2_VAL_DATA).
export SWE_TB2_DATA=${SWE_TB2_DATA:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/evalsets/tb2_eval.jsonl}
export SWE_TB2_CKPT=$CKPT
export SWE_PROMPT_DATA=$SWE_TB2_DATA
export SWE_DP_SHARD=1 SWE_GEN_DP=1 SWE_GEN_BACKEND=vllm_native
export SWE_ROLLOUT_CONCURRENCY=445 SWE_NUM_ROLLOUT_WORKERS=8
export SWE_GPU_MEM_LIMIT=0.85 SWE_MAX_NUM_SEQS=256 SWE_GEN_PREFIX_CACHE=1
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_AGENT=terminus TMAX_EXEC_TIMEOUT_SEC=120 TMAX_TERMINUS_MAX_TURNS=120
export TMAX_TURN_MAX_TOKENS=32768
export TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2
# per rollout-worker process: 16 x 8 workers = 128 creates in flight per eval
export TT_DAYTONA_CREATE_CONCURRENCY=16 TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CREATE_RETRIES=8 TT_DAYTONA_LABEL=tb2_eval_local
export RL_GPUS=$OFF,$((OFF+1)) RL_GPU_OFFSET=$OFF
export PATH=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin:$PATH
export PYTHONPATH=$TRL_TT${PYTHONPATH:+:$PYTHONPATH}

TT_COMMIT=$(git -C "$TRL_TT" rev-parse --short HEAD)
[ -z "$(git -C "$TRL_TT" status --porcelain --untracked-files=no)" ] || TT_COMMIT=$TT_COMMIT-dirty
python - "$EVAL" "$STAMP" "$TT_COMMIT" "$CKPT" "$RUN_NAME" "$STEP" <<'PY'
import os
import sys
from pathlib import Path

# By file, not through the package: rl/__init__.py imports vllm.
sys.path.insert(0, os.path.join(os.environ["TRL_TT"], "torchtitan/experiments/rl/examples/tmax"))
import layout  # noqa: E402

eval_dir, started, tt_commit, ckpt, run_name, step = sys.argv[1:7]
prefixes = ("SWE_", "TMAX_", "TT_DAYTONA", "RL_", "TRL_", "CUDA_VISIBLE", "WANDB_PROJECT")
layout.write_json_atomic(Path(eval_dir) / "launch.json", {
    "eval": Path(eval_dir).name,
    "started": started,
    "profile": os.environ["TRL_PROFILE"],
    "tt": os.environ["TRL_TT"],
    "tt_commit": tt_commit,
    "checkpoint": ckpt or None,
    "run": run_name,
    "step": int(step),
    "gpus": os.environ["RL_GPUS"],
    "env": {k: v for k, v in os.environ.items() if k.startswith(prefixes)},
})
PY

echo "[tb2-eval-local] eval=$EVAL ckpt=${CKPT:-<base>} gpus=$RL_GPUS tt=$TRL_TT@$TT_COMMIT"
# The trainer's own output goes under trainer/ (--dump_folder; the code default
# is the CWD-relative outputs/rl); W&B still writes wandb/ under the CWD.
cd "$EVAL"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators 1 \
    --dump_folder "$EVAL/trainer" \
    --hf_assets_path /scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B
