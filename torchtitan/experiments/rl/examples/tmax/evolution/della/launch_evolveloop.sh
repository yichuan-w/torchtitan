#!/bin/bash
# Start the evolve loop for one training workdir, from nothing, as a systemd
# user unit running evolve_ondella.py from the checkout this script sits in.
#
#   usage: TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
#          launch_evolveloop.sh <workdir> [workers]
#
# This is the production loop that feeds a training run: restart it only at
# an agreed moment (restart_evolve.sh carries its environment across). Try a
# change first with evolve_dev_round.sh over a dev workdir.
set -euo pipefail
W=${1:?workdir, e.g. /scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-20260903b}
WORKERS=${2:-16}
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/evolveloop_env.sh"

ARGS=()
while IFS= read -r kv; do ARGS+=(-E "$kv"); done < <(
  env | grep -E '^(PATH|HOME|SYNTH_|TRL_|PYTHONPATH|SWE_|TT_DAYTONA_|DAYTONA_|OPENAI_)')
systemd-run --user --unit="$UNIT" --collect --working-directory="$W" "${ARGS[@]}" \
  bash -c "exec $PY $EVO/evolve_ondella.py --interval 120 --workers $WORKERS --log $LOG >> ${LOG%.log}_stdout.log 2>&1"
sleep 8
echo "unit $UNIT: $(systemctl --user is-active "$UNIT")"
systemctl --user show "$UNIT" -p MainPID -p ActiveEnterTimestamp
tail -3 "$LOG"
