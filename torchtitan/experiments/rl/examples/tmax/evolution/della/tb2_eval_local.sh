#!/bin/bash
# TB2 eval run LOCALLY on della-tridao B300s (bypasses the pli-lc queue).
#   tb2_eval_local.sh <ckpt-dir|base> <gpu-offset>
# gpu-offset picks a contiguous 2-GPU window (0 -> GPUs 0,1; 2 -> GPUs 2,3).
# The train.py allocator ignores CUDA_VISIBLE_DEVICES and places meshes from
# RL_GPU_OFFSET, so that is the knob used here.
set -u
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
CKPT=$1; OFF=$2
[ "$CKPT" = base ] && CKPT=""
STAMP=$(date +%Y%m%d-%H%M%S)
LABEL=${CKPT##*/}; LABEL=${LABEL:-base}
DUMP=$R/runs/tb2-eval-local-$LABEL-$STAMP
mkdir -p $DUMP

set -a; . ~/.config/daytona/env; set +a
export SWE_TB2_DATA=$R/data/mix/tb2_eval.jsonl
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
# The profile says which checkout to run; the script's own location is used
# only to find it. Two people launch from one account here and every path
# carries the same name, so neither a hardcoded path nor "wherever this file
# sits" answers the question.
: "${TRL_PROFILE:?name the profile whose checkout to run; see runbook/profiles/}"
_PROFILE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../runbook/profiles/$TRL_PROFILE.env
[ -f "$_PROFILE" ] || { echo "no profile $TRL_PROFILE at $_PROFILE" >&2; exit 2; }
export TRL_TT=$(sed -n 's/^TRL_TT=//p' "$_PROFILE")
[ -d "$TRL_TT" ] || { echo "profile $TRL_PROFILE names TRL_TT=$TRL_TT, not a directory" >&2; exit 2; }
export RL_GPUS=$OFF,$((OFF+1)) RL_GPU_OFFSET=$OFF
export PATH=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin:$PATH
export PYTHONPATH=$TRL_TT${PYTHONPATH:+:$PYTHONPATH}

echo "[tb2-eval-local] label=$LABEL ckpt=${CKPT:-<base>} gpus=$OFF,$((OFF+1)) dump=$DUMP" | tee $DUMP/launch.info
env | grep -E "^(SWE_|TMAX_|TT_DAYTONA|RL_GPU)" | sort >> $DUMP/launch.info
cd "$DUMP"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators 1 \
    --hf_assets_path /scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B 2>&1
