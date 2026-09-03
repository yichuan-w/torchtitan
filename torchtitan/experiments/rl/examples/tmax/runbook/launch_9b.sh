#!/bin/bash
# Start the reference 9B TerminalWorld RL run.
#
#   TRL_PROFILE=andy ./launch_9b.sh         # whose checkout and data root
#   TRL_PROFILE=yichuan RL_DATA=/path/other.jsonl ./launch_9b.sh
#
# Two people launch on this box from the same account, each from their own
# checkout and data root. profiles/<name>.env holds those paths (and nothing
# else); rltrain.env holds the recipe, which is shared. TRL_PROFILE has no
# default on purpose: the one time it was implied, every run launched from the
# other person's tree.
#
# Anything already set in the environment wins over both files, so a one-off
# override needs no edit to either. Everything this script does not set is left
# at the code default on purpose.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# KEY=VALUE lines, no expansion; never clobber what the caller already exported.
load_env_file() {
    while IFS='=' read -r k v; do
        case "$k" in ''|\#*) continue ;; esac
        [ -n "${!k-}" ] || export "$k=$v"
    done < "$1"
}

if [ -z "${TRL_PROFILE:-}" ] || [ ! -f "$HERE/profiles/$TRL_PROFILE.env" ]; then
    echo "[launch] set TRL_PROFILE to one of: $(ls "$HERE/profiles" | sed 's/\.env$//' | tr '\n' ' ')" >&2
    echo "[launch] it picks profiles/<name>.env: the checkout (TRL_TT) and data root (TRL_BASE) to run from." >&2
    exit 2
fi
load_env_file "$HERE/profiles/$TRL_PROFILE.env"
[ -f "$HERE/rltrain.env" ] && load_env_file "$HERE/rltrain.env"

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
if [ ! -f "$SWE_PROMPT_DATA" ]; then
    echo "[launch] SWE_PROMPT_DATA=$SWE_PROMPT_DATA does not exist (profile $TRL_PROFILE)." >&2
    echo "[launch] build the mix there, or export RL_DATA=/path/to/mix.jsonl." >&2
    exit 2
fi
[ -n "${SWE_TASK_EVOLUTION_DIR:-}" ] && mkdir -p "$SWE_TASK_EVOLUTION_DIR"

# Checkpoints go to the host-local disk, not the dump directory. The shared GPFS
# fileset was down to 337 GB free on 2026-09-02 with 39 x 102 GB checkpoints on
# it, and a save that hits the quota leaves a truncated step dir behind. A run
# from before this rule that is resumed keeps its checkpoints where they are; a
# new run gets a symlink from its own directory so the run alone still says
# where its checkpoints went. Host-local means the trainer must run on this box.
_ckpt_link="$DUMP/outputs/rl/checkpoint"
if [ -d "$_ckpt_link" ] && [ ! -L "$_ckpt_link" ]; then
    export SWE_CKPT_FOLDER=${SWE_CKPT_FOLDER:-$_ckpt_link}
else
    export SWE_CKPT_FOLDER=${SWE_CKPT_FOLDER:-/scratch/al9080/terminal-rl/ckpt/$(basename "$DUMP")}
    # The folder is NOT created here: the checkpointer creates it on the first
    # save. Before the 2026-09-02 fix in components/checkpoint.py, a folder that
    # existed without a step-* made the trainer skip the initial HF load and
    # start from random weights; not pre-creating it keeps that from mattering
    # on a tree without the fix.
    mkdir -p "$DUMP/outputs/rl"
    [ -L "$_ckpt_link" ] || ln -s "$SWE_CKPT_FOLDER" "$_ckpt_link"
fi
# Say which weights the trainer will start from, so launch.info answers it.
_latest_step=$(ls -d "$SWE_CKPT_FOLDER"/step-* 2>/dev/null | sort -V | tail -1)
if [ -n "$_latest_step" ]; then
    echo "[launch] resuming from $_latest_step"
else
    echo "[launch] fresh start: initial weights from $TRL_MODEL (no step-* under $SWE_CKPT_FOLDER)"
fi

echo "[launch] profile=$TRL_PROFILE tt=$TRL_TT dump=$DUMP data=$SWE_PROMPT_DATA gpus=$CUDA_VISIBLE_DEVICES ckpt=$SWE_CKPT_FOLDER" | tee "$DUMP/launch.info"
env | grep -E "^(SWE_|TMAX_|TT_DAYTONA|RL_|TRL_|CUDA_VISIBLE|WANDB_PROJECT)" | sort >> "$DUMP/launch.info"

# Checkpoints and W&B files land under the CWD, so run from the dump directory.
cd "$DUMP"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 1 \
    --hf_assets_path "$TRL_MODEL" 2>&1
