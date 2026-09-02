#!/bin/bash
# Put the RL environment back to what it was before FA4 was installed into it.
#
# On 2026-08-12, installing flash-attn-4 here to investigate the B300 hang
# dragged two shared dependencies past what vllm allows:
#
#   vllm         apache-tvm-ffi ==0.1.11      flash-attn-4 wants >=0.1.12
#   vllm         nvidia-cutlass-dsl ==4.6.0   flash-attn-4 wants ==4.6.0.dev0
#
# Both moved, and both broke something. The DSL mismatch is what made FA4's
# backward hang. The tvm-ffi bump made vllm's generator abort on a duplicate
# TVM FFI registration, which stayed hidden until FlashInfer's JIT could
# actually run, and then read as a mysterious regression in code nobody had
# touched.
#
# FA4 keeps its own environment (fa4-correct-dsl). This one goes back to being
# the thing that works: the RL loop, trainer and vllm generator in one process
# group, which is how torchtitan's RL is built.
set -uo pipefail

VENV=/scratch/gpfs/TRIDAO/al9080/titan-rl
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=/scratch/gpfs/TRIDAO/al9080/uv-cache

show() {
  "$VENV/bin/python" - <<'PY'
from importlib.metadata import version
for p in ("apache-tvm-ffi", "nvidia-cutlass-dsl", "flash-attn-4", "vllm",
          "tilelang", "ninja"):
    try:
        print(f"  {p} {version(p)}")
    except Exception:
        print(f"  {p} (absent)")
PY
}

echo "=== before ==="
show

# flash-attn-4 is the only thing here that wanted the newer pins, and nothing
# in the RL loop imports it: vllm uses its own fork under third_party/tml_fa4,
# and its references to the standalone package are in the ROCm branch and one
# model. Removing it is what lets the shared pins go back.
uv pip uninstall --python "$VENV/bin/python" flash-attn-4 2>&1 | tail -2

# Back to the version vllm and tilelang both accept.
uv pip install --python "$VENV/bin/python" --quiet "apache-tvm-ffi==0.1.11" \
  || echo "WARN: could not restore apache-tvm-ffi"

echo "=== after ==="
show

echo "=== pins now satisfied? ==="
"$VENV/bin/python" - <<'PY'
from importlib.metadata import distributions, version
installed = version("apache-tvm-ffi")
bad = []
for d in distributions():
    for r in (d.requires or []):
        if "apache-tvm-ffi" in r.lower() and "extra ==" not in r:
            bad.append((d.metadata["Name"], r))
print(f"apache-tvm-ffi installed: {installed}")
for name, req in bad:
    print(f"  {name} requires {req}")
PY
