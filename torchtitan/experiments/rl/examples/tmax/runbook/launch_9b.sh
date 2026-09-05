#!/bin/bash
# Start the reference 9B TerminalWorld RL run.
#
#   TRL_PROFILE=andy ./launch_9b.sh                  # whose checkout and data root
#   TRL_PROFILE=andy ./launch_9b.sh --dry-run        # lay the run out, start nothing
#   TRL_PROFILE=andy RL_RESUME_FROM=tmax-9b--20260904-181500Z ./launch_9b.sh
#   TRL_PROFILE=yichuan RL_DATA=/path/other.jsonl ./launch_9b.sh
#
# Two people launch on this box from the same account, each from their own
# checkout and data root. profiles/<name>.env holds those two paths (and nothing
# else); rltrain.env holds the recipe, which is shared. TRL_PROFILE has no
# default on purpose: the one time it was implied, every run launched from the
# other person's tree.
#
# Anything already set in the environment wins over both files, so a one-off
# override needs no edit to either. Everything this script does not set is left
# at the code default on purpose.
#
# Where a run's files go is LAYOUT.md's decision, not this script's: one
# directory per process lifetime under $TRL_BASE/runs/, with everything the run
# read or produced inside it. This script makes that directory, records what
# the run was given (launch.json, inputs/), and tells the trainer where it is
# (TRL_RUN_DIR and --dump_folder). layout.py spells the paths; the few repeated
# here in shell are spelled the same way.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
for _arg in "$@"; do
    case "$_arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "[launch] unknown argument $_arg; the only flag is --dry-run" >&2; exit 2 ;;
    esac
done

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

: "${TRL_BASE:?set TRL_BASE (the experiment root: data/, runs/, evolution/)}"
: "${TRL_TT:?set TRL_TT (torchtitan checkout)}"
: "${TRL_MODEL:?set TRL_MODEL (Qwen3.5-9B directory)}"
: "${TRL_VENV:=/scratch/gpfs/TRIDAO/al9080/titan-rl}"

# The old resume knob reused the previous run's directory. Refuse it outright:
# silently starting a fresh run when a resume was meant costs the GPU-hours
# between here and the moment somebody reads the step counter.
if [ -n "${RL_RESUME_DUMP:-}" ]; then
    echo "[launch] RL_RESUME_DUMP is gone. A resume is a new run directory that records" >&2
    echo "[launch] where it came from: RL_RESUME_FROM=<run dir or run name>." >&2
    exit 2
fi

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

# ---- the run directory -------------------------------------------------------
# runs/<prefix>--<UTC stamp>: the name is the identity, and stamps sort by time.
# Not `mkdir -p`: two launches in one second must not share a directory.
STAMP=$(date -u +%Y%m%d-%H%M%SZ)
RUN=$TRL_BASE/runs/tmax-9b--$STAMP
mkdir -p "$TRL_BASE/runs"
mkdir "$RUN"
export TRL_RUN_DIR=$RUN
# From here on everything this script and the trainer print is the run's
# stdout.log as well as the terminal (the journal, under systemd). Before this
# line the log went to a flat logs/ under a name that was not the run's, and
# matching the two meant reading timestamps.
exec > >(tee -a "$RUN/stdout.log") 2>&1
echo "[launch] run=$RUN"

# The allocator reads RL_GPUS and gives each mesh its slice of that list BY
# POSITION, overwriting CUDA_VISIBLE_DEVICES inside the spawned process before
# CUDA starts. The list does not have to be contiguous. Order is the placement:
# the trainer takes the first SWE_DP_SHARD entries, then one entry per generator
# engine, then one per eval-generator engine, so put the quietest GPUs where the
# generators land and the eval host last.
export RL_GPUS=${RL_GPUS:-0,1,2,3,4}
_n=$(awk -F, '{print NF}' <<< "$RL_GPUS")
_uniq=$(tr ',' '\n' <<< "$RL_GPUS" | sort -u | grep -c .)
if [ "$_uniq" -ne "$_n" ]; then
    echo "[launch] RL_GPUS=$RL_GPUS names the same device twice." >&2
    exit 2
fi
# Eval generators are GPUs too. This check used to leave them out entirely, so any
# run with SWE_NUM_EVAL_GENERATORS>0 passed here and then failed in the allocator
# ("RL_GPUS lists N device(s) but this run needs M") after the launcher had already
# stamped a dump directory. SWE_EVAL_GEN_DP unset means an eval host is as wide as a
# training one, which is what train.py assumes too.
_eval_n=${SWE_NUM_EVAL_GENERATORS:-0}
_eval_dp=${SWE_EVAL_GEN_DP:-0}
[ "$_eval_dp" -eq 0 ] && _eval_dp=${SWE_GEN_DP:-3}
_want=$(( ${SWE_DP_SHARD:-2} + ${SWE_GEN_DP:-3} + _eval_n * _eval_dp ))
if [ "$_want" -ne "$_n" ]; then
    echo "[launch] SWE_DP_SHARD($SWE_DP_SHARD) + SWE_GEN_DP($SWE_GEN_DP)" >&2
    echo "[launch]   + $_eval_n eval generator(s) x $_eval_dp GPU = $_want" >&2
    echo "[launch] but RL_GPUS lists $_n GPUs. They must match." >&2
    exit 2
fi
export CUDA_VISIBLE_DEVICES=$RL_GPUS

# torchtitan is NOT installed into the venv; it is imported from the checkout.
export PYTHONPATH=$TRL_TT
# The mix is a convention of the root, not a setting (LAYOUT.md rule 5):
# data/mix/live.jsonl, a hardlink to the version currently served. RL_DATA, or
# an exported SWE_PROMPT_DATA, points one run at something else; that file is
# then copied into the run and has no mix version.
export SWE_PROMPT_DATA=${RL_DATA:-${SWE_PROMPT_DATA:-$TRL_BASE/data/mix/live.jsonl}}
if [ ! -f "$SWE_PROMPT_DATA" ]; then
    echo "[launch] SWE_PROMPT_DATA=$SWE_PROMPT_DATA does not exist (profile $TRL_PROFILE)." >&2
    echo "[launch] publish the mix there, or export RL_DATA=/path/to/mix.jsonl." >&2
    exit 2
fi
# The two per-rollout outputs are switches, on by default, and carry no path:
# the trainer writes rollouts/ and signals/ under TRL_RUN_DIR. Exported so
# launch.json shows the value the run had.
export SWE_ROLLOUT_RECORDS=${SWE_ROLLOUT_RECORDS:-1}
export SWE_EVOLUTION_SIGNALS=${SWE_EVOLUTION_SIGNALS:-1}

# ---- checkpoints -------------------------------------------------------------
# Checkpoints go to the host-local disk, not the run directory. The shared GPFS
# fileset was down to 337 GB free on 2026-09-02 with 39 x 102 GB checkpoints on
# it, and a save that hits the quota leaves a truncated step dir behind. The run
# gets a `checkpoints` symlink to wherever they went, so the run directory alone
# still says. Host-local means the trainer must run on this box.
#
# A resume is a NEW run directory (LAYOUT.md: one process lifetime, one
# directory) that points at the previous run's checkpoint folder and records
# which run that was. The checkpointer itself picks the latest step-* in the
# folder; the step is read here only so launch.json can say it.
RESUMED_FROM=""
CHECKPOINT_STEP=""
if [ -n "${RL_RESUME_FROM:-}" ]; then
    case "$RL_RESUME_FROM" in
        */*) _old=$RL_RESUME_FROM ;;
        *)   _old=$TRL_BASE/runs/$RL_RESUME_FROM ;;
    esac
    if [ ! -d "$_old" ]; then
        echo "[launch] RL_RESUME_FROM=$RL_RESUME_FROM: no run directory at $_old" >&2
        exit 2
    fi
    if [ -L "$_old/checkpoints" ]; then
        SWE_CKPT_FOLDER=$(readlink "$_old/checkpoints")
    elif [ -d "$_old/checkpoints" ]; then
        SWE_CKPT_FOLDER=$_old/checkpoints
    else
        echo "[launch] $_old has no checkpoints link or folder; nothing to resume from." >&2
        exit 2
    fi
    export SWE_CKPT_FOLDER
    RESUMED_FROM=$(basename "$_old")
else
    export SWE_CKPT_FOLDER=${SWE_CKPT_FOLDER:-/scratch/al9080/terminal-rl/ckpt/$(basename "$RUN")}
    # The folder is NOT created here: the checkpointer creates it on the first
    # save. Before the 2026-09-02 fix in components/checkpoint.py, a folder that
    # existed without a step-* made the trainer skip the initial HF load and
    # start from random weights; not pre-creating it keeps that from mattering
    # on a tree without the fix.
fi
ln -s "$SWE_CKPT_FOLDER" "$RUN/checkpoints"
# Say which weights the trainer will start from. `ls` exits 2 when the glob
# matches nothing, and under `set -o pipefail` that is the pipeline's status,
# so without the `|| true` a fresh run (no step-* yet) died silently with exit
# 2 before printing a single [launch] line. Keep the failure local.
_latest_step=$(ls -d "$SWE_CKPT_FOLDER"/step-* 2>/dev/null | sort -V | tail -1 || true)
if [ -n "$_latest_step" ]; then
    CHECKPOINT_STEP=${_latest_step##*/step-}
    echo "[launch] resuming from $_latest_step${RESUMED_FROM:+ (run $RESUMED_FROM)}"
elif [ -n "$RESUMED_FROM" ]; then
    echo "[launch] RL_RESUME_FROM=$RESUMED_FROM but no step-* under $SWE_CKPT_FOLDER; nothing to resume from." >&2
    exit 2
else
    echo "[launch] fresh start: initial weights from $TRL_MODEL (no step-* under $SWE_CKPT_FOLDER)"
fi

# ---- the record --------------------------------------------------------------
# launch.json says what this run was given; inputs/mix.jsonl IS what it read
# (the same inode as the served version, so a later publish cannot change it);
# runs/latest points here; experiment.json is written once per root.
TT_COMMIT=$(git -C "$TRL_TT" rev-parse --short HEAD)
if [ -n "$(git -C "$TRL_TT" status --porcelain --untracked-files=no)" ]; then
    # A commit that does not describe the files is worse than none: say so.
    TT_COMMIT=$TT_COMMIT-dirty
fi
python - "$RUN" "$STAMP" "$TT_COMMIT" "$RESUMED_FROM" "$CHECKPOINT_STEP" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

# layout.py is imported by file: going through the package runs
# torchtitan/experiments/rl/__init__.py, which imports vllm, and the launcher
# needs paths, not a GPU stack.
sys.path.insert(0, os.path.join(os.environ["TRL_TT"], "torchtitan/experiments/rl/examples/tmax"))
import layout  # noqa: E402

run_dir, started, tt_commit, resumed_from, checkpoint_step = sys.argv[1:6]
root = layout.Root.from_env()
run = layout.Run(Path(run_dir))
mix = root.mix
data = Path(os.environ["SWE_PROMPT_DATA"])

# Which version the root is serving right now, whatever this run reads.
served = mix.live_version()

# The mix this run boots from: a hardlink to the history version when it reads
# live.jsonl, a copy with no version when it reads anything else.
if served is not None and data.resolve() == mix.live.resolve():
    mix_version, version_path = served
    try:
        mix_sha256 = json.loads(layout.MixDir.manifest_of(version_path).read_text())["sha256"]
    except (OSError, ValueError, KeyError):
        mix_sha256 = layout.sha256_file(version_path)
    layout.link_or_copy(version_path, run.inputs_mix)
    how = f"hardlink to {version_path.relative_to(root.path)}"
else:
    mix_version, mix_sha256 = None, layout.sha256_file(data)
    run.inputs.mkdir(parents=True, exist_ok=True)
    shutil.copy2(data, run.inputs_mix)
    how = f"copy of {data} (not a served mix version)"

prefixes = ("SWE_", "TMAX_", "TT_DAYTONA", "RL_", "TRL_", "CUDA_VISIBLE", "WANDB_PROJECT")
layout.write_json_atomic(run.launch_json, {
    "run": run.name,
    "started": started,
    "profile": os.environ["TRL_PROFILE"],
    "tt": os.environ["TRL_TT"],
    "tt_commit": tt_commit,
    "mix_version": mix_version,
    "mix_sha256": mix_sha256,
    "gpus": os.environ["CUDA_VISIBLE_DEVICES"],
    "resumed_from": resumed_from or None,
    "checkpoint_step": int(checkpoint_step) if checkpoint_step else None,
    "env": {k: v for k, v in os.environ.items() if k.startswith(prefixes)},
})

# runs/latest: a new link renamed over the old one, so a reader never finds it
# missing (GNU ln -sfn before coreutils 9.1 unlinks first, then links).
tmp = root.latest.with_name("latest.incoming")
if tmp.is_symlink() or tmp.exists():
    tmp.unlink()
os.symlink(run.name, tmp)
os.replace(tmp, root.latest)

if not root.experiment_json.exists():
    layout.write_json_atomic(root.experiment_json, {
        "name": root.path.name,
        "created": started,
        "profile": os.environ["TRL_PROFILE"],
        "purpose": "",
        "seed_mix_version": served[0] if served else None,
        "forked_from": None,
    })
    print(f"[launch] first run in this root: wrote {root.experiment_json} (fill in purpose)")

print(f"[launch] mix version={mix_version} sha256={mix_sha256[:12]} inputs/mix.jsonl is a {how}")
PY

echo "[launch] profile=$TRL_PROFILE tt=$TRL_TT@$TT_COMMIT data=$SWE_PROMPT_DATA gpus=$CUDA_VISIBLE_DEVICES ckpt=$SWE_CKPT_FOLDER"
if [ "$DRY_RUN" = 1 ]; then
    echo "[launch] dry run: laid out and recorded, trainer not started"
    echo "$RUN"
    exit 0
fi

# W&B writes wandb/ under the CWD, so run from the run directory. The trainer's
# own output (structured logs, metrics, profiles, validation traces) is told
# where to go explicitly: --dump_folder is Controller.Config.dump_folder, whose
# code default is the CWD-relative outputs/rl.
cd "$RUN"
exec python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 1 \
    --dump_folder "$RUN/trainer" \
    --hf_assets_path "$TRL_MODEL"
