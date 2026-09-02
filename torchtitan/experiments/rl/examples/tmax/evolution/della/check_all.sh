#!/bin/bash
# One readout for both things in flight: the training run and the codex review.
# Three traps this avoids: grep the log only AFTER the current boot offset (a
# pre-restart line reads as current otherwise), pick the codex log by mtime
# (review-round1.log sorts after review5.log), and read the checkpoint count out
# of the dump directory THIS boot named rather than a hardcoded one -- a stale
# path there reports a finished run's checkpoints as the live run's, which is
# worse than reporting none, because it says the run is recoverable when it is
# not.
ROOT=/scratch/gpfs/TRIDAO/al9080/terminal-rl
L=$ROOT/logs/rltrain_take8.log
W=/scratch/gpfs/TRIDAO/al9080/codex-review
B=$(grep -abo "launch] dump" $L | tail -1 | cut -d: -f1)
SEG=$(tail -c +$B $L)
DUMP=$(tail -c +$B $L | grep -aoE 'dump=[^ ]+' | head -1 | cut -d= -f2)
STEP=$(echo "$SEG" | grep -a trainer_loop | grep -aoE 'step [0-9]+' | tail -1 | grep -oE '[0-9]+')
ROT=$(echo "$SEG" | grep -aoE '[0-9]+ in rotation' | tail -1)
DONE=$(echo "$SEG" | grep -ac status=completed)
VAL=$(echo "$SEG" | grep -a 'validation trace report' | tail -1 | grep -oE 'policy_version=[0-9]+\): avg@k=[0-9.]+ pass@k=[0-9.]+')
LATEST=$(ls -t $W/review*.log 2>/dev/null | head -1)
# codex echoes AGENTS.md into its own log, and that prompt names both verdict
# strings -- so a running review always "has" a verdict. Only read one once the
# process is gone.
if [ "$(pgrep -cf 'codex exec')" = "0" ]; then
    VERD=$(grep -aoE "VERDICT: (SHIP|DO NOT SHIP)" "$LATEST" 2>/dev/null | tail -1)
else
    VERD="running"
fi
CK=$(ls "$DUMP/outputs/rl/checkpoint" 2>/dev/null | wc -l)
echo "ST=$(systemctl --user is-active rltrain.service) STEP=${STEP:-warmup} ROT=${ROT:-?} DONE=$DONE CK=$CK DUMP=$(basename ${DUMP:-?})"
# Rollout wall clock, once the run is on a build that logs secs=. The pair to
# watch is p90 against the budget: they converge when tasks are being cut off.
# sort -n does the ordering so this stays on plain awk (mawk has no asort).
SECS=$(echo "$SEG" | grep -aoE 'secs=[0-9]+' | cut -d= -f2 | sort -n)
if [ -n "$SECS" ]; then
    echo "$SECS" | awk '{v[n++]=$1} END {printf "SECS n=%d p50=%d p90=%d p99=%d max=%d  budgets:", n, v[int(n*.5)], v[int(n*.9)], v[int(n*.99)], v[n-1]}'
    echo "$SEG" | grep -aoE 'budget=[0-9]+' | cut -d= -f2 | sort -n | uniq -c \
        | sort -rn | head -4 | awk '{printf " %ss x%s", $2, $1}'
    echo ""
else
    echo "SECS=not logged (build predates f774700)"
fi
echo "EVAL=${VAL:-none}"
echo "CODEX run=$(pgrep -cf 'codex exec') log=$(basename ${LATEST:-none}) verdict=${VERD:-pending}"
