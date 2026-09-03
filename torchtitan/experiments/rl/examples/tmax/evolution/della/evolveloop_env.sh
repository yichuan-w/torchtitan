#!/bin/bash
# The evolve loop's environment for one training workdir, shared by the
# launcher (systemd unit) and the dev round (foreground, --once). Source it
# with W set; it exports everything evolve_ondella.py reads and leaves PY, TT,
# EVO, UNIT and LOG set for the caller.
#
#   W=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/<wd> . evolveloop_env.sh
#
# The three TT_DAYTONA_* are the trainer's fleet defaults and have to match its
# launch env: a row declaring no daytona_* of its own is verified at this size.
: "${W:?workdir}"
: "${TT_DAYTONA_CPU:?the per-sandbox vCPU default the trainer runs with}"
: "${TT_DAYTONA_MEM_GB:?the per-sandbox memory default the trainer runs with, GiB}"
: "${TT_DAYTONA_DISK_GB:?the per-sandbox disk default the trainer runs with, GiB}"
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
EVO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TT=$(git -C "$EVO" rev-parse --show-toplevel)
PY=${TRL_VENV_PY:-/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python}
UNIT=evolve-$(basename "$W")
LOG=$W/logs/evolve.log

# Without the daytona env, structural revalidation fails `no_docker`; without
# SYNTH_ENV_FILE the loop dies at startup with `no OPENAI_API_KEY`.
# shellcheck disable=SC1090
. ~/.config/daytona/env
export SYNTH_ENV_FILE=$R/.synth_env
export TRL_TT=$TT PYTHONPATH=$TT TRL_BASE=$W
export SWE_PROMPT_DATA=$W/data/mix/mix_live.jsonl
export SWE_TASK_EVOLUTION_DIR=$W/evolution/signals
export SWE_EVOLUTION_TRACE_DIR=$SWE_TASK_EVOLUTION_DIR/codex_traces
# Agentic retune with the full failure traces as files, no chat fallback; a
# vague hint level, since specific where-to-look hints teach hint-following.
# The easier arm stays off: 0/k signals defer to evolution/deferred_easier.
export SWE_RETUNE_AGENT=codex SWE_SIMPLIFY_HINT=vague SWE_EVOLVE_SIMPLIFY=0
export TT_DAYTONA_CPU TT_DAYTONA_MEM_GB TT_DAYTONA_DISK_GB
mkdir -p "$SWE_TASK_EVOLUTION_DIR" "$SWE_EVOLUTION_TRACE_DIR" "$(dirname "$LOG")"
