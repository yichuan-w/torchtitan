#!/usr/bin/bash
# Per-host entrypoint on the MAST node for multi-host Search-R1 RL.
#
# Every role (controller + trainer + each generator) goes through this script for
# the base host setup (platform010 LD_PRELOAD, libcuda symlinks, NCCL, Manifold
# mount). Only the CONTROLLER role additionally:
#   1. points HuggingFace + the Search-R1 parquet at the mounted bucket (offline),
#   2. starts the dense-retrieval server on the controller's (otherwise idle) GPUs,
#   3. waits for http://127.0.0.1:8000 to be healthy,
#   4. runs the controller command ("$@") with the data_path/dump_folder overrides,
#   5. tears the retriever down on exit.
# The rollouter + env (the retrieval HTTP calls) run in the controller process, so
# the retriever only needs to be reachable from the controller -- not the workers.
set -eExu -o pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

# This role is the controller iff it runs main.py (workers run the monarch
# bootstrap: ``python -X faulthandler -c '<...>'``).
IS_CONTROLLER=0
if [[ "$*" == *"main.py"* ]]; then
    IS_CONTROLLER=1
fi

# ---------------------------------------------------------------------------
# Base MAST env setup (all roles)
# ---------------------------------------------------------------------------
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
    PLATFORM="platform010-aarch64"
else
    PLATFORM="platform010"
fi
PLATFORM_LIB="/usr/local/fbcode/${PLATFORM}/lib"

export PATH="$CONDA_DIR/bin:$PATH"
export CONDA_PREFIX="$CONDA_DIR"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export NCCL_DEBUG=${NCCL_DEBUG:-WARN}
# NCCL transport, selected by NCCL_TRANSPORT (forwarded from the submit env by
# mast.py; default Socket):
#   Socket -- TCP. Correct everywhere; the single-host roles (generator TP=8,
#     controller) never need cross-host RDMA, and the cross-host FSDP trainer runs
#     correctly over Socket (slower than RDMA/RoCE, but the generation-bound
#     rollout dominates wall-clock).
#   IB -- RoCE/InfiniBand via the env's pinned rdma-core (==57.0; build_conda.sh).
#     Speeds up the cross-host FSDP all-gather/reduce-scatter. GDR_LEVEL=5 turns on
#     GPUDirect RDMA; USE_TORCHCOMMS routes collectives through torchcomms.
# Get rdma-core from the conda env, NOT by adding PLATFORM_LIB to LD_LIBRARY_PATH:
# that pulls platform010's libc.so.6, whose glibc version breaks system binaries
# (ln) and aborts this script.
NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"
if [[ "${NCCL_TRANSPORT}" == "IB" ]]; then
    export NCCL_NET=IB
    export NCCL_IB_DISABLE=0
    export GDR_LEVEL=${GDR_LEVEL:-5}
    export USE_TORCHCOMMS=${USE_TORCHCOMMS:-0}
else
    export NCCL_NET=Socket
    export NCCL_IB_DISABLE=1
fi
# TorchStore weight-sync transport. On GB200/GB300 the monarch RDMA buffer alloc
# fails ("failed to request buffer ... channel closed"), so disable it and let
# TorchStore fall back to gloo (TCP, cross-host). Weight sync is not the bottleneck
# (the SWE rollout dominates wall-clock). aarch64-only -> H100 keeps RDMA (default 1).
if [ "$ARCH" = "aarch64" ]; then
    export TORCHSTORE_RDMA_ENABLED="${TORCHSTORE_RDMA_ENABLED:-0}"
fi
# Find a working gcc for torch.compile/Triton + FlashInfer's c++ link step.
GCC_DIR=$(mktemp -d)
# Prefer the arch-matched conda compiler (aarch64-conda-... on GB200/GB300,
# x86_64-conda-... on H100), then a bare conda gcc, then the system gcc. Triton /
# torch.compile (e.g. vllm's sampler) needs a working host compiler; on a minimal
# aarch64 MAST image /usr/bin/gcc is absent, so the conda compiler must be shipped.
for _cc in "${CONDA_DIR}/bin/aarch64-conda-linux-gnu-gcc" \
           "${CONDA_DIR}/bin/x86_64-conda-linux-gnu-gcc" \
           "${CONDA_DIR}/bin/gcc" "/usr/bin/gcc"; do
    if [ -x "$_cc" ]; then ln -sf "$_cc" "$GCC_DIR/gcc"; break; fi
done
for _cxx in "${CONDA_DIR}/bin/aarch64-conda-linux-gnu-g++" \
            "${CONDA_DIR}/bin/x86_64-conda-linux-gnu-g++" \
            "${CONDA_DIR}/bin/g++" "/usr/bin/g++"; do
    if [ -x "$_cxx" ]; then ln -sf "$_cxx" "$GCC_DIR/g++"; break; fi
done
[ -e "$GCC_DIR/gcc" ] && ln -sf "$GCC_DIR/gcc" "$GCC_DIR/cc"
[ -e "$GCC_DIR/g++" ] && ln -sf "$GCC_DIR/g++" "$GCC_DIR/c++"
export CC="${GCC_DIR}/gcc"
export CXX="${GCC_DIR}/g++"
export PATH="${GCC_DIR}:${PATH}"

if [[ -n "${TRITON_LIBCUDA_PATH:-}" ]]; then
    echo "Detected production-ready mast env"
else
    echo "Open-source conda env - will set up env variables for mast"
    NEED_TO_SETUP_ENV_FOR_MAST=1
fi

if [[ -n "${NEED_TO_SETUP_ENV_FOR_MAST:-}" ]]; then
    if command -v patchelf &> /dev/null; then
        patchelf --set-interpreter ${PLATFORM_LIB}/ld.so $CONDA_DIR/bin/python
    fi
    export TRITON_LIBCUDA_PATH=${PLATFORM_LIB}/libcuda.so
    # vllm's _C_stable_libtorch needs cublasGetMathMode (exported by libcublas, NOT
    # libcublasLt) plus libcublasLt. On aarch64 (GB200/GB300) preload BOTH from
    # torch's bundled nvidia cu13 wheel so they match torch's ABI -- the conda
    # cuda-toolkit cublas is a different build and leaves cublasGetMathMode
    # unresolved (ImportError at vllm import). The x86 path is battle-tested and
    # resolves cublas via conda lib, so leave it as-is.
    if [ "$ARCH" = "aarch64" ]; then
        NV_CUBLAS_DIR=$(dirname "$(find ${CONDA_PREFIX}/lib/python*/site-packages/nvidia -name 'libcublas.so.*' -not -type d 2>/dev/null | head -1)")
        CUBLAS=$(ls ${NV_CUBLAS_DIR}/libcublas.so.* 2>/dev/null | head -1)
        CUBLAS_LT=$(ls ${NV_CUBLAS_DIR}/libcublasLt.so.* 2>/dev/null | head -1)
        [ -n "${NV_CUBLAS_DIR}" ] && [ -d "${NV_CUBLAS_DIR}" ] && export LD_LIBRARY_PATH=${NV_CUBLAS_DIR}:${LD_LIBRARY_PATH:-}
    else
        CUBLAS=""
        CUBLAS_LT=$(find ${CONDA_PREFIX}/lib -name "libcublasLt.so*" -not -type d 2>/dev/null | head -1)
    fi
    CUBLAS_PRELOAD=""
    [ -n "${CUBLAS:-}" ] && CUBLAS_PRELOAD="${CUBLAS}:"
    [ -n "${CUBLAS_LT:-}" ] && CUBLAS_PRELOAD="${CUBLAS_PRELOAD}${CUBLAS_LT}:"
    export LD_PRELOAD=${CUBLAS_PRELOAD}${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so
    export LIBRARY_PATH=${PLATFORM_LIB}/
else
    export LD_PRELOAD=${PLATFORM_LIB}/libcuda.so:${PLATFORM_LIB}/libnvidia-ml.so
fi

LIBCUDA_REAL=$(readlink -f ${PLATFORM_LIB}/libcuda.so 2>/dev/null || ls ${PLATFORM_LIB}/libcuda.so.* 2>/dev/null | head -1)
ln -sf "$LIBCUDA_REAL" "${CONDA_PREFIX}/lib/libcuda.so"
ln -sf "$LIBCUDA_REAL" "${CONDA_PREFIX}/lib/libcuda.so.1"
ln -sf ${PLATFORM_LIB}/libnvidia-ml.so "${CONDA_PREFIX}/lib/libnvidia-ml.so"
ln -sf ${PLATFORM_LIB}/libnvidia-ml.so "${CONDA_PREFIX}/lib/libnvidia-ml.so.1"
# faiss-gpu (cu12) JIT-compiles PTX for sm_90 at search time and needs the driver's
# PTX JIT compiler lib; symlink it from the platform010 driver so faiss GPU works.
PTXJIT_REAL=$(ls ${PLATFORM_LIB}/libnvidia-ptxjitcompiler.so* 2>/dev/null | head -1)
if [[ -n "${PTXJIT_REAL:-}" ]]; then
    ln -sf "$PTXJIT_REAL" "${CONDA_PREFIX}/lib/libnvidia-ptxjitcompiler.so"
    ln -sf "$PTXJIT_REAL" "${CONDA_PREFIX}/lib/libnvidia-ptxjitcompiler.so.1"
fi

export LD_LIBRARY_PATH=${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}
export CUDA_HOME=${CONDA_PREFIX}
ln -sfn ${CONDA_PREFIX}/lib ${CONDA_PREFIX}/lib64
rm -rf /root/.cache/flashinfer

# GDN (Qwen3.5/3.6 Gated DeltaNet): the FLA/triton kernels JIT a cubin that needs
# the pip nvidia/cu13 toolkit's ptxas (13.2); the default conda CUDA_HOME ptxas is
# too old and triton fails with "KeyError: 'cubin'". Also disable vLLM's flashinfer
# sampler (the cu13 pip toolkit is version-skewed for it). Gated on SWE_GDN so dense
# runs are untouched. Validated locally (rlmast, 4B GDN async RL, finite loss).
if [[ -n "${SWE_GDN:-}" ]]; then
    NV_CU13_BIN=$(find ${CONDA_PREFIX}/lib/python*/site-packages/nvidia/cu13/bin -name nvcc 2>/dev/null | head -1)
    if [[ -n "${NV_CU13_BIN:-}" ]]; then
        NV_CU13_BIN=$(dirname "${NV_CU13_BIN}")
        export CUDA_HOME="$(dirname "${NV_CU13_BIN}")"
        export PATH="${NV_CU13_BIN}:${PATH}"
        # The nvidia/cu13 wheel is internally inconsistent: its nvcc reports 13.2
        # (__CUDACC_VER_MAJOR__/MINOR__) while its own headers report CUDART_VERSION
        # 13000 = 13.0. cccl's cuda_toolkit.h demands they be EQUAL and #errors with
        # "CUDA compiler and CUDA toolkit headers are incompatible", which kills every
        # JIT that compiles against cccl -- tilelang's GDN kernels and FlashInfer's
        # trtllm FMHA alike. That failure is also unrecoverable in the RL loop: the
        # tvm_ffi error it raises cannot be pickled across Monarch's RPC boundary, so
        # one bad kernel compile tears down the whole job (seen on GB200 after 4h).
        # NVCC_PREPEND_FLAGS reaches every nvcc invocation, whoever spawns it.
        export NVCC_PREPEND_FLAGS="-DCCCL_DISABLE_CTK_COMPATIBILITY_CHECK ${NVCC_PREPEND_FLAGS:-}"
    fi
    # tilelang links against a libcudart_stub filename, which FlashInfer can
    # later mistake for the real CUDA runtime by inspecting loaded mappings.
    # Resolve that filename to the matching full runtime before Python starts.
    REAL_CUDART=$(readlink -f "${CONDA_PREFIX}/lib/libcudart.so.13")
    CUDART_ALIAS_DIR=$(mktemp -d)
    ln -s "${REAL_CUDART}" "${CUDART_ALIAS_DIR}/libcudart_stub.so"
    export LD_LIBRARY_PATH="${CUDART_ALIAS_DIR}:${LD_LIBRARY_PATH:-}"
    export VLLM_USE_FLASHINFER_SAMPLER=0
fi

# vLLM's flashinfer sampler JIT fails on the cu13 toolkit; use native torch sampling.
export VLLM_USE_FLASHINFER_SAMPLER=0

# ---------------------------------------------------------------------------
# Mount the Manifold bucket / OilFS workspace (all roles)
# ---------------------------------------------------------------------------
if [[ -n "${OILFS_WORKSPACE:-}" ]]; then
    MOUNT_URI="ws://${OILFS_WORKSPACE}"
    MOUNT_POINT="/mnt/$(basename "${OILFS_WORKSPACE}")"
    OILFS_PROFILE_FLAG=""
elif [[ -n "${MANIFUSE_BUCKET:-}" ]]; then
    MOUNT_URI="manifold://${MANIFUSE_BUCKET}"
    MOUNT_POINT="/mnt/${MANIFUSE_BUCKET}"
    OILFS_PROFILE_FLAG="--profile=manifold"
fi

if [[ -n "${MOUNT_URI:-}" ]]; then
    echo "Mounting ${MOUNT_URI} at ${MOUNT_POINT}"
    mkdir -p "$MOUNT_POINT"
    if command -v /packages/oil.oilfs/oilfs-wrapper &> /dev/null; then
        env -i PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin" \
            HOME="$HOME" \
            /packages/oil.oilfs/oilfs-wrapper \
            ${OILFS_PROFILE_FLAG} \
            "${MOUNT_URI}" \
            "$MOUNT_POINT"
        ls -d "$MOUNT_POINT" >/dev/null && echo "Mount verified" || true
    else
        echo "WARNING: oilfs-wrapper not found, skipping mount"
    fi
    echo "${MOUNT_URI} available at ${MOUNT_POINT}"
fi

# Internet egress without TTLS (mast.py sets this when launcher.enable_ttls is
# off, i.e. on Nebius): plain fwdproxy:8080 is unreachable there, so relay a
# local port to fwdproxy's mTLS port and point every branch's proxy at it.
if [[ "${MAST_RL_FWDPROXY_RELAY:-0}" == "1" && "${IS_CONTROLLER}" == "1" ]]; then
    python "${SCRIPT_DIR}/fwdproxy_tls_relay.py" --port 18080 > /tmp/fwdproxy_relay.log 2>&1 &
    export SWE_PROXY="http://127.0.0.1:18080"
    echo "fwdproxy TLS relay on 127.0.0.1:18080 (pid $!)"
fi

# Explicit one-host staging mode for checkpoints whose large-file CDN is not
# reachable from the submit host. The fixed revision makes the copied snapshot
# reproducible; normal training jobs never enter this branch.
if [[ -n "${HF_STAGE_REPO:-}" ]]; then
    if [[ "${IS_CONTROLLER}" != "1" ]]; then
        echo "HF checkpoint staging runs only on the controller role."
        exit 0
    fi
    : "${HF_STAGE_REVISION:?HF_STAGE_REVISION is required}"
    : "${HF_STAGE_DEST:?HF_STAGE_DEST is required}"
    export HF_HUB_OFFLINE=0
    export TRANSFORMERS_OFFLINE=0
    export https_proxy="${SWE_PROXY:-http://fwdproxy:8080}"
    export http_proxy="${https_proxy}"
    export no_proxy="127.0.0.1,localhost,$(hostname)"
    export NO_PROXY="${no_proxy}"

    STAGE_LOCAL="/tmp/hf_stage/${HF_STAGE_REPO##*/}"
    rm -rf "${STAGE_LOCAL}"
    mkdir -p "${STAGE_LOCAL}"
    echo "Downloading ${HF_STAGE_REPO}@${HF_STAGE_REVISION} -> ${STAGE_LOCAL}"
    HF_HUB_DISABLE_XET=1 python - <<'PY'
import os

from huggingface_hub import snapshot_download


snapshot_download(
    repo_id=os.environ["HF_STAGE_REPO"],
    revision=os.environ["HF_STAGE_REVISION"],
    local_dir=f"/tmp/hf_stage/{os.environ['HF_STAGE_REPO'].rsplit('/', 1)[-1]}",
    max_workers=8,
)
PY

    STAGE_LOCAL="${STAGE_LOCAL}" python - <<'PY'
import json
import os
from pathlib import Path


root = Path(os.environ["STAGE_LOCAL"])
with (root / "model.safetensors.index.json").open() as f:
    index = json.load(f)
expected = sorted(set(index["weight_map"].values()))
missing = [name for name in expected if not (root / name).is_file()]
if missing:
    raise RuntimeError(f"missing checkpoint shards: {missing}")
actual_size = sum((root / name).stat().st_size for name in expected)
expected_size = int(index["metadata"]["total_size"])
if actual_size < expected_size:
    raise RuntimeError(
        f"checkpoint shard bytes are smaller than tensor data: "
        f"actual={actual_size}, tensor_data={expected_size}"
    )
print(
    f"Validated {len(expected)} shards ({actual_size} file bytes, "
    f"{expected_size} tensor bytes)",
    flush=True,
)
PY

    STAGE_TMP="${HF_STAGE_DEST}.tmp"
    rm -rf "${STAGE_TMP}"
    mkdir -p "$(dirname "${HF_STAGE_DEST}")" "${STAGE_TMP}"
    cp -a "${STAGE_LOCAL}/." "${STAGE_TMP}/"
    rm -rf "${HF_STAGE_DEST}"
    mv "${STAGE_TMP}" "${HF_STAGE_DEST}"
    echo "Staged checkpoint -> ${HF_STAGE_DEST}"
    exit 0
fi

# Offline HF (all roles: the trainer/generator load the tokenizer offline too).
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1

# ===========================================================================
# SWE-R2E aggregate branch (SWE_AGGREGATE=1): shard 0 runs the pure-CPU
# aggregator over the MOUNTED bucket results (fast local fs walk, no per-file
# RPC), writes the band JSONL + instance_summary.csv back to the bucket. Other
# roles exit immediately. Used to turn a finished wash into the curated dataset
# from a host where the bucket is mounted (devvm bucket access is rate-limited).
# ===========================================================================
if [[ "${SWE_AGGREGATE:-0}" == "1" ]]; then
    if [[ "${SWE_SHARD_ID:-0}" != "0" ]]; then
        echo "Aggregate: shard ${SWE_SHARD_ID} is a no-op (only shard 0 aggregates)."
        exit 0
    fi
    RES="${SWE_CURATE_OUT:-${MOUNT_POINT}/tree/yichuan/curate_runs/${SWE_CURATE_RUN:-curate}}/results"
    OUT="${SWE_BAND_OUT:-${MOUNT_POINT}/tree/yichuan/swe_data/r2e_band.jsonl}"
    echo "Aggregate: results=${RES} -> band=${OUT}"
    PYTHONPATH=. python torchtitan/experiments/rl/examples/swe_r2e/aggregate_passrate.py \
        --results-dir "${RES}" \
        --source-jsonl "${SWE_PROMPT_DATA}" \
        --out "${OUT}" \
        --pass-min "${SWE_BAND_MIN:-0.2}" --pass-max "${SWE_BAND_MAX:-0.7}" \
        --min-samples "${SWE_BAND_MIN_SAMPLES:-2}"
    exit $?
fi

# ===========================================================================
# SWE-R2E pass-rate data-washing branch (SWE_CURATE=1): EVERY role runs one
# shard of the offline curation worker instead of its controller/worker job.
# This is embarrassingly parallel (no monarch controller/trainer/mesh) -- each
# host serves the policy in-process (vLLM AsyncLLMEngine TP=8) and grades its
# shard of (task, sample) attempts, writing per-attempt JSON to a shared bucket
# dir. mast.py stamps SWE_SHARD_ID per role; SWE_NUM_SHARDS = total host count.
# Placed before the controller/worker split so all roles take it.
# ===========================================================================
if [[ "${SWE_CURATE:-0}" == "1" ]]; then
    SHARD="${SWE_SHARD_ID:-0}"
    NSHARD="${SWE_NUM_SHARDS:-1}"
    echo "Curate role: shard ${SHARD}/${NSHARD}"
    export TT_SANDBOX_BACKEND="${TT_SANDBOX_BACKEND:-daytona}"
    export https_proxy="${SWE_PROXY:-http://fwdproxy:8080}"
    export http_proxy="${https_proxy}"
    export no_proxy="127.0.0.1,localhost,$(hostname)"
    export NO_PROXY="${no_proxy}"
    # AsyncLLMEngine spawns its EngineCore; force spawn so CUDA re-init in the
    # child does not fail after the parent has touched CUDA.
    export VLLM_WORKER_MULTIPROC_METHOD="${VLLM_WORKER_MULTIPROC_METHOD:-spawn}"

    OUT_DIR="${SWE_CURATE_OUT:-${MOUNT_POINT}/tree/yichuan/curate_runs/${WANDB_RUN_NAME:-curate}}"
    mkdir -p "${OUT_DIR}" 2>/dev/null || true
    # Keep the Claude Code stream-json trajectory per rollout (for inspecting the
    # agent's behavior). Only shard 0 dumps to keep the bucket light.
    if [[ "${SHARD}" == "0" ]]; then
        export SWE_TRAJECTORY_DUMP_DIR="${SWE_TRAJECTORY_DUMP_DIR:-${OUT_DIR}/trajectories}"
        mkdir -p "${SWE_TRAJECTORY_DUMP_DIR}" 2>/dev/null || true
    fi
    LOG="${OUT_DIR}/curate_shard_${SHARD}.log"
    echo "Curate out -> ${OUT_DIR}; per-shard console -> ${LOG}"
    PYTHONUNBUFFERED=1 PYTHONPATH=. stdbuf -oL -eL \
        python -m torchtitan.experiments.rl.examples.swe_r2e.curate_passrate \
        --model "${SWE_CURATE_MODEL:-/mnt/torchtrain_datasets/tree/qwen3/Qwen3-32B}" \
        --data "${SWE_PROMPT_DATA}" \
        --out-dir "${OUT_DIR}" \
        --shard-id "${SHARD}" --num-shards "${NSHARD}" \
        --k "${SWE_CURATE_K:-8}" \
        --tensor-parallel "${SWE_TP:-8}" \
        --concurrency "${SWE_ROLLOUT_CONCURRENCY:-24}" 2>&1 | stdbuf -oL tee "${LOG}"
    exit "${PIPESTATUS[0]}"
fi

# ---------------------------------------------------------------------------
# Workers (trainer / generator): just run the monarch bootstrap.
# ---------------------------------------------------------------------------
if [[ "${IS_CONTROLLER}" -eq 0 ]]; then
    echo "Worker role: base setup done, exec'ing bootstrap."
    if [[ -n "${MAST_RL_HOSTS_DIR:-}" ]]; then
        python "${SCRIPT_DIR}/publish_worker_addr.py"
    fi
    PYTHONPATH=. "$@"
    exit $?
fi

# ===========================================================================
# Controller role, DAPO-Math branch: online HF datasets, no retrieval server.
# ===========================================================================
if [[ "${DAPO_MATH_RUN:-0}" == "1" ]]; then
    echo "Controller role: DAPO-Math (no retrieval server)."
    export HF_HUB_OFFLINE=0
    export TRANSFORMERS_OFFLINE=0
    export HF_DATASETS_OFFLINE=0
    export https_proxy="${SWE_PROXY:-http://fwdproxy:8080}"
    export http_proxy="${https_proxy}"
    export no_proxy="127.0.0.1,localhost,$(hostname)"
    export NO_PROXY="${no_proxy}"
    export HF_HOME="${HF_HOME:-/tmp/hf_dapo_math}"
    export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/tmp/hf_datasets_dapo_math}"

    DUMP_DIR="${DAPO_DUMP_DIR:-${MOUNT_POINT}/tree/yichuan/mast_runs/${WANDB_RUN_NAME:-dapo_math}}"
    export WANDB_DIR="${DUMP_DIR}"
    mkdir -p "${DUMP_DIR}" "${HF_HOME}" "${HF_DATASETS_CACHE}" 2>/dev/null || true
    CONSOLE_LOG="${DUMP_DIR}/controller_console.log"
    if [[ -s "${CONSOLE_LOG}" ]]; then
        mv -f "${CONSOLE_LOG}" "${DUMP_DIR}/controller_console.$(date -r "${CONSOLE_LOG}" +%Y%m%d-%H%M%S).log" || true
    fi
    echo "Outputs (and offline wandb) -> ${DUMP_DIR}"
    if [[ "${MAST_RL_NET_PROBE:-0}" == "1" ]]; then
        python mast_rl/net_probe.py 2>&1 | tee "${DUMP_DIR}/net_probe.log" || true
    fi
    PYTHONUNBUFFERED=1 PYTHONPATH=. stdbuf -oL -eL "$@" \
        --dump_folder "${DUMP_DIR}" 2>&1 | stdbuf -oL tee "${CONSOLE_LOG}"
    exit ${PIPESTATUS[0]}
fi

# ===========================================================================
# Controller role, SWE-R2E branch: Claude Code in Daytona sandboxes (no retriever).
# Selected by SWE_RUN=1 (set by mast.py for the swe_r2e recipe).
# ===========================================================================
if [[ "${SWE_RUN:-0}" == "1" ]]; then
    echo "Controller role: SWE-R2E Claude-Code harness (no retrieval server)."
    # Toolchain + R2E data ride in the workspace (mast_rl/swe_assets), shipped to
    # this host; override via env if staged elsewhere (e.g. the bucket).
    SWE_ASSETS="${SCRIPT_DIR}/swe_assets"
    export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-${SWE_ASSETS}/r2e_7tasks.jsonl}"
    # Daytona SDK needs egress to app.daytona.io via fwdproxy; loopback (the
    # on-box adapter, NCCL) must bypass the proxy.
    export TT_SANDBOX_BACKEND="${TT_SANDBOX_BACKEND:-daytona}"
    export https_proxy="${SWE_PROXY:-http://fwdproxy:8080}"
    export http_proxy="${https_proxy}"
    export no_proxy="127.0.0.1,localhost,$(hostname)"
    export NO_PROXY="${no_proxy}"

    DUMP_DIR="${SWE_DUMP_DIR:-${MOUNT_POINT}/tree/yichuan/mast_runs/${WANDB_RUN_NAME:-swe_run}}"
    export WANDB_DIR="${DUMP_DIR}"
    export SWE_TRAJECTORY_DUMP_DIR="${SWE_TRAJECTORY_DUMP_DIR:-${DUMP_DIR}/trajectories}"
    export SWE_ROLLOUT_DUMP_DIR="${SWE_ROLLOUT_DUMP_DIR:-${DUMP_DIR}/rollout_dumps}"
    # Stream zero-std prompt annotations to the (manifold-backed) dump dir, one
    # <instance_id>.json per zero-std prompt (write-once-per-file = persists on the
    # oilfs mount, unlike a shared append file). A later run passes this dir as
    # SWE_SKIP_PROMPTS to drop all-pass/all-fail prompts.
    export SWE_ZERO_STD_DIR="${SWE_ZERO_STD_DIR:-${DUMP_DIR}/zero_std}"
    mkdir -p "${DUMP_DIR}" "${SWE_TRAJECTORY_DUMP_DIR}" "${SWE_ROLLOUT_DUMP_DIR}" "${SWE_ZERO_STD_DIR}" 2>/dev/null || true
    echo "Outputs (+ traces, offline wandb) -> ${DUMP_DIR}"
    echo "Async rollout launch configuration:"
    printf '  WANDB_RUN_NAME=%s\n' "${WANDB_RUN_NAME:-<unset>}"
    printf '  SWE_ROLLOUT_CONCURRENCY=%s\n' "${SWE_ROLLOUT_CONCURRENCY:-<unset>}"
    printf '  SWE_INCLUDE_PROMPTS=%s\n' "${SWE_INCLUDE_PROMPTS:-<unset>}"
    printf '  SWE_TRAIN_STEPS=%s\n' "${SWE_TRAIN_STEPS:-<unset>}"
    printf '  SWE_NUM_GROUPS_PER_TRAIN_STEP=%s\n' "${SWE_NUM_GROUPS_PER_TRAIN_STEP:-<recipe-default>}"
    printf '  SWE_GROUP_SIZE=%s\n' "${SWE_GROUP_SIZE:-<recipe-default>}"
    printf '  SWE_DROP_ZERO_STD=%s\n' "${SWE_DROP_ZERO_STD:-<recipe-default>}"
    printf '  SWE_MAX_ACTIVE_GROUPS=%s\n' "${SWE_MAX_ACTIVE_GROUPS:-<unset>}"
    printf '  SWE_INITIAL_ACTIVE_GROUPS=%s\n' "${SWE_INITIAL_ACTIVE_GROUPS:-<recipe-default>}"
    printf '  SWE_MAX_NUM_SEQS=%s\n' "${SWE_MAX_NUM_SEQS:-<unset>}"
    printf '  SWE_OFFPOLICY_STEPS=%s\n' "${SWE_OFFPOLICY_STEPS:-<unset>}"
    printf '  SWE_NUM_ROLLOUT_WORKERS=%s\n' "${SWE_NUM_ROLLOUT_WORKERS:-<unset>}"
    printf '  SWE_SELECTION_WINDOW_GROUPS=%s\n' "${SWE_SELECTION_WINDOW_GROUPS:-<unset>}"
    if [[ -v SWE_MAX_BYPASS_GROUPS ]]; then
        printf '  SWE_MAX_BYPASS_GROUPS=%s\n' "${SWE_MAX_BYPASS_GROUPS:-<disabled>}"
    else
        printf '  SWE_MAX_BYPASS_GROUPS=%s\n' '<recipe-default>'
    fi
    printf '  SWE_STRICT_FIFO=%s\n' "${SWE_STRICT_FIFO:-<unset>}"
    printf '  SWE_DISABLE_SHUFFLE=%s\n' "${SWE_DISABLE_SHUFFLE:-<unset>}"
    printf '  SWE_TB2_VAL_DATA=%s\n' "${SWE_TB2_VAL_DATA:-<unset>}"
    printf '  SWE_VAL_INTERVAL=%s\n' "${SWE_VAL_INTERVAL:-<recipe-default>}"
    printf '  SWE_NUM_EVAL_GENERATORS=%s\n' "${SWE_NUM_EVAL_GENERATORS:-<unset>}"
    printf '  SWE_NUM_EVAL_ROLLOUT_WORKERS=%s\n' "${SWE_NUM_EVAL_ROLLOUT_WORKERS:-<recipe-default>}"
    printf '  SWE_EVAL_ROLLOUT_CONCURRENCY=%s\n' "${SWE_EVAL_ROLLOUT_CONCURRENCY:-<recipe-default>}"
    printf '  SWE_DP_FALLBACK_ROUTER=%s\n' "${SWE_DP_FALLBACK_ROUTER:-<recipe-default>}"
    printf '  SWE_DP_STICKY_REBALANCE=%s\n' "${SWE_DP_STICKY_REBALANCE:-<recipe-default>}"
    printf '  TT_DAYTONA_DISK_GB=%s\n' "${TT_DAYTONA_DISK_GB:-<unset>}"
    printf '  TT_DAYTONA_HEARTBEAT_SEC=%s\n' "${TT_DAYTONA_HEARTBEAT_SEC:-<unset>}"
    printf '  TT_DAYTONA_RPC_RETRIES=%s\n' "${TT_DAYTONA_RPC_RETRIES:-<unset>}"

    # Daytona egress preflight: prove the controller can actually create a cloud
    # sandbox over fwdproxy BEFORE a training run. Result is written to the bucket
    # (readable) since the controller stdout is not (lg is blocked on the sandbox).
    echo "Daytona egress preflight (writing ${DUMP_DIR}/daytona_preflight.txt)..."
    # Fail before reserving a long training run when command observation or the
    # provider fanout is unhealthy. The outer timeout still bounds a stuck create.
    # Cleanup is covered by the cloud-side auto-delete TTL. Do not let three
    # 60-second delete retries consume the preflight's entire wall-clock budget.
    # 300s was tight once the packed env grew: on a freshly unpacked fbpkg the first
    # torchtitan import runs on a cold filesystem, before any sandbox is created.
    TT_DAYTONA_RPC_RETRIES=0 PYTHONPATH=. timeout "${SWE_PREFLIGHT_TIMEOUT:-900}" python - "${DUMP_DIR}/daytona_preflight.txt" <<'PYEOF'
import asyncio, os, shlex, sys, time, traceback
out = sys.argv[1]
lines = []
failures = []
def persist():
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
def log(m):
    lines.append(m)
    print("[daytona_preflight]", m, flush=True)
    persist()

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def self_match_command(token):
    scanner = """import os
import signal
import sys

token = sys.argv[1].encode()
self_pid = os.getpid()
for entry in os.scandir("/proc"):
    if not entry.name.isdigit():
        continue
    pid = int(entry.name)
    if pid == self_pid:
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as cmdline_file:
            cmdline = cmdline_file.read()
    except OSError:
        continue
    if token not in cmdline:
        continue
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
print("pkill-survived", end="")
"""
    return f"python -c {shlex.quote(scanner)} {shlex.quote(token)}"

def ancestor_argv_check_command(token):
    scanner = """import os
import sys

token = sys.argv[1].encode()
pid = os.getppid()
matches = []
while pid > 1:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            command = f.read().replace(b"\\0", b" ")
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read()
    except (FileNotFoundError, ProcessLookupError):
        break
    if token in command:
        matches.append((pid, command.decode(errors="replace")))
    if b"/dev/shm/.torchtitan_exec/" in command and b".wrapper" in command:
        break
    close_paren = stat.rfind(b")")
    pid = int(stat[close_paren + 4 :].split(None, 1)[0])
if matches:
    print(repr(matches))
    raise SystemExit(1)
print("ancestor-argv-safe", end="")
"""
    return f"python -c {shlex.quote(scanner)} {shlex.quote(token)}"

IMG = os.environ.get("SWE_PREFLIGHT_IMAGE", "python:3.11-slim")
FANOUT = int(os.environ.get("SWE_PREFLIGHT_FANOUT", "4"))
CDN = os.environ.get("SWE_CLAUDE_CDN",
                     "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases")

async def time_one(make_sandbox, idx):
    """create + one session-exec; return per-phase latencies."""
    t0 = time.time()
    sb = make_sandbox(IMG)
    await sb.__aenter__()
    t_create = time.time() - t0
    try:
        t1 = time.time()
        rc, o, _ = await sb.exec("echo ok", timeout=120)
        t_exec = time.time() - t1
        return (idx, sb.sandbox_id[:8], rc, o.strip(), t_create, t_exec, None)
    finally:
        await sb.__aexit__(None, None, None)

async def main():
    try:
        from torchtitan.experiments.rl.harness.sandbox import make_sandbox
        from torchtitan.experiments.rl.examples.tmax.vanillux_loop import (
            _prepare_runtime,
            _run_bash,
        )
    except Exception as e:
        with open(out, "w") as f:
            f.write("IMPORT FAIL: " + repr(e) + "\n" + traceback.format_exc())
        raise RuntimeError("Daytona preflight import failed") from e

    # 1) Single sandbox: create + 3 sequential session-execs (per-cmd latency).
    try:
        t0 = time.time()
        sb = make_sandbox(IMG)
        await sb.__aenter__()
        log(f"[single] create id={sb.sandbox_id[:8]} in {time.time()-t0:.1f}s (img={IMG})")
        try:
            for i in range(3):
                t1 = time.time()
                rc, o, _ = await sb.exec(f"echo cmd{i}", timeout=120)
                log(f"[single] session-exec #{i} rc={rc} out={o.strip()!r} in {time.time()-t1:.2f}s")
                require((rc, o.strip()) == (0, f"cmd{i}"), "sequential exec mismatch")
            # python:3.11-slim lacks pkill. This /proc scanner has the same
            # self-match boundary: it excludes only itself and kills every other
            # process whose argv exposes the unique user-command token.
            t_pkill = time.time()
            token = f"tt_daytona_pkill_self_match_{os.getpid()}_{time.time_ns()}"
            rc, o, _ = await asyncio.wait_for(
                sb.exec(self_match_command(token), timeout=30),
                timeout=20,
            )
            require(
                (rc, o) == (0, "pkill-survived"),
                f"Daytona self-match recovery mismatch: rc={rc}, out={o!r}",
            )
            log(
                f"[single] daytona-self-match rc=0 out='pkill-survived' "
                f"in {time.time()-t_pkill:.2f}s"
            )

            # Exercise the exact production boundary too. Vanillux adds its own
            # persistent Bash wrapper around sb.exec, so neither wrapper may put
            # the decoded model command in a process argv.
            t_vanillux = time.time()
            await _prepare_runtime(sb)
            token = f"tt_vanillux_self_match_{os.getpid()}_{time.time_ns()}"
            raw, ec = await asyncio.wait_for(
                _run_bash(sb, self_match_command(token), timeout=30),
                timeout=20,
            )
            require(
                (ec, raw) == (0, "pkill-survived"),
                f"Vanillux self-match recovery mismatch: ec={ec}, raw={raw!r}",
            )
            log(
                f"[single] vanillux-self-match rc=0 out='pkill-survived' "
                f"in {time.time()-t_vanillux:.2f}s"
            )

            # A broad process-name pattern can match static text in an observable
            # supervisor argv even when the model command itself is encoded.
            # The production supervisor must expose only its fixed script path.
            t_static = time.time()
            static_token = "torchtitan: command output truncated"
            raw, ec = await asyncio.wait_for(
                _run_bash(sb, self_match_command(static_token), timeout=30),
                timeout=20,
            )
            require(
                (ec, raw) == (0, "pkill-survived"),
                f"Observable static-wrapper match: ec={ec}, raw={raw!r}",
            )
            log(
                f"[single] observable-static-wrapper-hidden rc=0 "
                f"out='pkill-survived' in {time.time()-t_static:.2f}s"
            )

            # H6 exposed a second form of the same bug: an encoded payload can
            # coincidentally contain a very short pkill pattern such as `nc`.
            # Check the live ancestor chain, not just the returned command text.
            t_ancestor = time.time()
            raw, ec = await asyncio.wait_for(
                _run_bash(sb, ancestor_argv_check_command("nc"), timeout=30),
                timeout=20,
            )
            require(
                (ec, raw) == (0, "ancestor-argv-safe"),
                f"Observable payload leaked into argv: ec={ec}, raw={raw!r}",
            )
            log(
                f"[single] observable-ancestor-argv-safe pattern='nc' rc=0 "
                f"in {time.time()-t_ancestor:.2f}s"
            )

            # Keep the fully wrapped session command below the provider's payload
            # limit even when one model action contains a large shell script.
            t_large = time.time()
            large_payload = (
                ": # " + ("x" * (64 * 1024)) + "\nprintf large-payload-survived"
            )
            raw, ec = await asyncio.wait_for(
                _run_bash(sb, large_payload, timeout=30),
                timeout=20,
            )
            require(
                (ec, raw) == (0, "large-payload-survived"),
                f"Vanillux large-payload mismatch: ec={ec}, raw={raw!r}",
            )
            log(
                f"[single] vanillux-large-payload bytes={len(large_payload.encode())} "
                f"rc=0 out='large-payload-survived' in {time.time()-t_large:.2f}s"
            )
            # A terminal agent routinely starts a server/watcher with `&` and uses
            # it in later tool calls. Prove the foreground shell returns while the
            # background child remains alive; Daytona command status alone can stay
            # RUNNING forever when it tracks that descendant. Keep the child output
            # unredirected so it inherits the wrapper's still-open capture file.
            t_bg = time.time()
            rc, o, _ = await asyncio.wait_for(
                sb.exec(
                    "sleep 600 & echo $!",
                    timeout=120,
                ),
                timeout=30,
            )
            pid = o.strip().splitlines()[-1] if o.strip() else ""
            if rc != 0 or not pid.isdigit():
                raise RuntimeError(f"background launch failed: rc={rc}, out={o!r}")
            alive_rc, _, _ = await sb.exec(f"kill -0 {pid}", timeout=30)
            await sb.exec(f"kill {pid}", timeout=30)
            if alive_rc != 0:
                raise RuntimeError(f"background child {pid} did not survive")
            log(
                f"[single] background-exec pid={pid} alive=1 "
                f"in {time.time()-t_bg:.2f}s"
            )
            # User commands may clean /tmp or our result paths. The already-open
            # capture FD and tmpfs status channel must still preserve rc + output.
            t_delete = time.time()
            rc, o, _ = await asyncio.wait_for(
                sb.exec(
                    "rm -rf /tmp/.torchtitan_exec /dev/shm/.torchtitan_exec; "
                    "printf restored; exit 7",
                    timeout=30,
                ),
                timeout=20,
            )
            require(
                (rc, o) == (7, "restored"),
                f"result-dir recovery mismatch: rc={rc}, out={o!r}",
            )
            log(
                f"[single] deleted-result-dirs rc=7 out='restored' "
                f"in {time.time()-t_delete:.2f}s"
            )

            # Preserve complete output through the current 8 MiB contract, then
            # return the first 4 MiB, a marker, and the true last 4 MiB.
            t_cap = time.time()
            rc, o, _ = await asyncio.wait_for(
                sb.exec(
                    "yes xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx "
                    "| head -c 12582912; echo; echo END",
                    timeout=120,
                ),
                timeout=130,
            )
            require(rc == 0, f"bounded-output command failed: rc={rc}")
            marker = "[torchtitan: command output truncated]"
            require(
                marker in o
                and o.rstrip().endswith("END")
                and 8 * 1024 * 1024 - 4096 < len(o) < 8 * 1024 * 1024 + 4096,
                f"bounded output mismatch: len={len(o)} tail={o[-120:]!r}",
            )
            log(
                f"[single] bounded-output len={len(o)} "
                f"in {time.time()-t_cap:.2f}s"
            )

            # A task can kill every Bash process, including the in-sandbox wrapper.
            # Only Daytona's command-status channel can recover this outcome.
            t_kill = time.time()
            rc, o, _ = await asyncio.wait_for(
                sb.exec(
                    "for f in /proc/[0-9]*/comm; do "
                    "read -r comm < \"$f\" || continue; "
                    "if [ \"$comm\" = bash ]; then "
                    "pid=${f#/proc/}; pid=${pid%/comm}; kill -9 \"$pid\"; "
                    "fi; done",
                    timeout=30,
                ),
                timeout=20,
            )
            require(
                rc == 137,
                f"bash-kill provider fallback mismatch: rc={rc}, out={o!r}",
            )
            follow_rc, follow_out, _ = await sb.exec("echo after-kill", timeout=30)
            require(
                (follow_rc, follow_out.strip()) == (0, "after-kill"),
                f"post-kill session failed: rc={follow_rc}, out={follow_out!r}",
            )
            log(
                f"[single] bash-kill rc=137 followup=ok "
                f"issues={sb.issue_tracker.counts} in {time.time()-t_kill:.2f}s"
            )
            # in-sandbox egress to the Claude CDN (the install_toolchain path)
            t2 = time.time()
            rc, o, _ = await sb.exec(
                f'curl -fsS -o /dev/null -w "%{{http_code}} %{{time_total}}s" {CDN}/stable || echo CURL_FAIL',
                timeout=120)
            log(f"[single] in-sandbox curl CDN/stable -> {o.strip()!r} (host wall {time.time()-t2:.2f}s)")
        finally:
            await sb.__aexit__(None, None, None)
    except Exception as e:
        log("[single] FAIL: " + repr(e))
        lines.append(traceback.format_exc())
        persist()
        failures.append("single")

    # 2) Concurrent fanout via the process-wide SHARED client (the real boot
    #    pattern). This is what timed out with per-sandbox clients.
    try:
        t0 = time.time()
        results = await asyncio.gather(
            *(time_one(make_sandbox, i) for i in range(FANOUT)),
            return_exceptions=True,
        )
        total = time.time() - t0
        ok = sum(1 for r in results if not isinstance(r, Exception))
        log(f"[fanout x{FANOUT}] {ok}/{FANOUT} ok, total wall {total:.1f}s (shared client)")
        for r in results:
            if isinstance(r, Exception):
                log(f"[fanout] ERR {type(r).__name__}: {str(r)[:160]}")
            else:
                idx, sid, rc, o, tc, te, _ = r
                log(f"[fanout] #{idx} id={sid} rc={rc} create={tc:.1f}s exec={te:.2f}s")
        require(ok == FANOUT, f"fanout only {ok}/{FANOUT} succeeded")
    except Exception as e:
        log("[fanout] FAIL: " + repr(e))
        lines.append(traceback.format_exc())
        persist()
        failures.append("fanout")

    persist()
    if failures:
        raise RuntimeError("Daytona preflight failed: " + ", ".join(failures))

asyncio.run(main())
PYEOF

    # Orphaned Daytona sandboxes self-reap via each sandbox's cloud-side
    # auto-delete TTL (TT_DAYTONA_AUTO_STOP_MIN / AUTO_DELETE_MIN).

    # -----------------------------------------------------------------------
    # Online task evolution (recursive task synthesis), config-gated on
    # SWE_TASK_EVOLUTION_DIR. The rollout workers write one signal per zero-std
    # group to that dir on the shared mount; here we bring up the evolve_ondella
    # loop on the (otherwise coordination-only) controller to re-tune each
    # signalled task with the SYNTH_* model and fold the result back into
    # SWE_PROMPT_DATA, which every role's TMaxDataset hot-reloads. The external
    # terminalworld-seeds scripts run unmodified as a subprocess -- no evolve
    # logic is ported into the training process. Unset the dir and this whole
    # block is skipped, so the run is identical to a plain one.
    EVOLVE_PID=""
    if [[ -n "${SWE_TASK_EVOLUTION_DIR:-}" ]]; then
        echo "Online task evolution ENABLED (signals -> ${SWE_TASK_EVOLUTION_DIR})"
        mkdir -p "${SWE_TASK_EVOLUTION_DIR}" 2>/dev/null || true
        # Per-run writable mix: seed it once from the read-only canonical seed, then
        # never clobber it -- a MAST auto-retry must resume onto the evolved mix, not
        # reset to the seed. Both the controller (CLI override below) and the workers
        # (whitelisted env) read this same shared-mount path, so a fold is seen by all.
        if [[ ! -s "${SWE_PROMPT_DATA}" && -n "${SWE_EVOLVE_SEED_DATA:-}" ]]; then
            echo "Seeding writable mix ${SWE_PROMPT_DATA} <- ${SWE_EVOLVE_SEED_DATA}"
            cp "${SWE_EVOLVE_SEED_DATA}" "${SWE_PROMPT_DATA}"
        fi
        # Stage the evolve scripts + the task packages (POOL_ROOTS) each signal is
        # resolved against. TRL_BASE is controller-local scratch (only this loop reads
        # its pool/retuned dirs); SIGNALS and MIX stay on the shared mount via their
        # own env (SWE_TASK_EVOLUTION_DIR / SWE_PROMPT_DATA). TRL_TT points
        # pack_to_dataset at this checkout so a folded row matches a prepared one.
        export TRL_BASE="${TRL_BASE:-/tmp/evolve_home}"
        # TRL_TT must be a dir X with X/torchtitan/.../prepare_rts_data.py -- pack_to_dataset
        # loads that adapter by file path. On the controller torchtitan is installed in the
        # conda env (site-packages), NOT a source checkout, so $PWD has no torchtitan/ subdir.
        # Derive the dir from the installed package and verify the adapter is actually there;
        # fall back through candidates so a genuine checkout layout still works.
        _TT_DERIVED="$(python -c 'import os,torchtitan;print(os.path.dirname(os.path.dirname(os.path.realpath(torchtitan.__file__))))' 2>/dev/null || true)"
        for _c in "${TRL_TT:-}" "${_TT_DERIVED}" "$PWD"; do
            if [[ -n "${_c}" && -f "${_c}/torchtitan/experiments/rl/examples/tmax/prepare_rts_data.py" ]]; then
                export TRL_TT="${_c}"; break
            fi
        done
        echo "evolve TRL_TT=${TRL_TT:-<unresolved -- folds will be declined>}"
        # Compound: each round re-tunes one hop from the task's CURRENT evolved
        # package, not the original seed, so a stuck task keeps getting easier hop by
        # hop. OUT_ROOT lives under TRL_BASE (controller-local scratch), so the chain
        # accumulates within a controller lifetime and resets on a MAST host restart.
        export SWE_EVOLVE_COMPOUND="${SWE_EVOLVE_COMPOUND:-1}"
        # Turn the dataset's hot reload ON (off by default) and publish each mix update as
        # a NEW versioned file (<stem>.v<N>.jsonl). A remote training host sees a new file
        # appear on the shared FUSE mount; a rewrite of the same base name only flips
        # mtime, which a remote FUSE client caches -> the fold would never reach training.
        # Single-host (colleague's della) works either way: SWE_MIX_VERSIONED=0 there keeps
        # the in-place same-name replace + mtime reload.
        export SWE_DATA_HOT_RELOAD="${SWE_DATA_HOT_RELOAD:-1}"
        export SWE_MIX_VERSIONED="${SWE_MIX_VERSIONED:-1}"
        # Harder direction (k/k too-easy -> long-horizon) is a STRUCTURAL retune
        # (rewrites the verifier), so feedback_loop revalidates it by rebuilding the
        # package on Daytona and re-running the reference solution -- there is no docker
        # on the controller. Point its probe at this role's training python (torchtitan
        # + daytona SDK) and a Daytona env file carrying the (whitelisted) key. Without
        # these the harder path declines gracefully (easier/instruction-only still runs).
        export TRL_VENV_PY="${TRL_VENV_PY:-$(command -v python)}"
        export DAYTONA_ENV_FILE="${DAYTONA_ENV_FILE:-${TRL_BASE}/daytona_env.sh}"
        mkdir -p "${TRL_BASE}/data/tw-extract" "${TRL_BASE}/data/swe-extract/tasks"
        if [[ -n "${DAYTONA_API_KEY:-}" ]]; then
            printf 'export DAYTONA_API_KEY=%q\n' "${DAYTONA_API_KEY}" > "${DAYTONA_ENV_FILE}"
        fi
        # Read the assets straight off the FUSE-mounted bucket (no manifold CLI needed
        # on the controller): evolve_assets.tar.gz (scripts + operator cards) and
        # tw_tasks.tar (the POOL_ROOTS packages each signal resolves against).
        EVOLVE_ASSETS_DIR="${SWE_EVOLVE_ASSETS:-${MOUNT_POINT}/tree/yichuan/evolve_assets}"
        if [[ -f "${EVOLVE_ASSETS_DIR}/evolve_assets.tar.gz" && -f "${EVOLVE_ASSETS_DIR}/tw_tasks.tar" ]]; then
            tar xzf "${EVOLVE_ASSETS_DIR}/evolve_assets.tar.gz" -C "${TRL_BASE}"           # -> scripts/ + data/
            tar xf  "${EVOLVE_ASSETS_DIR}/tw_tasks.tar" -C "${TRL_BASE}/data/tw-extract"   # -> tasks/<tid>/
            # openai SDK: the evolve model client follows the endpoint's 307 that raw
            # urllib will not re-POST. fwdproxy is already exported in this branch.
            python -m pip install -q openai 2>/dev/null || pip install -q --user openai 2>/dev/null || true
            EVOLVE_LOG="${DUMP_DIR}/evolve_ondella.log"
            echo "Launching evolve_ondella loop (log -> ${EVOLVE_LOG})"
            ( cd "${TRL_BASE}/scripts" && exec python evolve_ondella.py \
                --interval "${SWE_EVOLVE_INTERVAL:-120}" --log "${EVOLVE_LOG}" \
                --workers "${SWE_EVOLVE_WORKERS:-32}" \
                ) >>"${EVOLVE_LOG}" 2>&1 &
            EVOLVE_PID=$!
            echo "evolve_ondella pid=${EVOLVE_PID}"
            # Teardown on controller exit; the branch ends in `exit`, so EXIT fires.
            trap '[[ -n "${EVOLVE_PID}" ]] && kill "${EVOLVE_PID}" 2>/dev/null || true' EXIT
        else
            echo "WARN: evolve assets not found at ${EVOLVE_ASSETS_DIR}; evolution DISABLED for this run."
        fi
    fi

    # Mirror the controller's stdout+stderr (incl. any Python traceback) to the
    # bucket: MAST task stdout is not readable from the dev sandbox (lg is blocked),
    # so a crash is otherwise invisible. Line-buffer + unbuffered Python so the
    # traceback is flushed before exit; PIPESTATUS preserves the controller's code.
    CONSOLE_LOG="${DUMP_DIR}/controller_console.log"
    # A MAST retry reruns this script; keep the previous attempt's console, which
    # holds the only copy of its Python traceback.
    if [[ -s "${CONSOLE_LOG}" ]]; then
        mv -f "${CONSOLE_LOG}" "${DUMP_DIR}/controller_console.$(date -r "${CONSOLE_LOG}" +%Y%m%d-%H%M%S).log" || true
    fi
    echo "Controller console mirrored to ${CONSOLE_LOG}"
    # Validation reads the prompt file too, unless the recipe was pointed at a
    # separate benchmark set (SWE_TB2_VAL_DATA). A CLI override always wins over
    # the config, so pass the benchmark path here or the recipe's choice is lost.
    VAL_DATA="${SWE_TB2_VAL_DATA:-${SWE_PROMPT_DATA}}"
    echo "  validation_dataset.data_path=${VAL_DATA}"
    PYTHONUNBUFFERED=1 PYTHONPATH=. stdbuf -oL -eL "$@" --dump_folder "${DUMP_DIR}" \
        --rollouter.train_dataset.data_path "${SWE_PROMPT_DATA}" \
        --rollouter.validation_dataset.data_path "${VAL_DATA}" 2>&1 \
        | stdbuf -oL tee "${CONSOLE_LOG}"
    exit ${PIPESTATUS[0]}
fi

# ===========================================================================
# Controller role only: Search-R1 data + retriever + training driver.
# ===========================================================================
echo "Controller role: setting up Search-R1 data + retrieval server."

# Where the Search-R1 assets are staged on the bucket (override via env).
STAGE_ROOT="${SEARCH_R1_STAGE_ROOT:-${MOUNT_POINT}/tree/yichuan}"

# Parquet QA data, passed to training as data_path overrides (offline-safe loader).
export SEARCH_R1_TRAIN_PARQUET="${SEARCH_R1_TRAIN_PARQUET:-${STAGE_ROOT}/Search-R1/data/nq_hotpotqa_train/train.parquet}"
export SEARCH_R1_TEST_PARQUET="${SEARCH_R1_TEST_PARQUET:-${STAGE_ROOT}/Search-R1/data/nq_hotpotqa_train/test.parquet}"

# HuggingFace cache for the e5 retriever model (resolved offline from the bucket).
export HF_HOME="${HF_HOME:-${STAGE_ROOT}/hf}"
# datasets converts the 14GB corpus jsonl to an Arrow cache; keep it on node-local disk.
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/tmp/hf_datasets}"
mkdir -p "${HF_DATASETS_CACHE}" 2>/dev/null || true

RETRIEVER_INDEX="${RETRIEVER_INDEX:-${STAGE_ROOT}/search-r1-index/e5_Flat.index}"
RETRIEVER_CORPUS="${RETRIEVER_CORPUS:-${STAGE_ROOT}/search-r1-index/wiki_dump.jsonl}"

# Persist outputs (incl. the OFFLINE wandb run) to the bucket so they survive the
# ephemeral MAST node and can be `wandb sync`'d from the devvm afterward.
DUMP_DIR="${SEARCH_R1_DUMP_DIR:-${STAGE_ROOT}/mast_runs/${WANDB_RUN_NAME:-run}}"
export WANDB_DIR="${DUMP_DIR}"
mkdir -p "${DUMP_DIR}" 2>/dev/null || true
echo "Outputs (and offline wandb) -> ${DUMP_DIR}"

# ---------------------------------------------------------------------------
# Start the dense-retrieval server on the controller's GPUs.
# ---------------------------------------------------------------------------
# The controller host does no training (trainer/generators are on other hosts), so
# all its GPUs are free. The 64.5GB fp16 Flat index is sharded across the LAST 2.
NUM_GPUS=8
if command -v nvidia-smi >/dev/null 2>&1; then
    n=$(nvidia-smi -L 2>/dev/null | wc -l) || n=0
    [ "${n:-0}" -gt 0 ] && NUM_GPUS="$n"
fi
RETRIEVER_TOPK="${RETRIEVER_TOPK:-3}"
RETRIEVER_TIMEOUT="${RETRIEVER_TIMEOUT:-3600}"
FAISS_GPU=${RETRIEVER_FAISS_GPU:-1}
FAISS_GPU_FLAG=""
if [[ "${FAISS_GPU}" == "1" ]]; then
    FAISS_GPU_FLAG="--faiss_gpu"
    RETRIEVER_GPU="${RETRIEVER_GPU:-$((NUM_GPUS - 2)),$((NUM_GPUS - 1))}"
else
    RETRIEVER_GPU="${RETRIEVER_GPU:-$((NUM_GPUS - 1))}"
fi

echo "Starting retrieval server on GPU ${RETRIEVER_GPU} (faiss_gpu=${FAISS_GPU})"
echo "  index : ${RETRIEVER_INDEX}"
echo "  corpus: ${RETRIEVER_CORPUS}"
RETRIEVER_LOG="/tmp/retrieval_server.log"
CUDA_VISIBLE_DEVICES="${RETRIEVER_GPU}" \
    python -u "${SCRIPT_DIR}/retrieval_server.py" \
    --index_path "${RETRIEVER_INDEX}" \
    --corpus_path "${RETRIEVER_CORPUS}" \
    --topk "${RETRIEVER_TOPK}" \
    --retriever_name e5 \
    --retriever_model intfloat/e5-base-v2 \
    ${FAISS_GPU_FLAG} \
    > "${RETRIEVER_LOG}" 2>&1 &
RETRIEVER_PID=$!

cleanup() {
    echo "Stopping retrieval server (pid ${RETRIEVER_PID})"
    kill "${RETRIEVER_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Waiting up to ${RETRIEVER_TIMEOUT}s for the retriever to come up..."
DEADLINE=$(( $(date +%s) + RETRIEVER_TIMEOUT ))
until python - <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8000/retrieve",
    data=json.dumps({"queries": ["healthcheck"], "topk": 1, "return_scores": False}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
do
    if ! kill -0 "${RETRIEVER_PID}" 2>/dev/null; then
        echo "ERROR: retrieval server died during startup. Last log lines:"
        tail -n 80 "${RETRIEVER_LOG}" || true
        exit 1
    fi
    echo "--- retriever log tail ($(date +%H:%M:%S)) ---"
    tail -n 3 "${RETRIEVER_LOG}" 2>/dev/null || true
    if [ "$(date +%s)" -ge "${DEADLINE}" ]; then
        echo "ERROR: retrieval server not healthy after ${RETRIEVER_TIMEOUT}s. Last log lines:"
        tail -n 80 "${RETRIEVER_LOG}" || true
        exit 1
    fi
    sleep 10
done
echo "Retrieval server is healthy at http://127.0.0.1:8000/retrieve"

# ---------------------------------------------------------------------------
# Run the controller. MAST is offline, so point the Search-R1 datasets at the
# staged local parquets (the offline-safe "parquet" loader) and redirect outputs
# (incl. the offline wandb run) to the bucket.
# ---------------------------------------------------------------------------
PYTHONPATH=. "$@" --dump_folder "${DUMP_DIR}" \
    --rollouter.train_dataset.data_path "${SEARCH_R1_TRAIN_PARQUET}" \
    --rollouter.validation_dataset.data_path "${SEARCH_R1_TEST_PARQUET}"
