#!/bin/bash
# Remote driver: build the aarch64 rlmast_arm env on the GB200 build host.
# Runs build_conda_aarch64.sh under the fwdproxy with a clean PYTHONPATH.
set -uo pipefail

export https_proxy=http://fwdproxy:8080
export http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
# A stray x86 PYTHONPATH from the login profile would contaminate the ARM env.
unset PYTHONPATH

REPO="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
export TORCHTITAN_SRC="${REPO}"

source "${HOME}/miniforge3/etc/profile.d/conda.sh"
if ! conda env list | grep -qE '^rlmast_arm[[:space:]]'; then
    echo "=== creating rlmast_arm (python 3.12) ==="
    conda create -n rlmast_arm python=3.12 -y
fi
conda activate rlmast_arm
echo "env python: $(python -c 'import platform;print(platform.machine(), platform.python_version())')"

cd "${REPO}"
echo "=== running build_conda_aarch64.sh ==="
bash mast_rl/build_conda_aarch64.sh
echo "BUILD_EXIT=$?"
