#!/bin/bash
# Collect every accepted task produced so far and re-measure it independently.
#
# The loop's difficulty gate samples k=4 once and gets it wrong about half the
# time — of fourteen accepted tasks re-measured at k=5, seven stayed in the
# usable band and seven moved out. So acceptance is a filter and this is the
# verdict: same harness and protocol as the seed corpus, no knowledge of what the
# gate decided.
set -u
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

OUT=${OUT:-data/accepted-all}
TAG=${TAG:-all}

args=()
for d in baseline-v*/; do
  base=$(basename "$d")
  for tasks in "$d"tasks "$d"synth-*; do
    [ -d "$tasks" ] || continue
    ver=$(basename "$tasks")
    if [ "$ver" = tasks ]; then
      glob="$d${base#baseline-}"
      args+=(--runs "${d}synth_${base#baseline-}_p*.jsonl" --tasks "$tasks")
    else
      args+=(--runs "${d}synth_${ver#synth-}_p*.jsonl" --tasks "$tasks")
    fi
  done
done

echo "collecting from ${#args[@]} path arguments"
python3 collect_accepted.py "${args[@]}" \
  --out "$OUT" --tar "$OUT/tasks-00000.tar"

n=$(wc -l < "$OUT/accepted_ids.txt")
echo "$n accepted tasks collected; re-measuring at k=5"
[ "$n" -eq 0 ] && exit 0

exec python3 solve_eval.py \
  --tar "$OUT/tasks-00000.tar" \
  --ids "$OUT/accepted_ids.txt" \
  --results "results/solve_accepted_${TAG}.jsonl" \
  --work "./work-acc-${TAG}" \
  --attempts 5 --max-turns 25 --workers 6
