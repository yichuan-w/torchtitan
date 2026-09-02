#!/bin/bash
# Re-run the DSL sweep, on both attention paths, with outcomes kept apart.
#
# Two things forced a rewrite of the first version. The first pass recorded
# 4.6.0 as a hang, while the same release against the same DSL raised a
# compile-time type error about `mO_cur` elsewhere — one combination cannot be
# both, and nothing goes upstream until that is settled. And the sweep only
# covered dense SDPA, while training uses varlen: with 4.6.2, dense passes and
# varlen stops inside `_bwd_postprocess_convert`, so the dense-only table would
# have supported a claim about training that is false.
#
# Three runs per cell, full output kept, compile failures reported as
# themselves rather than folded into a generic error.
set -uo pipefail

ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-correct-dsl
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
OUT=$ROOT/verify
RESULTS=$OUT/rows.tsv
GPU=${GPU:-7}
REPS=${REPS:-3}
TIMEOUT=${TIMEOUT:-180}

export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR=$ROOT/.uv-cache
mkdir -p "$OUT"
: > "$RESULTS"

classify() {  # rc, logfile -> verdict
  local rc=$1 log=$2
  case $rc in
    0) echo COMPLETES ;;
    124|137) echo NO_RETURN ;;
    *)
      if grep -qiE "same type|mO_cur|DSLRuntimeError|Compilation failed|MLIR" "$log"; then
        echo COMPILE_ERROR
      else
        echo "ERROR_rc$rc"
      fi ;;
  esac
}

for v in 4.6.0.dev0 4.6.0 4.6.1 4.6.2; do
  echo "--- cutlass-dsl $v ---"
  if ! uv pip install --python "$ROOT/venv/bin/python" --quiet --prerelease allow \
      "nvidia-cutlass-dsl==$v" >"$OUT/install_$v.log" 2>&1; then
    printf '%s\t-\tINSTALL_FAILED\t-\n' "$v" >>"$RESULTS"
    continue
  fi
  # A failed install that leaves the previous version in place would otherwise
  # be recorded under the version that was asked for.
  got=$("$ROOT/venv/bin/python" -c \
    'from importlib.metadata import version; print(version("nvidia-cutlass-dsl"))' 2>/dev/null)
  if [ "$got" != "$v" ]; then
    printf '%s\t-\tWRONG_VERSION\t%s\n' "$v" "$got" >>"$RESULTS"
    continue
  fi

  for path in dense varlen; do
    for r in $(seq 1 "$REPS"); do
      log=$OUT/run_${v}_${path}_$r.out
      if [ "$path" = dense ]; then
        CUDA_VISIBLE_DEVICES=$GPU timeout -k 10 "$TIMEOUT" "$ROOT/venv/bin/python" \
          "$SRC/_fa4_acceptance_one.py" 2 4 256 64 0 >"$log" 2>&1
      else
        CUDA_VISIBLE_DEVICES=$GPU timeout -k 10 "$TIMEOUT" "$ROOT/venv/bin/python" \
          "$SRC/_fa4_varlen_one.py" 4 256 4 64 >"$log" 2>&1
      fi
      verdict=$(classify $? "$log")
      printf '%s\t%s\t%s\trun%s\n' "$v" "$path" "$verdict" "$r" >>"$RESULTS"
      echo "  $path run$r -> $verdict"
    done
  done
done

echo "=== summary ==="
awk -F'\t' '$3!="" {c[$1"\t"$2"\t"$3]++} END {for (k in c) printf "%s\t%dx\n", k, c[k]}' \
  "$RESULTS" | sort
uv pip install --python "$ROOT/venv/bin/python" --quiet --prerelease allow \
  "nvidia-cutlass-dsl==4.6.0.dev0" >/dev/null 2>&1
echo "restored to 4.6.0.dev0"
