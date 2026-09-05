#!/usr/bin/env bash
# (Re)start the evolve loop for one experiment root as a systemd user unit.
#
#   TRL_PROFILE=andy TRL_BASE=<root> TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 \
#     TT_DAYTONA_DISK_GB=2 restart_evolve.sh [workers] [interval]
#
# Everything derives from TRL_PROFILE and TRL_BASE through
# della/evolveloop_env.sh: the checkout is the profile's, the unit is
# evolve-<basename of TRL_BASE>, the log is <root>/evolution/loop.log and the
# environment the unit runs with is snapshotted to <root>/evolution/loop.env.
# A running loop is found through <root>/evolution/loop.lock (the loop writes
# host and pid there); when one is alive on this host its whole process group
# is stopped first -- Codex sessions and probes must not outlive the loop
# being replaced -- and the records they leave `running` are marked
# interrupted. With no loop alive this is the launcher.
#
# A user unit rather than setsid nohup: nohup'd processes are SIGKILLed with
# the ssh session on della-tridao. --collect removes the unit on exit, so read
# loop.log for health rather than systemctl status.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORKERS=${1:-16}
INTERVAL=${2:-120}
# shellcheck disable=SC1091
. "$HERE/della/evolveloop_env.sh" || exit 2
EV=$TRL_BASE/evolution
LOCK=$EV/loop.lock
ENVFILE=$EV/loop.env
"$PY" -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)' 2>/dev/null \
  || { echo "$PY is not a virtualenv python; set TRL_VENV to the training venv"; exit 1; }

# The loop alive over this root, if any: the lock names its host and pid.
OLD=
if [ -f "$LOCK" ]; then
  host=$(sed -n 's/^host=\([^ ]*\) .*/\1/p' "$LOCK" | head -1)
  pid=$(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$LOCK" | head -1)
  if [ "$host" = "$(hostname)" ] && [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
     && tr '\0' '\n' < "/proc/$pid/cmdline" | grep -q 'evolve_ondella\.py$'; then
    OLD=$pid
  elif [ -n "$host" ] && [ "$host" != "$(hostname)" ] \
       && [ $(( $(date +%s) - $(stat -c %Y "$LOCK") )) -lt 90 ]; then
    echo "a loop on $host holds $LOCK (heartbeat under 90 s); stop it there first"; exit 1
  fi
fi

if [ -n "$OLD" ]; then
  PGID=$(ps -o pgid= -p "$OLD" | tr -d ' ')
  # A signal to group 0 or -1 reaches every process of the user; never that.
  [ "${PGID:-0}" -gt 1 ] 2>/dev/null || { echo "refusing to signal process group '$PGID'"; exit 1; }
  echo "stopping loop pid $OLD (pgid $PGID) over $TRL_BASE"
  kill -TERM -- "-$PGID"
  for _ in $(seq 1 30); do kill -0 "$OLD" 2>/dev/null || break; sleep 2; done
  kill -0 "$OLD" 2>/dev/null && { echo "still alive, SIGKILL"; kill -KILL -- "-$PGID"; sleep 2; }
  "$PY" "$EVO/finalize_interrupted_traces.py" --stopped-loop-pid "$OLD" \
    || echo "warning: some session or rewrite records could not be finalized"
fi

# The unit's environment, in systemd EnvironmentFile form (KEY="value",
# backslash and double quote escaped): what evolveloop_env.sh exported plus
# the credentials it sourced. Session-scoped variables belong to whoever
# launched it, not to the loop. Under evolution/, which the lineage snapshot
# never adds wholesale -- it names its files one by one.
umask 077
env | python3 -c '
import sys
keep = ("PATH", "HOME", "LANG", "LC_", "SYNTH_", "TRL_", "PYTHONPATH", "SWE_", "TMAX_",
        "TT_DAYTONA_", "DAYTONA_", "OPENAI_", "CODEX_", "EVOLVE_")
for line in sys.stdin.read().split("\n"):
    if "=" not in line:
        continue
    k, v = line.split("=", 1)
    if not k.startswith(keep) or not k.replace("_", "a").isalnum():
        continue
    v = v.replace("\\", "\\\\").replace("\"", "\\\"")
    print(f"{k}=\"{v}\"")
' > "$ENVFILE.new" || exit 1
mv "$ENVFILE.new" "$ENVFILE"

systemctl --user stop "$UNIT" 2>/dev/null
systemd-run --user --unit="$UNIT" --collect --working-directory="$TRL_BASE" \
  -p EnvironmentFile="$ENVFILE" \
  bash -c "exec $PY $EVO/evolve_ondella.py --interval $INTERVAL --workers $WORKERS >> $LOG 2>&1"
sleep 8
echo "unit $UNIT: $(systemctl --user is-active "$UNIT")"
systemctl --user show "$UNIT" -p MainPID -p ActiveEnterTimestamp
tail -3 "$LOG"
