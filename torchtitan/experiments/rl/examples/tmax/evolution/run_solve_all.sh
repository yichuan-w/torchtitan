#!/bin/bash
# Ask GPT-5.6 to do all 861 tasks whose reference solution passes, five times
# each — RST's own sandbox pass@5 convention, so the numbers land next to theirs.
#
# Validation only ever established that a task is well-formed. This is the first
# thing that asks whether it is worth training on: a task solved on every attempt
# teaches nothing, and one solved on none teaches nothing either, and until a
# solver runs there is no way to tell which of the 861 are which.
#
# Waits out whatever solve run is already going rather than contending with it
# for the same disk, then resumes into the same results file — the ten already
# judged are skipped, not repeated.
set -u
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

while pgrep -f "python3 solve_eval.py" > /dev/null; do sleep 30; done
echo "$(date -u +%FT%TZ) previous solve run finished, starting the full corpus"

# Disk is the constraint, not cores: the box has 224 of them and 40G free on a
# filesystem other people share. Six concurrent builds sits inside the script's
# own 25G prune threshold with room for the largest image.
exec python3 solve_eval.py \
  --tar chunk000.tar tw_retry_small.tar big/*.tar \
  --ids results/solve_all861.ids \
  --results results/solve_all861.jsonl \
  --work ./work-solve-all \
  --attempts 5 \
  --max-turns 25 \
  --workers 6 \
  --build-attempts 3
