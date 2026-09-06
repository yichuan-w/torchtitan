#!/usr/bin/env bash
# Run daytona_revalidate.py over a list of task packages, a few at a time, and
# keep every verdict. One line per task in <out>/verdicts.jsonl, one log per
# task in <out>/<id>.log, progress in <out>/run.log. Resumable: a task with a
# verdict already recorded is skipped, so a killed run continues.
#
#   revalidate_ids.sh <tasks-dir> <sizes.tsv> <out-dir> [parallel=3]
#
# sizes.tsv: "<task_id>\t<cpu>\t<mem_gb>\t<disk_gb>" per line, the size the task
# is published at (measured_resources.csv's provision_* columns). A task is
# booted at exactly that size, like training would.
#
# Needs: the training venv on PATH as python (or $PY), PYTHONPATH pointing at
# the torchtitan checkout, and the Daytona env sourced.
set -u
TASKS=${1:?tasks dir}; SIZES=${2:?sizes.tsv}; OUT=${3:?out dir}; PAR=${4:-3}
PY=${PY:-python}
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"
VERDICTS="$OUT/verdicts.jsonl"; RUNLOG="$OUT/run.log"
touch "$VERDICTS"

one() {
    local tid=$1 cpu=$2 mem=$3 disk=$4
    if grep -q "\"task_id\": \"$tid\"" "$VERDICTS" 2>/dev/null; then
        echo "[$(date -u +%FT%TZ)] task=$tid status=skip reason=already_has_verdict" >> "$RUNLOG"; return 0
    fi
    local log="$OUT/$tid.log" t0 t1 last
    t0=$(date +%s)
    echo "[$(date -u +%FT%TZ)] task=$tid status=start cpu=$cpu mem_gb=$mem disk_gb=$disk" >> "$RUNLOG"
    "$PY" "$HERE/daytona_revalidate.py" "$TASKS/$tid" --cpu "$cpu" --mem-gb "$mem" --disk-gb "$disk" > "$log" 2>&1
    local rc=$?
    t1=$(date +%s)
    last=$(grep -E '^\{' "$log" | tail -n 1)
    [ -n "$last" ] || last='{"ok": false, "stage": "no_verdict_line"}'
    printf '%s\n' "$last" | "$PY" -c '
import json,sys
v=json.loads(sys.stdin.read()); v.update(task_id=sys.argv[1], cpu=int(sys.argv[2]), mem_gb=int(sys.argv[3]), disk_gb=int(sys.argv[4]), elapsed_s=int(sys.argv[5]), rc=int(sys.argv[6]), t_end=sys.argv[7])
print(json.dumps(v))' "$tid" "$cpu" "$mem" "$disk" "$((t1-t0))" "$rc" "$(date -u +%FT%TZ)" >> "$VERDICTS"
    echo "[$(date -u +%FT%TZ)] task=$tid status=done rc=$rc elapsed=$((t1-t0))s verdict=$(printf '%s' "$last" | cut -c1-160)" >> "$RUNLOG"
}
export -f one; export TASKS OUT VERDICTS RUNLOG PY HERE

echo "[$(date -u +%FT%TZ)] batch start tasks=$TASKS sizes=$SIZES out=$OUT parallel=$PAR pending=$(grep -c . "$SIZES")" >> "$RUNLOG"
grep -v '^\s*$' "$SIZES" | xargs -P "$PAR" -L 1 bash -c 'one "$0" "$1" "$2" "$3"'
echo "[$(date -u +%FT%TZ)] batch done verdicts=$(grep -c . "$VERDICTS")" >> "$RUNLOG"
