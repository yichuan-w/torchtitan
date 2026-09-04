#!/bin/bash
# One round of the evolve loop over a DEV workdir, in the foreground, from
# the checkout this script sits in: the way to try a change without touching
# the loop that feeds a training run.
#
#   usage: TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
#          evolve_dev_round.sh <dev-workdir> [limit] [workers]
#
# A dev workdir is a training workdir's shape without a trainer: a copy of
# the mix at data/mix/mix_live.jsonl (folds land in the copy), the task pools
# under data/ (symlinks are fine), and evolution/signals fed by
# replay_signals.sh from a real run's consumed signals. Each round consumes
# the signals it processes and costs one Codex session and a few sandboxes
# per k/k signal, so keep the limit small.
set -euo pipefail
W=${1:?dev workdir, e.g. /scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-evolve-dev}
LIMIT=${2:-3}
WORKERS=${3:-4}
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/evolveloop_env.sh"
case "$W" in
  *wd-evolve-dev*|*-dev*) ;;
  *) echo "refusing: $W does not look like a dev workdir (name it *-dev)"; exit 1 ;;
esac
# The dev checkout usually carries a branch's files rsynced over HEAD, so say
# both: which commit, and how many tracked files differ from it.
echo "checkout $TT at $(git -C "$TT" log --oneline -1); $(git -C "$TT" status --porcelain | wc -l | tr -d ' ') file(s) differ from HEAD"
echo "signals pending: $(ls "$SWE_TASK_EVOLUTION_DIR"/*.json 2>/dev/null | wc -l), limit $LIMIT, log $LOG"
cd "$W"
exec "$PY" "$EVO/evolve_ondella.py" --once --limit "$LIMIT" --workers "$WORKERS" --log "$LOG"
