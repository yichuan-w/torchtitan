#!/bin/bash
set -euo pipefail

# Build the aarch64 conda env for the Qwen3-32B SWE-R2E RL job on MAST **GB300**
# (Grace ARM64, Blackwell). The x86 build_conda.sh / rlmast pack cannot exec on a
# GB300 host -- its bin/python is an x86 ELF. This builds the ARM analog.
#
# MUST run on an aarch64 build host (a GB200/GB300 DevGPU or ANY plain aarch64
# devvm -- no GPU needed just to build/pack/submit). On x86 you hit
# `qemu-aarch64: Could not open '/lib/ld-linux-aarch64.so.1'`; conda-pack cannot
# cross-build, so there is no x86 shortcut.
#
# Usage (on the aarch64 host):
#   conda create -n rlmast_arm python=3.12 -y && conda activate rlmast_arm
#   TORCHTITAN_SRC=/path/to/this/checkout bash mast_rl/build_conda_aarch64.sh
#
# Differences from the x86 build_conda.sh, all GB300/Blackwell-driven:
#   * FlashAttention 3 is DROPPED -- FA3 is tightly coupled to H100 and is not
#     supported on Blackwell GB200/GB300. vLLM falls back to FlashInfer (its
#     Blackwell default); the trainer uses the model's varlen/FA2 path. (If varlen
#     fails on Blackwell, switch the recipe's attn_backend or use the CUTLASS
#     Blackwell FMHA from fbgemm_gpu.)
#   * faiss / fastapi / the retrieval-server deps are DROPPED -- the SWE-R2E recipe
#     has no dense retriever (that is Search-R1 only).
#   * the Monarch wheel picked from the (multi-arch) fbpkg is the **aarch64** one.
#   * daytona + aiohttp are installed so the Claude-Code/Daytona harness packs in.

if [[ "$(uname -m)" != "aarch64" ]]; then
    echo "ERROR: GB300 is aarch64; run this on an aarch64 build host. uname -m=$(uname -m)." >&2
    echo "       conda-pack cannot cross-build an ARM env from x86." >&2
    exit 1
fi
if [[ "${CONDA_DEFAULT_ENV:-}" != "rlmast_arm" ]]; then
    echo "ERROR: activate the 'rlmast_arm' conda env first: conda activate rlmast_arm" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
TORCHTITAN_SRC="${TORCHTITAN_SRC:-$( cd "${SCRIPT_DIR}/.." && pwd )}"

echo "=== Step 1: PyTorch nightly + vLLM + torchcomms (cu130, aarch64 wheels) ==="
# On an aarch64 host pip auto-selects the manylinux_2_28_aarch64 cu130 wheels.
# uv's resolver handles the cu130 nightly skew (torch vs vllm lag) better than pip.
command -v uv >/dev/null 2>&1 || pip install uv
uv pip install torch vllm torchcomms --pre \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu130 \
    --index-strategy unsafe-best-match

echo "=== Step 2: TorchStore + helpers ==="
pip install --no-deps "git+https://github.com/meta-pytorch/torchstore.git@main"
pip install pygtrie portpicker

# NOTE: no Flash-Attention 3 on Blackwell (see header). vLLM uses FlashInfer.

echo "=== Step 3: batch-invariant ops ==="
pip install --no-deps "git+https://github.com/thinking-machines-lab/batch_invariant_ops.git@main"

echo "=== Step 4: CUDA 13 toolkit (FlashInfer JIT on MAST) ==="
conda install -y -c nvidia cuda-toolkit=13.0.2

echo "=== Step 5: rdma-core==60.0 (match Monarch stablev60; conda-forge aarch64) ==="
conda install -y -c conda-forge "rdma-core==60.0"

echo "=== Step 5c: aarch64 host compiler (Triton/torch.compile need it on MAST) ==="
# The minimal aarch64 MAST image has no /usr/bin/gcc; ship the conda compiler so
# vllm's torch.compile/Triton (e.g. the sampler) can build. Mirrors x86's
# x86_64-conda-linux-gnu-gcc. run.sh symlinks aarch64-conda-linux-gnu-gcc into CC.
conda install -y -c conda-forge gcc_linux-aarch64 gxx_linux-aarch64

echo "=== Step 6: renderers (RL chat/tool renderer) ==="
pip install "renderers==0.1.8.dev42"

echo "=== Step 7: SWE-R2E harness runtime deps (Daytona sandbox client) ==="
# No faiss / retrieval-server deps -- SWE-R2E has no retriever. daytona + aiohttp
# drive the cloud sandbox + the on-box Anthropic adapter bridge.
pip install daytona aiohttp tqdm

echo "=== Step 8: TorchTitan (non-editable, from the OSS checkout) ==="
echo "Installing torchtitan from ${TORCHTITAN_SRC}"
( cd "${TORCHTITAN_SRC}" && pip install . )

echo "=== Step 8b: re-pin the torch (+ torchvision) nightly vLLM was built against ==="
# The newest torch nightly is usually days ahead of the vLLM nightly, and vLLM's
# compiled ops then fail to load (undefined NCCL symbols at `import torch`).
TORCH_PIN=$(python -c "import importlib.metadata as m; print(next(r.split('==')[1].split(';')[0].strip() for r in m.requires('vllm') if r.startswith('torch==')))")
TORCH_DATE=$(echo "${TORCH_PIN}" | sed -E 's/.*dev([0-9]{8}).*/\1/')
TORCH_MINOR=$(echo "${TORCH_PIN}" | cut -d. -f2)
# torchvision 0.(N+15) pairs with torch 2.N; take the same nightly date.
uv pip install --no-deps "torch==${TORCH_PIN}" \
    "torchvision==0.$((TORCH_MINOR + 15)).0.dev${TORCH_DATE}+cu130" \
    --pre --index-url https://download.pytorch.org/whl/nightly/cu130
printf 'torch==%s\nvllm==%s\n' "${TORCH_PIN}" "$(python -c 'import importlib.metadata as m; print(m.version("vllm"))')" \
    > /tmp/rlmast_arm_constraints.txt

echo "=== Step 8c: GDN, FA4, terminus agent, DAPO-Math grader ==="
# fla: Qwen3.5 Gated DeltaNet kernels. flash-attn-4: the trainer's varlen
# attention on SM100 (FA3 is Hopper-only). harbor: the terminus agent.
# torchvision: vLLM's kernel warmup imports it. Versions match the B300 lock
# (examples/tmax/runbook/requirements.lock.txt).
uv pip install "fla-core==0.5.2" "flash-linear-attention==0.5.2" "flash-attn-4==4.0.0b26" \
    "tilelang==0.1.13" "harbor==0.22.0" "math-verify[antlr4_13_2]" datasets \
    -c /tmp/rlmast_arm_constraints.txt --pre \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu130 --index-strategy unsafe-best-match
# The workspace fbpkg build picks up the miniforge base env's setuptools in place
# of a different pip-installed one, leaving a mixed copy that fails on
# `import distutils.core`. Keep the two identical.
uv pip install "setuptools==$("${CONDA_EXE%/bin/conda}/bin/python" -c 'import setuptools; print(setuptools.__version__)')"
# launcher.py imports monarch on this host, which needs platform010's glibc.
conda install -y -c conda-forge patchelf
patchelf --set-interpreter /usr/local/fbcode/platform010-aarch64/lib/ld.so "${CONDA_PREFIX}/bin/python3.12"

echo "=== Step 9: Meta-internal Monarch (aarch64 wheel) + torchx ==="
# monarch_nightly_torch is MULTI-ARCH: the fbpkg ships both
#   torchmonarch-0.0.0-py3.12-none-linux_aarch64.whl
#   torchmonarch-0.0.0-py3.12-none-linux_x86_64.whl
# Pick the aarch64 one (the x86 glob in build_conda.sh would grab both / the wrong one).
rm -rf ~/monarch_wheel && mkdir ~/monarch_wheel
fbpkg fetch monarch_nightly_torch:latest_contbuild -d ~/monarch_wheel
MONARCH_WHL=$(ls ~/monarch_wheel/torchmonarch-*-py3.12-none-linux_aarch64.whl | head -1)
if [[ -z "${MONARCH_WHL:-}" || ! -f "${MONARCH_WHL}" ]]; then
    echo "ERROR: no aarch64 torchmonarch wheel in the fbpkg. Contents:" >&2
    ls -la ~/monarch_wheel >&2
    exit 1
fi
echo "Installing aarch64 Monarch wheel: ${MONARCH_WHL}"
pip install --no-deps --force-reinstall "${MONARCH_WHL}"

rm -rf ~/torchx_wheel && mkdir ~/torchx_wheel
fbpkg fetch torchx_wheel:stable -d ~/torchx_wheel
pip install ~/torchx_wheel/torchx-*-py3.12-none-any.whl  # pure-python, arch-agnostic
pip install decorator flask  # FB torchx + monarch.distributed_telemetry runtime deps

echo ""
echo "Done. Submit from this aarch64 host, e.g. on Nebius GB300:"
echo "  bash mast_rl/submit_math_nebius_smoke.sh   # 1 host, DAPO-Math"
echo "  bash mast_rl/submit_tmax_9b_nebius.sh      # 5 hosts, 9B TMax (Daytona key in ~/.daytona_key)"
