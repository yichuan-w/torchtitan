#!/usr/bin/env bash
# Qwen3.5-9B online-hardening run on H100 MAST.
#
# The default action is read-only and prints the complete resolved plan. The
# --validate action checks the prepared root and parsed config without submitting.
# A full submission requires the explicit --launch flag and a successful canary.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
# Operator identity: local state, storage prefix and W&B entity derive from it.
TMAX_OPERATOR=${TMAX_OPERATOR:-$(id -un)}
TMAX_LOCAL_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}
TMAX_WANDB_ENTITY=${TMAX_WANDB_ENTITY:-$TMAX_OPERATOR}
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}
LOCAL_MOUNT=${TMAX_EVOLVE_LOCAL_MOUNT:-$TMAX_LOCAL_ROOT/.torchtrain_datasets}
MAST_MOUNT=${TMAX_EVOLVE_MAST_MOUNT:-/mnt/torchtrain_datasets}
STORAGE_PREFIX=${TMAX_EVOLVE_STORAGE_PREFIX:-tree/$TMAX_OPERATOR/tmax_evolution}
RUN_TAG=${RUN_TAG:-tmax-longlongcheck431-evolve-codex-s150-v2}
TMAX_EVOLVE_SEED_PROFILE=${TMAX_EVOLVE_SEED_PROFILE:-tmax-longlongcheck431}
SYNTH_MODEL=${SYNTH_MODEL:-gpt-5-6-sol-genai-background-mode}
if [[ -z "${METAGEN_SUBMISSION_MODE:-}" ]]; then
    if [[ "$SYNTH_MODEL" == *-genai-responses ]]; then
        METAGEN_SUBMISSION_MODE=foreground
    else
        METAGEN_SUBMISSION_MODE=background
    fi
fi
LOCAL_STATE_ROOT=${TMAX_EVOLVE_LOCAL_STATE_ROOT:-$TMAX_LOCAL_ROOT/.tmax_evolution_state/$RUN_TAG}
EVOLVE_WORKERS=${EVOLVE_WORKERS:-20}
EVOLVE_INTERVAL_SEC=${EVOLVE_INTERVAL_SEC:-120}
METAGEN_PROXY_MAX_CONCURRENCY=${METAGEN_PROXY_MAX_CONCURRENCY:-$EVOLVE_WORKERS}
EVOLVE_AGENT_TIMEOUT=${EVOLVE_AGENT_TIMEOUT:-7200}
EVOLVE_WORK_ROOT=${EVOLVE_WORK_ROOT:-$TMAX_LOCAL_ROOT/.tmax_evolution_work/$RUN_TAG}
EVOLVE_REWRITE_BUDGET_SEC=${EVOLVE_REWRITE_BUDGET_SEC:-}
EVOLVE_FINAL_VALIDATION_RESERVE_SEC=${EVOLVE_FINAL_VALIDATION_RESERVE_SEC:-2400}
EVOLVE_STATUS_REFRESH_SEC=${EVOLVE_STATUS_REFRESH_SEC:-60}
if [[ -z "${SWE_EVOLVE_SIMPLIFY+x}" ]]; then
    if [[ "$TMAX_EVOLVE_SEED_PROFILE" == rebench-tmax-reaudit452 ]]; then
        SWE_EVOLVE_SIMPLIFY=1
    else
        SWE_EVOLVE_SIMPLIFY=0
    fi
fi
SWE_SIMPLIFY_HINT=${SWE_SIMPLIFY_HINT:-vague}
EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=${EVOLVE_EASIER_MIN_SCORED_ATTEMPTS:-16}
EVOLVE_SPEC_REPAIR=${EVOLVE_SPEC_REPAIR:-0}
SWE_POLICY_CANARY_K=${SWE_POLICY_CANARY_K:-0}
SWE_POLICY_CANARY_MAX_CONCURRENCY=${SWE_POLICY_CANARY_MAX_CONCURRENCY:-4}
SWE_POLICY_CANARY_MAX_VERSION_SPAN=${SWE_POLICY_CANARY_MAX_VERSION_SPAN:-5}
SWE_POLICY_CANARY_TIMEOUT_SEC=${SWE_POLICY_CANARY_TIMEOUT_SEC:-7200}
SWE_POLICY_CANARY_POLL_SEC=${SWE_POLICY_CANARY_POLL_SEC:-10}
EXPERIMENT_REL="${STORAGE_PREFIX}/${RUN_TAG}"
LOCAL_EXPERIMENT_DIR="${LOCAL_MOUNT}/${EXPERIMENT_REL}"
SIDECAR_UNIT="tmax-evolve-$(printf '%s' "$RUN_TAG" | tr -c 'A-Za-z0-9_.-' '-')"
LIFECYCLE_UNIT="${SIDECAR_UNIT}-lifecycle"
MAST_JOB_RECORD="${LOCAL_STATE_ROOT}/mast_job_name"
export TRL_BASE="${MAST_MOUNT}/${EXPERIMENT_REL}/root"
export TRL_RUN_DIR="${TRL_BASE}/runs/${RUN_TAG}"
export TMAX_EVOLVE_SEED_PROFILE
export SYNTH_MODEL
export METAGEN_SUBMISSION_MODE

case "$TMAX_EVOLVE_SEED_PROFILE" in
    tmax-longlongcheck431)
        SEED_DESCRIPTION="431-row repaired TMax longlongcheck release"
        SEED_COMPOSITION="431 TMax"
        SEED_RELEASE_REVISION=ef0a100cd93cd2ecd316adb30eb3819ec3f37201
        SEED_RELEASE_SHA256=3d0f1eedc6e68c15e67b67cd8a59c2d763de0c8efa19d4f002083ff7cc17c328
        SEED_SOLUTION_FORMAT="recorded terminal actions (gpt6_actions.json)"
        ;;
    rebench-tmax-reaudit452)
        SEED_DESCRIPTION="static-baseline-matched SWE-ReBench and TMax release"
        SEED_COMPOSITION="1,317 SWE-ReBench + 452 TMax = 1,769 rows"
        SEED_RELEASE_REVISION="pinned source manifests"
        SEED_RELEASE_SHA256=14fd9af954deeee889f2be1d94d897ba10bc2e64abab008ba2052711a0393c67
        SEED_SOLUTION_FORMAT="solution/solve.sh for both corpora"
        ;;
    *)
        echo "ERROR: unsupported TMAX_EVOLVE_SEED_PROFILE: $TMAX_EVOLVE_SEED_PROFILE" >&2
        exit 2
        ;;
esac

for value_name in EVOLVE_WORKERS EVOLVE_INTERVAL_SEC METAGEN_PROXY_MAX_CONCURRENCY EVOLVE_AGENT_TIMEOUT EVOLVE_FINAL_VALIDATION_RESERVE_SEC EVOLVE_STATUS_REFRESH_SEC; do
    value=${!value_name}
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "ERROR: $value_name must be a positive integer, got '$value'" >&2
        exit 2
    fi
done
if [[ ! "$EVOLVE_EASIER_MIN_SCORED_ATTEMPTS" =~ ^[0-9]+$ ]] || \
    (( EVOLVE_EASIER_MIN_SCORED_ATTEMPTS < 2 )); then
    echo "ERROR: EVOLVE_EASIER_MIN_SCORED_ATTEMPTS must be at least 2" >&2
    exit 2
fi
if [[ "$SWE_EVOLVE_SIMPLIFY" != 0 && "$SWE_EVOLVE_SIMPLIFY" != 1 ]]; then
    echo "ERROR: SWE_EVOLVE_SIMPLIFY must be 0 or 1" >&2
    exit 2
fi
if [[ "$EVOLVE_SPEC_REPAIR" != 0 && "$EVOLVE_SPEC_REPAIR" != 1 ]]; then
    echo "ERROR: EVOLVE_SPEC_REPAIR must be 0 or 1" >&2
    exit 2
fi
if [[ "$METAGEN_SUBMISSION_MODE" != background && \
    "$METAGEN_SUBMISSION_MODE" != foreground ]]; then
    echo "ERROR: METAGEN_SUBMISSION_MODE must be background or foreground" >&2
    exit 2
fi
if [[ -n "$EVOLVE_REWRITE_BUDGET_SEC" && ! "$EVOLVE_REWRITE_BUDGET_SEC" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: EVOLVE_REWRITE_BUDGET_SEC must be a positive integer" >&2
    exit 2
fi
if [[ -n "$EVOLVE_WORK_ROOT" && "$EVOLVE_WORK_ROOT" != /* ]]; then
    echo "ERROR: EVOLVE_WORK_ROOT must be absolute: $EVOLVE_WORK_ROOT" >&2
    exit 2
fi

ACTION=plan
if [[ "${1:-}" == "--launch" ]]; then
    ACTION=launch
    shift
elif [[ "${1:-}" == "--validate" ]]; then
    ACTION=validate
    shift
elif [[ "${1:-}" == "--plan" ]]; then
    shift
fi

export PATH="$TMAX_ENV/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export CONDA_PREFIX="$TMAX_ENV"
export CONDA_DEFAULT_ENV=tmax
export RLMAST_ENV=tmax
export TORCHTITAN_SRC="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SWE_RUN=1
export SWE_GDN=1
export SWE_AGENT_MODE=host_loop

# Data and evolution contract.
export SWE_PROMPT_DATA="${TRL_BASE}/data/mix/live.jsonl"
export SWE_HOLDOUT_N=0
export SWE_ROLLOUT_RECORDS=1
export SWE_EVOLUTION_SIGNALS=1
export SWE_EVOLUTION_HARDER_RATIO=0.9
export SWE_EVOLUTION_EASIER_RATIO=0.0
export SWE_DATA_HOT_RELOAD=1
export SWE_EVOLVE_SIMPLIFY
export SWE_SIMPLIFY_HINT
export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS
export EVOLVE_SPEC_REPAIR
export SWE_POLICY_CANARY_K SWE_POLICY_CANARY_MAX_CONCURRENCY
export SWE_POLICY_CANARY_MAX_VERSION_SPAN SWE_POLICY_CANARY_TIMEOUT_SEC
export SWE_POLICY_CANARY_POLL_SEC

# Terminus-2 rollout behavior.
export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768
export TMAX_EXEC_TIMEOUT_SEC=120
export TMAX_EVAL_TIMEOUT_SEC=600
export SWE_TIME_BUDGET_SEC=2400
export SWE_AGENT_TIMEOUT_FLOOR_SEC=7200

# Trainer: three HSDP replicas, each sharded over one eight-GPU host.
export SWE_DP_SHARD=8
export SWE_DP_REPLICATE=3

# Rollout serving: eight independent MAST generator roles. Each role occupies
# one host and runs DP=8, TP=1, for 64 single-GPU replicas in total.
export SWE_GEN_DP=8
export SWE_NUM_GENERATORS=8
export SWE_ROLLOUT_CONCURRENCY=1500
export SWE_NUM_ROLLOUT_WORKERS=16
export SWE_MAX_NUM_SEQS=32
export SWE_GEN_BACKEND=torchtitan_wrapper
export SWE_GEN_CUDAGRAPH=1
export SWE_GEN_PREFIX_CACHE=1
export SWE_SALT_KV=1
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export SWE_DP_FALLBACK_ROUTER=leastloaded
export SWE_DP_STICKY_REBALANCE=0

# Batch construction and optimization.
export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0
export SWE_OFFPOLICY_STEPS=5
export SWE_MAX_ACTIVE_GROUPS=160
export SWE_INITIAL_ACTIVE_GROUPS=160
export SWE_SELECTION_WINDOW_GROUPS=""
export SWE_MAX_BYPASS_GROUPS=off
export SWE_STRICT_FIFO=0
export SWE_DISABLE_SHUFFLE=0
export SWE_LOSS=dppo
export SWE_DPPO_DIVERGENCE_THRESHOLD=0.1
export SWE_DPPO_RATIO_CAP=0
export SWE_LR=0.000001
export SWE_LOSS_CHUNKS=32
export SWE_REWARD_DENSE=0
export SWE_TRAIN_STEPS=150

# Checkpoint and fixed TB2.1 validation schedule.
export SWE_DUMP_DIR="${TRL_RUN_DIR}/trainer"
export SWE_CKPT_INTERVAL=${SWE_CKPT_INTERVAL:-5}
export SWE_CKPT_FIRST_STEP=${SWE_CKPT_FIRST_STEP:-0}
export SWE_CKPT_CONTROLLER_DATA=${SWE_CKPT_CONTROLLER_DATA:-1}
export SWE_CKPT_KEEP=2
export SWE_CKPT_ASYNC=async
export SWE_TB2_VAL_DATA=mast_rl/swe_assets/runbook_20260909/tb2_1_eval.jsonl
export SWE_VAL_INTERVAL=20
export SWE_VAL_SAMPLES=89
export SWE_TB2_VAL_K=5
export SWE_TB2_VAL_TEMPERATURE=1.0
export SWE_TB2_VAL_TOP_P=1.0
export TMAX_CONTEXT_NOTICE_STYLE=della
export TMAX_CONTEXT_WARN_FRAC=0.5
export SWE_NUM_EVAL_GENERATORS="${SWE_NUM_EVAL_GENERATORS:-1}"
export SWE_EVAL_GEN_DP=8
export SWE_NUM_EVAL_ROLLOUT_WORKERS="${SWE_NUM_EVAL_ROLLOUT_WORKERS:-8}"
export SWE_EVAL_ROLLOUT_CONCURRENCY="${SWE_EVAL_ROLLOUT_CONCURRENCY:-64}"
export SWE_VAL_TRACES=1

# Daytona and MAST reliability settings.
export TT_DAYTONA_EPHEMERAL=1
export TT_DAYTONA_CPU=2
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_CREATE_CONCURRENCY=8
export TT_DAYTONA_CREATE_RETRIES=8
export TT_DAYTONA_HEARTBEAT_SEC=180
export TT_DAYTONA_AUTO_DELETE_MIN=15
export SWE_PREFLIGHT_FANOUT=4
export NCCL_TRANSPORT=IB
export MONARCH_SUPERVISION_WATCHDOG_TIMEOUT=1000s
export MONARCH_HOST_SPAWN_READY_TIMEOUT=180s
export MONARCH_MESSAGE_DELIVERY_TIMEOUT=300s
export MONARCH_MESH_ORPHAN_TIMEOUT=24h
export MONARCH_MESH_PROC_SPAWN_MAX_IDLE=24h

print_plan() {
    local root_state=not-prepared canary_state=not-run easier_canary_state=not-run
    local sidecar_state=inactive
    local lifecycle_state=inactive wandb_state=inactive wandb_id=not-created
    local student_feedback="normal training rollouts"
    local student_admission="disabled (0 extra rollouts)"
    local student_probe_concurrency=disabled student_probe_policy_span=disabled
    if (( SWE_POLICY_CANARY_K > 0 )); then
        student_feedback="paired student-policy admission before publication"
        student_admission="$SWE_POLICY_CANARY_K original + $SWE_POLICY_CANARY_K candidate"
        student_probe_concurrency="$SWE_POLICY_CANARY_MAX_CONCURRENCY candidates (controller orchestration, training generators)"
        student_probe_policy_span="at most $SWE_POLICY_CANARY_MAX_VERSION_SPAN optimizer versions"
    fi
    local prepare_command="python mast_rl/prepare_tmax_evolution.py prepare --run-tag $RUN_TAG"
    local sidecar_command="bash mast_rl/tmax_evolution_sidecar.sh"
    local launch_command="bash mast_rl/submit_tmax_evolving_150.sh --launch"
    if [[ -n "${TMAX_RUN_RECIPE:-}" ]]; then
        prepare_command="$TMAX_RUN_RECIPE prepare"
        sidecar_command="$TMAX_RUN_RECIPE"
        launch_command="$TMAX_RUN_RECIPE launch"
    fi
    [[ -f "$LOCAL_EXPERIMENT_DIR/root/experiment.json" ]] && root_state=prepared
    [[ -s "$LOCAL_EXPERIMENT_DIR/preflight/canary-batch.ok" ]] && canary_state=passed
    [[ -s "$LOCAL_EXPERIMENT_DIR/preflight/canary-easier.ok" ]] \
        && easier_canary_state=passed
    if systemctl --user is-active --quiet "tmax-evolve-${RUN_TAG}" 2>/dev/null; then
        sidecar_state=active
    fi
    if systemctl --user is-active --quiet \
        "tmax-evolve-${RUN_TAG}-wandb" 2>/dev/null; then
        wandb_state=active
    fi
    if systemctl --user is-active --quiet "$LIFECYCLE_UNIT" 2>/dev/null; then
        lifecycle_state=active
    fi
    [[ -s "$LOCAL_EXPERIMENT_DIR/preflight/wandb_mirror_id" ]] \
        && wandb_id=$(tr -d '[:space:]' \
            <"$LOCAL_EXPERIMENT_DIR/preflight/wandb_mirror_id")
    cat <<EOF
Evolving run plan (nothing submitted)
  git_commit:                 $(git -C "$REPO_ROOT" rev-parse HEAD)
  run_tag:                    $RUN_TAG
  seed_profile:               $TMAX_EVOLVE_SEED_PROFILE
  seed:                       $SEED_DESCRIPTION
  seed_composition:           $SEED_COMPOSITION
  seed_release_revision:      $SEED_RELEASE_REVISION
  seed_release_sha256:        $SEED_RELEASE_SHA256
  seed_solution_format:       $SEED_SOLUTION_FORMAT
  production_root_on_MAST:    $TRL_BASE
  root_state:                 $root_state
  hardening_dry_canary:       $canary_state (3-5 independent seeds)
  easier_dry_canary:          $easier_canary_state (3-5 independent all-failed groups)
  evolution_sidecar:          $sidecar_state (devvm, workers=$EVOLVE_WORKERS, interval=${EVOLVE_INTERVAL_SEC}s)
  MAST_lifecycle_watch:       $lifecycle_state (armed automatically by launch)
  metagen_proxy_concurrency:   $METAGEN_PROXY_MAX_CONCURRENCY
  evolution_model:            $SYNTH_MODEL
  metagen_submission_mode:    $METAGEN_SUBMISSION_MODE
  evolution_arm:              codex (student-trace guided through MetaGen Responses)
  evolution_effort:           medium
  evolution_workers:          $EVOLVE_WORKERS
  evolution_work_root:        ${EVOLVE_WORK_ROOT:-in-place}
  rewrite_session_timeout:    ${EVOLVE_AGENT_TIMEOUT}s
  rewrite_end_to_end_budget:  ${EVOLVE_REWRITE_BUDGET_SEC:-dynamic}${EVOLVE_REWRITE_BUDGET_SEC:+s}
  final_validation_reserve:   ${EVOLVE_FINAL_VALIDATION_RESERVE_SEC}s
  status_refresh_interval:    ${EVOLVE_STATUS_REFRESH_SEC}s
  evolution_repair_rounds:    3
  hardening_threshold:        15/16 (ratio 0.9)
  easier_arm:                 $SWE_EVOLVE_SIMPLIFY (1=enabled, 0=disabled)
  easier_trigger:             exactly 0/16 scored rollouts
  easier_min_scored_attempts: $EVOLVE_EASIER_MIN_SCORED_ATTEMPTS
  easier_hint_level:          $SWE_SIMPLIFY_HINT
  specification_repair:       $EVOLVE_SPEC_REPAIR (separate from simplification)
  student_difficulty_feedback: $student_feedback
  student_admission_trials:   $student_admission
  student_probe_concurrency:  $student_probe_concurrency
  student_probe_policy_span:  $student_probe_policy_span
  hot_reload:                 enabled (polls live mix about every 20s)

  trainer:                    HSDP=3 x FSDP=8 = 24 H100s / 3 nodes
  training_generators:        8 nodes x DP=8 x TP=1 = 64 H100s
  validation_generators:      $SWE_NUM_EVAL_GENERATORS nodes x DP=8 x TP=1
  controller:                 1 node
  total_allocation:           $((12 + SWE_NUM_EVAL_GENERATORS)) H100 nodes / $(((12 + SWE_NUM_EVAL_GENERATORS) * 8)) allocated GPUs
  active_model_workers:       $((88 + SWE_NUM_EVAL_GENERATORS * 8)) GPUs (controller host has no model worker)

  optimizer_steps:            150, fresh base weights
  batch_per_step:             32 groups x 16 rollouts = 512 rollouts
  rollout_concurrency:        1500
  rollout_workers:            16
  active_groups:              initial=160, max=160
  max_offpolicy_steps:        5
  group_selection:            take-any (no selection window)
  zero_variance_groups:       retained for scheduling/signals; skipped in loss
  loss/lr:                    DPPO (TV=0.1, no ratio cap), lr=1e-6
  precision:                  FP32 master, BF16 compute, FP32 reduction
  activation_checkpointing:   full
  prefix_cache/salted_KV:     enabled/enabled
  DP fallback/sticky rebalance: leastloaded/disabled

  agent:                      Terminus-2, max_turns=120
  context/per_turn_tokens:    63488 / 32768
  context_notice:             $TMAX_CONTEXT_NOTICE_STYLE at $TMAX_CONTEXT_WARN_FRAC of budget
  task/agent_time_budget:     2400s / floor 7200s
  command/verifier_timeout:   120s / 600s
  checkpointing:              every $SWE_CKPT_INTERVAL steps, first-step=$SWE_CKPT_FIRST_STEP, keep 2, async/full-state
  preemption recovery:        dataset cursor + pending prompts; unique rollout attempt paths
  rewrite restart gate:       expiring lease + fresh training heartbeat
  validation:                 TB2.1 89 tasks x 5, every 20 steps, async, temp=1.0/top_p=1.0
  validation routing:         DP=8, TP=1, workers=$SWE_NUM_EVAL_ROLLOUT_WORKERS, concurrency=$SWE_EVAL_ROLLOUT_CONCURRENCY
  W&B on MAST:                offline files under $SWE_DUMP_DIR/wandb
  W&B UI:                     devvm autosync every 60s to $TMAX_WANDB_ENTITY/torchtitan
  W&B restart recovery:       ${WANDB_RESTART_AWARE:-0} (1=per-attempt cursor, rollback-safe history)
  W&B autosync:               $wandb_state (mirror id: $wandb_id)

Preparation sequence (still does not submit training):
  $prepare_command
  $sidecar_command probe
  $sidecar_command sandbox-probe
  $sidecar_command batch-canary
  $sidecar_command easier-canary

After we approve this plan:
  $sidecar_command start
  $launch_command
EOF
}

print_plan
if [[ "$ACTION" == plan ]]; then
    exit 0
fi

"$TMAX_ENV/bin/python" "$HERE/prepare_tmax_evolution.py" check \
    --run-tag "$RUN_TAG" --local-mount "$LOCAL_MOUNT" \
    --storage-prefix "$STORAGE_PREFIX" \
    --seed-profile "$TMAX_EVOLVE_SEED_PROFILE" >/dev/null
if [[ "$ACTION" == validate ]]; then
    "$TMAX_ENV/bin/python" "$HERE/validate_tmax_evolving_config.py"
    exit 0
fi
"$TMAX_ENV/bin/python" "$HERE/validate_tmax_evolving_config.py" >/dev/null
[[ -s "$LOCAL_EXPERIMENT_DIR/preflight/canary-batch.ok" ]] || {
    echo "ERROR: the multi-seed dry evolution canary has not passed" >&2
    exit 1
}
grep -Fq "model=$SYNTH_MODEL" \
    "$LOCAL_EXPERIMENT_DIR/preflight/canary-batch.ok" || {
    echo "ERROR: the hardening canary did not validate $SYNTH_MODEL" >&2
    exit 1
}
grep -Fq "submission_mode=$METAGEN_SUBMISSION_MODE" \
    "$LOCAL_EXPERIMENT_DIR/preflight/canary-batch.ok" || {
    echo "ERROR: the hardening canary did not validate $METAGEN_SUBMISSION_MODE mode" >&2
    exit 1
}
if [[ "$SWE_EVOLVE_SIMPLIFY" == 1 && \
    ! -s "$LOCAL_EXPERIMENT_DIR/preflight/canary-easier.ok" ]]; then
    echo "ERROR: the multi-seed easier dry evolution canary has not passed" >&2
    exit 1
fi
if [[ "$SWE_EVOLVE_SIMPLIFY" == 1 ]]; then
    if [[ "$EVOLVE_SPEC_REPAIR" == 1 ]] && ! grep -Fq 'spec_repair=1' \
        "$LOCAL_EXPERIMENT_DIR/preflight/canary-easier.ok"; then
        echo "ERROR: rerun the easier canary with specification repair enabled" >&2
        exit 1
    fi
    grep -Fq "model=$SYNTH_MODEL" \
        "$LOCAL_EXPERIMENT_DIR/preflight/canary-easier.ok" || {
        echo "ERROR: the easier canary did not validate $SYNTH_MODEL" >&2
        exit 1
    }
    grep -Fq "submission_mode=$METAGEN_SUBMISSION_MODE" \
        "$LOCAL_EXPERIMENT_DIR/preflight/canary-easier.ok" || {
        echo "ERROR: the easier canary did not validate $METAGEN_SUBMISSION_MODE mode" >&2
        exit 1
    }
fi
systemctl --user is-active --quiet "tmax-evolve-${RUN_TAG}" || {
    echo "ERROR: start the evolution sidecar before submitting training" >&2
    exit 1
}
systemctl --user is-active --quiet "tmax-evolve-${RUN_TAG}-wandb" || {
    echo "ERROR: start the W&B autosync sidecar before submitting training" >&2
    exit 1
}
if systemctl --user is-active --quiet "$LIFECYCLE_UNIT"; then
    echo "ERROR: $LIFECYCLE_UNIT is already tracking another MAST job" >&2
    exit 1
fi
if [[ -z "${DAYTONA_API_KEY:-}" && -s $HOME/.daytona_env ]]; then
    set -a
    # shellcheck disable=SC1091
    source $HOME/.daytona_env
    set +a
fi
: "${DAYTONA_API_KEY:?set DAYTONA_API_KEY before submitting}"

LAUNCHER_ARGS=(
    --launcher.job-name-prefix "q35-9b-${RUN_TAG}"
    --launcher.host-type grandteton_80g_roce
    --launcher.region pci
    --launcher.wandb-team "$TMAX_WANDB_ENTITY"
    --launcher.wandb-project torchtitan
    --launcher.max-retries 10
)

cd "$REPO_ROOT"
mkdir -p "$LOCAL_STATE_ROOT"
job_capture=$(mktemp "${LOCAL_STATE_ROOT}/.mast-job-name.XXXXXX")
trap 'rm -f -- "$job_capture"' EXIT
export MAST_JOB_NAME_FILE="$job_capture"

set +e
bash mast_rl/submit_swe_tmax_9b.sh "${LAUNCHER_ARGS[@]}" "$@"
submit_status=$?
set -e

if [[ ! -s "$job_capture" ]]; then
    bash "$HERE/tmax_evolution_sidecar.sh" stop-evolution
    echo "ERROR: submission did not record a MAST job; evolution was stopped" >&2
    if [[ "$submit_status" -ne 0 ]]; then
        exit "$submit_status"
    fi
    exit 1
fi

job_name=$(tr -d '[:space:]' <"$job_capture")
if [[ ! "$job_name" =~ ^[A-Za-z0-9_-]+$ ]]; then
    bash "$HERE/tmax_evolution_sidecar.sh" stop-evolution
    echo "ERROR: launcher recorded invalid MAST job name '$job_name'; evolution was stopped" >&2
    exit 1
fi
mv -f -- "$job_capture" "$MAST_JOB_RECORD"

if ! bash "$HERE/tmax_evolution_sidecar.sh" watch-job "$job_name"; then
    bash "$HERE/tmax_evolution_sidecar.sh" stop-evolution
    echo "ERROR: MAST job $job_name was submitted, but lifecycle monitoring failed; evolution was stopped" >&2
    exit 1
fi

if [[ "$submit_status" -ne 0 ]]; then
    exit "$submit_status"
fi
exit 0
