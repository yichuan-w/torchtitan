#!/usr/bin/env bash
# Restart the della-side evolution loop, preserving the environment it needs.
#
# The loop's credentials and knobs live only in its own environment -- there is
# no env file it reads at startup -- so a restart has to carry them across from
# the process being replaced. Word-splitting a saved environment does not
# survive contact with it: SSH_CONNECTION and LESSOPEN hold spaces, and
# `env $(cat saved)` turns the second word of the first such value into the
# command name. Read it line by line instead, value verbatim to end of line.
set -uo pipefail
ROOT=/scratch/gpfs/TRIDAO/al9080/terminal-rl
VENV=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
ENVFILE=${1:-$ROOT/tmp/evolve.env}
WORKERS=${EVOLVE_WORKERS:-16}
INTERVAL=${EVOLVE_INTERVAL:-120}

[ -f "$ENVFILE" ] || { echo "no env snapshot at $ENVFILE"; exit 1; }

OLD_PID=$(pgrep -f evolve_ondella.py | head -1)
if [ -n "$OLD_PID" ]; then
    OLD_PGID=$(ps -o pgid= -p "$OLD_PID" | tr -d ' ')
    OLD_TRACE_DIR=$(tr '\0' '\n' < "/proc/$OLD_PID/environ" | sed -n 's/^SWE_EVOLUTION_TRACE_DIR=//p')
    case "$OLD_PGID" in
        ''|*[!0-9]*) echo "invalid process group for pid $OLD_PID: $OLD_PGID"; exit 1 ;;
    esac
    [ "$OLD_PGID" -gt 1 ] || { echo "refusing to stop process group $OLD_PGID"; exit 1; }
    echo "stopping the running loop process group $OLD_PGID"
    # The loop is started with setsid. Stop every process in its group so Codex and
    # validator subprocesses cannot outlive the loop being replaced.
    kill -TERM -- "-$OLD_PGID"
    for _ in $(seq 1 20); do
        kill -0 -- "-$OLD_PGID" 2>/dev/null || break
        sleep 3
    done
    kill -0 -- "-$OLD_PGID" 2>/dev/null && kill -KILL -- "-$OLD_PGID"
    sleep 2
    if [ -n "$OLD_TRACE_DIR" ]; then
        "$VENV" "$ROOT/evolve-onhost/scripts/finalize_interrupted_traces.py" \
            "$OLD_TRACE_DIR" --stopped-loop-pid "$OLD_PID" || \
            echo "warning: some Codex trace records could not be finalized"
    fi
fi

# Session-scoped variables belong to whoever was logged in when the snapshot was
# taken, not to the loop; carrying them forward is at best noise.
while IFS= read -r line; do
    case "$line" in
        ""|"#"*) continue ;;
        SSH_*|_=*|SHLVL=*|PWD=*|OLDPWD=*|TERM=*|LESSOPEN=*|LESSCLOSE=*) continue ;;
        # Exported shell functions arrive as `BASH_FUNC_name%%=() {` and run
        # over several lines, so every line after the first parses as garbage.
        # They are the shell's, not the loop's.
        BASH_FUNC_*|"}"|"("*|" "*|")"*) continue ;;
    esac
    key=${line%%=*}
    case "$key" in
        [A-Za-z_][A-Za-z0-9_]*) export "$key=${line#*=}" ;;
    esac
done < "$ENVFILE"

export SYNTH_ENV_FILE=${SYNTH_ENV_FILE:-$ROOT/.synth_env}
export TRL_TT=${TRL_TT:-$HOME/torchtitan}
export SWE_RETUNE_AGENT=${SWE_RETUNE_AGENT:-codex}
export SWE_SIMPLIFY_HINT=${SWE_SIMPLIFY_HINT:-vague}
export SWE_EVOLVE_SIMPLIFY=${SWE_EVOLVE_SIMPLIFY:-0}
SWE_TASK_EVOLUTION_DIR=${SWE_TASK_EVOLUTION_DIR:-$ROOT/evolution/signals}
export SWE_TASK_EVOLUTION_DIR
EVOLUTION_ROOT=$(dirname "$SWE_TASK_EVOLUTION_DIR")
LEGACY_TRACE_DIR=$EVOLUTION_ROOT/codex_traces
# Old environment snapshots may contain the previous derived default as an
# explicit value. Rewrite only that value; preserve custom trace directories.
if [ -z "${SWE_EVOLUTION_TRACE_DIR:-}" ] || [ "$SWE_EVOLUTION_TRACE_DIR" = "$LEGACY_TRACE_DIR" ]; then
    SWE_EVOLUTION_TRACE_DIR=$SWE_TASK_EVOLUTION_DIR/codex_traces
fi
export SWE_EVOLUTION_TRACE_DIR
export SWE_EVOLUTION_STATS=${SWE_EVOLUTION_STATS:-$EVOLUTION_ROOT/evolution_stats.json}
export SWE_EVOLUTION_LINEAGE=${SWE_EVOLUTION_LINEAGE:-$EVOLUTION_ROOT/evolution_lineage.jsonl}
LOG=${SWE_EVOLUTION_LOG:-$EVOLUTION_ROOT/evolve_ondella.log}
mkdir -p "$SWE_TASK_EVOLUTION_DIR" "$SWE_EVOLUTION_TRACE_DIR" "$(dirname "$LOG")"

cd "$ROOT" || exit 1
setsid nohup "$VENV" evolve-onhost/scripts/evolve_ondella.py \
    --interval "$INTERVAL" --workers "$WORKERS" --log "$LOG" \
    >> "$LOG" 2>&1 < /dev/null &
sleep 15

PID=$(pgrep -f evolve_ondella.py | head -1)
if [ -z "$PID" ]; then
    echo "FAILED to start; last lines of $LOG:"
    tail -5 "$LOG"
    exit 1
fi
echo "running pid=$PID workers=$WORKERS interval=$INTERVAL"
echo "  SWE_RETUNE_AGENT=$(tr '\0' '\n' < /proc/$PID/environ | sed -n 's/^SWE_RETUNE_AGENT=//p')"
echo "  SWE_TASK_EVOLUTION_DIR=$(tr '\0' '\n' < /proc/$PID/environ | sed -n 's/^SWE_TASK_EVOLUTION_DIR=//p')"
echo "  SWE_EVOLUTION_TRACE_DIR=$(tr '\0' '\n' < /proc/$PID/environ | sed -n 's/^SWE_EVOLUTION_TRACE_DIR=//p')"
echo "  DAYTONA_API_KEY set: $(tr '\0' '\n' < /proc/$PID/environ | grep -c '^DAYTONA_API_KEY=')"
echo "  SYNTH_ENV_FILE=$(tr '\0' '\n' < /proc/$PID/environ | sed -n 's/^SYNTH_ENV_FILE=//p')"
