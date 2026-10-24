#!/bin/bash
# Submit a multi-host, multi-generator Search-R1 RL job to MAST.
#
# The launcher parses the chosen --config (a mast_rl MastRLConfig) to infer the
# node count from its trainer/generator parallelism + --num_generators, builds a
# multi-role AppDef (controller + trainer + one role per generator), conda-packs
# the active rlmast env (with torchtitan installed from THIS checkout), and submits.
# run.sh on the controller host brings up the dense-retrieval server, then training.
#
# Usage:
#   conda activate rlmast
#   bash mast_rl/submit.sh                                   # 32B, 3 generators (default)
#   bash mast_rl/submit.sh --config rl_grpo_qwen3_1_7b_search_r1 --num_generators 3
#   bash mast_rl/submit.sh --no-reinstall                    # skip torchtitan reinstall
#   bash mast_rl/submit.sh --launcher.region pci             # any --launcher.* override
#
# --config and --num_generators are pulled out; everything else is forwarded to the
# controller as config overrides (e.g. --launcher.hpc-identity, --launcher.region).

cd "$(dirname "$0")/.." || exit 1  # repo root (the OSS torchtitan checkout)

# Default x86 env is 'rlmast'; the GB300/aarch64 submit sets RLMAST_ENV=rlmast_arm.
RLMAST_ENV="${RLMAST_ENV:-rlmast}"
if [[ "${CONDA_DEFAULT_ENV:-}" != "${RLMAST_ENV}" ]]; then
    echo "ERROR: activate the '${RLMAST_ENV}' conda env first: conda activate ${RLMAST_ENV}" >&2
    exit 1
fi

# --- Defaults ---
MODULE="mast_rl"
CONFIG="${CONFIG:-rl_grpo_qwen3_32b_search_r1}"
NUM_GENERATORS="${NUM_GENERATORS:-3}"
# ----------------

REINSTALL=1
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-reinstall) REINSTALL=0; shift ;;
        --config)  CONFIG="$2"; shift 2 ;;
        --config=*) CONFIG="${1#*=}"; shift ;;
        --num_generators)  NUM_GENERATORS="$2"; shift 2 ;;
        --num_generators=*) NUM_GENERATORS="${1#*=}"; shift ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# Reinstall torchtitan from this checkout so the conda-pack ships local changes.
# Point TORCHTITAN_SRC at another root to ship a different worktree's torchtitan.
if [[ "${REINSTALL}" -eq 1 ]]; then
    TORCHTITAN_ROOT="${TORCHTITAN_SRC:-$(pwd)}"
    echo "Reinstalling torchtitan from ${TORCHTITAN_ROOT}."
    ( rm -rf build torchtitan.egg-info )
    if ! pip install --no-build-isolation --no-deps --force-reinstall "${TORCHTITAN_ROOT}"; then
        echo "ERROR: torchtitan reinstall failed; aborting submit" >&2
        exit 1
    fi
    echo ""
else
    echo "Skipping torchtitan reinstall (--no-reinstall)."
    echo ""
fi

# On an OSS conda dev server, importing monarch (for the submit) pulls in torch,
# whose bundled NVIDIA libs aren't on the system linker path. Prepend the env's
# nvidia libs so `python mast_rl/launcher.py` can import monarch.tools.
NV_DIR="${CONDA_PREFIX}/lib/python3.12/site-packages/nvidia"
if [[ -d "${NV_DIR}" ]]; then
    NV_LIBS=""
    for d in "${NV_DIR}"/*/lib; do
        [[ -d "$d" ]] && NV_LIBS="${NV_LIBS}${NV_LIBS:+:}$d"
    done
    export LD_LIBRARY_PATH="${NV_LIBS}${LD_LIBRARY_PATH:+:}${LD_LIBRARY_PATH:-}"
    if [[ -f "${NV_DIR}/cu13/lib/libcublas.so.13" ]]; then
        export LD_PRELOAD="${NV_DIR}/cu13/lib/libcublas.so.13:${NV_DIR}/cu13/lib/libcublasLt.so.13${LD_PRELOAD:+:}${LD_PRELOAD:-}"
    fi
fi

echo "Submitting MAST multi-host Search-R1 RL job:"
echo "  MODULE:          ${MODULE}"
echo "  CONFIG:          ${CONFIG}"
echo "  NUM_GENERATORS:  ${NUM_GENERATORS}"
echo "  EXTRA_ARGS:      ${EXTRA_ARGS[*]:-<none>}"
echo ""

exec python mast_rl/launcher.py \
    -- mast_rl/main.py --module "${MODULE}" --config "${CONFIG}" \
    --num_generators "${NUM_GENERATORS}" "${EXTRA_ARGS[@]}"
