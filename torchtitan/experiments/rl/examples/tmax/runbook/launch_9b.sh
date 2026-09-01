#!/bin/bash
# Start the reference 9B TerminalWorld RL run.
#
#   ./launch_9b.sh                 # uses rltrain.env next to this script
#   RL_DATA=/path/other.jsonl ./launch_9b.sh
#
# Anything already set in the environment wins over rltrain.env, so a one-off
# override needs no edit to the file. Everything this script does not set is left
# at the code default on purpose.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# rltrain.env first, but never clobber what the caller already exported.
if [ -f "$HERE/rltrain.env" ]; then
    while IFS='=' read -r k v; do
        case "$k" in ''|\#*) continue ;; esac
        [ -n "${!k-}" ] || export "$k=$v"
    done < "$HERE/rltrain.env"
fi

: "${TRL_BASE:?set TRL_BASE (data/runs/logs root)}"
: "${TRL_TT:?set TRL_TT (torchtitan checkout)}"
: "${TRL_MODEL:?set TRL_MODEL (Qwen3.5-9B directory)}"
: "${TRL_VENV:=/scratch/gpfs/TRIDAO/al9080/titan-rl}"

# Credentials come from outside the repo. On our box this file exports
# DAYTONA_API_KEY (and DAYTONA_API_URL / DAYTONA_TARGET if you use them).
[ -f "$HOME/.config/daytona/env" ] && . "$HOME/.config/daytona/env"
if [ -z "${DAYTONA_API_KEY:-}" ]; then
    echo "[launch] DAYTONA_API_KEY is not set. Every rollout needs a sandbox;" >&2
    echo "[launch] without it the run boots and then fails every single one." >&2
    exit 2
fi

. "$TRL_VENV/bin/activate"
cd "$TRL_TT"

STAMP=$(date +%Y%m%d-%H%M%S)
# Point RL_RESUME_DUMP at an existing run directory to resume from its latest
# checkpoint instead of starting a new one.
DUMP=${RL_RESUME_DUMP:-$TRL_BASE/runs/tmax-9b-$STAMP}
mkdir -p "$DUMP"

# The allocator reads RL_GPUS and gives each mesh its slice of that list BY
# POSITION, overwriting CUDA_VISIBLE_DEVICES inside the spawned process before
# CUDA starts. The list does not have to be contiguous. Order is the placement:
# the trainer takes the first SWE_DP_SHARD entries, then one entry per generator,
# so put the quietest GPUs where the generators land.
export RL_GPUS=${RL_GPUS:-0,1,2,3,4}
_n=$(awk -F, '{print NF}' <<< "$RL_GPUS")
_uniq=$(tr ',' '\n' <<< "$RL_GPUS" | sort -u | grep -c .)
if [ "$_uniq" -ne "$_n" ]; then
    echo "[launch] RL_GPUS=$RL_GPUS names the same device twice." >&2
    exit 2
fi
_want=$(( ${SWE_DP_SHARD:-2} + ${SWE_GEN_DP:-3} ))
if [ "$_want" -ne "$_n" ]; then
    echo "[launch] SWE_DP_SHARD($SWE_DP_SHARD) + SWE_GEN_DP($SWE_GEN_DP) = $_want" >&2
    echo "[launch] but RL_GPUS lists $_n GPUs. They must match." >&2
    exit 2
fi
export CUDA_VISIBLE_DEVICES=$RL_GPUS

# torchtitan is NOT installed into the venv; it is imported from the checkout.
export PYTHONPATH=$TRL_TT
export SWE_PROMPT_DATA=${RL_DATA:-$SWE_PROMPT_DATA}
[ -n "${SWE_TASK_EVOLUTION_DIR:-}" ] && mkdir -p "$SWE_TASK_EVOLUTION_DIR"

echo "[launch] dump=$DUMP data=$SWE_PROMPT_DATA gpus=$CUDA_VISIBLE_DEVICES" | tee "$DUMP/launch.info"
env | grep -E "^(SWE_|TMAX_|TT_DAYTONA|RL_|CUDA_VISIBLE|WANDB_PROJECT)" | sort >> "$DUMP/launch.info"

# Checkpoints and W&B files land under the CWD, so run from the dump directory.
cd "$DUMP"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 1 \
    --hf_assets_path "$TRL_MODEL" 2>&1
