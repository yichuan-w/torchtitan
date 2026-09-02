#!/bin/bash
# Tidy the working environment without breaking what currently runs in it.
#
# What was found there: flash_fwd_sm100.py carries a local patch, and a .bak of
# the original sits beside it. The patch is not stale — it is what lets FA4
# forward compile against the DSL that environment has, and forward does run.
# The .bak is stale: nothing reads it, and a copy of the original that looks
# current is exactly the kind of artifact that sends the next reader to a
# confident wrong conclusion.
#
# The DSL is deliberately left alone. Three packages there pin three
# incompatible versions — flash-attn-4 wants 4.6.0.dev0, vllm wants 4.6.0,
# quack-kernels wants 4.6.2 — so moving it to satisfy FA4 would break vllm,
# which the rollout side depends on. FA4 work belongs in its own environment,
# which is what fa4-correct-dsl is.
#
# Backups land in a dated directory and the patch is also saved as a diff into
# the repo, so the local change stops living only inside site-packages.
set -euo pipefail

ENVDIR=/scratch/gpfs/TRIDAO/al9080/titan-rl/lib/python3.12/site-packages/flash_attn/cute
PRISTINE=/scratch/gpfs/TRIDAO/al9080/fa4-pristine/pkg/flash_attn/cute
BACKUP=/scratch/gpfs/TRIDAO/al9080/fa4-fix/backups/$(date -u +%Y%m%dT%H%M%SZ)
OUTDIR=/scratch/gpfs/TRIDAO/al9080/fa4-fix

mkdir -p "$BACKUP"

# Back up both the live patched file and the stale copy before touching either.
cp -p "$ENVDIR/flash_fwd_sm100.py" "$BACKUP/flash_fwd_sm100.py.patched"
if [ -f "$ENVDIR/flash_fwd_sm100.py.bak" ]; then
  cp -p "$ENVDIR/flash_fwd_sm100.py.bak" "$BACKUP/flash_fwd_sm100.py.bak"
fi
echo "backed up to $BACKUP"

# Save the local change as a diff so it exists somewhere a reader will look.
if [ -f "$PRISTINE/flash_fwd_sm100.py" ]; then
  diff -u "$PRISTINE/flash_fwd_sm100.py" "$ENVDIR/flash_fwd_sm100.py" \
    > "$OUTDIR/local_patch_flash_fwd_sm100.diff" || true
  echo "patch saved as local_patch_flash_fwd_sm100.diff ($(wc -l < "$OUTDIR/local_patch_flash_fwd_sm100.diff") lines)"
fi

# The .bak is byte-identical to the published release, so the backup above plus
# the release itself both hold a copy; removing it loses nothing.
if [ -f "$ENVDIR/flash_fwd_sm100.py.bak" ]; then
  if cmp -s "$PRISTINE/flash_fwd_sm100.py" "$ENVDIR/flash_fwd_sm100.py.bak"; then
    rm "$ENVDIR/flash_fwd_sm100.py.bak"
    echo "removed stale flash_fwd_sm100.py.bak (identical to the release)"
  else
    echo "kept flash_fwd_sm100.py.bak — it differs from the release, so it is not"
    echo "merely a duplicate and needs a look before removal"
  fi
fi

echo "=== remaining differences from the release ==="
diff -rq "$PRISTINE" "$ENVDIR" 2>&1 | grep -vE "__pycache__|\.pyc" | head -10
