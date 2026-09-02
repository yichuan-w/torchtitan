#!/bin/bash
# Watch the re-judging run on flow-matic and record its progress locally, so
# the laptop has the trace even if the link to Berkeley drops.
#
# Polls until the validator's process is gone, then prints the final tally.
set -u
LOG=logs/recover_watch.log
mkdir -p logs

while true; do
  line=$(ssh -o ConnectTimeout=20 -o BatchMode=yes flow-matic-andy \
    'cd /work/tianxia/tw-recover && printf "%s done | %s | disk %s | %s\n" \
       "$(wc -l < results/recover/recover_v1.jsonl 2>/dev/null)" \
       "$(pgrep -f docker_validate.py >/dev/null && echo running || echo STOPPED)" \
       "$(df -h / | awk "NR==2{print \$4}")" \
       "$(tail -1 results/recover/recover_v1.log 2>/dev/null | cut -c1-160)"' \
    2>&1)
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$line" >> "$LOG"
  case "$line" in
    *STOPPED*) echo "run finished: $line"; break ;;
  esac
  sleep 120
done
