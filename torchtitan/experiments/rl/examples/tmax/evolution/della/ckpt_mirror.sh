#!/usr/bin/env bash
# Keep one copy of a run's newest complete checkpoint on GPFS.
#
#   TRL_BASE=... ckpt_mirror.sh [run dir]      # default: $TRL_BASE/runs/latest
#
# Checkpoints are written to the host's local RAID (runs/<run>/checkpoints links
# there) and the trainer keeps the last SWE_CKPT_KEEP steps. That disk is one
# box with no backup: a failed array or a wiped /scratch takes every step of
# every run with it. This copies the newest complete step into the run's own
# checkpoints-mirror/ on GPFS and removes the older mirror, so the run always
# has exactly one step off the box, at the cost of one checkpoint of GPFS quota
# (102 GB for the 9B) and a copy per checkpoint interval. A step is complete
# when it holds no *.tmp and has not changed for two minutes, the same test
# eval_watcher.sh uses. Run it from a timer:
#
#   systemd-run --user --unit=ckpt-mirror --on-calendar='*:0/15' \
#       -E TRL_BASE=$TRL_BASE bash $EVO/della/ckpt_mirror.sh
#
# Runs are sequential in a root, so mirroring runs/latest follows the chain.
set -uo pipefail
: "${TRL_BASE:?the experiment root}"
RUN=$(readlink -f "${1:-$TRL_BASE/runs/latest}")
[ -d "$RUN" ] || { echo "no run directory at ${1:-$TRL_BASE/runs/latest}" >&2; exit 2; }
SRC=$(readlink -f "$RUN/checkpoints" 2>/dev/null)
[ -d "$SRC" ] || { echo "$RUN has no checkpoints yet" >&2; exit 0; }
DST=$RUN/checkpoints-mirror
LOG=$TRL_BASE/logs/ckpt_mirror.log
mkdir -p "$DST" "$TRL_BASE/logs"
stamp() { date -u +%Y%m%d-%H%M%SZ; }

# newest complete step: highest step-N with no *.tmp and untouched for 2 minutes
STEP=
for d in $(ls -d "$SRC"/step-* 2>/dev/null | sort -t- -k2 -n -r); do
    [ -n "$(find "$d" -name '*.tmp' -print -quit 2>/dev/null)" ] && continue
    [ -z "$(find "$d" -maxdepth 0 -mmin +2 2>/dev/null)" ] && continue
    STEP=$(basename "$d"); break
done
[ -n "$STEP" ] || { echo "$(stamp) run=$(basename "$RUN") nothing complete yet" >> "$LOG"; exit 0; }
if [ -d "$DST/$STEP" ]; then
    exit 0   # already mirrored; nothing newer is complete
fi
t0=$(date +%s)
if rsync -a --delete "$SRC/$STEP/" "$DST/$STEP.incoming/"; then
    mv "$DST/$STEP.incoming" "$DST/$STEP"
    for old in "$DST"/step-*; do
        [ "$old" = "$DST/$STEP" ] && continue
        case "$old" in *.incoming) ;; esac
        rm -rf "$old"
    done
    echo "$(stamp) run=$(basename "$RUN") step=$STEP secs=$(( $(date +%s) - t0 )) bytes=$(du -sb "$DST/$STEP" | cut -f1) status=ok" >> "$LOG"
else
    rm -rf "$DST/$STEP.incoming"
    echo "$(stamp) run=$(basename "$RUN") step=$STEP secs=$(( $(date +%s) - t0 )) status=rsync_failed" >> "$LOG"
    exit 1
fi
