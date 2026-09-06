#!/usr/bin/env bash
# Start training, dedicated TB evaluation, and evolution from one configuration.
set -euo pipefail
trap 'echo "start failed at line $LINENO; check the run config and the output above" >&2' ERR
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG=$(realpath "${1:?usage: start.sh /path/to/run.env [--dry-run]}")
MODE=${2:-}
case "$MODE" in ''|--dry-run) ;; *) echo "unknown option: $MODE" >&2; exit 2 ;; esac
set -a
. "$HERE/rltrain.env"
. "$CONFIG"
set +a
: "${TRL_PROFILE:?set TRL_PROFILE in the run config}"
: "${TRL_BASE:?set a separate experiment root in the run config}"
: "${TRL_VENV:?set TRL_VENV in the run config}"
PROFILE=$HERE/profiles/$TRL_PROFILE.env
test -f "$PROFILE"
TRL_TT=$(sed -n 's/^TRL_TT=//p' "$PROFILE")
export TRL_TT
test -d "$TRL_TT"
test -x "$TRL_VENV/bin/python"
test -s "$TRL_MODEL/config.json"
test -s "$TRL_BASE/data/mix/live.jsonl"
test -s "$SWE_TB2_VAL_DATA"
test "${SWE_VAL_SAMPLES:-0}" -eq 89
test "${SWE_TB2_VAL_K:-0}" -eq 5
test "${SWE_NUM_EVAL_GENERATORS:-0}" -eq 1
test "${SWE_EVAL_GEN_DP:-0}" -eq 1
test -s "${SYNTH_ENV_FILE:?set SYNTH_ENV_FILE in the run config}"
test -x "$TRL_BASE/bin/codex"
test -x "$TRL_BASE/bin/jq"
[ -f "$HOME/.config/daytona/env" ] && . "$HOME/.config/daytona/env"
: "${DAYTONA_API_KEY:?set DAYTONA_API_KEY or configure ~/.config/daytona/env}"
EVO=$TRL_TT/torchtitan/experiments/rl/examples/tmax/evolution
TRAIN_UNIT=train-$(basename "$TRL_BASE")
EVOLVE_UNIT=evolve-$(basename "$TRL_BASE")
systemctl --user show-environment >/dev/null
for unit in "$TRAIN_UNIT" "$EVOLVE_UNIT"; do
    if systemctl --user is-active --quiet "$unit"; then
        echo "$unit is already running; use a different root or stop it deliberately" >&2
        exit 2
    fi
done
echo "root=$TRL_BASE profile=$TRL_PROFILE GPUs=$RL_GPUS TB=89x5 interval=$SWE_VAL_INTERVAL"
echo "trainer: $TRL_BASE/runs/latest/stdout.log"
echo "evolution: $TRL_BASE/evolution/loop.log"
if [ "$MODE" = --dry-run ]; then
    bash "$HERE/launch_9b.sh" --dry-run
    echo "dry run: evolution and systemd services were not started"
    exit 0
fi
bash "$EVO/restart_evolve.sh" "${EVOLVE_WORKERS:-2}" 120
systemctl --user is-active --quiet "$EVOLVE_UNIT"
# Expand positional arguments in the service shell, not in this launcher.
# shellcheck disable=SC2016
if ! systemd-run --user --unit="$TRAIN_UNIT" --collect --working-directory="$TRL_TT" \
    bash -c 'set -a; . "$1"; . "$2"; set +a; exec bash "$3"' \
    -- "$HERE/rltrain.env" "$CONFIG" "$HERE/launch_9b.sh"; then
    systemctl --user stop "$EVOLVE_UNIT"
    exit 1
fi
sleep 2
if ! systemctl --user is-active --quiet "$TRAIN_UNIT"; then
    systemctl --user stop "$EVOLVE_UNIT"
    journalctl --user -u "$TRAIN_UNIT" -n 20 --no-pager
    exit 1
fi
echo "started $TRAIN_UNIT and $EVOLVE_UNIT"
