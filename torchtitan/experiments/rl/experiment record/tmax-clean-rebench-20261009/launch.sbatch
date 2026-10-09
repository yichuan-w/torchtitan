#!/bin/bash
#SBATCH --job-name=tmax-clean-rebench
#SBATCH --partition=gpu-b300
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:8
#SBATCH --cpus-per-task=176
#SBATCH --mem=1600G
#SBATCH --time=3-00:00:00
#SBATCH --output=/mnt/data/yichuan_wang+pocd36/tmax-clean-rebench-csk4gby2-400-20261009/slurm-%j.out
set -euo pipefail
ROOT=/mnt/data/yichuan_wang+pocd36/tmax-clean-rebench-csk4gby2-400-20261009
unset RL_RESUME_FROM RL_RESUME_DUMP SWE_INITIAL_DCP_CHECKPOINT
unset SWE_PROMPT_DATA RL_DATA TRL_RUN_DIR SWE_DISABLE_SHUFFLE SWE_SKIP_IDS SWE_INCLUDE_IDS
set -a
. "$HOME/.config/tmax/credentials.env"
. "$ROOT/restart.env"
set +a
if compgen -G "$SWE_CKPT_FOLDER/step-*" >/dev/null; then
    echo 'Fresh experiment refused: checkpoint directory already contains steps.' >&2
    exit 2
fi
export RL_GPUS=$CUDA_VISIBLE_DEVICES
echo "JOB=$SLURM_JOB_ID HOST=$(hostname) RL_GPUS=$RL_GPUS TT=$TRL_TT@$(git -C "$TRL_TT" rev-parse --short HEAD)"
nvidia-smi --query-gpu=index,name,memory.used --format=csv
exec bash "$TRL_TT/torchtitan/experiments/rl/examples/tmax/runbook/launch_9b.sh" "$@"
