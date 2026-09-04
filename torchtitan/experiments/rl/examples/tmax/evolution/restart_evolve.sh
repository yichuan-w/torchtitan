#!/usr/bin/env bash
# Restart the della-side evolution loop, carrying the environment of the loop
# it replaces, as a systemd user unit.
#
# Everything comes from the running loop: its environment (read from
# /proc/<pid>/environ; there is no env file it reads at startup), its log,
# worker and interval arguments (from its argv) and the workdir it serves
# (from SWE_TASK_EVOLUTION_DIR). The replacement runs evolve_ondella.py from
# the checkout this script sits in, so fast-forward that checkout first. A
# user unit rather than setsid nohup: nohup'd processes are SIGKILLed with the
# ssh session on della-tridao. --collect removes the unit on exit, so read the
# loop's log for health rather than systemctl status.
#
#   usage: restart_evolve.sh <old loop pid>
#
# TT_DAYTONA_CPU / TT_DAYTONA_MEM_GB / TT_DAYTONA_DISK_GB set in this shell
# override the snapshot: the loop needs the trainer's values for a row that
# declares no daytona_* of its own, and an older loop may not carry them.
# TRL_VENV_PY names the interpreter when the old loop's own is not the venv
# python (default: the old loop's argv[0]).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OLD=${1:?old loop pid}

kill -0 "$OLD" 2>/dev/null || { echo "pid $OLD is not running"; exit 1; }
tr '\0' '\n' < "/proc/$OLD/cmdline" | grep -q 'evolve_ondella\.py$' \
  || { echo "pid $OLD is not an evolve_ondella.py process"; exit 1; }
argval() {  # value following --<name> in the old loop's argv, else $2
  local v; v=$(tr '\0' '\n' < "/proc/$OLD/cmdline" | awk -v k="--$1" '$0==k{getline; print; exit}')
  echo "${v:-$2}"
}
envval() { tr '\0' '\n' < "/proc/$OLD/environ" | sed -n "s/^$1=//p" | head -1; }

# The profile the stopped loop was launched with says which checkout the
# replacement runs; this script only needs its own location to find that
# profile. A copy invoked from elsewhere still restarts the profile's tree.
TRL_PROFILE=${TRL_PROFILE:-$(envval TRL_PROFILE)}
: "${TRL_PROFILE:?the stopped loop carried no TRL_PROFILE; pass one}"
PROFILE=$HERE/../runbook/profiles/$TRL_PROFILE.env
[ -f "$PROFILE" ] || { echo "no profile $TRL_PROFILE at $PROFILE"; exit 2; }
TT=$(sed -n 's/^TRL_TT=//p' "$PROFILE")
[ -d "$TT" ] || { echo "profile $TRL_PROFILE names TRL_TT=$TT, not a directory"; exit 2; }
EVO=$TT/torchtitan/experiments/rl/examples/tmax/evolution

SIGNALS=$(envval SWE_TASK_EVOLUTION_DIR)
[ -n "$SIGNALS" ] || { echo "old loop carries no SWE_TASK_EVOLUTION_DIR"; exit 1; }
EVROOT=$(dirname "$SIGNALS")
W=$(dirname "$EVROOT")
UNIT=evolve-$(basename "$W")
LOG=$(argval log "$EVROOT/evolve_ondella.log")
WORKERS=$(argval workers 16)
INTERVAL=$(argval interval 120)
# argv[0], not the resolved executable: the venv's bin/python is a symlink to
# the interpreter, and the interpreter run by its real path has no venv, so
# the loop starts and then finds no daytona or openai to import.
PY=${TRL_VENV_PY:-$(tr '\0' '\n' < "/proc/$OLD/cmdline" | head -1)}
"$PY" -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)' 2>/dev/null \
  || { echo "$PY is not a virtualenv python; set TRL_VENV_PY to the loop's venv"; exit 1; }
TRACE_DIR=$(envval SWE_EVOLUTION_TRACE_DIR)
TRACE_DIR=${TRACE_DIR:-$SIGNALS/codex_traces}
# Not under EVROOT: the loop's lineage snapshot `git add -A`s that directory,
# and the snapshot carries credentials.
ENVFILE=${EVOLVE_ENV_FILE:-$W/meta/evolve.env}
mkdir -p "$(dirname "$ENVFILE")" "$(dirname "$LOG")"

# Snapshot the running loop's environment in systemd EnvironmentFile form
# (KEY="value", backslash and double quote escaped). Session-scoped variables
# belong to whoever launched it, not to the loop.
umask 077
[ -f "$ENVFILE" ] && cp -p "$ENVFILE" "$ENVFILE.bak-$(date +%Y%m%d-%H%M%S)"
tr '\0' '\n' < "/proc/$OLD/environ" | python3 -c '
import os, sys
skip = ("SSH_", "BASH_FUNC_", "LESSOPEN", "LESSCLOSE")
drop = {"_", "SHLVL", "PWD", "OLDPWD", "TERM", "DISPLAY"}
override = {k: os.environ[k] for k in ("TT_DAYTONA_CPU", "TT_DAYTONA_MEM_GB", "TT_DAYTONA_DISK_GB")
            if os.environ.get(k)}
seen = set()
def emit(k, v):
    v = v.replace("\\", "\\\\").replace("\"", "\\\"")
    print(f"{k}=\"{v}\"")
for line in sys.stdin.read().split("\n"):
    if "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k in drop or k.startswith(skip) or not k.replace("_", "a").isalnum():
        continue
    seen.add(k)
    emit(k, override.get(k, v))
for k, v in override.items():
    if k not in seen:
        emit(k, v)
missing = [k for k in ("TT_DAYTONA_CPU", "TT_DAYTONA_MEM_GB", "TT_DAYTONA_DISK_GB")
           if k not in seen and k not in override]
if missing:
    print("warning: the loop will carry no " + " ".join(missing)
          + "; a row declaring no daytona_* is then verified at the harness default 2/4/6,"
          + " not at the size the trainer uses", file=sys.stderr)
' > "$ENVFILE.new" || exit 1
mv "$ENVFILE.new" "$ENVFILE"

# Stop the whole process group: Codex sessions and probes must not outlive
# the loop being replaced. Then mark the trace records they leave running.
PGID=$(ps -o pgid= -p "$OLD" | tr -d ' ')
# A signal to group 0 or -1 reaches every process of the user; never that.
[ "${PGID:-0}" -gt 1 ] 2>/dev/null || { echo "refusing to signal process group '$PGID'"; exit 1; }
echo "stopping loop pid $OLD (pgid $PGID) serving $W"
kill -TERM -- "-$PGID"
for _ in $(seq 1 30); do kill -0 "$OLD" 2>/dev/null || break; sleep 2; done
kill -0 "$OLD" 2>/dev/null && { echo "still alive, SIGKILL"; kill -KILL -- "-$PGID"; sleep 2; }
"$PY" "$EVO/finalize_interrupted_traces.py" "$TRACE_DIR" --stopped-loop-pid "$OLD" \
  || echo "warning: some Codex trace records could not be finalized"

systemctl --user stop "$UNIT" 2>/dev/null
systemd-run --user --unit="$UNIT" --collect --working-directory="$W" \
  -p EnvironmentFile="$ENVFILE" \
  bash -c "exec $PY $EVO/evolve_ondella.py --interval $INTERVAL --workers $WORKERS --log $LOG >> ${LOG%.log}_stdout.log 2>&1"
sleep 8
echo "unit $UNIT: $(systemctl --user is-active "$UNIT")"
systemctl --user show "$UNIT" -p MainPID -p ActiveEnterTimestamp
tail -3 "$LOG"
