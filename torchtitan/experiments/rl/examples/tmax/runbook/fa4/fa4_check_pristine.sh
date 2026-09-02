#!/bin/bash
# Is the working library tree still what the wheel shipped?
#
# Several rounds edited files under site-packages and were asked to restore
# them. If any edit survived, the stall every round is anchored on could be
# ours rather than the library's — which would invalidate the whole
# investigation, so it is worth one direct check against the published wheel
# rather than against another install that may differ for its own reasons.
set -u

INSTALLED=/scratch/gpfs/TRIDAO/al9080/titan-rl/lib/python3.12/site-packages/flash_attn
WORK=/scratch/gpfs/TRIDAO/al9080/fa4-pristine
VER=4.0.0b26

export PATH="$HOME/.local/bin:$PATH"
rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK"

# Unpack the release into a directory of its own rather than into any
# environment, so nothing that is currently running moves as a side effect of
# checking. --target does exactly that and needs no separate download step.
uv pip install "flash-attn-4==$VER" --no-deps --target "$WORK/pkg" \
  >"$WORK/fetch.log" 2>&1 || { echo "could not fetch the release"; tail -5 "$WORK/fetch.log"; exit 1; }

[ -d "$WORK/pkg/flash_attn" ] || { echo "no flash_attn in the unpacked release"; exit 1; }

echo "unpacked flash-attn-4 $VER"
echo "=== files that differ from the release ==="
diff -rq "$WORK/pkg/flash_attn" "$INSTALLED" 2>&1 \
  | grep -vE "__pycache__|\.pyc" | head -30
echo "=== end ==="
