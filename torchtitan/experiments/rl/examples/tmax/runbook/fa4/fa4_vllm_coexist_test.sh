#!/bin/bash
# Can FA4 training and vllm inference share one environment?
#
# They do not collide the way the pins suggest. vllm carries its own fork of FA4
# under vllm/third_party/tml_fa4, forward-only, for inference; training uses the
# standalone flash_attn for its backward. The only thing they share is
# nvidia-cutlass-dsl, and they pin it differently: vllm at 4.6.0, flash-attn-4
# 4.0.0b26 at 4.6.0.dev0.
#
# One direction is worth trying, because the asymmetry favours it. 4.6.0 is the
# stricter of the two — it is what rejects flash-attn's forward, and vllm's fork
# already carries the fix for that rejection. Code that compiles under the
# stricter version usually compiles under the looser one, so dev0 is the
# candidate that could serve both. Pinning the other way cannot work: b26's
# backward is exactly what hangs against 4.6.0.
#
# Installs vllm beside the working FA4 environment, forces the DSL back to
# dev0, and then runs both sides for real rather than checking that they import.
set -euo pipefail

ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-coexist
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
DONOR=/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl/venv
# The build the working environment has, 1.0.0.dev20260806+cu130, is not on the
# public nightly index — that index serves only the current commit. Any recent
# nightly answers the question anyway, since what is being tested is whether
# vllm's vendored forward compiles against the prerelease DSL at all.
VLLM_SPEC=vllm

export UV_CACHE_DIR=$ROOT/.uv-cache
export HF_HOME=$ROOT/.hf
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$ROOT" "$UV_CACHE_DIR" "$HF_HOME"
cd "$ROOT"

uv venv --clear --python 3.12 venv
uv pip install --python venv/bin/python --quiet \
  --pre torch==2.14.0.dev20260806+cu130 \
  --index-url https://download.pytorch.org/whl/nightly/cu130

echo "installing $VLLM_SPEC (pulls DSL 4.6.0)"
# unsafe-best-match: vllm exists on both indexes, and uv otherwise takes the
# first index that has the name at all, which lands on the PyPI release rather
# than the nightly.
uv pip install --python venv/bin/python --quiet \
  --index-strategy unsafe-best-match \
  --index-url https://wheels.vllm.ai/nightly \
  --extra-index-url https://pypi.org/simple \
  --prerelease allow \
  "$VLLM_SPEC"

echo "installing flash-attn-4 and forcing the DSL back to the prerelease"
uv pip install --python venv/bin/python --quiet --prerelease allow \
  "flash-attn-4==4.0.0b26" einops
uv pip install --python venv/bin/python --quiet --prerelease allow \
  "nvidia-cutlass-dsl==4.6.0.dev0"

venv/bin/python - <<'PY'
from importlib.metadata import version
for p in ("torch", "vllm", "flash-attn-4", "nvidia-cutlass-dsl"):
    try:
        print(f"  {p} {version(p)}")
    except Exception as exc:
        print(f"  {p} missing ({exc.__class__.__name__})")
PY

echo "=== vllm inference on the shared DSL ==="
CUDA_VISIBLE_DEVICES=6 venv/bin/python - <<'PY' 2>&1 | tail -12
from vllm import LLM, SamplingParams
llm = LLM(model="facebook/opt-125m", gpu_memory_utilization=0.25,
          max_model_len=256, enforce_eager=False)
out = llm.generate(["The capital of France is"], SamplingParams(max_tokens=8))
print("VLLM_OK:", repr(out[0].outputs[0].text))
PY

echo "=== FA4 acceptance on the shared DSL ==="
CUDA_VISIBLE_DEVICES=6 venv/bin/python -u "$SRC/fa4_bwd_acceptance.py" --timeout 180 \
  2>&1 | tee "$ROOT/acceptance_coexist.log" | tail -16
