#!/usr/bin/env bash
# Fixed entry point for the fifth TMax-only online-hardening experiment.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMAX_ENV=${TMAX_ENV:-/home/shuhuay/.conda/envs/tmax}

export RUN_TAG=tmax-longlongcheck431-evolve-codex-s150-v5
export EVOLVE_WORKERS=20
export METAGEN_PROXY_MAX_CONCURRENCY=12
export CODEX_STREAM_MAX_RETRIES=5
export CODEX_REQUEST_MAX_RETRIES=3
export EVOLVE_AGENT_TIMEOUT=7200
export EVOLVE_WORK_ROOT=/home/shuhuay/rl/.tmax_evolution_work/$RUN_TAG
export EVOLVE_REWRITE_BUDGET_SEC=21600
export EVOLVE_FINAL_VALIDATION_RESERVE_SEC=2400
export EVOLVE_STATUS_REFRESH_SEC=60
export SWE_NUM_EVAL_GENERATORS=2
export SWE_NUM_EVAL_ROLLOUT_WORKERS=16
export SWE_EVAL_ROLLOUT_CONCURRENCY=128

action=${1:-plan}
shift || true
case "$action" in
    plan)
        exec bash "$HERE/submit_tmax_evolving_150.sh" --plan "$@"
        ;;
    prepare)
        exec "$TMAX_ENV/bin/python" \
            "$HERE/prepare_tmax_evolution.py" prepare --run-tag "$RUN_TAG" "$@"
        ;;
    validate)
        exec bash "$HERE/submit_tmax_evolving_150.sh" --validate "$@"
        ;;
    launch)
        exec bash "$HERE/submit_tmax_evolving_150.sh" --launch "$@"
        ;;
    probe|sandbox-probe|canary|batch-canary|batch-canary-verify|start-wandb|start|stop|status|storage-check|logs)
        exec bash "$HERE/tmax_evolution_sidecar.sh" "$action" "$@"
        ;;
    *)
        echo "usage: $0 {plan|prepare|probe|sandbox-probe|batch-canary|validate|start-wandb|start|launch|stop|status|logs}" >&2
        exit 2
        ;;
esac
