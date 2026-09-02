#!/bin/bash
# Leave the RL environment in a state that was chosen, not one that six
# diagnostic experiments happened to end on.
#
# Two of those experiments moved packages that turned out to be irrelevant, and
# both left the environment violating requirements it does not need to violate:
# quack-kernels went to 0.5.3 (vllm asks for >=0.6.1) and apache-tvm-ffi to
# 0.1.13.post3 (tilelang 0.1.13 asks for <0.1.13). Neither changed the failure.
#
# The change that did matter stays: nvidia-cutlass-dsl-libs-cu13 at 4.6.2. It is
# the variant this process loads, vllm's [cu13]==4.6.0 pin holds it back at the
# version whose backward never returns, and the meta-package version does not
# reveal which variant is live.
#
# Target, and who each value is for:
#   libs-cu12 / libs-cu13 4.6.2   the loaded runtime, and the only one FA4 works on
#   quack-kernels 0.6.4           satisfies vllm >=0.6.1 and flash-attn-4 >=0.5.3
#   apache-tvm-ffi 0.1.12         flash-attn-4's floor, inside tilelang 0.1.13's range
#   tilelang 0.1.13               needed for that tvm-ffi range
#
# vllm's ==4.6.0 and ==0.1.11 stay violated on purpose: they are locks from a
# nightly wheel, and stage-1 runs clean against them.
set -uo pipefail

VENV=/scratch/gpfs/TRIDAO/al9080/titan-rl
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=/scratch/gpfs/TRIDAO/al9080/uv-cache

show() {
  "$VENV/bin/python" - <<'PY'
from importlib.metadata import version
for p in ("nvidia-cutlass-dsl", "nvidia-cutlass-dsl-libs-cu12",
          "nvidia-cutlass-dsl-libs-cu13", "apache-tvm-ffi", "tilelang",
          "quack-kernels", "flash-attn-4"):
    try:
        print(f"  {p} {version(p)}")
    except Exception:
        print(f"  {p} absent")
PY
}

echo "=== before ==="
show

uv pip install --python "$VENV/bin/python" --quiet --no-deps \
  "quack-kernels==0.6.4" "apache-tvm-ffi==0.1.12" || echo "WARN: install failed"

echo "=== after ==="
show

echo "=== what the process actually loads ==="
CUDA_VISIBLE_DEVICES=0 "$VENV/bin/python" - <<'PY'
import pathlib
import torch  # noqa: F401  — loading torch first is what pulls the runtime in
import cutlass  # noqa: F401
maps = pathlib.Path("/proc/self/maps").read_text()
for line in maps.splitlines():
    if "cute_dsl_runtime" in line or "_cutlass_ir" in line:
        print("  " + line.split()[-1])
PY
