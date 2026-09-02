#!/bin/bash
# Add the training context to the varlen case one step at a time.
#
# Standalone varlen completes on every DSL version tested, and training stops on
# 4.6.2 — so the DSL version is not what separates them. The differences left
# are the shape and the compiled graph: the RL recipe runs sequence 2048 through
# aot_eager, this runs 4x256 directly. Each rung adds one of those.
set -uo pipefail
ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
GPU=${GPU:-7}
OUT=$ROOT/verify/ladder.tsv
: > "$OUT"
run() {
  local label=$1; shift
  CUDA_VISIBLE_DEVICES=$GPU timeout -k 10 240 "$ROOT/venv/bin/python" \
    "$SRC/_fa4_varlen_ctx.py" "$@" >"$ROOT/verify/ladder_${label}.out" 2>&1
  local rc=$? v
  case $rc in 0) v=COMPLETES ;; 124|137) v=NO_RETURN ;; *) v="ERROR_rc$rc" ;; esac
  printf '%s\t%s\n' "$label" "$v" | tee -a "$OUT"
}
run baseline        --nseq 4 --seq 256
run long_seq        --nseq 2 --seq 2048
run model_heads     --nseq 2 --seq 2048 --heads 16 --dim 128
run compiled        --nseq 4 --seq 256 --compile
run long_compiled   --nseq 2 --seq 2048 --compile
run all             --nseq 2 --seq 2048 --heads 16 --dim 128 --compile
echo "=== ladder ==="; cat "$OUT"
