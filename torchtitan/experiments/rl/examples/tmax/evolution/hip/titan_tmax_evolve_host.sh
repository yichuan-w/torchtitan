#!/usr/bin/env bash
# RL training with the online evolve loop on the twice-hardened tmax mix, on
# all 8 of this box's MI350Xs (Zhifei 09-18: 可以用八张卡), from the host venv
# (not the container) so RCCL keeps GPU P2P. The loop that consumes this run's
# signals is started separately by evolve_hip.sh over the same root.
#
#   titan_tmax_evolve_host.sh [extra train args]
#   RL_RESUME_FROM=<run name or dir> titan_tmax_evolve_host.sh   resume that run's
#       newest step-* checkpoint in a new run directory (as della's launch_9b.sh:
#       SWE_CKPT_FOLDER points at the old run's checkpoint directory and the new
#       run's `checkpoints` link records it). Unset = fresh from the base weights.
#
# Every value below is the reference 9B run's value from
# runbook/rltrain.env, EXCEPT the ones this machine forces. Those are marked
# `# CHANGED:` with the reason, so a later reader can tell a port from a
# retune. The learning settings -- group size, groups per step, steps, LR,
# drop_zero_std -- are untouched, so this is the same experiment on other
# hardware.
set -euo pipefail
ROOT=/data/yichuan_wang/trl
BASE=$ROOT/work/titan/tmax-v2-evolve
[ -f $ROOT/work/daytona.env ] || { echo "no $ROOT/work/daytona.env"; exit 2; }
[ -f $ROOT/work/wandb.env ] || { echo "no $ROOT/work/wandb.env"; exit 2; }
[ -f $BASE/experiment.json ] || { echo "no experiment root at $BASE"; exit 2; }
[ -f $BASE/data/mix/live.jsonl ] || { echo "no mix at $BASE/data/mix/live.jsonl"; exit 2; }
STAMP=$(date -u +%Y%m%d-%H%M%SZ)
RUN=$BASE/runs/tmax-9b--$STAMP
mkdir -p $RUN $ROOT/work/cache/vllm $ROOT/work/cache/triton
RESUMED_FROM=; CHECKPOINT_STEP=
if [ -n "${RL_RESUME_FROM:-}" ]; then
    case "$RL_RESUME_FROM" in */*) _old=$RL_RESUME_FROM ;; *) _old=$BASE/runs/$RL_RESUME_FROM ;; esac
    if [ -L "$_old/checkpoints" ]; then _ckpt=$(readlink "$_old/checkpoints")
    elif [ -d "$_old/trainer/checkpoint" ]; then _ckpt=$_old/trainer/checkpoint
    else echo "[train] RL_RESUME_FROM=$RL_RESUME_FROM: no checkpoints under $_old" >&2; exit 2; fi
    _latest=$(ls -d "$_ckpt"/step-* 2>/dev/null | sort -V | tail -1 || true)
    [ -n "$_latest" ] || { echo "[train] RL_RESUME_FROM=$RL_RESUME_FROM: no step-* under $_ckpt" >&2; exit 2; }
    export SWE_CKPT_FOLDER=$_ckpt
    ln -s "$_ckpt" "$RUN/checkpoints"
    RESUMED_FROM=$(basename "$_old"); CHECKPOINT_STEP=${_latest##*/step-}
    echo "[train] resuming from $_latest (run $RESUMED_FROM)"
fi
set -a
. $ROOT/work/daytona.env
. $ROOT/work/wandb.env
TRL_BASE=$BASE; TRL_RUN_DIR=$RUN
# CHANGED: model lives on this box, not on della's GPFS.
TRL_MODEL=$ROOT/models/Qwen3.5-9B
# CHANGED: all 8 cards, not the reference run's 5, and SIX INDEPENDENT
# single-card generators rather than della's one generator with DP inside it.
# The DP ranks of one generator step in lockstep (one LoopDecision broadcast per
# step burst, actors/generator.py), so a rank mid-prefill stalls every other
# rank's decode: measured on this box at ~24 sequences per engine, DP-6 gave
# 282 tok/s per engine (11.7 tok/s per sequence, 43 s per agent turn, 19% of
# rollouts hitting the 2400 s budget), DP-4 gave 451, and the 09-18 eval's six
# independent engines gave 620-1135 (15-20 tok/s per sequence, 26 s per turn,
# 3% timeouts). RL_GPUS is positional: the trainer takes the first
# SWE_DP_SHARD entries (0,1), then one entry per generator (2..7); the
# allocator wants SWE_DP_SHARD + generators x SWE_GEN_DP == the GPU count:
# 2 + 6 x 1 = 8.
RL_GPUS=0,1,2,3,4,5,6,7; SWE_DP_SHARD=2; SWE_GEN_DP=1; SWE_NUM_GENERATORS=6
# CHANGED: SWE_VAL_SAMPLES=0 means no in-training validation runs, but the
# config still resolves the path, and della's TB-2.0 evalset is not on this box.
# Pointed at the training mix so the path exists; nothing is scored from it.
SWE_TB2_VAL_DATA=$BASE/data/mix/live.jsonl
# The training mix. della's launch_9b.sh resolves this from the root, which is
# why rltrain.env carries no path; this script has to set it itself.
SWE_PROMPT_DATA=$BASE/data/mix/live.jsonl
SWE_DATA_HOT_RELOAD=1
SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_GROUP_SIZE=16
SWE_MAX_ACTIVE_GROUPS=160
SWE_INITIAL_ACTIVE_GROUPS=64
SWE_TRAIN_STEPS=150
SWE_LR=3e-6
SWE_DROP_ZERO_STD=0
TMAX_AGENT=terminus
TMAX_TERMINUS_MAX_TURNS=120
SWE_MAX_CONTEXT_LEN=63488
TMAX_TURN_MAX_TOKENS=32768
TMAX_EXEC_TIMEOUT_SEC=120
SWE_TIME_BUDGET_SEC=2400
SWE_AGENT_TIMEOUT_FLOOR_SEC=7200
SWE_GEN_BACKEND=vllm_native
# CHANGED: vLLM here refuses 0.9 (it sees ~228 of 288 GiB free at startup);
# 0.7 is the ceiling measured on 09-15 with the card otherwise empty, which
# every card is now that lihanc holds nothing on them.
SWE_GPU_MEM_LIMIT=0.7
SWE_GEN_PREFIX_CACHE=1
SWE_GEN_VLLM_DEFAULT_COMPILE=1
SWE_DISABLE_CUSTOM_ALL_REDUCE=1
# CHANGED: 160, not della's 1536. This box decodes far slower per card than a
# B300, so della's concurrency starved 70% of the 09-17 run's rollouts past
# the 2400 s budget (which is a controlled variable and stays at 2400: see
# CLAUDE.md). The 09-17/18 eval measured six independent engines at 160 ->
# 0-3% timeouts (per-turn wait median 23-26 s) and at 256 -> 5% queue-tail
# timeouts. Raise only after measuring per-turn wait at 160 with this layout.
SWE_ROLLOUT_CONCURRENCY=160
SWE_NUM_ROLLOUT_WORKERS=16
SWE_CKPT_INTERVAL=5
SWE_CKPT_KEEP=3
SWE_VAL_SAMPLES=0
SWE_VAL_INTERVAL=20
SWE_NUM_EVAL_GENERATORS=0
TT_DAYTONA_EPHEMERAL=1
TT_DAYTONA_CPU=1
TT_DAYTONA_MEM_GB=2
TT_DAYTONA_MAX_MEM_GB=8
TT_DAYTONA_DISK_GB=2
TT_DAYTONA_CREATE_CONCURRENCY=128
TT_DAYTONA_CREATE_RETRIES=8
TT_DAYTONA_HEARTBEAT_SEC=180
TT_DAYTONA_AUTO_DELETE_MIN=15
TT_DAYTONA_TTL_MIN=300
# CHANGED: its own label, so this run's sandboxes are separable from every other
# tenant's in the shared Daytona account.
TT_DAYTONA_LABEL=hip_evolve_v2
SWE_EVOLUTION_HARDER_RATIO=0.9
SWE_LMHEAD_TF32=1
SWE_AC=selective
SWE_LOSS_CHUNKS=8
# Live W&B, as on della: the key comes from work/wandb.env (Zhifei 09-18: hip
# is a trusted environment, every key may live here).
WANDB_PROJECT=terminal-agent-rl
RL_OBSERVE_REWARDS=1
HF_HOME=$ROOT/hf-home; PYTHONPATH=$ROOT/torchtitan
# ~/.cache on this account is a dangling symlink, so every cache root is redirected.
XDG_CACHE_HOME=$ROOT/work/cache; VLLM_CACHE_ROOT=$ROOT/work/cache/vllm
TRITON_CACHE_DIR=$ROOT/work/cache/triton
VLLM_ALLREDUCE_USE_FLASHINFER=0
# The wheel is built against ROCm 7.2.3 and links whatever libamdhip64/libhsa/
# librccl it finds; this host has 7.2.0, under which a Triton attention kernel
# aborts the queue seconds into generation. The 7.2.3 tree copied out of the
# vLLM image goes first on the library path. HSA_ENABLE_IPC_MODE_LEGACY, which
# that image also sets, is deliberately NOT set: with it hipIpcGetMemHandle
# fails and RCCL cannot use GPU P2P.
ROCM_PATH=$ROOT/rocm-7.2.3; LD_LIBRARY_PATH=$ROOT/rocm-7.2.3/lib:${LD_LIBRARY_PATH:-}
HSA_NO_SCRATCH_RECLAIM=1; HIP_FORCE_DEV_KERNARG=1
SAFETENSORS_FAST_GPU=1; PYTORCH_NVML_BASED_CUDA_CHECK=1; TOKENIZERS_PARALLELISM=false
set +a
# The same arithmetic the allocator enforces, checked here so a mismatch fails
# at launch with the numbers in it rather than after a dump directory exists.
_want=$(( SWE_DP_SHARD + SWE_NUM_GENERATORS * SWE_GEN_DP ))
_have=$(echo $RL_GPUS | tr ',' '\n' | grep -c .)
[ "$_want" = "$_have" ] || {
    echo "[train] RL_GPUS lists $_have GPUs but DP_SHARD($SWE_DP_SHARD) + $SWE_NUM_GENERATORS x GEN_DP($SWE_GEN_DP) = $_want" >&2
    exit 2
}
cd $RUN
# What this run was given, in the shape della's launch.json has (LAYOUT.md).
python3 - "$RUN" "$RESUMED_FROM" "$CHECKPOINT_STEP" <<'PY'
import json, os, sys, time
run, resumed, step = sys.argv[1:]
env = {k: v for k, v in os.environ.items() if k.startswith(("SWE_", "TMAX_", "TT_DAYTONA_", "RL_", "TRL_", "WANDB_"))}
json.dump({"run": os.path.basename(run), "started": time.strftime("%Y%m%d-%H%M%SZ", time.gmtime()),
           "resumed_from": resumed or None, "checkpoint_step": int(step) if step else None,
           "gpus": os.environ.get("RL_GPUS"), "env": env},
          open(os.path.join(run, "launch.json"), "w"), indent=1, sort_keys=True)
PY
echo "[train] root=$BASE run=$RUN gpus=$RL_GPUS trainer=$SWE_DP_SHARD generators=$SWE_NUM_GENERATORS x dp$SWE_GEN_DP steps=$SWE_TRAIN_STEPS${RESUMED_FROM:+ resumed_from=$RESUMED_FROM@step-$CHECKPOINT_STEP}"
setsid nohup $ROOT/venv/bin/python -u -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators $SWE_NUM_GENERATORS \
    --hf_assets_path $TRL_MODEL --dump_folder $RUN/trainer "$@" \
    > $RUN/stdout.log 2>&1 < /dev/null &
echo "$! -> $RUN/stdout.log"
