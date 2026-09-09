#!/usr/bin/env bash
set -euo pipefail

audit_checkout=${1:?checkout required}
audit_python=${2:?python interpreter required}
audit_credentials=${3:?credential environment file required}
audit_config=${4:?frozen config required}
audit_output=${5:?output directory required}

cd "$audit_checkout"
set -a
source "$audit_credentials"
set +a
export TRL_TT="$PWD" PYTHONPATH="$PWD"
export SWE_BOOT_RETRIES=1 TT_DAYTONA_CREATE_RETRIES=1
export SWE_BOOT_CONCURRENCY=4 TT_DAYTONA_CREATE_CONCURRENCY=4
exec "$audit_python" -u \
  torchtitan/experiments/rl/examples/tmax/evolution/seed_verifier_audit.py \
  --config "$audit_config" --output "$audit_output"
