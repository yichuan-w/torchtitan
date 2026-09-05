#!/bin/bash
# Auto-submit a TB2 eval for every new checkpoint of a run. Runs on
# della-tridao; sbatch happens via ssh to the login node (della9). One eval per
# step dir, tracked in a submitted-marker file. Exits after 24h.
#
#   eval_watcher.sh [run dir]       # default: $TRL_BASE/runs/latest
#
# Checkpoints live on della-tridao's LOCAL RAID (the run's `checkpoints` link
# points there) because the shared GPFS fileset can be filled by other tenants
# mid-save. pli eval nodes can only read GPFS, so each new local step is
# rsynced to the run's `checkpoints-staged/` on GPFS before sbatch, and the
# staged copy is pruned once its eval job leaves the queue (the local copy is
# the permanent archive). A run whose `checkpoints` is a real directory on
# GPFS degrades to watching it directly (rsync from a dir to itself is a no-op).
: "${TRL_BASE:?the experiment root}"
: "${TRL_TT:?the checkout the eval runs}"
: "${TRL_MODEL:?the base model directory}"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=$TRL_BASE
D=$(readlink -f "${1:-$R/runs/latest}")
[ -d "$D" ] || { echo "no run directory at ${1:-$R/runs/latest}" >&2; exit 2; }
GCK=$D/checkpoints-staged
SRC=$(readlink -f "$D/checkpoints" 2>/dev/null)
[ -d "$SRC" ] || SRC=$GCK
mkdir -p "$R/evals" "$R/logs"
MARK=$R/evals/submitted.list
LOG=$R/logs/eval_watcher--$(date -u +%Y%m%d-%H%M%SZ).log
touch $MARK
deadline=$(( $(date +%s) + 86400 ))
while [ $(date +%s) -lt $deadline ]; do
  for CK in $(ls -d $SRC/step-* 2>/dev/null); do
    S=$(basename $CK)
    grep -q "^$SRC/$S$" $MARK && continue
    # complete = no tmp files and dir older than 2 min
    [ -n "$(find $CK -name "*.tmp" 2>/dev/null | head -1)" ] && continue
    [ -z "$(find $CK -maxdepth 0 -mmin +2 2>/dev/null)" ] && continue
    if [ "$SRC" != "$GCK" ]; then
      # GPFS needs ~110 GiB for the staged copy; skip this pass if the
      # fileset cannot take it, and retry on the next.
      FREE_GB=$(checkquota 2>/dev/null | awk "/fileset TRIDAO/ {u=\$7; l=\$9; gsub(/TiB/,\"\",u); gsub(/TiB/,\"\",l); printf \"%d\", (l-u)*1024; exit}")
      if [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt 115 ]; then
        echo "$(date "+%F %T") $S deferred: fileset free ${FREE_GB}GiB < 115GiB" >> $LOG
        continue
      fi
      mkdir -p $GCK
      if ! rsync -a --delete "$CK/" "$GCK/$S/"; then
        echo "$(date "+%F %T") $S rsync to GPFS failed; will retry" >> $LOG
        rm -rf "$GCK/$S"
        continue
      fi
    fi
    OUT=$(ssh -o BatchMode=yes -o ConnectTimeout=20 della9 "sbatch --export=ALL,TRL_TT=$TRL_TT,TRL_BASE=$TRL_BASE,TRL_MODEL=$TRL_MODEL --output=$R/logs/tb2_eval--%j.log $HERE/tb2_eval.sbatch $GCK/$S" 2>&1)
    echo "$(date "+%F %T") $S -> $OUT" >> $LOG
    echo "$SRC/$S" >> $MARK
  done
  # prune GPFS staging copies whose eval job left the queue (local is the
  # archive; pending evals for pre-migration steps stay until their jobs run)
  if [ "$SRC" != "$GCK" ]; then
    QUEUED=$(ssh -o BatchMode=yes -o ConnectTimeout=20 della9 "squeue -u al9080 -h -o %i" 2>/dev/null)
    for G in $(ls -d $GCK/step-* 2>/dev/null); do
      S=$(basename $G)
      [ -d "$SRC/$S" ] || continue   # never prune a copy with no local original
      JID=$(grep -a " $S -> Submitted batch job" $LOG | tail -1 | grep -oE "[0-9]+$")
      [ -n "$JID" ] || continue
      if ! echo "$QUEUED" | grep -q "^$JID$"; then
        echo "$(date "+%F %T") pruning GPFS copy $S (job $JID done)" >> $LOG
        rm -rf "$G"
      fi
    done
  fi
  sleep 120
done
