#!/bin/bash
# Does FA3 actually run on Blackwell sm_100 (this GB200 host == GB300 arch)?
set -o pipefail
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH
source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm

echo "=== install flash-attn-3 aarch64 (test/cu130) ==="
pip install --no-deps flash-attn-3 --extra-index-url=https://download.pytorch.org/whl/test/cu130 2>&1 | tail -4

SP="${CONDA_PREFIX}/lib/python3.12/site-packages"
PLATFORM_LIB=/usr/local/fbcode/platform010-aarch64/lib
NV="${SP}/nvidia/cu13/lib"
export LD_LIBRARY_PATH="${NV}:${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="${NV}/libcublas.so.13:${NV}/libcublasLt.so.13:${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so"

echo "=== import + tiny FA3 forward on sm_100 ==="
python - <<'PY' 2>&1 | tail -15
import torch
print("device:", torch.cuda.get_device_name(0), "cap:", torch.cuda.get_device_capability(0))
import flash_attn_interface as fa3
print("flash_attn_interface import OK")
q = torch.randn(1, 256, 8, 128, dtype=torch.bfloat16, device="cuda")
k = torch.randn(1, 256, 8, 128, dtype=torch.bfloat16, device="cuda")
v = torch.randn(1, 256, 8, 128, dtype=torch.bfloat16, device="cuda")
try:
    out = fa3.flash_attn_func(q, k, v, causal=True)
    o = out[0] if isinstance(out, tuple) else out
    torch.cuda.synchronize()
    print("FA3 forward OK, out:", tuple(o.shape))
    print("FA3_WORKS_ON_BLACKWELL")
except Exception as e:
    print("FA3 forward FAILED:", repr(e)[:200])
    print("FA3_BROKEN_ON_BLACKWELL")
PY
