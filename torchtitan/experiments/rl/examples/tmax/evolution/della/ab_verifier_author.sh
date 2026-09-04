#!/bin/bash
# The same k/k signals through the evolve loop twice, SWE_VERIFIER_AUTHOR=same
# then =blind, on a DEV workdir, so the two modes are compared on identical
# tasks from an identical starting pool. Prints nothing decisive itself; the
# record is $W/ab/<mode>/ and the trace directories it names, which
# ab_verifier_author_summary.py reads.
#
#   usage: TRL_PROFILE=andy ab_verifier_author.sh <dev-workdir> <from-consumed-dir> <signal-file>...
#
# Between the two runs the dev mix and evolution/parents are put back to the
# state they were in before the first, so the second run starts from the same
# seeds and the same rungs. A signal already consumed in the dev workdir is
# re-queued, since a replay is the point.
set -euo pipefail
W=${1:?dev workdir}
FROM=${2:?the consumed directory of the live round}
shift 2
[ $# -gt 0 ] || { echo "name the signal files to replay" >&2; exit 2; }
case "$W" in *-dev*) ;; *) echo "refusing: $W is not a dev workdir" >&2; exit 1 ;; esac
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
AB=$W/ab/$STAMP
mkdir -p "$AB"
cp "$W/data/mix/mix_live.jsonl" "$AB/mix_before.jsonl"
rm -rf "$AB/parents_before"; cp -r "$W/evolution/parents" "$AB/parents_before" 2>/dev/null || mkdir -p "$AB/parents_before"
printf '%s\n' "$@" > "$AB/signals.txt"
echo "[ab] $STAMP  $# signals  record $AB"

for MODE in same blind; do
    OUT=$AB/$MODE
    mkdir -p "$OUT"
    cp "$AB/mix_before.jsonl" "$W/data/mix/mix_live.jsonl"
    rm -rf "$W/evolution/parents"; cp -r "$AB/parents_before" "$W/evolution/parents"
    for f in "$@"; do
        rm -f "$W/evolution/consumed/$f"
        cp "$FROM/$f" "$W/evolution/signals/$f"
    done
    N=$(wc -l < "$AB/signals.txt")
    START=$(date +%s)
    echo "[ab] mode=$MODE start $(date -Is) limit=$N workers=3"
    # Every session and every check the round runs is inside the workdir's
    # trace directories; the lineage file names them.
    BEFORE=$(wc -l < "$W/evolution/evolution_lineage.jsonl" 2>/dev/null || echo 0)
    SWE_VERIFIER_AUTHOR=$MODE TT_DAYTONA_CPU=${TT_DAYTONA_CPU:-1} TT_DAYTONA_MEM_GB=${TT_DAYTONA_MEM_GB:-2} \
        TT_DAYTONA_DISK_GB=${TT_DAYTONA_DISK_GB:-2} \
        "$HERE/evolve_dev_round.sh" "$W" "$N" 3 > "$OUT/round.log" 2>&1 || echo "[ab] mode=$MODE round exited $?" | tee -a "$OUT/round.log"
    END=$(date +%s)
    tail -n +$((BEFORE + 1)) "$W/evolution/evolution_lineage.jsonl" > "$OUT/lineage.jsonl" 2>/dev/null || true
    cp "$W/data/mix/mix_live.jsonl" "$OUT/mix_after.jsonl"
    echo "{\"mode\": \"$MODE\", \"started\": $START, \"finished\": $END, \"wall_s\": $((END - START))}" > "$OUT/timing.json"
    echo "[ab] mode=$MODE done in $((END - START))s; $(grep -c '"event": "folded"' "$OUT/lineage.jsonl" 2>/dev/null || echo 0) folded"
done
echo "[ab] finished; summarise with: python3 $HERE/../ab_verifier_author_summary.py $AB"
