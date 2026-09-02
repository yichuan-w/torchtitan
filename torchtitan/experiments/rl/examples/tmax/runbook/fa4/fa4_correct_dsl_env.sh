#!/bin/bash
# Run the acceptance test against the DSL the release actually pins.
#
# flash-attn-4 4.0.0b26 requires nvidia-cutlass-dsl==4.6.0.dev0. That is a
# prerelease, and uv excludes prereleases by default, so resolving it reports
# "no version of nvidia-cutlass-dsl==4.6.0.dev0" and the obvious next step is to
# install 4.6.0 instead. That is what the working environment has — plus a hand
# patch to flash_fwd_sm100.py, added because the release does not compile
# against 4.6.0 at all.
#
# So the stall every round has been anchored on was measured on a combination
# upstream never shipped. This builds the combination it did ship.
set -euo pipefail

ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
export UV_CACHE_DIR=$ROOT/.uv-cache
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$ROOT" "$UV_CACHE_DIR"
cd "$ROOT"

uv venv --clear --python 3.12 venv
uv pip install --python venv/bin/python --quiet \
  --pre torch==2.14.0.dev20260806+cu130 \
  --index-url https://download.pytorch.org/whl/nightly/cu130
uv pip install --python venv/bin/python --quiet --prerelease allow \
  "flash-attn-4==4.0.0b26" einops

venv/bin/python - <<'PY'
from importlib.metadata import version
import torch
print("torch", torch.__version__)
print("flash-attn-4", version("flash-attn-4"))
print("cutlass-dsl", version("nvidia-cutlass-dsl"))
PY

echo "=== acceptance test, GPU 7 ==="
CUDA_VISIBLE_DEVICES=7 venv/bin/python -u "$SRC/fa4_bwd_acceptance.py" --timeout 180 \
  2>&1 | tee "$ROOT/acceptance_correct_dsl.log" | tail -20
