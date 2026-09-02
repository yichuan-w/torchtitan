#!/bin/bash
# Re-judge the tasks whose recorded verdict came from the runner rather than
# from the task, on flow-matic where the corpus and a real Docker both live.
#
# Three populations, one run, because they need the same fixed runner:
#   113  unjudgeable — the build never completed, so no reward was ever read
#    96  judged fail while their declared ENTRYPOINT was suppressed
#    92  judged pass under the same suppression — the control, which has to
#         stay passing for the other 96 to mean anything
#
# Detached from the launching shell on purpose: the link to Berkeley drops, and
# a run that dies with its ssh session loses hours of builds. Progress is in the
# log, verdicts append to the jsonl one task at a time, and re-running the same
# command resumes from what is already there.
set -u
cd /work/tianxia/tw-recover

IDS=results/recover/recover_v1.ids
OUT=results/recover/recover_v1.jsonl

cat results/recover/entrypoint-fail.ids results/recover/entrypoint-pass.ids \
    unknown_113_ids.txt | sort -u > "$IDS"
echo "$(date -u +%FT%TZ) launching over $(wc -l < "$IDS") tasks"

exec python3 docker_validate.py \
  --tar chunk000.tar tw_retry_small.tar big/*.tar \
  --ids "$IDS" \
  --results "$OUT" \
  --work ./work-recover \
  --workers 3 \
  --repair \
  --build-attempts 3
