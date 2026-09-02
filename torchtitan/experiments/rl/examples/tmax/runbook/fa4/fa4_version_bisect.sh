#!/bin/bash
# Walk flash-attn-4 releases backwards until the B300 backward stops hanging.
#
# This is the one line of attack that needs neither a second Blackwell nor a
# test program of our own. If an earlier release completes, the change that
# introduced the stall is between it and the next one, and a diff over that
# range is the root cause — with the acceptance test, not a reproducer, as the
# judge. b26 is the newest release and already contains everything upstream has
# for the cute path, so there is no fix waiting to be picked up.
#
# Runs in its own virtualenv so the working one is untouched. Appends one line
# per version as it goes and skips versions already recorded, so it can be
# killed and restarted without losing the walk.
set -u

ROOT=/scratch/gpfs/TRIDAO/al9080/fa4-bisect
SRC=/scratch/gpfs/TRIDAO/al9080/fa4-fix
RESULTS=$ROOT/versions.tsv
LOG=$ROOT/bisect.log
# GPU 7 is running the acceptance test that establishes the current anchor.
GPU=${GPU:-6}
# Smallest configuration the acceptance test reports as hanging.
CFG="2 4 256 64 0"

export UV_CACHE_DIR=$ROOT/.uv-cache
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$ROOT" "$UV_CACHE_DIR"
touch "$RESULTS"

if [ ! -x "$ROOT/venv/bin/python" ]; then
  echo "$(date -Is) creating venv" | tee -a "$LOG"
  uv venv --python 3.12 "$ROOT/venv" >>"$LOG" 2>&1
  uv pip install --python "$ROOT/venv/bin/python" --quiet \
    --pre torch==2.14.0.dev20260806+cu130 \
    --index-url https://download.pytorch.org/whl/nightly/cu130 >>"$LOG" 2>&1
  uv pip install --python "$ROOT/venv/bin/python" --quiet einops >>"$LOG" 2>&1
fi

# Hold the DSL fixed at the version the B300 machine runs, and vary only
# flash-attn. Two reasons. The releases pin nvidia-cutlass-dsl==4.6.0.dev0,
# which is not on PyPI at all, so honest resolution fails outright; and letting
# each release pull its own DSL moves two things at once — an earlier walk did
# that, landed on 4.6.2, and got API errors rather than results. The variable of
# interest is flash-attn's own code.
uv pip install --python "$ROOT/venv/bin/python" --quiet \
  nvidia-cutlass-dsl==4.6.0 >>"$LOG" 2>&1

for v in 4.0.0b26 4.0.0b25 4.0.0b24 4.0.0b23 4.0.0b22 4.0.0b21 \
         4.0.0b20 4.0.0b19 4.0.0b18 4.0.0b17 4.0.0b16 4.0.0b15; do
  if cut -f1 "$RESULTS" | grep -qx "$v"; then
    echo "$(date -Is) $v already recorded, skipping" | tee -a "$LOG"
    continue
  fi

  echo "$(date -Is) installing $v" | tee -a "$LOG"
  if ! uv pip install --python "$ROOT/venv/bin/python" --quiet \
        --reinstall-package flash-attn-4 --no-deps \
        "flash-attn-4==$v" >>"$LOG" 2>&1; then
    printf '%s\tINSTALL_FAILED\t\n' "$v" >>"$RESULTS"
    echo "$(date -Is) $v install failed" | tee -a "$LOG"
    continue
  fi

  dsl=$("$ROOT/venv/bin/python" -c \
    'from importlib.metadata import version; print(version("nvidia-cutlass-dsl"))' \
    2>/dev/null || echo "?")

  echo "$(date -Is) running $v (dsl $dsl)" | tee -a "$LOG"
  # -k: a kernel that never returns ignores SIGTERM, so follow up with a kill.
  CUDA_VISIBLE_DEVICES=$GPU timeout -k 10 180 "$ROOT/venv/bin/python" \
    "$SRC/_fa4_acceptance_one.py" $CFG >"$ROOT/run_$v.out" 2>&1
  rc=$?
  case $rc in
    0) verdict=COMPLETE ;;
    124) verdict=HANG ;;
    *) verdict="ERROR_rc$rc" ;;
  esac
  printf '%s\t%s\tdsl=%s\n' "$v" "$verdict" "$dsl" >>"$RESULTS"
  echo "$(date -Is) $v -> $verdict" | tee -a "$LOG"

  # No pkill by pattern here: the acceptance test on the other GPU runs the
  # same file per config, and a pattern kill would take its processes too.

  if [ "$verdict" = COMPLETE ]; then
    echo "$(date -Is) first completing release is $v — stopping" | tee -a "$LOG"
    break
  fi
done

echo "$(date -Is) walk finished" | tee -a "$LOG"
cat "$RESULTS"
