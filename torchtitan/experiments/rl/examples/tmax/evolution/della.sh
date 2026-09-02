#!/usr/bin/env bash
# Run a command on della from the laptop, without retyping the askpass/hop plumbing.
#
#   della.sh 'cmd'          # on della-gpu (login node; sees /scratch/gpfs shared NFS)
#   della.sh -t 'cmd'       # on della-tridao (8xB300 box) via the internal hop
#   della.sh -p script.py   # copy script.py to della-gpu and run it under the
#                           # training venv with ~/.config/daytona/env sourced
#
# Why the plumbing: della is pubkey+password 2FA, so ssh needs SSH_ASKPASS
# forced (the password comes from ~/.ssh/della-askpass.sh). della-tridao has no
# tailnet node of its own by design, so process/GPU checks there must go through
# della-gpu -- a direct alias silently resolves to the 1xA100 login node and has
# twice produced false "training died" reports.
set -euo pipefail

VENV_PY=${TRL_VENV_PY:-/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python}
REMOTE_TMP=${DELLA_REMOTE_TMP:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/tmp/laptop-scripts}

export SSH_ASKPASS="$HOME/.ssh/della-askpass.sh"
export SSH_ASKPASS_REQUIRE=force
export DISPLAY=${DISPLAY:-:0}
SSH_OPTS=(-o ConnectTimeout=20 -o BatchMode=no)

usage() { sed -n '2,12p' "$0"; exit 1; }

MODE=login
case "${1:-}" in
  -t|--tridao) MODE=tridao; shift ;;
  -p|--python) MODE=python; shift ;;
  -h|--help|"") usage ;;
esac
[ $# -ge 1 ] || usage

case "$MODE" in
  login)
    exec ssh "${SSH_OPTS[@]}" della-ts "$*"
    ;;
  tridao)
    # Quote once for the outer shell so the inner hop receives the command intact.
    printf -v inner '%q' "$*"
    exec ssh "${SSH_OPTS[@]}" della-ts "ssh -o ConnectTimeout=15 della-tridao $inner"
    ;;
  python)
    src=$1; [ -f "$src" ] || { echo "no such script: $src" >&2; exit 2; }
    dst="$REMOTE_TMP/$(basename "$src")"
    ssh "${SSH_OPTS[@]}" della-ts "mkdir -p $REMOTE_TMP"
    scp "${SSH_OPTS[@]}" -q "$src" "della-ts:$dst"
    shift
    exec ssh "${SSH_OPTS[@]}" della-ts \
      "set -a; . ~/.config/daytona/env; set +a; $VENV_PY $dst $*"
    ;;
esac
