#!/bin/bash
# Swap the cutlass DSL in the RL environment, one direction per invocation.
#
# FA4's backward needs 4.6.0.dev0, the version flash-attn-4 pins; vllm's wheel
# declares 4.6.0. The trainer and the vllm generator run in one process group
# here, so they cannot each have their own — which is the whole question this
# swap is meant to settle, by running the real loop under each.
#
# Both versions are in the uv cache, so either direction is seconds and the
# reverse is always one command away. Prints what is installed before and after
# rather than assuming the install did what was asked.
#
# Usage: rl_swap_dsl.sh 4.6.0.dev0 | rl_swap_dsl.sh 4.6.0
set -uo pipefail

VENV=/scratch/gpfs/TRIDAO/al9080/titan-rl
WANT=${1:?usage: rl_swap_dsl.sh <version>}
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=/scratch/gpfs/TRIDAO/al9080/uv-cache

current() {
  "$VENV/bin/python" -c \
    'from importlib.metadata import version; print(version("nvidia-cutlass-dsl"))' \
    2>/dev/null || echo "?"
}

echo "before: $(current)"
uv pip install --python "$VENV/bin/python" --quiet --prerelease allow \
  "nvidia-cutlass-dsl==$WANT" || { echo "install failed"; exit 1; }
after=$(current)
echo "after:  $after"
[ "$after" = "$WANT" ] || { echo "version did not take"; exit 1; }

# The backward guard keys off this comparison, so it should go quiet exactly
# when the versions line up. Reporting it here makes that visible rather than
# something to remember.
"$VENV/bin/python" - <<'PY'
from importlib.metadata import requires, version
pin = next((r.split("==", 1)[1].split(";", 1)[0].strip()
            for r in (requires("flash-attn-4") or [])
            if r.startswith("nvidia-cutlass-dsl==")), None)
got = version("nvidia-cutlass-dsl")
print(f"flash-attn-4 pins {pin}; installed {got}; "
      f"{'backward allowed' if pin == got else 'backward will refuse'}")
PY
