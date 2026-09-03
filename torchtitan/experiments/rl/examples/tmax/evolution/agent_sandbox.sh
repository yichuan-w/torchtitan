#!/bin/bash
# The sandbox a task-evolution agent works in: `./sandbox help` lists the
# subcommands. Thin wrapper: sources the platform credentials and hands every
# argument to agent_sandbox.py in the harness directory. evolve_codex.py copies
# this file into the agent's package as ./sandbox, so from there $HERE holds no
# agent_sandbox.py; the caller exports EVOLVE_HARNESS_DIR, and $HERE stays as
# the fallback for running the script in place.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PY=${TRL_VENV_PY:-/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python}
# shellcheck disable=SC1091
. ~/.config/daytona/env
if [ "${1:-help}" = help ] || [ "${1:-}" = -h ] || [ "${1:-}" = --help ]; then
    "$PY" "${EVOLVE_HARNESS_DIR:-$HERE}/agent_sandbox.py" --help
    exit 0
fi
exec "$PY" "${EVOLVE_HARNESS_DIR:-$HERE}/agent_sandbox.py" --pkg "$HERE" "$@"
