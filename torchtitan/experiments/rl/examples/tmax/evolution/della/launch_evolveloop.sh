#!/bin/bash
# Start the evolve loop for one training workdir, from nothing, as a systemd
# user unit running evolve_ondella.py from the checkout this script sits in.
#
#   usage: TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
#          launch_evolveloop.sh <workdir> [workers]
#
# The three TT_DAYTONA_* are the trainer's fleet defaults and have to match its
# launch env: a row declaring no daytona_* of its own is verified at this size.
# To restart a running loop with new code, use restart_evolve.sh instead; it
# carries the running loop's environment across.
set -euo pipefail
W=${1:?workdir, e.g. /scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-20260903b}
WORKERS=${2:-16}
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

ARGS=()
while IFS= read -r kv; do ARGS+=(-E "$kv"); done < <(
  env | grep -E '^(PATH|HOME|SYNTH_|TRL_|PYTHONPATH|SWE_|TT_DAYTONA_|DAYTONA_|OPENAI_)')
systemd-run --user --unit="$UNIT" --collect --working-directory="$W" "${ARGS[@]}" \
  bash -c "exec $PY $EVO/evolve_ondella.py --interval 120 --workers $WORKERS --log $LOG >> ${LOG%.log}_stdout.log 2>&1"
sleep 8
echo "unit $UNIT: $(systemctl --user is-active "$UNIT")"
systemctl --user show "$UNIT" -p MainPID -p ActiveEnterTimestamp
tail -3 "$LOG"
