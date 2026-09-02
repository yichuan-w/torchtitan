#!/bin/bash
# Which cutlass-dsl versions can FA4's backward actually run on?
#
# The pin says 4.6.0.dev0 and 4.6.0 demonstrably hangs, but the pin looks stale:
# upstream fixed "compatibility issues with CuTe DSL 4.6.0+" on 2026-06-25,
# between dev0 (06-14) and 4.6.0 (07-02), and never moved it. Meanwhile the
# environment that has to host FA4 alongside vllm has other packages asking for
# other versions — quack-kernels wants 4.6.2, vllm wants 4.6.0 — so knowing
# which of them FA4 tolerates decides whether coexistence is possible at all.
#
# Runs in the isolated FA4 environment, so nothing the RL loop depends on moves.
# Records compile-vs-hang-vs-pass per version rather than stopping at the first
# failure, because the shape of the compatibility range is the answer here.
set -uo pipefail

ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
RESULTS=$ROOT/dsl_matrix.tsv
GPU=${GPU:-7}
CFG="2 4 256 64 0"

export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=$ROOT/.uv-cache
touch "$RESULTS"

for v in 4.6.0.dev0 4.6.0 4.6.1 4.6.2; do
  if cut -f1 "$RESULTS" | grep -qx "$v"; then
    echo "$v already recorded"
    continue
  fi
  echo "--- installing cutlass-dsl $v ---"
  if ! uv pip install --python "$ROOT/venv/bin/python" --quiet --prerelease allow \
      "nvidia-cutlass-dsl==$v" >/dev/null 2>&1; then
    printf '%s\tINSTALL_FAILED\n' "$v" >>"$RESULTS"
    continue
  fi

  CUDA_VISIBLE_DEVICES=$GPU timeout -k 10 180 "$ROOT/venv/bin/python" \
    "$SRC/_fa4_acceptance_one.py" $CFG >"$ROOT/dsl_$v.out" 2>&1
  rc=$?
  case $rc in
    0) verdict=PASS ;;
    124) verdict=HANG ;;
    *) # Separate a compile-time rejection from any other failure: they mean
       # different things for whether the version is usable at all.
       if grep -qiE "same type|DSLRuntimeError|type error|mO_cur" "$ROOT/dsl_$v.out"; then
         verdict=COMPILE_ERROR
       else
         verdict="ERROR_rc$rc"
       fi ;;
  esac
  printf '%s\t%s\n' "$v" "$verdict" >>"$RESULTS"
  echo "$v -> $verdict"
done

echo "=== matrix ==="
cat "$RESULTS"
# Leave the environment on the version that is known to work, so an interrupted
# run does not quietly become the state everything else is measured against.
uv pip install --python "$ROOT/venv/bin/python" --quiet --prerelease allow \
  "nvidia-cutlass-dsl==4.6.0.dev0" >/dev/null 2>&1
echo "restored to 4.6.0.dev0"
