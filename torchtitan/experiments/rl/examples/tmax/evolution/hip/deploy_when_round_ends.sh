#!/usr/bin/env bash
# Wait for the running evolve round to end, then move hip's checkout to the
# given branch tip and restart the loop unit -- so no Codex session in flight
# is interrupted by the restart. The trainer keeps running: it never imports
# the loop's modules, and the role files are read per session.
#   deploy_when_round_ends.sh <branch>
set -uo pipefail
BR=${1:?branch}
ROOT=/data/yichuan_wang/trl; R=$ROOT/work/titan/tmax-v2-evolve; LOG=$R/evolution/loop.log
n0=$(grep -a -c "INFO round:" $LOG)
echo "[$(date -u +%FT%TZ)] waiting for round $((n0+1)) to end (rounds so far: $n0)"
while [ "$(grep -a -c "INFO round:" $LOG)" -le "$n0" ]; do sleep 20; done
echo "[$(date -u +%FT%TZ)] round ended; deploying $BR"
cd $ROOT/torchtitan && git fetch -q origin "$BR" && git checkout -q --detach "origin/$BR" && git log --oneline -1
cd $ROOT && bash scripts/evolve_hip.sh 16 120
echo "[$(date -u +%FT%TZ)] done"
