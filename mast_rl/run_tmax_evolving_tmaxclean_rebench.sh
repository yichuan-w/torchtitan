#!/usr/bin/env bash
# Same training recipe as run_tmax_evolving_agentpick.sh (submit_tmax9b_agentpick_v7r2.sh:
# LR 3e-6, DPPO, V7-R2 topology, STEPS=200, EVAL=1 TB2.1), with three changes:
#   1. seed data: release 0e4f2cb8 = 1,317 SWE-ReBench (Fzz1/SWE-Rebench-Tasks-Clean@3eef12fd,
#      adapter swe_peaks) + 431 TMax longlongcheck (Fzz1/Tmax-Tasks-Clean@bb6cf8fd,
#      adapter tmax_reaudit); profile tmaxclean-rebench1748 in prepare_tmax_evolution.py.
#   2. both directions: harder (k/k) and easier/simplify (0/k, all 16 scored attempts).
#      The trainer already emits easier signals (SWE_EVOLUTION_EASIER_RATIO=0.0 marks
#      all-fail groups); SWE_EVOLVE_SIMPLIFY=1 makes the loop act on them, and the
#      sidecar's `start` refuses until `easier-canary` has passed.
#   3. EVOLVE_WORKERS=80 (and a MetaGen proxy concurrency of 80).
#
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh plan            # read-only
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh prepare
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh probe
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh sandbox-probe
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh batch-canary
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh easier-canary
#   bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh start          # old run's unit must be gone
#   STEPS=200 EVAL=1 bash mast_rl/run_tmax_evolving_tmaxclean_rebench.sh launch
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}
TMAX_LOCAL_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}
LOCAL_MOUNT=${TMAX_EVOLVE_LOCAL_MOUNT:-$TMAX_LOCAL_ROOT/.torchtrain_datasets}

export RUN_TAG=${RUN_TAG:-tmaxclean-rebench-evolve-both-1016}
export TMAX_EVOLVE_SEED_PROFILE=tmaxclean-rebench1748
export TMAX_FBSOURCE=${TMAX_FBSOURCE:-/data/users/$(id -un)/fbsource_mg}
export SYNTH_MODEL=${SYNTH_MODEL:-gpt-5-6-sol-genai-responses}
export METAGEN_SUBMISSION_MODE=${METAGEN_SUBMISSION_MODE:-foreground}
export EVOLVE_WORKERS=${EVOLVE_WORKERS:-80}
export METAGEN_PROXY_MAX_CONCURRENCY=${METAGEN_PROXY_MAX_CONCURRENCY:-$EVOLVE_WORKERS}
# Foreground preflight (probe / canaries) gets its own proxy port so it never lands on
# a live run's proxy at 18080. The systemd `serve` unit does not receive this variable
# and binds 18080, which is why `start` below requires 18080 to be free.
export METAGEN_PROXY_PORT=${METAGEN_PROXY_PORT:-18081}
export EVOLVE_INTERVAL_SEC=120
export CODEX_STREAM_MAX_RETRIES=5
export CODEX_REQUEST_MAX_RETRIES=3
export EVOLVE_AGENT_TIMEOUT=7200
export EVOLVE_WORK_ROOT=$TMAX_LOCAL_ROOT/.tmax_evolution_work/$RUN_TAG
export EVOLVE_REWRITE_BUDGET_SEC=${EVOLVE_REWRITE_BUDGET_SEC:-21600}
export EVOLVE_FINAL_VALIDATION_RESERVE_SEC=2400
export EVOLVE_STATUS_REFRESH_SEC=${EVOLVE_STATUS_REFRESH_SEC:-600}
export SWE_EVOLVE_SIMPLIFY=1
export EVOLVE_SPEC_REPAIR=${EVOLVE_SPEC_REPAIR:-0}
export SWE_SIMPLIFY_HINT=vague
export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=16
export SWE_EVOLUTION_HARDER_RATIO=0.9
export SWE_POLICY_CANARY_K=0
export WANDB_RESTART_AWARE=1

# Canary fixtures for the {swe-rebench, tmax} seed set (tmax_evolution_batch_canary.py):
# harder TMax cases replay the 431-row evolution run's signals (packages are byte-identical
# to this release), SWE-ReBench and all easier cases come from the static rebench+tmax run.
CANARY_OWNER_ROOT=$LOCAL_MOUNT/tree/shuhuay
export CANARY_REFERENCE_RUN=tmax-longlongcheck431-evolve-s150-v1
export CANARY_REFERENCE_ROOT=$CANARY_OWNER_ROOT/tmax_evolution/$CANARY_REFERENCE_RUN/root
export CANARY_REBENCH_STATIC_ROOT=$CANARY_OWNER_ROOT/mast_runs/q35_9b_rebench-tmax-static-s150-a3113-20260918
export CANARY_REBENCH_ROLLOUTS=$CANARY_REBENCH_STATIC_ROOT/rollout_samples.jsonl
export CANARY_REBENCH_EVENTS=$CANARY_REBENCH_STATIC_ROOT/training_lineage/events.jsonl

# Static copy of the release mix (gitignored). Under EVOLVE=1 the submit script points
# the trainer at the evolution root's live mix instead, whose rows are this file's rows.
export SWE_PROMPT_DATA=mast_rl/swe_assets/tmaxclean_bb6cf8fd_rebench_0e4f2cb8.jsonl

UNIT="tmax-evolve-$(printf '%s' "$RUN_TAG" | tr -c 'A-Za-z0-9_.-' '-')"

action=${1:-status}
shift || true
case "$action" in
    plan|check)
        exec "$TMAX_ENV/bin/python" "$HERE/prepare_tmax_evolution.py" "$action" \
            --run-tag "$RUN_TAG" --seed-profile "$TMAX_EVOLVE_SEED_PROFILE" "$@"
        ;;
    prepare)
        exec "$TMAX_ENV/bin/python" "$HERE/prepare_tmax_evolution.py" prepare \
            --run-tag "$RUN_TAG" --seed-profile "$TMAX_EVOLVE_SEED_PROFILE" "$@"
        ;;
    start)
        if ! systemctl --user is-active --quiet "$UNIT" && \
            ss -ltnH 'sport = :18080' 2>/dev/null | grep -q .; then
            echo "ERROR: 127.0.0.1:18080 is in use (another evolution unit's MetaGen proxy);" \
                "stop that run's evolution unit before starting $UNIT" >&2
            exit 1
        fi
        exec bash "$HERE/tmax_evolution_sidecar.sh" start "$@"
        ;;
    launch)
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
    probe|sandbox-probe|canary|batch-canary|batch-canary-verify|easier-canary|easier-canary-verify|start-wandb|stop-evolution|stop|status|storage-check|logs)
        exec bash "$HERE/tmax_evolution_sidecar.sh" "$action" "$@"
        ;;
    *)
        echo "usage: $0 {plan|check|prepare|probe|sandbox-probe|batch-canary|easier-canary|start|launch|stop|status|logs}" >&2
        exit 2
        ;;
esac
