#!/bin/bash
# Diagnose the vllm _C_stable_libtorch 'undefined symbol: cublasGetMathMode' on aarch64.
set -o pipefail
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH
source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm
SP="${CONDA_PREFIX}/lib/python3.12/site-packages"

echo "=== which libcublas does vllm's _C_stable_libtorch NEED? ==="
ldd "${SP}/vllm/_C_stable_libtorch.abi3.so" 2>/dev/null | grep -iE "cublas|not found" || true
echo ""
echo "=== all libcublas*.so* in the env, and do they export cublasGetMathMode? ==="
find "${CONDA_PREFIX}" "${SP}" -name "libcublas*.so*" -not -type d 2>/dev/null | while read -r f; do
    has=$(nm -D "$f" 2>/dev/null | grep -c "cublasGetMathMode")
    echo "  has_sym=${has}  ${f}"
done
echo ""
echo "=== nvidia cublas wheel versions installed ==="
pip list 2>/dev/null | grep -iE "nvidia-cublas"
echo ""
echo "=== try the deep import (same chain as MAST) ==="
PLATFORM_LIB=/usr/local/fbcode/platform010-aarch64/lib
CUBLAS_LT=$(find "${CONDA_PREFIX}/lib" -name "libcublasLt.so*" -not -type d 2>/dev/null | head -1)
export LD_PRELOAD="${CUBLAS_LT:+${CUBLAS_LT}:}${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so"
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
echo "CUBLAS_LT preload = ${CUBLAS_LT}"
python -c "import vllm._C_stable_libtorch; print('IMPORT OK')" 2>&1 | tail -3
