#!/bin/bash
#SBATCH --job-name=tb2-eval
#SBATCH --partition=pli-lc
#SBATCH --account=pli_xl
#SBATCH --qos=pli-lc
#SBATCH --gres=gpu:2
#SBATCH --cpus-per-task=16
#SBATCH --mem=200G
#SBATCH --time=06:00:00
#SBATCH --output=/scratch/gpfs/TRIDAO/al9080/terminal-rl/logs/tb2_eval_%j.log
# TB2 eval on pli-lc H100s. Arg 1 (optional): DCP checkpoint dir (SWE_TB2_CKPT);
# empty = base model (harness calibration: base avg@5 should land near 18.65%).
set -u
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
CKPT=${1:-}
STAMP=$(date +%Y%m%d-%H%M%S)
LABEL=${CKPT##*/}; LABEL=${LABEL:-base}
DUMP=$R/runs/tb2-eval-$LABEL-$STAMP
mkdir -p $DUMP

# compute nodes have no internet and no external DNS; the della-tridao egress
# proxy resolves inside CONNECT (allowlist: daytona/hf/wandb/openai)
export HTTPS_PROXY=http://172.17.2.45:3129 HTTP_PROXY=http://172.17.2.45:3129
export https_proxy=$HTTPS_PROXY http_proxy=$HTTP_PROXY
export NO_PROXY=localhost,127.0.0.1,.princeton.edu,172.16.0.0/12,10.0.0.0/8
export no_proxy=$NO_PROXY

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
export TT_DAYTONA_CREATE_CONCURRENCY=128 TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CREATE_RETRIES=8 TT_DAYTONA_LABEL=tb2_eval_h100
export TRL_TT=/home/al9080/torchtitan
export CUDA_VISIBLE_DEVICES=0,1
export PATH=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin:$PATH
export PYTHONPATH=$TRL_TT${PYTHONPATH:+:$PYTHONPATH}

echo "[tb2-eval] label=$LABEL ckpt=${CKPT:-<base>} dump=$DUMP host=$(hostname) job=$SLURM_JOB_ID" | tee $DUMP/launch.info
env | grep -E "^(SWE_|TMAX_|TT_DAYTONA|CUDA_VISIBLE)" | sort >> $DUMP/launch.info
cd "$DUMP"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators 1 \
    --hf_assets_path /scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B 2>&1
