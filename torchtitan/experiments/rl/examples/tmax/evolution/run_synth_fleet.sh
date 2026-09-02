#!/bin/bash
# Run the rewrite over the whole seed list, split across N workers.
#
# One worker gets through roughly four tasks an hour — five synthesis calls, a
# build, an oracle run and four rollouts of up to 25 turns each — so the corpus
# needs a fleet rather than a loop. Seeds are dealt round-robin so every worker
# sees the same mix of operators and difficulty; splitting by contiguous block
# would give one worker all the heavy images.
#
# Disk is the limit, not cores: the box has 224 and a shared filesystem. Each
# worker holds one image at a time and drops it when the task ends.
set -u
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

N=${N:-16}
OUT=${OUT:-data/synth-v2}
TAG=${TAG:-v2}

for p in $(pgrep -f "synth_loop[.]py"); do kill "$p" 2>/dev/null; done
sleep 4
docker ps -aq --filter name=syn- | xargs -r docker rm -f >/dev/null 2>&1

for i in $(seq -f "%02g" 0 $((N - 1))); do
  nohup setsid python3 synth_loop.py \
    --seeds "results/synth_p${i}.ids" \
    --tar chunk000.tar \
    --out "$OUT" \
    --rounds 1 --per-round 100 \
    --results "results/synth_${TAG}_p${i}.jsonl" \
    > "results/synth_${TAG}_p${i}.launch.log" 2>&1 < /dev/null &
done

sleep 12
echo "workers: $(pgrep -c -f "synth_loop[.]py")"
df -h / | tail -1
