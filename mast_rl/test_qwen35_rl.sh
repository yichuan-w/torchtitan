#!/bin/bash
# Quick feasibility: does qwen3_5-27B pass the RL trainer's model-build path?
# (no weights needed -- just config/assert level)
set -o pipefail
export https_proxy=http://fwdproxy:8080 http_proxy=http://fwdproxy:8080
export no_proxy=localhost,127.0.0.1,.facebook.com,.internalfb.com,fwdproxy
unset PYTHONPATH
source "${HOME}/miniforge3/etc/profile.d/conda.sh"
conda activate rlmast_arm
SP="${CONDA_PREFIX}/lib/python3.12/site-packages"; PL=/usr/local/fbcode/platform010-aarch64/lib; NV="${SP}/nvidia/cu13/lib"
export LD_LIBRARY_PATH="${NV}:${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="${NV}/libcublas.so.13:${NV}/libcublasLt.so.13:${PL}/libcuda.so:${PL}/libnvidia-ml.so"
python - <<'PY' 2>&1 | tail -30
import torch
from torchtitan.models.qwen3_5 import model_registry as q35
from torchtitan.models.common.attention import VarlenAttention, FlexAttention
print("building qwen3_5 27B model_spec (flex)...")
spec = q35("27B", attn_backend="flex")
layers = spec.model.layers
print("num layers:", len(layers))
# inspect layer 0 and a few layers' attention structure
for i in [0, 1, 2, 3]:
    L = layers[i]
    attn = getattr(L, "attention", None) or getattr(L, "attn", None)
    ia = getattr(attn, "inner_attention", None) if attn is not None else None
    print(f"layer{i}: block={type(L).__name__} attn={type(attn).__name__ if attn else None} inner={type(ia).__name__ if ia else None}")
# the RL trainer assert (trainer.py:359)
L0 = layers[0]
attn0 = getattr(L0, "attention", None) or getattr(L0, "attn", None)
ia0 = getattr(attn0, "inner_attention", None) if attn0 else None
ok = isinstance(ia0, (VarlenAttention.Config, FlexAttention.Config))
print("RL trainer assert (layer0 inner_attention in varlen/flex):", ok)
print("VERDICT:", "PASSES RL path" if ok else "FAILS RL path (hybrid GatedDeltaNet not supported by compute_logprobs)")
PY
echo "exit rc=$?"
