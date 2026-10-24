#!/bin/bash
# Confirm torch FlexAttention (Triton, torch.compile) runs on Blackwell sm_100.
set -o pipefail
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH
source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm
SP="${CONDA_PREFIX}/lib/python3.12/site-packages"
PLATFORM_LIB=/usr/local/fbcode/platform010-aarch64/lib
NV="${SP}/nvidia/cu13/lib"
export LD_LIBRARY_PATH="${NV}:${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="${NV}/libcublas.so.13:${NV}/libcublasLt.so.13:${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so"
# a writable, node-local triton/inductor cache
export TRITON_CACHE_DIR=/tmp/triton_cache TORCHINDUCTOR_CACHE_DIR=/tmp/inductor_cache
python - <<'PY' 2>&1 | tail -15
import torch
from torch.nn.attention.flex_attention import flex_attention, create_block_mask
print("device:", torch.cuda.get_device_name(0), "cap:", torch.cuda.get_device_capability(0))
B,H,L,D = 1,8,512,128
q = torch.randn(B,H,L,D, dtype=torch.bfloat16, device="cuda")
k = torch.randn(B,H,L,D, dtype=torch.bfloat16, device="cuda")
v = torch.randn(B,H,L,D, dtype=torch.bfloat16, device="cuda")
def causal(b,h,qi,ki): return qi >= ki
bm = create_block_mask(causal, B, H, L, L, device="cuda")
cfa = torch.compile(flex_attention)
out = cfa(q, k, v, block_mask=bm)
torch.cuda.synchronize()
print("flex out:", tuple(out.shape))
print("FLEX_WORKS_ON_BLACKWELL")
PY
