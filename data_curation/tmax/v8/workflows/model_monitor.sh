#!/usr/bin/env bash
# Continuous judge-model monitor for a V8 workflow run.
# Usage: model_monitor.sh <workflow_transcript_dir> <rows_dir> <expected_agents> <expected_rows> <allowed_model_id>
# Every 20s: tallies the JSONL top-level "model" field across ALL agent transcripts (every wave, every turn).
# Exits 2 immediately (ALERT) if any turn used a model other than the allowed id.
# Exits 0 when all agents have spawned and all rows are written, or after the timeout.
WF="$1"; ROWS="$2"; EXP_AGENTS="$3"; EXP_ROWS="$4"; OK="$5"
TIMEOUT_S=${TIMEOUT_S:-6000}
t=0
while [ "$t" -lt "$TIMEOUT_S" ]; do
  nA=$(ls "$WF"/agent-*.jsonl 2>/dev/null | wc -l)
  nR=$(ls "$ROWS" 2>/dev/null | wc -l)
  # top-level model field only (metadata); never reads task content
  bad=$(grep -ohE '"model":"claude-[a-z0-9-]+"' "$WF"/agent-*.jsonl 2>/dev/null | grep -v "\"$OK\"" | sort | uniq -c | sort -rn)
  if [ -n "$bad" ]; then
    echo "ALERT: non-$OK turns detected at t=${t}s (agents=$nA rows=$nR):"
    echo "$bad"
    for f in "$WF"/agent-*.jsonl; do
      if grep -qE '"model":"claude-[a-z0-9-]+"' "$f" && grep -oE '"model":"claude-[a-z0-9-]+"' "$f" | grep -qv "\"$OK\""; then
        echo "  offending agent: $(basename "$f") batch=$(grep -oE 'batch_[0-9]{3}' "$f" | head -1)"
      fi
    done
    exit 2
  fi
  echo "t=${t}s agents=$nA/$EXP_AGENTS rows=$nR/$EXP_ROWS all-$OK=yes"
  if [ "$nA" -ge "$EXP_AGENTS" ] && [ "$nR" -ge "$EXP_ROWS" ]; then echo "DONE: all agents spawned and all rows written, model clean"; exit 0; fi
  sleep 20; t=$((t+20))
done
echo "TIMEOUT after ${TIMEOUT_S}s (agents=$nA rows=$nR), model clean so far"; exit 0
