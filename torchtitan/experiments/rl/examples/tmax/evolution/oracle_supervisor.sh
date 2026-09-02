#!/bin/bash
# Watchdog for oracle_validate_seeds.py.
#
# Twice now the validator has wedged mid-run: a network blip freezes the SDK's
# HTTPS reads (no client-side timeout), all worker threads block forever, and
# the process sits "alive" producing nothing. Python threads can't be killed,
# so the fix is external: run in bounded batches under `timeout`; a wedged
# batch gets SIGKILLed and the next one resumes from the results file (done
# tasks are skipped, in-flight ones simply re-run).
set -u
cd "$(dirname "$0")"
TOTAL=1530
SUP_LOG=logs/oracle_supervisor.log

while true; do
    # Sweep BEFORE every batch, not just after a failed one: build-failed
    # sandboxes leak silently and fill the tier's disk quota, after which every
    # create fails and the run stalls indefinitely (2026-08-12: 63 leaked, ~13h lost).
    python scripts/sweep_orphans.py >> "$SUP_LOG" 2>&1 || true
    timeout -k 30 2400 python scripts/oracle_validate_seeds.py --workers 3 --limit 40 \
        >> logs/oracle_batches.log 2>&1
    rc=$?
    n=$(wc -l < results/oracle_validation.jsonl 2>/dev/null || echo 0)
    echo "[$(date -Is)] batch rc=$rc done_total=$n" >> "$SUP_LOG"
    if [ "$n" -ge "$TOTAL" ]; then
        echo "[$(date -Is)] ALL COMPLETE: $n/$TOTAL" >> "$SUP_LOG"
        break
    fi
    sleep 15
done
