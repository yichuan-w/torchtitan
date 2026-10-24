#!/usr/bin/env bash
# Run B's static agentpick recipe (submit_tmax9b_agentpick_v7r2.sh: LR 3e-6, 743-row
# clean+calibforge_clean mix, V7-R2 topology) with the evolve loop on this devvm.
# Harder arm only: agentpick has no easier canary fixtures, so simplification is off
# and all-fail signals are recorded as deferred.
#
#   bash mast_rl/run_tmax_evolving_agentpick.sh prepare
#   bash mast_rl/run_tmax_evolving_agentpick.sh probe
#   bash mast_rl/run_tmax_evolving_agentpick.sh sandbox-probe
#   bash mast_rl/run_tmax_evolving_agentpick.sh batch-canary
#   bash mast_rl/run_tmax_evolving_agentpick.sh start
#   STEPS=20 EVAL=0 bash mast_rl/run_tmax_evolving_agentpick.sh launch
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}
TMAX_LOCAL_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}

export RUN_TAG=${RUN_TAG:-agentpick-cleancf-evolve-lr3e6}
export TMAX_EVOLVE_SEED_PROFILE=agentpick-cleancf743
export TMAX_FBSOURCE=${TMAX_FBSOURCE:-/data/users/$(id -un)/fbsource_mg}
export SYNTH_MODEL=${SYNTH_MODEL:-gpt-5-6-sol-genai-responses}
export METAGEN_SUBMISSION_MODE=${METAGEN_SUBMISSION_MODE:-foreground}
export EVOLVE_WORKERS=${EVOLVE_WORKERS:-100}
export METAGEN_PROXY_MAX_CONCURRENCY=${METAGEN_PROXY_MAX_CONCURRENCY:-$EVOLVE_WORKERS}
export EVOLVE_INTERVAL_SEC=120
export CODEX_STREAM_MAX_RETRIES=5
export CODEX_REQUEST_MAX_RETRIES=3
export EVOLVE_AGENT_TIMEOUT=7200
export EVOLVE_WORK_ROOT=$TMAX_LOCAL_ROOT/.tmax_evolution_work/$RUN_TAG
export EVOLVE_REWRITE_BUDGET_SEC=${EVOLVE_REWRITE_BUDGET_SEC:-21600}
export EVOLVE_FINAL_VALIDATION_RESERVE_SEC=2400
export EVOLVE_STATUS_REFRESH_SEC=${EVOLVE_STATUS_REFRESH_SEC:-600}
export SWE_EVOLVE_SIMPLIFY=0
export EVOLVE_SPEC_REPAIR=0
export SWE_SIMPLIFY_HINT=vague
export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=16
export SWE_EVOLUTION_HARDER_RATIO=0.9
export SWE_POLICY_CANARY_K=0
export WANDB_RESTART_AWARE=1

# Canary evidence: B's sampled rollouts and lineage (static traces, no reference run).
EVIDENCE=${TMAX_AGENTPICK_CANARY_EVIDENCE:-/data/users/$(id -un)/scratch/agentpick/canary_evidence}
STORAGE_ROOT="$TMAX_LOCAL_ROOT/.torchtrain_datasets/tree/$(id -un)/tmax_evolution/$RUN_TAG"
export CANARY_REBENCH_ROLLOUTS="$EVIDENCE/rollout_samples.jsonl"
export CANARY_REBENCH_EVENTS="$EVIDENCE/events.jsonl"
export CANARY_REFERENCE_ROOT="$STORAGE_ROOT/root"
export CANARY_REFERENCE_RUN=unused

action=${1:-status}
shift || true
case "$action" in
    prepare)
        exec "$TMAX_ENV/bin/python" "$HERE/prepare_tmax_evolution.py" prepare \
            --run-tag "$RUN_TAG" --seed-profile "$TMAX_EVOLVE_SEED_PROFILE" "$@"
        ;;
    launch)
        UNIT="tmax-evolve-$(printf '%s' "$RUN_TAG" | tr -c 'A-Za-z0-9_.-' '-')"
        for unit in "$UNIT" "${UNIT}-wandb"; do
            systemctl --user is-active --quiet "$unit" || {
                echo "ERROR: $unit is not active; run '$0 start' first" >&2
                exit 1
            }
        done
        if systemctl --user is-active --quiet "${UNIT}-lifecycle"; then
            echo "ERROR: ${UNIT}-lifecycle is already tracking a MAST job" >&2
            exit 1
        fi
        state="$TMAX_LOCAL_ROOT/.tmax_evolution_state/$RUN_TAG"
        mkdir -p "$state"
        export MAST_JOB_NAME_FILE="$state/.mast-job-name.$$"
        EVOLVE=1 TAG="q35-9b-$RUN_TAG" STEPS=${STEPS:-200} EVAL=${EVAL:-1} \
            SWE_LR=${SWE_LR:-3e-6} SWE_GPU_MEM_LIMIT=${SWE_GPU_MEM_LIMIT:-0.8} \
            SWE_ROLLOUT_CONCURRENCY=${SWE_ROLLOUT_CONCURRENCY:-1500} \
            SWE_MAX_NUM_SEQS=${SWE_MAX_NUM_SEQS:-48} \
            SWE_EVAL_ROLLOUT_CONCURRENCY=${SWE_EVAL_ROLLOUT_CONCURRENCY:-445} \
            MAX_RETRIES=${MAX_RETRIES:-10} \
            bash /home/yichuan/submit_tmax9b_agentpick_v7r2.sh "$@"
        job=$(tr -d '[:space:]' <"$MAST_JOB_NAME_FILE")
        mv -f -- "$MAST_JOB_NAME_FILE" "$state/mast_job_name"
        echo "submitted $job; arming lifecycle watch"
        export SWE_TRAIN_STEPS=${STEPS:-200}
        exec bash "$HERE/tmax_evolution_sidecar.sh" watch-job "$job"
        ;;
    probe|sandbox-probe|batch-canary|batch-canary-verify|start-wandb|start|stop-evolution|stop|status|storage-check|logs)
        exec bash "$HERE/tmax_evolution_sidecar.sh" "$action" "$@"
        ;;
    *)
        echo "usage: $0 {prepare|probe|sandbox-probe|batch-canary|start|launch|stop|status|logs}" >&2
        exit 2
        ;;
esac
