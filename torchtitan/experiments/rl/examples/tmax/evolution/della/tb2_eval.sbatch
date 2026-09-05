#!/bin/bash
#SBATCH --job-name=tb2-eval
#SBATCH --partition=pli-lc
#SBATCH --account=pli_xl
#SBATCH --qos=pli-lc
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=16
#SBATCH --mem=200G
#SBATCH --time=06:00:00
# TB2 eval on pli-lc H100s. Arg 1 (optional): DCP checkpoint dir (SWE_TB2_CKPT);
# empty = base model (harness calibration: base avg@5 should land near 18.65%).
# The submitter passes --output=$TRL_BASE/logs/tb2_eval--%j.log and exports
# TRL_BASE, TRL_TT and TRL_MODEL (eval_watcher.sh does; a hand submission needs
# `sbatch --export=ALL` with those set): SLURM copies this script to a spool
# directory, so it cannot find its own tree or a profile.
set -u
: "${TRL_BASE:?the experiment root}"
: "${TRL_TT:?the checkout to run}"
: "${TRL_MODEL:?the base model directory}"
R=$TRL_BASE
CKPT=${1:-}
STAMP=$(date -u +%Y%m%d-%H%M%SZ)
if [ -n "$CKPT" ]; then
    STEP=$(basename "$CKPT")                      # step-40 -> step40, as evals/ names it
    LABEL=$(basename "$(dirname "$CKPT")")-${STEP/step-/step}
else
    LABEL=base-step0
fi
EVAL=$R/evals/$STAMP--$LABEL
mkdir -p "$EVAL/trainer"
exec > >(tee -a "$EVAL/stdout.log") 2>&1

# compute nodes have no internet and no external DNS; the della-tridao egress
# proxy resolves inside CONNECT (allowlist: daytona/hf/wandb/openai)
export HTTPS_PROXY=http://172.17.2.45:3129 HTTP_PROXY=http://172.17.2.45:3129
export https_proxy=$HTTPS_PROXY http_proxy=$HTTP_PROXY
export NO_PROXY=localhost,127.0.0.1,.princeton.edu,172.16.0.0/12,10.0.0.0/8
export no_proxy=$NO_PROXY

set -a; . ~/.config/daytona/env; set +a
export SWE_TB2_DATA=$R/data/evalsets/tb2_eval.jsonl
export SWE_TB2_CKPT=$CKPT
export SWE_PROMPT_DATA=$SWE_TB2_DATA
export SWE_DP_SHARD=1 SWE_GEN_DP=1 SWE_GEN_BACKEND=vllm_native
export SWE_ROLLOUT_CONCURRENCY=445 SWE_NUM_ROLLOUT_WORKERS=8
export SWE_GPU_MEM_LIMIT=0.85 SWE_MAX_NUM_SEQS=256 SWE_GEN_PREFIX_CACHE=1
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_AGENT=terminus TMAX_EXEC_TIMEOUT_SEC=120 TMAX_TERMINUS_MAX_TURNS=120
export TMAX_TURN_MAX_TOKENS=32768
export TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2
export TT_DAYTONA_CREATE_CONCURRENCY=128 TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CREATE_RETRIES=8 TT_DAYTONA_LABEL=tb2_eval_h100
export CUDA_VISIBLE_DEVICES=0,1
export PATH=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin:$PATH
export PYTHONPATH=$TRL_TT${PYTHONPATH:+:$PYTHONPATH}

python - "$EVAL" "$LABEL" "$CKPT" <<'PY'
import json, os, socket, sys, time
from pathlib import Path
out, label, ckpt = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
keys = ("SWE_", "TMAX_", "TT_DAYTONA", "TRL_", "CUDA_VISIBLE")
record = {"eval": out.name, "label": label, "checkpoint": ckpt or None,
          "started": time.strftime("%Y%m%d-%H%M%SZ", time.gmtime()),
          "host": socket.gethostname(), "slurm_job": os.environ.get("SLURM_JOB_ID"),
          "env": {k: v for k, v in sorted(os.environ.items()) if k.startswith(keys)}}
(out / "launch.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
PY
echo "[tb2-eval] label=$LABEL ckpt=${CKPT:-<base>} eval=$EVAL host=$(hostname) job=${SLURM_JOB_ID:-}"
cd "$EVAL"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators 1 \
    --hf_assets_path "$TRL_MODEL" \
    --dump_folder "$EVAL/trainer"
