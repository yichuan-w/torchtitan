#!/usr/bin/env bash
set -euo pipefail
oracle_output=${1:?Usage: run_release_oracle.sh OUTPUT prepare|run [sweep arguments]}
oracle_action=${2:?Missing action}
shift 2
oracle_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
oracle_repo=$(git -C "$oracle_here" rev-parse --show-toplevel)
cd "$oracle_repo"
export PYTHONPATH="$oracle_repo"
oracle_python=${TRL_VENV_PY:?Set TRL_VENV_PY to the validation virtualenv Python}
case "$oracle_action" in
  prepare)
    exec "$oracle_python" "$oracle_here/prepare_release_oracle.py" \
      --repository andylizf/TerminalWorld-Seeds-Clean \
      --revision e163d73559d30084ad472666cc42a656c7aba421 \
      --version 20260910-v2 --output "$oracle_output"
    ;;
  run)
    set -a
    source "$oracle_output/secrets/daytona.env"
    set +a
    export SWE_BOOT_CONCURRENCY=256
    export TT_DAYTONA_CREATE_CONCURRENCY=8
    export TT_DAYTONA_CPU=2 TT_DAYTONA_MEM_GB=4 TT_DAYTONA_DISK_GB=6
    export TMAX_TERMINUS_PARSER=xml
    exec "$oracle_python" "$oracle_here/environment_sweep.py" run \
      --output "$oracle_output/frozen" --label "$(basename "$oracle_output")" \
      --concurrency 256 "$@"
    ;;
  *) exit 2 ;;
esac
