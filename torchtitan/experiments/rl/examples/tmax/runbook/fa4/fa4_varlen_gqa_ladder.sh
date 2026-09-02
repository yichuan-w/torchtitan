#!/bin/bash
# The rung the first ladder left out: grouped-query attention.
#
# Qwen3-0.6B has 16 query heads against 8 key/value heads, and every rung so far
# used equal counts, so `enable_gqa` was False throughout — the one shape
# property of the real model that never got exercised. Everything else in the
# first ladder completed, including sequence 2048 through aot_eager, so this is
# what is left of "the training context" that a single call can reproduce.
#
# GPU 0 by absolute index: it is free, and 6 is running an independent check
# that must not be disturbed.
set -uo pipefail
ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
GPU=${GPU:-0}
OUT=$ROOT/verify/gqa_ladder.tsv
: > "$OUT"

run() {
  local label=$1; shift
  CUDA_VISIBLE_DEVICES=$GPU timeout -k 10 240 "$ROOT/venv/bin/python" \
    "$SRC/_fa4_varlen_ctx.py" "$@" >"$ROOT/verify/gqa_${label}.out" 2>&1
  local rc=$? v
  case $rc in 0) v=COMPLETES ;; 124|137) v=NO_RETURN ;; *) v="ERROR_rc$rc" ;; esac
  printf '%s\t%s\n' "$label" "$v" | tee -a "$OUT"
}

# Model shape is 16 query heads, 8 kv heads, head dim 128.
run gqa_small     --nseq 4 --seq 256  --heads 16 --kv-heads 8 --dim 128
run gqa_long      --nseq 2 --seq 2048 --heads 16 --kv-heads 8 --dim 128
run gqa_compiled  --nseq 2 --seq 2048 --heads 16 --kv-heads 8 --dim 128 --compile
echo "=== gqa ladder ==="; cat "$OUT"
