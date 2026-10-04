#!/bin/bash
# Pull finished shards' score matrices (not vectors) from della and brewster to Centralia, until all 65 are there.
# Runs from the MacBook, which is the only machine that reaches all three.
set -uo pipefail
C=/data/yichuan_wang/andy-tb21-top10-pool57-20261004/work/embed_out
D=/scratch/gpfs/TRIDAO/al9080/pool57
B=/data/yichuan_wang/andy-pool57-embed-brewster
ssh -n CentraliaB200 "mkdir -p $C"
while true; do
  have=$(ssh -n CentraliaB200 "ls $C | grep -cE '^shard_[0-9]{3}.npz$'" 2>/dev/null); have=${have:-0}
  echo "[$(date -u +%FT%TZ)] centralia_has=$have"
  [ "$have" -ge 65 ] && break  # 64 pool shards + shard_064 (extra.py tasks)
  for src in "della-ts $D /scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python" "BrewsterH200 $B /data/yichuan_wang/pixelrag-rebuttal-venvs/vllm/bin/python"; do
    set -- $src
    ssh -n "$1" "cd $2 && $3 scores_only.py embed_out scores_out" 2>&1 | sed "s/^/[$1] /"
    missing=$(comm -23 <(ssh -n "$1" "ls $2/scores_out 2>/dev/null | grep -E '^shard_[0-9]{3}.npz$'" | sort) \
                       <(ssh -n CentraliaB200 "ls $C | grep -E '^shard_[0-9]{3}.npz$'" | sort))
    [ -n "$missing" ] && ssh -n "$1" "cd $2/scores_out && tar -cf - $(echo $missing)" | ssh CentraliaB200 "tar -C $C -xf -" \
      && echo "[$1] pulled $(echo $missing | wc -w)"
  done
  sleep 120
done
