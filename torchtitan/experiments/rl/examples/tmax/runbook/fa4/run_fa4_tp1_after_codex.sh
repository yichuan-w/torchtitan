#!/bin/bash
# Wait for the independent check to finish, then run training without the
# distributed dimension.
#
# Every single-call reproduction of the training hang completes — sequence 2048,
# the model's 16x128 heads, aot_eager, grouped-query attention. What a single
# call cannot reproduce is the rest of the training context, and the largest
# piece of that is tensor parallelism: under TP=2 the attention module goes
# through `local_map` over DTensors. Trainer TP=1 removes it while leaving
# everything else, so completing would place the hang in the distributed path
# and hanging would rule it out.
#
# It waits rather than sharing GPU 6, because the independent check measures
# hangs by timeout and another job on the same device makes those numbers
# untrustworthy — the point of an independent check is lost if this one
# contaminates it.
set -uo pipefail

ARCH=/scratch/gpfs/TRIDAO/al9080/rl-outputs-archive
LOG=$HOME/terminal-rl/logs/smoke1_fa4_tp1_offset6.log

for _ in $(seq 1 120); do
  pgrep -x codex >/dev/null || break
  sleep 30
done
if pgrep -x codex >/dev/null; then
  echo "independent check still running after an hour; not starting"
  exit 1
fi
echo "$(date -Is) independent check finished; GPUs 6 and 7 are ours"

mkdir -p "$ARCH"
mv -f "$HOME/torchtitan/outputs/rl" "$ARCH/rl.pre_tp1.$(date +%s)" 2>/dev/null

cd "$HOME/terminal-rl/scripts" || exit 1
RL_GPU_OFFSET=6 SWE_TRAIN_TP=1 SWE_GEN_TP=1 SWE_ATTN_FA4=1 \
  bash smoke1_sized.sh > "$LOG" 2>&1
echo "$(date -Is) run finished; steps=$(grep -acE 'Train \| Step' "$LOG")"
