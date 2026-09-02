#!/bin/bash
# Which block-wide sync survives the copy loop, and under which barrier id.
# Usage: fa4_barrier_id_matrix.sh <python> <script> <gpu>
set -u
PY=$1
SRC=$2
GPU=$3
OUT="$(dirname "$SRC")/barrier_id_matrix.out"

run() {
  local label=$1
  shift
  CUDA_VISIBLE_DEVICES="$GPU" timeout 170 "$PY" "$SRC" do_t --copies 1 \
    --no-tmem --warps 2 --iterations 8 "$@" >"$OUT" 2>&1
  local rc=$?
  local verdict
  case $rc in
    0) verdict="complete" ;;
    124) verdict="HANG" ;;
    *) verdict="error rc=$rc" ;;
  esac
  printf '  %-30s %s\n' "$label" "$verdict"
}

echo "host=$(hostname -s) gpu=$GPU"
run "named barrier id=1"  --post-loop-barrier --barrier-id 1
run "named barrier id=4"  --post-loop-barrier --barrier-id 4
run "named barrier id=7"  --post-loop-barrier --barrier-id 7
run "plain syncthreads"   --post-loop-syncthreads
run "no sync (baseline)"
