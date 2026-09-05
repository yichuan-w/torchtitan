#!/bin/bash
# The same signals through the evolve loop twice, SWE_VERIFIER_AUTHOR=same
# then =blind, on a DEV root, so the two modes are compared on identical
# tasks from an identical starting state. Prints nothing decisive itself; the
# record is $DEV/logs/ab_verifier_author--<stamp>/<mode>/, which
# ab_verifier_author_summary.py reads.
#
#   usage: TRL_PROFILE=andy ab_verifier_author.sh <dev-root> <from-root> <signal-id>...
#
# A signal id is the ledger's name for it, <run>/<task>--g<group>, so
#   jq -r 'select(.direction=="harder") | .signal' $FROM/evolution/ledger.jsonl | tail -3
# feeds this directly. The signal file and the rollout records it names are
# put at the same relative path under $DEV/runs/<run>/; a run directory under
# a dev root is a replayed run, nothing trains there.
#
# The loop's pending set is every signal without a ledger line, so a replay is:
# drop the ids' lines from the dev ledger, run a round, and between the two
# modes put back what the round changed -- the ledger, the mix versions it
# published, and the task directories of the replayed tasks. Rewrites live
# inside the task directory, so each mode's task directories are copied into
# the record before they are put back.
set -euo pipefail
DEV=${1:?dev root, an experiment root whose name carries -dev}
FROM=${2:?the root whose run emitted the signals}
shift 2
[ $# -gt 0 ] || { echo "name the signal ids to replay, <run>/<task>--g<group>" >&2; exit 2; }
case "$DEV" in *-dev*) ;; *) echo "refusing: $DEV is not a dev root" >&2; exit 1 ;; esac
[ -f "$DEV/experiment.json" ] || { echo "refusing: $DEV has no experiment.json; make it with new_root.py --fork-from $FROM" >&2; exit 1; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
JQ=$DEV/bin/jq
[ -x "$JQ" ] || { echo "no jq at $JQ; a root's bin/ holds codex and jq" >&2; exit 1; }
STAMP=$(date -u +%Y%m%d-%H%M%SZ)
AB=$DEV/logs/ab_verifier_author--$STAMP
LEDGER=$DEV/evolution/ledger.jsonl
HISTORY=$DEV/data/mix/history
mkdir -p "$AB/before/tasks" "$DEV/evolution/tasks" "$HISTORY"
touch "$LEDGER"
printf '%s\n' "$@" > "$AB/signals.txt"
: > "$AB/tasks.txt"

# Stage the signals: the file, the run's launch.json, and every record the
# signal names, hardlinked (same inode, no second copy) where the two roots
# share a filesystem.
for SIG in "$@"; do
  RUN=${SIG%%/*}; NAME=${SIG#*/}
  SRC=$FROM/runs/$RUN/signals/$NAME.json
  [ -f "$SRC" ] || { echo "no signal $SRC" >&2; exit 1; }
  mkdir -p "$DEV/runs/$RUN/signals"
  cp -p "$SRC" "$DEV/runs/$RUN/signals/$NAME.json"
  if [ -f "$FROM/runs/$RUN/launch.json" ]; then cp -p "$FROM/runs/$RUN/launch.json" "$DEV/runs/$RUN/"; fi
  "$JQ" -r '.attempts[]' "$SRC" | while IFS= read -r REL; do
    mkdir -p "$(dirname "$DEV/runs/$RUN/$REL")"
    ln -f "$FROM/runs/$RUN/$REL" "$DEV/runs/$RUN/$REL" 2>/dev/null || cp -p "$FROM/runs/$RUN/$REL" "$DEV/runs/$RUN/$REL"
  done
  "$JQ" -r '.task' "$SRC" >> "$AB/tasks.txt"
done
sort -u -o "$AB/tasks.txt" "$AB/tasks.txt"
N=$(wc -l < "$AB/signals.txt")

# The state before the first round: the ledger without the replayed ids (so
# the loop sees them as pending), the newest mix version, the replayed tasks'
# directories as they are.
IDS=$(printf '%s\n' "$@" | "$JQ" -R . | "$JQ" -s .)
"$JQ" -c --argjson ids "$IDS" 'select(($ids | index(.signal)) == null)' "$LEDGER" > "$AB/before/ledger.jsonl"
BEFORE_LINES=$(wc -l < "$AB/before/ledger.jsonl")
BEFORE_VER=$(ls "$HISTORY"/v*.manifest.json 2>/dev/null | sed -E 's/.*\/v0*([0-9]+)--.*/\1/' | sort -n | tail -1)
BEFORE_VER=${BEFORE_VER:-0}
while read -r T; do
  if [ -d "$DEV/evolution/tasks/$T" ]; then cp -a "$DEV/evolution/tasks/$T" "$AB/before/tasks/"; fi
done < "$AB/tasks.txt"
echo "[ab] $STAMP  $N signals, $(wc -l < "$AB/tasks.txt") tasks, mix v$BEFORE_VER  record $AB"

restore_state() {
  cp "$AB/before/ledger.jsonl" "$LEDGER"
  # Versions the round published come off; live.jsonl goes back to the newest
  # that remains, as a hardlink the way publish() makes it.
  for F in "$HISTORY"/v*; do
    [ -e "$F" ] || continue
    V=$(basename "$F" | sed -E 's/^v0*([0-9]+)--.*/\1/')
    if [ "$V" -gt "$BEFORE_VER" ]; then rm -f "$F"; fi
  done
  LIVE=$(ls "$HISTORY"/v*.jsonl 2>/dev/null | sort | tail -1)
  if [ -n "$LIVE" ]; then ln -f "$LIVE" "$DEV/data/mix/live.jsonl"; fi
  while read -r T; do
    rm -rf "$DEV/evolution/tasks/$T"
    if [ -d "$AB/before/tasks/$T" ]; then cp -a "$AB/before/tasks/$T" "$DEV/evolution/tasks/"; fi
  done < "$AB/tasks.txt"
}

for MODE in same blind; do
  OUT=$AB/$MODE
  mkdir -p "$OUT/tasks"
  restore_state
  START=$(date +%s)
  echo "[ab] mode=$MODE start $(date -u -Is) limit=$N workers=3"
  SWE_VERIFIER_AUTHOR=$MODE TT_DAYTONA_CPU=${TT_DAYTONA_CPU:-1} TT_DAYTONA_MEM_GB=${TT_DAYTONA_MEM_GB:-2} \
    TT_DAYTONA_DISK_GB=${TT_DAYTONA_DISK_GB:-2} \
    "$HERE/evolve_dev_round.sh" "$DEV" "$N" 3 > "$OUT/round.log" 2>&1 || echo "[ab] mode=$MODE round exited $?" | tee -a "$OUT/round.log"
  END=$(date +%s)
  # What the round did: its ledger lines, and the task directories they name
  # (lineage, revisions, rewrites with their sessions), whole.
  tail -n +$((BEFORE_LINES + 1)) "$LEDGER" > "$OUT/ledger.jsonl"
  while read -r T; do
    if [ -d "$DEV/evolution/tasks/$T" ]; then cp -a "$DEV/evolution/tasks/$T" "$OUT/tasks/"; fi
  done < "$AB/tasks.txt"
  echo "{\"mode\": \"$MODE\", \"started\": $START, \"finished\": $END, \"wall_s\": $((END - START))}" > "$OUT/timing.json"
  ACCEPTED=0
  while read -r RW; do
    [ -n "$RW" ] || continue
    if [ "$("$JQ" -r .status "$OUT/tasks/${RW#tasks/}/rewrite.json" 2>/dev/null)" = accepted ]; then ACCEPTED=$((ACCEPTED + 1)); fi
  done < <("$JQ" -r 'select(.outcome == "handled") | .rewrite' "$OUT/ledger.jsonl")
  echo "[ab] mode=$MODE done in $((END - START))s; $(wc -l < "$OUT/ledger.jsonl") ledger lines, $ACCEPTED accepted"
done
echo "[ab] finished; summarise with: python3 $HERE/../ab_verifier_author_summary.py $AB"
