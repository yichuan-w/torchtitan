#!/usr/bin/env bash
# Launch the rebench c1000 3t4g1e run from an env overlay.
#
#   run_rebench_c1000_3t4g1e.sh --dry-run    # lay the run out, start nothing
#   run_rebench_c1000_3t4g1e.sh              # start training
#   REBENCH_ENV=/path/other.env run_rebench_c1000_3t4g1e.sh --dry-run
#
# Arguments go to launch_9b.sh unchanged. The env file is not an argument, so a
# flag can never be taken for it.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=${REBENCH_ENV:-"$HERE/rebench_c1000_3t4g1e.b300.env"}

set -a
. "$ENV_FILE"
set +a

exec bash "$TRL_TT/torchtitan/experiments/rl/examples/tmax/runbook/launch_9b.sh" "$@"
