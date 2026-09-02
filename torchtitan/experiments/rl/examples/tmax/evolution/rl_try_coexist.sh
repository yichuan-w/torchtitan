#!/bin/bash
# Try to put FA4 and vllm in one environment, on versions everything can accept.
#
# The blocking pins turned out to be softer than they looked. flash-attn-4 pins
# nvidia-cutlass-dsl==4.6.0.dev0, but a sweep of the FA4 acceptance test shows
# its backward passing on 4.6.0.dev0 and on 4.6.2, and hanging on 4.6.0 and
# 4.6.1 — so the pin excludes the one version the rest of the environment can
# also live with. quack-kernels asks for exactly 4.6.2. tilelang 0.1.13 widens
# apache-tvm-ffi to allow 0.1.12, which is flash-attn-4's floor.
#
# That leaves only vllm's ==4.6.0 and ==0.1.11, which come from a nightly wheel
# and read as build-time locks rather than API requirements. This deliberately
# violates them and then runs the real RL loop to find out whether they were
# real, because that is the only thing that answers it.
#
# Snapshots what it changes first. rl_restore_working_env.sh plus this snapshot
# put the environment back.
set -uo pipefail

VENV=/scratch/gpfs/TRIDAO/al9080/titan-rl
SNAP=/scratch/gpfs/TRIDAO/al9080/fa4-fix/env_snapshot_$(date -u +%Y%m%dT%H%M%SZ).txt
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=/scratch/gpfs/TRIDAO/al9080/uv-cache

"$VENV/bin/python" -m pip freeze > "$SNAP" 2>/dev/null \
  || uv pip freeze --python "$VENV/bin/python" > "$SNAP" 2>/dev/null
echo "snapshot: $SNAP ($(wc -l < "$SNAP") packages)"

echo "=== moving to the versions everything except vllm's lock can accept ==="
uv pip install --python "$VENV/bin/python" --quiet --prerelease allow \
  "nvidia-cutlass-dsl==4.6.2" "apache-tvm-ffi==0.1.12" "tilelang==0.1.13" \
  || { echo "install failed"; exit 1; }

# --no-deps: its own pins would drag the DSL back to the version that hangs.
uv pip install --python "$VENV/bin/python" --quiet --no-deps \
  "flash-attn-4==4.0.0b26" || { echo "flash-attn install failed"; exit 1; }

"$VENV/bin/python" - <<'PY'
from importlib.metadata import version
for p in ("nvidia-cutlass-dsl", "apache-tvm-ffi", "tilelang", "flash-attn-4",
          "quack-kernels", "vllm"):
    try:
        print(f"  {p} {version(p)}")
    except Exception:
        print(f"  {p} (absent)")
PY
