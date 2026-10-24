#!/usr/bin/env bash
# agentpick BOTH-directions run. Same as run_tmax_evolving_agentpick_harder.sh
# (group 12, train 80K, eval 160K, eval turn cap 300, 80 workers, proxy 180) except:
#   - easier on: SWE_EVOLVE_SIMPLIFY=1, easier at 0/12 (SWE_EVOLUTION_EASIER_RATIO=0.0,
#     EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=8 of 12), needs a passing easier-canary
#   - harder at 12/12 only (SWE_EVOLUTION_HARDER_RATIO=1.0)
# Seed: agentpick-cleancf743 (341 agentpick-clean + 402 calibforge-clean, release 58d825a3).
# Use a fresh RUN_TAG and a fresh prepare for every new run.
#
#   L=mast_rl/run_tmax_evolving_agentpick_both.sh; export RUN_TAG=<new tag>
#   bash $L prepare; bash $L probe; bash $L sandbox-probe; bash $L batch-canary; bash $L easier-canary
#   bash $L start; STEPS=200 EVAL=1 bash $L launch
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}
TMAX_LOCAL_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}
LOCAL_MOUNT=${TMAX_EVOLVE_LOCAL_MOUNT:-$TMAX_LOCAL_ROOT/.torchtrain_datasets}

# REMINDER (2026-10-22): before the next run, switch the evolve MetaGen key to entitlement mg-api-c8868146bba4 (ask the user for the key file).
export RUN_TAG=${RUN_TAG:-agentpick-cleancf-evolve-both-1022}
export TMAX_EVOLVE_SEED_PROFILE=${TMAX_EVOLVE_SEED_PROFILE:-agentpick-cleancf743}
export TMAX_FBSOURCE=${TMAX_FBSOURCE:-/data/users/$(id -un)/fbsource_mg}
export SYNTH_MODEL=${SYNTH_MODEL:-gpt-5-6-sol-genai-responses}
export METAGEN_SUBMISSION_MODE=${METAGEN_SUBMISSION_MODE:-foreground}
export EVOLVE_WORKERS=${EVOLVE_WORKERS:-80}
export METAGEN_PROXY_MAX_CONCURRENCY=${METAGEN_PROXY_MAX_CONCURRENCY:-180}
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
export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=8          # of 12; up to 4 infra failures still count
export SWE_EVOLUTION_MIN_SCORED=${SWE_EVOLUTION_MIN_SCORED:-8}    # same floor for harder (trainer side)
export SWE_EVOLUTION_HARDER_RATIO=1.0                # harder only at 12/12
export SWE_EVOLUTION_EASIER_RATIO=0.0                # easier at 0/12
export SWE_POLICY_CANARY_K=0
export WANDB_RESTART_AWARE=1

# Canary evidence: run B's sampled rollouts and lineage on this same 743-row mix
# (static traces, no reference run), as in run_tmax_evolving_agentpick.sh.
EVIDENCE=${TMAX_AGENTPICK_CANARY_EVIDENCE:-/data/users/$(id -un)/scratch/agentpick/canary_evidence}
# Easier cases come from the agentpick big1015 evolve run (same 743-row seed, 0/16 groups
# with full per-rollout records); harder cases use run B's static evidence above.
EASIER_REFERENCE_TAG=agentpick-cleancf-evolve-big1015
export CANARY_REBENCH_ROLLOUTS="$EVIDENCE/rollout_samples.jsonl"
export CANARY_REBENCH_EVENTS="$EVIDENCE/events.jsonl"
export CANARY_REFERENCE_ROOT="$LOCAL_MOUNT/tree/$(id -un)/tmax_evolution/$EASIER_REFERENCE_TAG/root"
export CANARY_REFERENCE_RUN="$EASIER_REFERENCE_TAG--attempt-20261015-084454Z--24e0ac66c7a14cd19afc90e634df733d"

# --- Training / eval settings for this run (exported before the submit script so its
# ${VAR:-default} lines keep them). Smoke q35-9b-smoke-ctx80k-eval160k-af2f4f checked
# 80K train / 160K eval KV and memory on 64 + 24 single-GPU generators.
export SWE_GROUP_SIZE=${SWE_GROUP_SIZE:-12}                     # was 16
export SWE_TMAX_CONTEXT=${SWE_TMAX_CONTEXT:-81920}              # train max_model_len + packing width
export SWE_MAX_CONTEXT_LEN=${SWE_MAX_CONTEXT_LEN:-79872}        # train agent cap (81920 - 2048)
export SWE_LOSS_CHUNKS=${SWE_LOSS_CHUNKS:-40}                   # 81920/40 = 2048 tok/chunk
export SWE_EVAL_MAX_MODEL_LEN=${SWE_EVAL_MAX_MODEL_LEN:-163840} # eval generators only
export SWE_EVAL_MAX_CONTEXT_LEN=${SWE_EVAL_MAX_CONTEXT_LEN:-161792}
export SWE_NUM_EVAL_GENERATORS=${SWE_NUM_EVAL_GENERATORS:-24}   # 3 hosts x 8; 16 overloaded KV (1.4x slower/token, 2.5x more eval timeouts)
export SWE_EVAL_GPU_MEMORY_LIMIT=${SWE_EVAL_GPU_MEMORY_LIMIT:-0.8}
export SWE_EVAL_ROLLOUT_CONCURRENCY=${SWE_EVAL_ROLLOUT_CONCURRENCY:-445}  # one wave
export SWE_EVAL_MAX_TURNS=${SWE_EVAL_MAX_TURNS:-300}           # eval turn cap; no solve past 195 turns in the uncapped run
export SWE_EVAL_AGENT_BUDGET_CAP_SEC=${SWE_EVAL_AGENT_BUDGET_CAP_SEC:-5400}   # eval only: = the per-task floor; trims the 10 tasks budgeted 7200/12000s
export SWE_EVAL_VERIFIER_BUDGET_CAP_SEC=${SWE_EVAL_VERIFIER_BUDGET_CAP_SEC:-900}  # eval only: no solved trial graded >860s
export SWE_RESHUFFLE_EACH_EPOCH=${SWE_RESHUFFLE_EACH_EPOCH:-1}   # reshuffle each epoch, as in 1018
export TMAX_CONTEXT_NOTICE_STYLE=${TMAX_CONTEXT_NOTICE_STYLE:-della}  # train + eval; 1018 ran della, 1020/1021 checked
export SWE_VAL_INTERVAL=${SWE_VAL_INTERVAL:-25}                # eval every 25 steps (one pass ~1.5-2h)

# Static copy of the release mix (gitignored). Under EVOLVE=1 the submit script points
# the trainer at the evolution root's live mix instead, whose rows are this file's rows.
export SWE_PROMPT_DATA=${SWE_PROMPT_DATA:-mast_rl/swe_assets/agentpick_clean_cf_58d825a3.jsonl}

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
            ss -ltnH "sport = :${EVOLVE_SERVE_PORT:-18080}" 2>/dev/null | grep -q .; then
            echo "ERROR: 127.0.0.1:${EVOLVE_SERVE_PORT:-18080} is in use (another evolution unit's MetaGen proxy; set EVOLVE_SERVE_PORT);" \
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
        EVOLVE=1 TAG="${LAUNCH_TAG:-q35-9b-$RUN_TAG}" STEPS=${STEPS:-200} EVAL=${EVAL:-1} \
            SWE_LR=${SWE_LR:-3e-6} \
            SWE_ROLLOUT_CONCURRENCY=${SWE_ROLLOUT_CONCURRENCY:-2000} \
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
