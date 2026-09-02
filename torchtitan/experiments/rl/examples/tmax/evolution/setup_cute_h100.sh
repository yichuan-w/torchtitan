#!/bin/bash
# Build the environment for the cross-architecture control.
#
# The two-warp producer/consumer copy test times out on B300. That is only
# evidence about SM103 if the same test completes on another architecture —
# otherwise the likeliest explanation is a phase-parity mistake in the test
# itself, which produces exactly the same symptom (completes iteration 0, hangs
# on the first buffer reuse). Hopper has the bulk tensor copies the test needs,
# so an H100 settles it.
#
# Pinned to the same cutlass-dsl version as the B300 machine so the API the test
# was written against is the API it runs against.
set -euo pipefail

# Both caches must live on /work: the root filesystem has ~20G free and the
# torch + CUDA wheels alone exceed that, which is what "No space left on device"
# was about — not the venv, the download cache.
export UV_CACHE_DIR=/work/tianxia/.uv-cache
export UV_PYTHON_INSTALL_DIR=/work/tianxia/.uv-python
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

cd /work/tianxia
rm -rf cute-h100
uv venv --python 3.12 cute-h100
uv pip install --python cute-h100/bin/python --quiet \
  torch --index-url https://download.pytorch.org/whl/cu128
uv pip install --python cute-h100/bin/python --quiet nvidia-cutlass-dsl==4.6.0

cute-h100/bin/python - <<'PY'
import torch
import cutlass
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("cutlass dsl import ok")
print("gpu", torch.cuda.get_device_name(0),
      torch.cuda.get_device_capability(0))
PY
