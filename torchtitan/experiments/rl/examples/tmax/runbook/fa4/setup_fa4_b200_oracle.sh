#!/bin/bash
# Build the B200 environment that can act as an oracle for the real kernel.
#
# The H100 cannot: on Hopper the backward dispatches to flash_bwd.py, a
# different kernel from the flash_bwd_sm100.py that stalls on B300. A B200 is
# compute capability 10.0 and runs the same file, so "completes on B200, stalls
# on B300" would be an architecture result about the real kernel, with no test
# program of ours in between.
#
# Versions are pinned to whatever the B300 machine has, because a version
# difference would reopen exactly the question the comparison is meant to close.
set -euo pipefail

ROOT=/work/yichuan_wang/fa4-b200-oracle
export UV_CACHE_DIR=$ROOT/.uv-cache
export UV_PYTHON_INSTALL_DIR=$ROOT/.uv-python
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
cd "$ROOT"

uv venv --clear --python 3.12 venv
uv pip install --python venv/bin/python --quiet \
  --pre torch==2.14.0.dev20260806+cu130 \
  --index-url https://download.pytorch.org/whl/nightly/cu130
# flash-attn-4 4.0.0b26 pins nvidia-cutlass-dsl to 4.6.0.dev0, but the B300
# machine runs it against 4.6.0. Resolving the pin honestly would install a
# different DSL than the one the stall was observed with, and a version
# difference is exactly the objection this comparison exists to remove — so
# install the DSL first and the wheel without its dependency resolution, which
# is the combination the B300 machine actually has.
uv pip install --python venv/bin/python --quiet nvidia-cutlass-dsl==4.6.0
uv pip install --python venv/bin/python --quiet --no-deps "flash-attn-4==4.0.0b26"
uv pip install --python venv/bin/python --quiet einops pytest

venv/bin/python - <<'PY'
import torch
from importlib.metadata import version
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("flash-attn-4", version("flash-attn-4"))
print("cutlass-dsl", version("nvidia-cutlass-dsl"))
print("gpu", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))
PY
