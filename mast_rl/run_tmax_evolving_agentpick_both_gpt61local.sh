#!/usr/bin/env bash
# 1022 (run_tmax_evolving_agentpick_both.sh) with one change: rewrite sessions run
# on this devvm's codex CLI (internet mode) with gpt-6.1-sol at effort medium,
# instead of codex -> MetaGen GPT-5.6. The loop's chat calls still use the MetaGen
# proxy, billed to entitlement mg-api-c8868146bba4. Same workers / concurrency.
#
#   L=mast_rl/run_tmax_evolving_agentpick_both_gpt61local.sh
#   bash $L prepare; bash $L probe; bash $L sandbox-probe; bash $L batch-canary; bash $L easier-canary
#   bash $L start; STEPS=200 EVAL=1 bash $L launch
set -euo pipefail
export RUN_TAG=${RUN_TAG:-agentpick-cleancf-evolve-both-1023-gpt61local}
# MAST caps task names at 78 chars; the job name prefix must stay short.
export LAUNCH_TAG=${LAUNCH_TAG:-q35-9b-ap-evolve-both-1023-gpt61local}
export EVOLVE_CODEX_HARNESS=local
export EVOLVE_CODEX_LOCAL_BIN=${EVOLVE_CODEX_LOCAL_BIN:-$HOME/rl/.cache/codex-local-internet/codex}
export EVOLVE_CODEX_MODEL=${EVOLVE_CODEX_MODEL:-gpt-6.1-sol}
export METAGEN_ENV_FILE=$HOME/.metagen_env_c886
unset METAGEN_API_KEY  # ~/.bashrc exports the shared key; load it from the file above
exec bash "$(dirname "${BASH_SOURCE[0]}")/run_tmax_evolving_agentpick_both.sh" "$@"
