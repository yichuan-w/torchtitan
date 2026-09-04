#!/bin/bash
# One round of the evolve loop over a DEV workdir, in the foreground, from
# the checkout this script sits in: the way to try a change without touching
# the loop that feeds a training run.
#
#   usage: TRL_PROFILE=andy TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 \
#          TT_DAYTONA_DISK_GB=2 evolve_dev_round.sh <dev-workdir> [limit] [workers]
#
# A dev workdir is a training workdir's shape without a trainer: a copy of
# the mix at data/mix/mix_live.jsonl (folds land in the copy), the task pools
# under data/ (symlinks are fine), and evolution/signals fed by
# replay_signals.sh from a real run's consumed signals. Each round consumes
# the signals it processes and costs one Codex session and a few sandboxes
# per k/k signal, so keep the limit small.
#
# Run it from the checkout your profile names, with the branch checked out --
# never from a tree files were copied into, or the commit this prints is not
# the code that ran. evolveloop_env.sh refuses when the two disagree.
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
# The commit is the record of what ran, so it has to be the whole record: a
# tracked file differing from HEAD means the tree was edited or copied into and
# the commit named here did not produce this round. Expect zero.
echo "checkout $TT at $(git -C "$TT" log --oneline -1); $(git -C "$TT" status --porcelain --untracked-files=no | wc -l | tr -d ' ') tracked file(s) differ from HEAD"
echo "signals pending: $(ls "$SWE_TASK_EVOLUTION_DIR"/*.json 2>/dev/null | wc -l), limit $LIMIT, log $LOG"
cd "$W"
exec "$PY" "$EVO/evolve_ondella.py" --once --limit "$LIMIT" --workers "$WORKERS" --log "$LOG"
