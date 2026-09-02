#!/bin/bash
# Produce the shippable set: rewrites that cleared every gate and then held up
# when measured again, independently, by the same solver that gated them.
#
# Two stages, because one estimate of difficulty is not a property of a task.
# The loop samples k=4 once and about half of what it accepts sits outside the
# band on a second look — so acceptance is the cheap filter and this is the
# verdict. Both stages must run the same solver: measuring the gate with one and
# the verification with another measures the solvers.
#
#   ship_dataset.sh v19            -> data/shipped-v19/tasks-00000.tar
set -u
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

TAG=${1:?usage: ship_dataset.sh <tag>}
RAW=data/accepted-$TAG
OUT=data/shipped-$TAG
VERDICT=results/solve_accepted_$TAG.jsonl

echo "== collecting what the gates accepted =="
python3 collect_accepted.py \
  --runs "results/synth_${TAG}_p*.jsonl" --tasks "data/synth-$TAG" \
  --out "$RAW" --tar "$RAW/tasks-00000.tar"

n=$(wc -l < "$RAW/accepted_ids.txt")
[ "$n" -eq 0 ] && { echo "nothing accepted"; exit 0; }

echo "== re-measuring all $n at k=5, same solver as the gate =="
python3 solve_eval.py \
  --tar "$RAW/tasks-00000.tar" --ids "$RAW/accepted_ids.txt" \
  --results "$VERDICT" --work "./work-ship-$TAG" \
  --attempts 5 --max-turns 25 --workers 6

echo "== keeping what held =="
python3 collect_accepted.py \
  --runs "results/synth_${TAG}_p*.jsonl" --tasks "data/synth-$TAG" \
  --verified "$VERDICT" \
  --out "$OUT" --tar "$OUT/tasks-00000.tar"

python3 - "$OUT" "$VERDICT" <<'PY'
import json, sys, collections
out, verdict = sys.argv[1], sys.argv[2]
shipped = [l.strip() for l in open(f"{out}/accepted_ids.txt") if l.strip()]
seen = {}
for l in open(verdict):
    r = json.loads(l)
    if r.get("graded"):
        seen[r["task_id"]] = r["pass_at_k"]
band = collections.Counter()
for t in shipped:
    band[round(seen.get(t, -1), 2)] += 1
print(f"\nshipped {len(shipped)} tasks")
for k in sorted(band, reverse=True):
    print(f"  pass@5 {k:.2f}  {band[k]}")
PY
