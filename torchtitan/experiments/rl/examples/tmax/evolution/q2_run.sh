#!/usr/bin/env bash
set -euo pipefail
q2_campaign=${1:?campaign directory required}
q2_run=${2:?run ID required}
q2_here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
q2_python=$(jq -r .python "$q2_campaign/execution.json")
q2_credentials=$(jq -r .daytona_env "$q2_campaign/execution.json")
set -a
source "$q2_credentials"
set +a
export DAYTONA_ENV_FILE="$q2_credentials"
exec "$q2_python" -u "$q2_here/q2_rewrite.py" --campaign "$q2_campaign" --run-id "$q2_run"
