#!/bin/bash
# Launch the evolve loop on della-tridao with its full credential environment.
# This recipe previously lived only in shell history; a daemon relaunched
# without SYNTH_ENV_FILE fails every retune with "no OPENAI_API_KEY", and
# without the daytona env it cannot revalidate. Usage:
#   setsid nohup bash /scratch/al9080/terminal-rl/seeds/scripts/della/launch_evolveloop.sh \
#     >> /scratch/gpfs/TRIDAO/al9080/terminal-rl/evolution/evolve_ondella.log 2>&1 &
set -euo pipefail
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
. ~/.config/daytona/env
export SYNTH_ENV_FILE=$R/.synth_env
export TRL_TT=$HOME/torchtitan-yichuan
export SWE_TASK_EVOLUTION_DIR=${SWE_TASK_EVOLUTION_DIR:-$R/evolution/signals}
EVOLUTION_ROOT=$(dirname "$SWE_TASK_EVOLUTION_DIR")
export SWE_EVOLUTION_TRACE_DIR=${SWE_EVOLUTION_TRACE_DIR:-$SWE_TASK_EVOLUTION_DIR/codex_traces}
export SWE_EVOLUTION_STATS=${SWE_EVOLUTION_STATS:-$EVOLUTION_ROOT/evolution_stats.json}
export SWE_EVOLUTION_LINEAGE=${SWE_EVOLUTION_LINEAGE:-$EVOLUTION_ROOT/evolution_lineage.jsonl}
EVOLUTION_LOG=${SWE_EVOLUTION_LOG:-$EVOLUTION_ROOT/evolve_ondella.log}
mkdir -p "$SWE_TASK_EVOLUTION_DIR" "$SWE_EVOLUTION_TRACE_DIR"
export SWE_RETUNE_AGENT=codex
export SWE_SIMPLIFY_HINT=vague
# Easier arm stays OFF: 0/k signals defer to evolution/deferred_easier instead
# of being simplified. Opening it is a pending owner decision; take8 ran with
# it off (142 deferrals in the take8 window prove the live setting).
export SWE_EVOLVE_SIMPLIFY=0
cd $R
exec /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python \
  evolve-onhost/scripts/evolve_ondella.py --interval 120 --workers "${1:-16}" \
  --log "$EVOLUTION_LOG"
