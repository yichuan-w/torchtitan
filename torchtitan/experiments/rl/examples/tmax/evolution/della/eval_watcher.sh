#!/bin/bash
# Auto-submit a TB2 eval for every new take8 checkpoint. Runs on della-tridao;
# sbatch happens via ssh to the login node (della9). One eval per step dir,
# tracked in a submitted-marker file. Exits after 24h.
#
# Checkpoints may live on della-tridao's LOCAL RAID (SWE_CKPT_FOLDER in
# rltrain.env) because the shared GPFS fileset can be filled by other tenants
# mid-save. pli eval nodes can only read GPFS, so each new local step is
# rsynced to the dump's GPFS checkpoint dir before sbatch, and the GPFS copy
# is pruned once its eval job leaves the queue (the local copy is the
# permanent archive). With SWE_CKPT_FOLDER unset, this degrades to the old
# watch-GPFS-directly behavior (rsync from a dir to itself is a no-op).
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
MARK=$R/logs/eval_submitted.list
LOG=$R/logs/eval_watcher.log
touch $MARK
deadline=$(( $(date +%s) + 86400 ))
while [ $(date +%s) -lt $deadline ]; do
  D=$(sed -n "s/^RL_RESUME_DUMP=//p" $R/scripts/rltrain.env 2>/dev/null | tail -1)
  [ -d "$D" ] || D=$(ls -d $R/runs/tw-mix-take8-* 2>/dev/null | tail -1)
  GCK=$D/outputs/rl/checkpoint
  SRC=$(sed -n "s/^SWE_CKPT_FOLDER=//p" $R/scripts/rltrain.env 2>/dev/null | tail -1)
  [ -d "$SRC" ] || SRC=$GCK
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
    OUT=$(ssh -o BatchMode=yes -o ConnectTimeout=20 della9 "sbatch --export=ALL,TRL_TT=$TRL_TT $R/scripts/tb2_eval.sbatch $GCK/$S" 2>&1)
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
