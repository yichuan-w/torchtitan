#!/bin/bash
# Drives the local TB2 eval queue on della-tridao, two evals per wave
# (GPU windows 0-1 and 2-3). Waits for any in-flight evals first, then
# works through the checkpoint list in information-value order. Each eval's
# avg@k/pass@k line is appended to results/tb2_local_results.txt as it ends.
set -u
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
GPFS_CK=$R/runs/tw-mix-take8-20260829-213707/outputs/rl/checkpoint
LOCAL_CK=/scratch/al9080/terminal-rl/ckpt/tw-mix-take8-20260829-213707
OUT=$R/results/tb2_local_results.txt
QUEUE="step-20 step-10 step-30 step-5 step-25 step-15 step-35"

ckpt_path() {  # prefer local RAID copy, fall back to GPFS
  [ -d "$LOCAL_CK/$1" ] && echo "$LOCAL_CK/$1" || echo "$GPFS_CK/$1"
}
busy() { pgrep -f "torchtitan.experiments.rl.train.*tb2_eval" >/dev/null; }
harvest() {  # label logfile
  local line
  line=$(grep -aoE "avg@k=[0-9.]+ pass@k=[0-9.]+" "$2" | tail -1)
  echo "$(date "+%F %T") $1 ${line:-NO_RESULT_LINE}" >> $OUT
}

while busy; do sleep 60; done
harvest base $R/logs/tb2_local_base.log
harvest step-40 $R/logs/tb2_local_step-40.log

set -- $QUEUE
while [ $# -gt 0 ]; do
  A=$1; shift
  B=${1:-}; [ $# -gt 0 ] && shift
  setsid bash $R/scripts/tb2_eval_local.sh "$(ckpt_path $A)" 0 > $R/logs/tb2_local_$A.log 2>&1 &
  PA=$!
  PB=
  if [ -n "$B" ]; then
    setsid bash $R/scripts/tb2_eval_local.sh "$(ckpt_path $B)" 2 > $R/logs/tb2_local_$B.log 2>&1 &
    PB=$!
  fi
  wait $PA 2>/dev/null; [ -n "$PB" ] && wait $PB 2>/dev/null
  harvest $A $R/logs/tb2_local_$A.log
  [ -n "$B" ] && harvest $B $R/logs/tb2_local_$B.log
done
echo "$(date "+%F %T") DRIVER_DONE" >> $OUT
