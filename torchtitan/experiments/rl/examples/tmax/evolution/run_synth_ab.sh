#!/bin/bash
# A/B the cross-file consistency pass, in one run, on disjoint seeds.
#
# Oracle failures rose from 32% to 39% after that pass was told to check whether
# each verifier check has anything in the solution behind it, and 27 of 38 of
# them were a solution that exited 0 against a verifier that scored it zero. The
# pass rewrites whole files, so it can introduce a disagreement as easily as
# remove one — an argument either way is worth less than a measurement.
#
# Seeds are dealt round-robin across the 24 slices, so the first twelve and the
# last twelve see the same mix and the halves are comparable.
set -u
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

for p in $(pgrep -f "synth_loop[.]py"); do kill "$p" 2>/dev/null; done
sleep 4
docker ps -aq --filter name=syn- | xargs -r docker rm -f >/dev/null 2>&1

launch() {   # $1 = first slice, $2 = last slice, $3 = tag, $4 = consistency flag
  for i in $(seq -f "%02g" "$1" "$2"); do
    SYNTH_CONSISTENCY="$4" nohup setsid python3 synth_loop.py \
      --seeds "results/synth_p${i}.ids" \
      --tar chunk000.tar \
      --out "data/synth-$3" \
      --rounds 1 --per-round 100 --attempts 4 \
      --results "results/synth_$3_p${i}.jsonl" \
      > "results/synth_$3_p${i}.launch.log" 2>&1 < /dev/null &
  done
}

launch 0 11 v8on 1
launch 12 23 v8off 0

sleep 12
echo "workers: $(pgrep -c -f "synth_loop[.]py")"
df -h / | tail -1
