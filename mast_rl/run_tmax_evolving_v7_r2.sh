#!/usr/bin/env bash
# Mixed-seed evolution with separate defect repair and normal training feedback.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}

export RUN_TAG=rebench-tmax-reaudit452-evolve-codex-s150-v7-r2
export TMAX_RUN_RECIPE="bash mast_rl/run_tmax_evolving_v7_r2.sh"
export TMAX_EVOLVE_SEED_PROFILE=rebench-tmax-reaudit452
export SYNTH_MODEL=gpt-5-6-sol-genai-responses
export METAGEN_SUBMISSION_MODE=foreground
export EVOLVE_WORKERS=100
export METAGEN_PROXY_MAX_CONCURRENCY=100
export TT_DAYTONA_CREATE_CONCURRENCY=8
export EVOLVE_INTERVAL_SEC=120
export CODEX_STREAM_MAX_RETRIES=5
export CODEX_REQUEST_MAX_RETRIES=3
export EVOLVE_AGENT_TIMEOUT=7200
export EVOLVE_WORK_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}/.tmax_evolution_work/$RUN_TAG
export EVOLVE_REWRITE_BUDGET_SEC=21600
export EVOLVE_FINAL_VALIDATION_RESERVE_SEC=2400
export EVOLVE_STATUS_REFRESH_SEC=60
export EVOLVE_SPEC_REPAIR=1
export SWE_EVOLVE_SIMPLIFY=1
export SWE_SIMPLIFY_HINT=vague
export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=16
export SWE_EVOLUTION_HARDER_RATIO=0.9
# Publish validated rewrites without dedicated student-policy rollouts.
export SWE_POLICY_CANARY_K=0
export SWE_NUM_EVAL_GENERATORS=2
export SWE_NUM_EVAL_ROLLOUT_WORKERS=16
export SWE_EVAL_ROLLOUT_CONCURRENCY=128
export SWE_CKPT_INTERVAL=2
export SWE_CKPT_FIRST_STEP=1
export SWE_CKPT_CONTROLLER_DATA=1
export WANDB_RESTART_AWARE=1

action=${1:-plan}
shift || true
case "$action" in
    plan|validate|launch)
        exec bash "$HERE/submit_tmax_evolving_150.sh" "--$action" "$@"
        ;;
    prepare)
        exec "$TMAX_ENV/bin/python" "$HERE/prepare_tmax_evolution.py" prepare \
            --run-tag "$RUN_TAG" --seed-profile "$TMAX_EVOLVE_SEED_PROFILE" "$@"
        ;;
    probe|sandbox-probe|canary|batch-canary|batch-canary-verify|easier-canary|easier-canary-verify|start-wandb|start|stop-evolution|stop|status|storage-check|logs)
        exec bash "$HERE/tmax_evolution_sidecar.sh" "$action" "$@"
        ;;
    *)
        echo "usage: $0 {plan|prepare|probe|sandbox-probe|batch-canary|easier-canary|validate|start|launch|stop|status|logs}" >&2
        exit 2
        ;;
esac
