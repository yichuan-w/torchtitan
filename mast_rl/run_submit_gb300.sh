#!/bin/bash
# Remote driver: submit the GB300 SWE-R2E job from the aarch64 build host.
# Sets up platform010-aarch64 glibc/libs so launcher.py can import monarch.tools
# (the conda python's interpreter is already patched to platform010-aarch64/ld.so).
set -o pipefail

export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH

source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm

# Newer glibc + driver libs for the monarch native ext at submit time. Also preload
# libcublas + libcublasLt from torch's nvidia cu13 wheel so vllm's _C_stable_libtorch
# resolves cublasGetMathMode regardless of import order (same as run.sh on MAST).
PLATFORM_LIB=/usr/local/fbcode/platform010-aarch64/lib
export TRITON_LIBCUDA_PATH="${PLATFORM_LIB}/libcuda.so"
NV_CUBLAS_DIR=$(dirname "$(find "${CONDA_PREFIX}"/lib/python*/site-packages/nvidia -name 'libcublas.so.*' -not -type d 2>/dev/null | head -1)")
CUBLAS=$(ls "${NV_CUBLAS_DIR}"/libcublas.so.* 2>/dev/null | head -1)
CUBLAS_LT=$(ls "${NV_CUBLAS_DIR}"/libcublasLt.so.* 2>/dev/null | head -1)
export LD_PRELOAD="${CUBLAS:+${CUBLAS}:}${CUBLAS_LT:+${CUBLAS_LT}:}${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so"
export LD_LIBRARY_PATH="${NV_CUBLAS_DIR}:${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"

# torchx scratch dir (submit dies with FileExistsError if missing).
mkdir -p /dev/shm/torchx_out && ln -sfn /dev/shm/torchx_out "${HOME}/.torchx/out" 2>/dev/null || true
mkdir -p "${HOME}/.torchx" && ln -sfn /dev/shm/torchx_out "${HOME}/.torchx/out" 2>/dev/null || true

export DAYTONA_API_KEY="$(cat "${HOME}/.daytona_key")"

cd "$( dirname "${BASH_SOURCE[0]}" )/.."
# Which submit wrapper to run (default: 3-host smoke; set SWE_SUBMIT to the 8x8 run).
SWE_SUBMIT="${SWE_SUBMIT:-submit_swe_gb300.sh}"
echo "=== submitting GB300 SWE-R2E via mast_rl/${SWE_SUBMIT} ==="
bash "mast_rl/${SWE_SUBMIT}" "$@"
echo "SUBMIT_EXIT=$?"
