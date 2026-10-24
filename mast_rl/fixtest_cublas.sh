#!/bin/bash
set -o pipefail
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH
source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm
SP="${CONDA_PREFIX}/lib/python3.12/site-packages"
PLATFORM_LIB=/usr/local/fbcode/platform010-aarch64/lib

# Use torch's own bundled cublas (the nvidia cu13 wheel) for BOTH libcublas and libcublasLt.
NV="${SP}/nvidia/cu13/lib"
CUBLAS=$(ls "${NV}/libcublas.so.13" 2>/dev/null | head -1)
CUBLAS_LT=$(ls "${NV}/libcublasLt.so.13" 2>/dev/null | head -1)
export LD_LIBRARY_PATH="${NV}:${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="${CUBLAS}:${CUBLAS_LT}:${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so"
echo "preload: ${LD_PRELOAD}"
echo ""
echo "=== test 1: bare _C_stable_libtorch (no torch first) ==="
python -c "import vllm._C_stable_libtorch; print('  _C_stable_libtorch OK')" 2>&1 | tail -2
echo "=== test 2: full MAST import chain ==="
python -c "import torchtitan.experiments.rl; print('  torchtitan.experiments.rl OK')" 2>&1 | tail -4
echo "=== test 3: the controller entrypoint imports ==="
python -c "from torchtitan.experiments.rl.train import main; print('  rl.train.main OK')" 2>&1 | tail -4
