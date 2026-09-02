#!/usr/bin/env bash
# Build the eval-host Python environment on flow-matic, pinned to the versions the
# della-tridao trainer runs, so TB-2.0 numbers from this host are comparable to the
# ones the training loop produces.
#
# The account has no home directory on this machine, so HOME is redirected into the
# work tree; every cache (uv, pip, HF, triton) follows it.
set -uo pipefail

W=/var/tmp/tw-eval
export HOME=$W/home
export UV_CACHE_DIR=$W/cache/uv
export HF_HOME=$W/cache/hf
export TRITON_CACHE_DIR=$W/cache/triton
export XDG_CACHE_HOME=$W/cache
mkdir -p "$HOME" "$UV_CACHE_DIR" "$HF_HOME" "$TRITON_CACHE_DIR" "$W/logs"

TS=$(date +%Y%m%d-%H%M%S)
LOG=$W/logs/setup-$TS.log
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

log "=== eval-host setup start (host=$(hostname)) ==="
log "workdir=$W  free=$(df -h $W | awk 'NR==2{print $4}')"

# --- uv -------------------------------------------------------------------
# This host's resolver intermittently fails to answer for astral.sh, so the
# installer is only the first of three routes to a uv binary.
UV=$HOME/.local/bin/uv
install_uv() {
  for attempt in 1 2 3; do
    log "uv bootstrap attempt $attempt: astral.sh installer"
    curl -LsSf --retry 3 --retry-all-errors --max-time 120 https://astral.sh/uv/install.sh 2>>"$LOG" \
      | env UV_INSTALL_DIR="$HOME/.local/bin" sh >>"$LOG" 2>&1
    [ -x "$UV" ] && return 0
    log "  installer route failed; trying PyPI"
    /usr/bin/python3 -m pip install --quiet --target "$HOME/.uvpkg" uv >>"$LOG" 2>&1
    if [ -x "$HOME/.uvpkg/bin/uv" ]; then
      mkdir -p "$HOME/.local/bin" && cp "$HOME/.uvpkg/bin/uv" "$UV" && chmod +x "$UV"
      [ -x "$UV" ] && return 0
    fi
    log "  PyPI route failed; trying GitHub release"
    curl -LsSf --retry 3 --max-time 180 \
      https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz \
      -o "$HOME/uv.tgz" >>"$LOG" 2>&1 \
      && tar -xzf "$HOME/uv.tgz" -C "$HOME" >>"$LOG" 2>&1 \
      && find "$HOME" -name uv -type f -perm -u+x 2>/dev/null | head -1 \
         | xargs -r -I{} sh -c 'mkdir -p '"$HOME"'/.local/bin && cp {} '"$UV"''
    [ -x "$UV" ] && return 0
    sleep 10
  done
  return 1
}
if ! [ -x "$UV" ]; then install_uv || { log "FATAL: all three uv routes failed"; exit 1; }; fi
log "uv: $("$UV" --version 2>&1)"

# --- venv -----------------------------------------------------------------
VENV=$W/venv
if [ ! -x "$VENV/bin/python" ]; then
  log "creating venv (python 3.12.13)"
  "$UV" venv --python 3.12.13 "$VENV" >>"$LOG" 2>&1 || { log "FATAL: venv creation failed"; exit 1; }
fi
log "python: $($VENV/bin/python -V 2>&1)"

# --- packages -------------------------------------------------------------
REQ=$W/repo/eval_host/requirements-della.txt
[ -f "$REQ" ] || REQ=$W/requirements-della.txt
log "installing from $REQ ($(grep -vcE '^\s*(#|$|--)' "$REQ") pinned packages)"
# --no-deps is deliberate: the pin list is the complete closure of the trainer's
# environment, so re-resolving only reintroduces conflicts the trainer itself does
# not have (torchstore pins torchmonarch==0.4.1; the trainer runs 0.6.0).
# --index-strategy: the torch nightly index also carries common PyPI packages at
# different versions, and uv's default would let it shadow PyPI for those names.
# Both indexes here are first-party (PyPI, download.pytorch.org).
"$UV" pip install --python "$VENV/bin/python" --no-deps \
  --index-strategy unsafe-best-match -r "$REQ" >>"$LOG" 2>&1
RC=$?
log "uv pip install exit=$RC"

# --- verify ---------------------------------------------------------------
log "=== verification ==="
"$VENV/bin/python" - <<'PY' 2>&1 | tee -a "$LOG"
import importlib, sys
def probe(mod, attr="__version__"):
    try:
        m = importlib.import_module(mod)
        print(f"  OK   {mod:<28} {getattr(m, attr, '?')}")
        return True
    except Exception as e:
        print(f"  FAIL {mod:<28} {type(e).__name__}: {str(e)[:90]}")
        return False
ok = all([probe("torch"), probe("vllm"), probe("transformers"),
          probe("fla"), probe("renderers"), probe("daytona")])
import torch
print(f"  cuda available: {torch.cuda.is_available()}  devices: {torch.cuda.device_count()}")
if torch.cuda.is_available():
    print(f"  device0: {torch.cuda.get_device_name(0)}  capability {torch.cuda.get_device_capability(0)}")
sys.exit(0 if ok else 1)
PY
VRC=$?
log "=== setup done (install=$RC verify=$VRC) log=$LOG ==="
exit $VRC
