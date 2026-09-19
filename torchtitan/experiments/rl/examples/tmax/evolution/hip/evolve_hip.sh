#!/usr/bin/env bash
# The evolve loop over the tmax-v2-evolve root on this box, as a systemd user
# unit -- della's restart_evolve.sh + della/evolveloop_env.sh +
# claude_proxy/claude_env.sh folded into one file, because those derive
# everything from a runbook profile and this box has none.
#
#   evolve_hip.sh [workers] [interval]     (re)start the unit; defaults 16, 120
#   evolve_hip.sh dry                      one foreground round, --once --dry
#
# Model calls: Codex CLI -> 127.0.0.1:4000 -> reverse ssh tunnel -> LiteLLM on
# mac-mini -> Claude. Only the proxy's master key is on this box
# (work/llm-proxy.env); the Anthropic key never leaves mac-mini.
set -uo pipefail
ROOT=/data/yichuan_wang/trl
export TRL_BASE=$ROOT/work/titan/tmax-v2-evolve
export TRL_TT=$ROOT/torchtitan TRL_VENV=$ROOT/venv PYTHONPATH=$ROOT/torchtitan
PY=$TRL_VENV/bin/python
EVO=$TRL_TT/torchtitan/experiments/rl/examples/tmax/evolution
[ -f $TRL_BASE/experiment.json ] || { echo "no root at $TRL_BASE"; exit 2; }
[ -x $TRL_BASE/bin/codex ] || { echo "no codex at $TRL_BASE/bin/codex"; exit 2; }
set -a
. $ROOT/work/daytona.env          # DAYTONA_API_KEY: the trainer's, so revalidation runs where rollouts do
. $ROOT/work/llm-proxy.env        # LITELLM_MASTER_KEY
set +a
export OPENAI_API_KEY=$LITELLM_MASTER_KEY SYNTH_API_BASE=http://127.0.0.1:4000/v1 SYNTH_MODEL=claude-opus-5
unset EVOLVE_CODEX_AUTH_FILE EVOLVE_CODEX_ACCOUNT_HOME
# Fleet defaults: must match titan_tmax_evolve_host.sh, since a row declaring
# no daytona_* of its own is verified at this size.
export TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2
export TT_DAYTONA_EPHEMERAL=1 TT_DAYTONA_AUTO_DELETE_MIN=15 TT_DAYTONA_LABEL=hip_evolve_v2_loop
# As della's loop: agentic retune on the rollout records, vague hints, easier
# arm off, student-mode hardening (no operator menu).
export SWE_RETUNE_AGENT=codex SWE_SIMPLIFY_HINT=vague
export SWE_EVOLVE_SIMPLIFY=${SWE_EVOLVE_SIMPLIFY:-0} EVOLVE_HARDER_OPERATORS=${EVOLVE_HARDER_OPERATORS:-0}
# claude_env.sh: a Claude turn can think for minutes and a busy proxy stalls
# longer than the CLI's default reconnects; and Claude at ~1.8 turns/min needs
# more than the 40-minute session default for the verifier and repair stages.
export EVOLVE_CODEX_EXTRA_CONFIG="model_providers.oai.stream_max_retries=20 model_providers.oai.request_max_retries=10 model_providers.oai.stream_idle_timeout_ms=900000"
export EVOLVE_AGENT_TIMEOUT=${EVOLVE_AGENT_TIMEOUT:-7200}
EV=$TRL_BASE/evolution; LOCK=$EV/loop.lock; ENVFILE=$EV/loop.env; LOG=$EV/loop.log
UNIT=evolve-$(basename "$TRL_BASE")
mkdir -p "$EV"
curl -s -m 5 http://127.0.0.1:4000/health/liveliness >/dev/null || { echo "proxy not reachable on 127.0.0.1:4000 (tunnel from mac-mini down?)"; exit 2; }

if [ "${1:-}" = dry ]; then
  cd "$TRL_BASE" && exec $PY $EVO/evolve_ondella.py --once --dry --limit 1
fi
WORKERS=${1:-16}; INTERVAL=${2:-120}

# The loop alive over this root, if any (as restart_evolve.sh).
OLD=
if [ -f "$LOCK" ]; then
  pid=$(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$LOCK" | head -1)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
     && tr '\0' '\n' < "/proc/$pid/cmdline" | grep -q 'evolve_ondella\.py$'; then OLD=$pid; fi
fi
if [ -n "$OLD" ]; then
  PGID=$(ps -o pgid= -p "$OLD" | tr -d ' ')
  [ "${PGID:-0}" -gt 1 ] 2>/dev/null || { echo "refusing to signal process group '$PGID'"; exit 1; }
  echo "stopping loop pid $OLD (pgid $PGID)"
  kill -TERM -- "-$PGID"
  for _ in $(seq 1 30); do kill -0 "$OLD" 2>/dev/null || break; sleep 2; done
  kill -0 "$OLD" 2>/dev/null && { echo "still alive, SIGKILL"; kill -KILL -- "-$PGID"; sleep 2; }
  $PY "$EVO/finalize_interrupted_traces.py" --stopped-loop-pid "$OLD" || echo "warning: some records could not be finalized"
fi

umask 077
env | python3 -c '
import sys
keep = ("PATH", "HOME", "LANG", "LC_", "SYNTH_", "TRL_", "PYTHONPATH", "SWE_", "TMAX_",
        "TT_DAYTONA_", "DAYTONA_", "OPENAI_", "CODEX_", "EVOLVE_")
for line in sys.stdin.read().split("\n"):
    if "=" not in line: continue
    k, v = line.split("=", 1)
    if not k.startswith(keep) or not k.replace("_", "a").isalnum(): continue
    v = v.replace("\\", "\\\\").replace("\"", "\\\"")
    print(f"{k}=\"{v}\"")
' > "$ENVFILE.new" || exit 1
mv "$ENVFILE.new" "$ENVFILE"

systemctl --user stop "$UNIT" 2>/dev/null; systemctl --user reset-failed "$UNIT" 2>/dev/null
systemd-run --user --unit="$UNIT" --collect --working-directory="$TRL_BASE" \
  -p EnvironmentFile="$ENVFILE" \
  bash -c "exec $PY $EVO/evolve_ondella.py --interval $INTERVAL --workers $WORKERS >> $LOG 2>&1"
sleep 8
echo "unit $UNIT: $(systemctl --user is-active "$UNIT")"
systemctl --user show "$UNIT" -p MainPID -p ActiveEnterTimestamp
tail -3 "$LOG"
