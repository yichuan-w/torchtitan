#!/bin/bash
# The sandbox a task-evolution agent works in: `./sandbox help` lists the
# subcommands. Thin wrapper: sources the platform credentials and hands every
# argument to agent_sandbox.py in the harness directory. evolve_codex.py copies
# this file into the agent's package as ./sandbox, so from there $HERE holds no
# agent_sandbox.py; the caller exports EVOLVE_HARNESS_DIR, and $HERE stays as
# the fallback for running the script in place.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The python that runs agent_sandbox.py. Prefer an explicit override, then the
# training venv ($TRL_VENV), then whatever python3 is on PATH -- never a
# machine-specific absolute path, so this runs on any host the loop is launched from.
PY=${TRL_VENV_PY:-}
if [ -z "$PY" ]; then
    if [ -n "${TRL_VENV:-}" ] && [ -x "$TRL_VENV/bin/python" ]; then
        PY="$TRL_VENV/bin/python"
    else
        PY=$(command -v python3 || command -v python) || {
            echo "agent_sandbox: no python found (set TRL_VENV_PY or TRL_VENV)" >&2
            exit 3
        }
    fi
fi
# Platform credentials: source the daytona env file if the host has one, else
# rely on DAYTONA_API_KEY etc. already being in the environment (the loop unit
# exports them). A missing file is not an error.
# shellcheck disable=SC1091
[ -f "${DAYTONA_ENV_FILE:-$HOME/.config/daytona/env}" ] && . "${DAYTONA_ENV_FILE:-$HOME/.config/daytona/env}"
if [ "${1:-help}" = help ] || [ "${1:-}" = -h ] || [ "${1:-}" = --help ]; then
    "$PY" "${EVOLVE_HARNESS_DIR:-$HERE}/agent_sandbox.py" --help
    exit 0
fi
exec "$PY" "${EVOLVE_HARNESS_DIR:-$HERE}/agent_sandbox.py" --pkg "$HERE" "$@"
