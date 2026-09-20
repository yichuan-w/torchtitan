#!/bin/bash
set -euo pipefail
export HF_HOME="${HF_HOME:-/data/yichuan_wang/cache/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-$HF_HOME/datasets}"
export HF_HUB_DISABLE_XET=1
export PYTHONUNBUFFERED=1
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python -u "$HERE/compute_tb21_tfidf.py" \
  --out "$HERE/out" \
  --cache "$HERE/cache" \
  --reuse-cache \
  --top-k 30
