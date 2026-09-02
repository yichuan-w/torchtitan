#!/bin/bash
# Re-run the tasks the main pass could not judge, once it finishes.
#
# Three buckets came back without a verdict, and only one of them is about the
# tasks:
#
#   error (21)        the harness raised. Every one was a UnicodeDecodeError out
#                     of subprocess.run(text=True) — a solver ran something that
#                     emitted a binary and strict UTF-8 decoding took the task
#                     down with it. Fixed; these deserve another pass.
#   build_failed (13) the environment did not build. Retried here in case it was
#                     the network again, and left alone if it was not.
#   ungraded (33)     every attempt was refused by the provider's content filter.
#                     Re-running against the same model will refuse them again;
#                     they are listed so the count is on the record, and they are
#                     the argument for evaluating this corpus on a second model.
#
# The results file is the same one, and resume skips whatever already has a
# verdict, so this adds to the run rather than replacing it.
set -u
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

while pgrep -f "python3 solve_eval.py" > /dev/null; do sleep 60; done
echo "$(date -u +%FT%TZ) main pass finished, re-running what it could not judge"

python3 - <<'PY'
import json
keep = {"error", "build_failed", "ungraded"}
ids = [json.loads(l)["task_id"] for l in open("results/solve_all861.jsonl")
       if l.strip() and json.loads(l).get("status") in keep]
open("results/solve_leftovers.ids", "w").write("\n".join(sorted(set(ids))) + "\n")
print(f"{len(set(ids))} tasks to re-run")
PY

exec python3 solve_eval.py \
  --tar chunk000.tar tw_retry_small.tar big/*.tar \
  --ids results/solve_leftovers.ids \
  --results results/solve_all861.jsonl \
  --work ./work-solve-left \
  --attempts 5 \
  --max-turns 25 \
  --workers 4 \
  --build-attempts 3
