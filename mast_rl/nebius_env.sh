# Source on the aarch64 submit host (a GB300 devgpu) before a Nebius GB300 submit.
#
# Activates rlmast_arm (build_conda_aarch64.sh) and sets up what launcher.py needs
# to import monarch + torch on the devgpu: the env's python interpreter must be
# patched to platform010-aarch64's ld.so (monarch needs glibc 2.38; once per env):
#   patchelf --set-interpreter /usr/local/fbcode/platform010-aarch64/lib/ld.so \
#       "$CONDA_PREFIX/bin/python3.12"
# run.sh re-applies the same patch on the MAST host.

source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm
# A stray x86 PYTHONPATH from the login profile would contaminate the ARM env.
unset PYTHONPATH

export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy

# torch's bundled cu13 libs are not on the system linker path; cublas must be
# preloaded so vllm's _C_stable_libtorch resolves cublasGetMathMode.
_NV_DIR="${CONDA_PREFIX}/lib/python3.12/site-packages/nvidia"
_NV_LIBS=""
for _d in "${_NV_DIR}"/*/lib; do
    [[ -d "${_d}" ]] && _NV_LIBS="${_NV_LIBS}${_NV_LIBS:+:}${_d}"
done
export LD_LIBRARY_PATH="${_NV_LIBS}:${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="${_NV_DIR}/cu13/lib/libcublas.so.13:${_NV_DIR}/cu13/lib/libcublasLt.so.13"
export TRITON_LIBCUDA_PATH=/usr/lib64
unset _NV_DIR _NV_LIBS _d

# torchx scratch dir (submit dies with FileExistsError if missing).
mkdir -p /dev/shm/torchx_out "${HOME}/.torchx"
ln -sfn /dev/shm/torchx_out "${HOME}/.torchx/out" 2>/dev/null || true

export RLMAST_ENV=rlmast_arm
if [[ -z "${DAYTONA_API_KEY:-}" && -f "${HOME}/.daytona_key" ]]; then
    DAYTONA_API_KEY="$(cat "${HOME}/.daytona_key")"
    export DAYTONA_API_KEY
fi
