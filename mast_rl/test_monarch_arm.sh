#!/bin/bash
# Verify the aarch64 conda python can import monarch.tools for SUBMIT, by giving it
# platform010-aarch64's newer glibc (GLIBC_2.38) the same way run.sh does on MAST.
set -o pipefail
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH

source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm
PY="${CONDA_PREFIX}/bin/python3.12"
PLATFORM_LIB=/usr/local/fbcode/platform010-aarch64/lib

command -v patchelf >/dev/null 2>&1 || pip install -q patchelf

echo "interp before: $(patchelf --print-interpreter "$PY")"
patchelf --set-interpreter "${PLATFORM_LIB}/ld.so" "$PY"
echo "interp after:  $(patchelf --print-interpreter "$PY")"

export TRITON_LIBCUDA_PATH="${PLATFORM_LIB}/libcuda.so"
CUBLAS_LT=$(find "${CONDA_PREFIX}/lib" -name "libcublasLt.so*" -not -type d 2>/dev/null | head -1)
export LD_PRELOAD="${CUBLAS_LT:+${CUBLAS_LT}:}${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so"
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"

"$PY" - <<'PYEOF'
import torch; print("torch", torch.__version__)
import monarch; print("monarch OK")
from monarch.tools import commands; print("monarch.tools.commands OK")
print("SUBMIT_IMPORTS_OK")
PYEOF
echo "test rc=$?"
