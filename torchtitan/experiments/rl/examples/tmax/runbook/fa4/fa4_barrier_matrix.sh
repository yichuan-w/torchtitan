#!/bin/bash
# Post-loop barrier matrix, run identically on both architectures.
#
# Inline ssh commands were being interpreted by a non-POSIX login shell on one
# of the two machines, which turned shell-level argument mangling into exit
# code 2 and made three runs look like program failures. Shipping a script and
# invoking bash explicitly removes that whole class of confusion, and makes the
# two machines run byte-identical logic.
#
# Usage: fa4_barrier_matrix.sh <python> <script> <gpu>
set -u
PY=$1
SRC=$2
GPU=$3

run() {
  local label=$1
  shift
  CUDA_VISIBLE_DEVICES="$GPU" timeout 170 "$PY" "$SRC" do_t --copies 1 \
    --no-tmem --warps 2 "$@" >"$(dirname "$SRC")/barrier_matrix.out" 2>&1
  local rc=$?
  case $rc in
    0) local verdict="complete" ;;
    124) local verdict="HANG" ;;
    *) local verdict="error rc=$rc (see barrier_matrix.out)" ;;
  esac
  printf '  %-28s %s\n' "$label" "$verdict"
}

echo "host=$(hostname -s) gpu=$GPU"
run "iters=8  no barrier"    --iterations 8
run "iters=8  post barrier"  --iterations 8 --post-loop-barrier
run "iters=2  post barrier"  --iterations 2 --post-loop-barrier
run "iters=1  post barrier"  --iterations 1 --post-loop-barrier
run "iters=0  post barrier"  --iterations 0 --post-loop-barrier
