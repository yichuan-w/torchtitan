#!/bin/bash
# Queue one della gpupool task per embedding shard (1 GPU each).
# Usage: scripts/submit_embed.sh <time> <first> <last>   e.g. scripts/submit_embed.sh 40m 0 63
# Shards whose output already exists skip themselves inside embed.py.
set -euo pipefail
TIME=${1:?time}; FIRST=${2:-0}; LAST=${3:-63}
POOL=~/.claude/skills/gpu-pool/tool/pool.py
DIR=/scratch/gpfs/TRIDAO/al9080/pool57
for s in $(seq "$FIRST" "$LAST"); do
  python3 "$POOL" queue add --gpus 1 --type H200 --time "$TIME" --cpus-per-gpu 8 --mem-per-gpu 64 \
    --cwd "$DIR" --note "pool57 embed shard $s (Qwen3-Embedding-8B)" -- \
    "export PATH=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin:\$PATH && python embed.py --shard $s"
done
