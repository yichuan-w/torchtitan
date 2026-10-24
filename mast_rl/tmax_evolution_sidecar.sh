#!/usr/bin/env bash
# Run the TMax evolution loop on the devvm against the MAST shared root.
# MetaGen remains local to this host; only the OpenAI-compatible loop client
# talks to the localhost bridge.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
# Operator identity: local state, storage prefix and W&B entity derive from it.
TMAX_OPERATOR=${TMAX_OPERATOR:-$(id -un)}
TMAX_LOCAL_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}
TMAX_WANDB_ENTITY=${TMAX_WANDB_ENTITY:-$TMAX_OPERATOR}
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}
FBSOURCE=${TMAX_FBSOURCE:-${FBSOURCE:-/data/users/$TMAX_OPERATOR/fbsource}}
LOCAL_MOUNT=${TMAX_EVOLVE_LOCAL_MOUNT:-$TMAX_LOCAL_ROOT/.torchtrain_datasets}
STORAGE_PREFIX=${TMAX_EVOLVE_STORAGE_PREFIX:-tree/$TMAX_OPERATOR/tmax_evolution}
RUN_TAG=${RUN_TAG:-tmax-longlongcheck431-evolve-codex-s150-v2}
TMAX_EVOLVE_SEED_PROFILE=${TMAX_EVOLVE_SEED_PROFILE:-tmax-longlongcheck431}
LOCAL_STATE_ROOT=${TMAX_EVOLVE_LOCAL_STATE_ROOT:-$TMAX_LOCAL_ROOT/.tmax_evolution_state/$RUN_TAG}
EXPERIMENT_DIR="${LOCAL_MOUNT}/${STORAGE_PREFIX}/${RUN_TAG}"
PRODUCTION_ROOT="${EXPERIMENT_DIR}/root"
CANARY_ROOT="${EXPERIMENT_DIR}/canary-root"
CANARY_BATCH_ROOT="${EXPERIMENT_DIR}/canary-batch/root"
CANARY_EASIER_ROOT="${EXPERIMENT_DIR}/canary-easier/root"
CANARY_BATCH_SIZE=${CANARY_BATCH_SIZE:-4}
CANARY_REWRITE_BUDGET_SEC=${CANARY_REWRITE_BUDGET_SEC:-28800}
CANARY_REFERENCE_ROOT=${CANARY_REFERENCE_ROOT:-${LOCAL_MOUNT}/${STORAGE_PREFIX}/tmax-longlongcheck431-evolve-s150-v1/root}
CANARY_REFERENCE_RUN=${CANARY_REFERENCE_RUN:-tmax-longlongcheck431-evolve-s150-v1}
CANARY_REBENCH_STATIC_ROOT=${CANARY_REBENCH_STATIC_ROOT:-${LOCAL_MOUNT}/tree/shuhuay/mast_runs/q35_9b_rebench-tmax-static-s150-a3113-20260918}
CANARY_REBENCH_ROLLOUTS=${CANARY_REBENCH_ROLLOUTS:-${CANARY_REBENCH_STATIC_ROOT}/rollout_samples.jsonl}
CANARY_REBENCH_EVENTS=${CANARY_REBENCH_EVENTS:-${CANARY_REBENCH_STATIC_ROOT}/training_lineage/events.jsonl}
PORT=${METAGEN_PROXY_PORT:-18080}
MODEL=${SYNTH_MODEL:-gpt-5-6-sol-genai-background-mode}
if [[ -z "${METAGEN_SUBMISSION_MODE:-}" ]]; then
    if [[ "$MODEL" == *-genai-responses ]]; then
        METAGEN_SUBMISSION_MODE=foreground
    else
        METAGEN_SUBMISSION_MODE=background
    fi
fi
EVOLVE_WORKERS=${EVOLVE_WORKERS:-20}
EVOLVE_INTERVAL_SEC=${EVOLVE_INTERVAL_SEC:-120}
# codex and the Python sandbox client each start a tokio runtime with one worker
# per visible core (368 here); the agents mostly wait on the model and sandboxes.
EVOLVE_TOKIO_WORKER_THREADS=${EVOLVE_TOKIO_WORKER_THREADS:-4}
EVOLVE_CPU_AFFINITY=${EVOLVE_CPU_AFFINITY:-0-31}
METAGEN_PROXY_MAX_CONCURRENCY=${METAGEN_PROXY_MAX_CONCURRENCY:-$EVOLVE_WORKERS}
CODEX_STREAM_MAX_RETRIES=${CODEX_STREAM_MAX_RETRIES:-5}
CODEX_REQUEST_MAX_RETRIES=${CODEX_REQUEST_MAX_RETRIES:-3}
EVOLVE_AGENT_TIMEOUT=${EVOLVE_AGENT_TIMEOUT:-7200}
EVOLVE_WORK_ROOT=${EVOLVE_WORK_ROOT:-$TMAX_LOCAL_ROOT/.tmax_evolution_work/$RUN_TAG}
PRODUCTION_REWRITE_BUDGET_SEC=${EVOLVE_REWRITE_BUDGET_SEC:-}
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
STORAGE_PROBE_TIMEOUT_SEC=${TMAX_EVOLVE_STORAGE_PROBE_TIMEOUT_SEC:-15}
MAST_STATUS_POLL_SEC=${TMAX_MAST_STATUS_POLL_SEC:-60}
MAST_STATUS_QUERY_TIMEOUT_SEC=${TMAX_MAST_STATUS_QUERY_TIMEOUT_SEC:-30}
ACTION=${1:-status}
UNIT="tmax-evolve-$(printf '%s' "$RUN_TAG" | tr -c 'A-Za-z0-9_.-' '-')"
WANDB_UNIT="${UNIT}-wandb"
LIFECYCLE_UNIT="${UNIT}-lifecycle"
WANDB_RUN_NAME="q35-9b-${RUN_TAG}"
WANDB_BUCKET_WANDB="torchtrain_datasets/${STORAGE_PREFIX}/${RUN_TAG}/root/runs/${RUN_TAG}/trainer/wandb"
WANDB_RUN_ID_FILE="${EXPERIMENT_DIR}/preflight/wandb_mirror_id"
MAST_LIFECYCLE_STATUS_FILE="${LOCAL_STATE_ROOT}/mast_lifecycle.json"
OPTIMIZATION_COMPLETE_MARKER="${PRODUCTION_ROOT}/runs/${RUN_TAG}/trainer/training_lineage/optimization_complete.json"
TRAINING_ACTIVITY="${PRODUCTION_ROOT}/runs/${RUN_TAG}/trainer/training_lineage/training_activity.json"
ADMISSION_FILE="${LOCAL_STATE_ROOT}/rewrite_admission.json"

for value_name in EVOLVE_WORKERS EVOLVE_INTERVAL_SEC METAGEN_PROXY_MAX_CONCURRENCY EVOLVE_AGENT_TIMEOUT EVOLVE_FINAL_VALIDATION_RESERVE_SEC EVOLVE_STATUS_REFRESH_SEC STORAGE_PROBE_TIMEOUT_SEC CANARY_REWRITE_BUDGET_SEC MAST_STATUS_POLL_SEC MAST_STATUS_QUERY_TIMEOUT_SEC; do
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
if [[ -n "$PRODUCTION_REWRITE_BUDGET_SEC" && ! "$PRODUCTION_REWRITE_BUDGET_SEC" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: EVOLVE_REWRITE_BUDGET_SEC must be a positive integer" >&2
    exit 2
fi
if [[ -n "$EVOLVE_WORK_ROOT" && "$EVOLVE_WORK_ROOT" != /* ]]; then
    echo "ERROR: EVOLVE_WORK_ROOT must be absolute: $EVOLVE_WORK_ROOT" >&2
    exit 2
fi
for value_name in CODEX_STREAM_MAX_RETRIES CODEX_REQUEST_MAX_RETRIES; do
    value=${!value_name}
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "ERROR: $value_name must be nonnegative, got '$value'" >&2
        exit 2
    fi
done
if [[ ! "$CANARY_BATCH_SIZE" =~ ^[3-5]$ ]]; then
    echo "ERROR: CANARY_BATCH_SIZE must be between 3 and 5" >&2
    exit 2
fi

storage_is_mounted() {
    [[ "$(findmnt -rn -M "$LOCAL_MOUNT" -o SOURCE 2>/dev/null || true)" == \
        manifold://torchtrain_datasets ]]
}

storage_probe() {
    local probe_dir="${LOCAL_MOUNT}/${STORAGE_PREFIX}/.storage-health"
    timeout "$STORAGE_PROBE_TIMEOUT_SEC" "$TMAX_ENV/bin/python" - "$probe_dir" <<'PY'
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
probe_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex}"
target = directory / f"{probe_id}.json"
incoming = target.with_name(target.name + ".incoming")
payload = {"probe": probe_id, "written_at_ns": time.time_ns()}
try:
    incoming.write_text(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(incoming, target)
    if json.loads(target.read_text()) != payload:
        raise RuntimeError("storage probe read did not match its write")
except Exception as error:
    print(f"storage probe failed: {type(error).__name__}: {error}", file=sys.stderr)
    raise SystemExit(1) from None
finally:
    for path in (incoming, target):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
PY
}

mount_storage() {
    local lock_file=$TMAX_LOCAL_ROOT/.tmax_evolution_mount.lock
    local source
    mkdir -p "$(dirname "$LOCAL_MOUNT")" "$(dirname "$lock_file")"
    exec {mount_lock_fd}>"$lock_file"
    flock -x "$mount_lock_fd"

    if storage_is_mounted && storage_probe; then
        flock -u "$mount_lock_fd"
        exec {mount_lock_fd}>&-
        return 0
    fi

    source=$(findmnt -rn -M "$LOCAL_MOUNT" -o SOURCE 2>/dev/null || true)
    if [[ -n "$source" ]]; then
        if [[ "$source" != manifold://torchtrain_datasets ]]; then
            echo "ERROR: refusing to detach unexpected mount at $LOCAL_MOUNT: $source" >&2
            return 1
        fi
        echo "OilFS health probe failed; detaching stale mount at $LOCAL_MOUNT" >&2
        /usr/bin/fusermount3 -uz "$LOCAL_MOUNT"
    fi

    mkdir -p "$LOCAL_MOUNT"
    /usr/local/bin/oilfs --profile manifold \
        --uri manifold://torchtrain_datasets \
        --mount "$LOCAL_MOUNT" --no-sudo
    for _ in $(seq 1 30); do
        if storage_is_mounted && storage_probe >/dev/null 2>&1; then
            flock -u "$mount_lock_fd"
            exec {mount_lock_fd}>&-
            echo "OilFS mount is healthy at $LOCAL_MOUNT" >&2
            return 0
        fi
        sleep 1
    done
    echo "ERROR: OilFS did not become healthy at $LOCAL_MOUNT" >&2
    return 1
}

root_state_key() {
    local root=$1
    local relative
    if [[ "$root" != "$EXPERIMENT_DIR"/* ]]; then
        echo "ERROR: evolution root is outside $EXPERIMENT_DIR: $root" >&2
        return 1
    fi
    relative=${root#"$EXPERIMENT_DIR"/}
    printf '%s\n' "${relative//\//_}"
}

metagen_state_dir() {
    local root=$1
    printf '%s/%s/metagen_requests\n' "$LOCAL_STATE_ROOT" "$(root_state_key "$root")"
}

metagen_log_file() {
    local root=$1
    printf '%s/%s/metagen_proxy.log\n' "$LOCAL_STATE_ROOT" "$(root_state_key "$root")"
}

load_daytona_credentials() {
    local daytona_env=${DAYTONA_ENV_FILE:-$HOME/.daytona_env}
    if [[ -z "${DAYTONA_API_KEY:-}" && -s "$daytona_env" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$daytona_env"
        set +a
    fi
    : "${DAYTONA_API_KEY:?set DAYTONA_API_KEY or place it in $daytona_env}"
    # The loop's final Daytona probe sources this file itself (feedback_loop.py,
    # default ~/.config/daytona/env); a systemd unit does not inherit it from
    # the login shell, and without it every structural rewrite is rejected as
    # daytona_unavailable.
    if [[ -s "$daytona_env" ]]; then
        export DAYTONA_ENV_FILE="$daytona_env"
    fi
}

load_credentials() {
    local metagen_env=${METAGEN_ENV_FILE:-$HOME/.metagen_env}
    if [[ -z "${METAGEN_API_KEY:-}" && -s "$metagen_env" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$metagen_env"
        set +a
    fi
    : "${METAGEN_API_KEY:?set METAGEN_API_KEY or place it in $metagen_env}"
    load_daytona_credentials
}

metagen_bootstrap() {
    local target=fbcode//gen_ai/web_search/serving_v2/scripts:metagen_cli
    local output artifact
    output=$(cd "$FBSOURCE" && /usr/local/bin/buck2 build "$target" --show-output)
    artifact=$(printf '%s\n' "$output" | awk -v target="$target" '$1 == target {print $2}')
    if [[ -z "$artifact" ]]; then
        echo "ERROR: could not resolve the MetaGen Python runtime" >&2
        return 1
    fi
    printf '%s/%s#link-tree/_bootstrap.sh\n' "$FBSOURCE" "${artifact%.par}"
}

check_root() {
    mount_storage
    "$TMAX_ENV/bin/python" "$HERE/prepare_tmax_evolution.py" check \
        --run-tag "$RUN_TAG" --local-mount "$LOCAL_MOUNT" \
        --storage-prefix "$STORAGE_PREFIX" \
        --seed-profile "$TMAX_EVOLVE_SEED_PROFILE" >/dev/null
}

configure_local_lock() {
    local root=$1
    local lock_dir=$TMAX_LOCAL_ROOT/.tmax_evolution_locks
    local local_lock state_key
    state_key=$(root_state_key "$root")
    local_lock="${lock_dir}/${RUN_TAG}-${state_key}.lock"
    local shared_lock="${root}/evolution/loop.lock"
    local holder_pid
    mkdir -p "$lock_dir" "$(dirname "$shared_lock")"
    touch "$local_lock"
    chmod 600 "$local_lock"
    if [[ -e "$shared_lock" && ! -L "$shared_lock" ]]; then
        holder_pid=$(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$shared_lock" 2>/dev/null)
        if [[ -n "$holder_pid" ]] && kill -0 "$holder_pid" 2>/dev/null; then
            echo "ERROR: evolution lock is held by pid $holder_pid" >&2
            return 1
        fi
        rm -f -- "$shared_lock"
    elif [[ -L "$shared_lock" ]]; then
        rm -f -- "$shared_lock"
    fi
    ln -s "$local_lock" "$shared_lock"
}

configure_evolution() {
    local root=$1
    local host_name
    if [[ "$root" == "$PRODUCTION_ROOT" ]]; then
        export EVOLVE_ADMISSION_STATUS_FILE="$ADMISSION_FILE"
    else
        unset EVOLVE_ADMISSION_STATUS_FILE || true
    fi
    configure_local_lock "$root"
    export PATH="$TMAX_ENV/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
    export CONDA_PREFIX="$TMAX_ENV"
    export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
    export TRL_PROFILE=${TRL_PROFILE:-$TMAX_OPERATOR-mast}
    export TRL_TT="$REPO_ROOT"
    export TRL_BASE="$root"
    test -x "$TRL_BASE/bin/codex"
    test -x "$TRL_BASE/bin/jq"
    export SWE_RETUNE_AGENT=codex
    if [[ "$root" == "$CANARY_EASIER_ROOT" ]]; then
        export SWE_EVOLVE_SIMPLIFY=1
        export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=4
    else
        export SWE_EVOLVE_SIMPLIFY
        export EVOLVE_EASIER_MIN_SCORED_ATTEMPTS
    fi
    export SWE_SIMPLIFY_HINT
    export EVOLVE_SPEC_REPAIR
    export SWE_POLICY_CANARY_K SWE_POLICY_CANARY_MAX_CONCURRENCY
    export SWE_POLICY_CANARY_MAX_VERSION_SPAN SWE_POLICY_CANARY_TIMEOUT_SEC
    export SWE_POLICY_CANARY_POLL_SEC
    export SWE_EVOLUTION_HARDER_RATIO=${SWE_EVOLUTION_HARDER_RATIO:-0.9}
    export EVOLVE_HARDER_OPERATORS=0
    export EVOLVE_REPAIR_ROUNDS=3
    export CODEX_RETUNE_TIMEOUT=${CODEX_RETUNE_TIMEOUT:-600}
    export CODEX_RETUNE_MAX_CALLS=${CODEX_RETUNE_MAX_CALLS:-25}
    export EVOLVE_AGENT_TIMEOUT
    export EVOLVE_WORK_ROOT
    export EVOLVE_FINAL_VALIDATION_RESERVE_SEC
    export EVOLVE_STATUS_REFRESH_SEC
    export EVOLVE_CODEX_DRIVER=exec
    export EVOLVE_CODEX_LOCAL_STATE_ROOT=${EVOLVE_CODEX_LOCAL_STATE_ROOT:-$TMAX_LOCAL_ROOT/.tmax_codex_state/$RUN_TAG}
    if [[ "$root" == "$CANARY_BATCH_ROOT" || "$root" == "$CANARY_EASIER_ROOT" ]]; then
        export EVOLVE_REWRITE_BUDGET_SEC="$CANARY_REWRITE_BUDGET_SEC"
    elif [[ -n "$PRODUCTION_REWRITE_BUDGET_SEC" ]]; then
        export EVOLVE_REWRITE_BUDGET_SEC="$PRODUCTION_REWRITE_BUDGET_SEC"
    else
        unset EVOLVE_REWRITE_BUDGET_SEC || true
    fi
    unset EVOLVE_CODEX_AUTH_FILE EVOLVE_CODEX_ACCOUNT_HOME || true
    unset CODEX_BASE_URL CODEX_THREAD_ID CODEX_SESSION_ID || true
    unset META_CODEX_USE_AZURE_DIRECT || true
    export SYNTH_API_BASE="http://127.0.0.1:${PORT}/v1"
    export SYNTH_MODEL="$MODEL"
    export SYNTH_EFFORT=medium
    export SYNTH_TIMEOUT_SEC=${SYNTH_TIMEOUT_SEC:-7500}
    export SYNTH_MAX_RETRIES=${SYNTH_MAX_RETRIES:-0}
    export SYNTH_CONSISTENCY=1
    export SYNTH_ORACLE_REPAIR=1
    export METAGEN_MAX_OUTPUT_TOKENS=${METAGEN_MAX_OUTPUT_TOKENS:-32768}
    # Meta devvms require the provider id "responses". The public TMax code
    # also defines "oai"; these later overrides select the local MetaGen facade.
    export EVOLVE_CODEX_EXTRA_CONFIG="model_providers.oai.wire_api=responses model_providers.oai.stream_max_retries=$CODEX_STREAM_MAX_RETRIES model_providers.oai.request_max_retries=$CODEX_REQUEST_MAX_RETRIES model_providers.oai.stream_idle_timeout_ms=7500000 model_providers.responses.name=metagen model_providers.responses.base_url=$SYNTH_API_BASE model_providers.responses.env_key=OPENAI_API_KEY model_providers.responses.wire_api=responses model_providers.responses.stream_max_retries=$CODEX_STREAM_MAX_RETRIES model_providers.responses.request_max_retries=$CODEX_REQUEST_MAX_RETRIES model_providers.responses.stream_idle_timeout_ms=7500000 model_provider=responses"
    export OPENAI_API_KEY=local-metagen-proxy
    # EVOLVE_CODEX_HARNESS=local: rewrite sessions run this devvm's own codex CLI
    # (internet-mode wrapper, model EVOLVE_CODEX_MODEL); chat calls stay on the proxy.
    if [[ "${EVOLVE_CODEX_HARNESS:-}" == local ]]; then
        export EVOLVE_CODEX_HARNESS
        export EVOLVE_CODEX_LOCAL_BIN=${EVOLVE_CODEX_LOCAL_BIN:-$TMAX_LOCAL_ROOT/.cache/codex-local-internet/codex}
        export EVOLVE_CODEX_MODEL=${EVOLVE_CODEX_MODEL:-gpt-6.1-sol}
        unset EVOLVE_CODEX_EXTRA_CONFIG
    fi
    export TT_SANDBOX_BACKEND=daytona
    export TT_DAYTONA_CPU=2
    export TT_DAYTONA_MEM_GB=4
    export TT_DAYTONA_DISK_GB=10
    export TT_DAYTONA_EPHEMERAL=1
    export TT_DAYTONA_CREATE_CONCURRENCY=8
    export TT_DAYTONA_CREATE_RETRIES=8
    export TT_DAYTONA_HEARTBEAT_SEC=180
    export TT_DAYTONA_AUTO_DELETE_MIN=15
    export https_proxy=${SWE_PROXY:-http://fwdproxy:8080}
    export http_proxy="$https_proxy"
    host_name=$(hostname)
    export no_proxy="127.0.0.1,localhost,$host_name"
    export NO_PROXY="$no_proxy"
}

run_with_proxy() {
    local root=$1
    shift
    local bootstrap proxy_pid state_dir log_file
    load_credentials
    configure_evolution "$root"
    state_dir=$(metagen_state_dir "$root")
    log_file=$(metagen_log_file "$root")
    mkdir -p "$state_dir" "$(dirname "$log_file")"
    bootstrap=$(metagen_bootstrap)
    METAGEN_PROXY_STATE_DIR="$state_dir" \
        METAGEN_PROXY_MAX_CONCURRENCY="$METAGEN_PROXY_MAX_CONCURRENCY" \
        METAGEN_REQUEST_TIMEOUT_SEC=7200 \
        METAGEN_API_KEY="$METAGEN_API_KEY" \
        "$bootstrap" "$HERE/metagen_chat_proxy.py" \
            --host 127.0.0.1 --port "$PORT" --model "$MODEL" \
            --submission-mode "$METAGEN_SUBMISSION_MODE" \
            >>"$log_file" 2>&1 &
    proxy_pid=$!
    cleanup_proxy() {
        if [[ -n "${proxy_pid:-}" ]]; then
            kill "$proxy_pid" 2>/dev/null || true
            wait "$proxy_pid" 2>/dev/null || true
        fi
    }
    trap cleanup_proxy EXIT INT TERM
    for _ in $(seq 1 120); do
        if curl --noproxy '*' -fsS "http://127.0.0.1:${PORT}/health" >/dev/null; then
            break
        fi
        if ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "ERROR: MetaGen proxy exited; see $log_file" >&2
            return 1
        fi
        sleep 1
    done
    curl --noproxy '*' -fsS "http://127.0.0.1:${PORT}/health" >/dev/null
    "$@"
    local command_status=$?
    cleanup_proxy
    trap - EXIT INT TERM
    return "$command_status"
}

codex_probe_inner() {
    local probe_dir output
    mkdir -p "$TMAX_LOCAL_ROOT/.tmax_codex_probes"
    probe_dir=$(mktemp -d "$TMAX_LOCAL_ROOT/.tmax_codex_probes/${RUN_TAG}.XXXXXXXX")
    mkdir -p "$probe_dir/codex-home"
    output="$EXPERIMENT_DIR/preflight/codex-probe-events.jsonl"
    CODEX_HOME="$probe_dir/codex-home" \
        "$CANARY_ROOT/bin/codex" exec \
        --dangerously-bypass-approvals-and-sandbox \
        --skip-git-repo-check --ignore-user-config --ignore-rules --json \
        -C "$CANARY_ROOT" \
        -c project_doc_max_bytes=0 \
        -c features.skip_host_skill_discovery=true \
        -c features.plugins=false -c features.apps=false -c features.hooks=false \
        -c skills.include_instructions=false \
        -c model_providers.responses.name=metagen \
        -c model_providers.responses.base_url="$SYNTH_API_BASE" \
        -c model_providers.responses.env_key=OPENAI_API_KEY \
        -c model_providers.responses.wire_api=responses \
        -c model_providers.responses.stream_max_retries="$CODEX_STREAM_MAX_RETRIES" \
        -c model_providers.responses.request_max_retries="$CODEX_REQUEST_MAX_RETRIES" \
        -c model_providers.responses.stream_idle_timeout_ms=7500000 \
        -c model_provider=responses \
        -c model_reasoning_effort=medium \
        -m "$MODEL" \
        'Run pwd with the command tool, then reply exactly CODEX_METAGEN_OK.' \
        >"$output"
    jq -se 'any(.[]; .type == "item.completed" and .item.type == "command_execution" and .item.status == "completed")' \
        "$output" >/dev/null
    jq -se 'any(.[]; .type == "item.completed" and .item.type == "agent_message" and (.item.text | contains("CODEX_METAGEN_OK")))' \
        "$output" >/dev/null
    echo "Codex/MetaGen Responses tool-loop probe: OK"
}

probe() {
    check_root
    mkdir -p "$EXPERIMENT_DIR/preflight"
    run_with_proxy "$CANARY_ROOT" codex_probe_inner
    mkdir -p "$EXPERIMENT_DIR/preflight"
    printf '%s model=%s submission_mode=%s arm=codex wire=responses\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODEL" \
        "$METAGEN_SUBMISSION_MODE" \
        >"$EXPERIMENT_DIR/preflight/metagen.ok"
}

sandbox_probe() {
    check_root
    load_daytona_credentials
    configure_evolution "$CANARY_ROOT"
    local signal_id canary_corpus task package verdict log_file output
    local probe_count=0 expected_probe_count ok_file incoming
    log_file="$EXPERIMENT_DIR/preflight/daytona.log"
    ok_file="$EXPERIMENT_DIR/preflight/daytona.ok"
    incoming="${ok_file}.incoming"
    mkdir -p "$EXPERIMENT_DIR/preflight"
    : >"$log_file"
    : >"$incoming"
    expected_probe_count=$(
        jq -r \
            'if (.canary_signals | type) == "object" then (.canary_signals | length) else 1 end' \
            "$EXPERIMENT_DIR/launch_paths.json"
    )
    while IFS=$'\t' read -r canary_corpus signal_id; do
        task=${signal_id#*/}
        task=${task%--g*}
        package="$CANARY_ROOT/data/sources/$canary_corpus/tasks/$task"
        if ! output=$(
            "$TMAX_ENV/bin/python" \
                "$REPO_ROOT/torchtitan/experiments/rl/examples/tmax/evolution/daytona_revalidate.py" \
                "$package" --shortcut true \
                2>>"$log_file"
        ); then
            printf '%s\n' "$output" >>"$log_file"
            echo "ERROR: Daytona seed probe failed for $canary_corpus; see $log_file" >&2
            rm -f -- "$incoming"
            return 1
        fi
        printf '%s\n' "$output" >>"$log_file"
        verdict=$(printf '%s\n' "$output" | tail -1)
        if ! jq -e '.ok == true and .passed == false and .reward == 0' \
            <<<"$verdict" >/dev/null; then
            echo "ERROR: Daytona seed probe failed for $canary_corpus; see $log_file" >&2
            rm -f -- "$incoming"
            return 1
        fi
        printf '%s corpus=%s task=%s\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$canary_corpus" "$task" \
            >>"$incoming"
        probe_count=$((probe_count + 1))
        echo "Daytona seed build/boot/null-grade probe: OK ($canary_corpus/$task)"
    done < <(
        if jq -e '.canary_signals | type == "object"' \
            "$EXPERIMENT_DIR/launch_paths.json" >/dev/null; then
            jq -r '.canary_signals | to_entries[] | [.key, .value] | @tsv' \
                "$EXPERIMENT_DIR/launch_paths.json"
        else
            jq -r '[.canary_corpus // "tmax", .canary_signal] | @tsv' \
                "$EXPERIMENT_DIR/launch_paths.json"
        fi
    )
    if [[ "$probe_count" -ne "$expected_probe_count" ]]; then
        echo "ERROR: probed $probe_count of $expected_probe_count seed corpora" >&2
        rm -f -- "$incoming"
        return 1
    fi
    mv "$incoming" "$ok_file"
}

canary() {
    check_root
    [[ -s "$EXPERIMENT_DIR/preflight/daytona.ok" ]] || {
        echo "ERROR: run '$0 sandbox-probe' before the full canary" >&2
        return 1
    }
    local signal_id started_at
    signal_id=$(jq -r .canary_signal "$EXPERIMENT_DIR/launch_paths.json")
    started_at=$(date +%s)
    run_with_proxy "$CANARY_ROOT" \
        "$TMAX_ENV/bin/python" \
        "$HERE/run_evolution_daytona.py" \
        --signal "$signal_id" --workers 1
    local latest status arm metagen_success session_count
    latest=$(find "$CANARY_ROOT/evolution/tasks" -path '*/rewrites/*/rewrite.json' \
        -type f -newermt "@${started_at}" -printf '%T@ %p\n' \
        | sort -nr | head -1 | cut -d' ' -f2-)
    [[ -n "$latest" ]] || {
        echo "ERROR: the canary produced no rewrite record" >&2
        return 1
    }
    status=$(jq -r .status "$latest")
    arm=$(jq -r .arm "$latest")
    [[ "$arm" == codex ]] || {
        echo "ERROR: dry canary used arm=$arm instead of codex ($latest)" >&2
        return 1
    }
    session_count=$(find "$(dirname "$latest")/sessions" -mindepth 1 -maxdepth 1 \
        -type d 2>/dev/null | wc -l)
    [[ "$session_count" -gt 0 ]] || {
        echo "ERROR: dry canary produced no Codex session traces ($latest)" >&2
        return 1
    }
    case "$status" in
        accepted|rejected|kept|blocked) ;;
        *) echo "ERROR: dry canary status is $status ($latest)" >&2; return 1 ;;
    esac
    case "$(jq -r .stage "$latest")" in
        build|daytona_error|no_docker|package_error|setup|error)
            echo "ERROR: dry canary hit an infrastructure stage ($latest)" >&2
            return 1
            ;;
    esac
    metagen_success=$(find "$(metagen_state_dir "$CANARY_ROOT")" -type f \
        -newermt "@${started_at}" -print0 \
        | xargs -0 -r jq -r 'select(.state == "SUCCEEDED") | .local_id' \
        | head -1)
    [[ -n "$metagen_success" ]] || {
        echo "ERROR: the canary completed without a successful MetaGen request" >&2
        return 1
    }
    mkdir -p "$EXPERIMENT_DIR/preflight"
    printf '%s status=%s rewrite=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$status" "$latest" \
        >"$EXPERIMENT_DIR/preflight/canary.ok"
    echo "Dry evolution canary completed: status=$status rewrite=$latest"
}

batch_canary() {
    check_root
    [[ -s "$EXPERIMENT_DIR/preflight/metagen.ok" ]] || {
        echo "ERROR: run '$0 probe' before the multi-seed canary" >&2
        return 1
    }
    [[ -s "$EXPERIMENT_DIR/preflight/daytona.ok" ]] || {
        echo "ERROR: run '$0 sandbox-probe' before the multi-seed canary" >&2
        return 1
    }
    "$TMAX_ENV/bin/python" "$HERE/tmax_evolution_batch_canary.py" prepare \
        --experiment "$EXPERIMENT_DIR" --count "$CANARY_BATCH_SIZE" \
        --reference-root "$CANARY_REFERENCE_ROOT" \
        --reference-run "$CANARY_REFERENCE_RUN" \
        --rebench-rollouts "$CANARY_REBENCH_ROLLOUTS" \
        --rebench-events "$CANARY_REBENCH_EVENTS"
    run_with_proxy "$CANARY_BATCH_ROOT" \
        "$TMAX_ENV/bin/python" \
        "$HERE/run_evolution_daytona.py" \
        --once --dry --limit "$CANARY_BATCH_SIZE" --workers "$CANARY_BATCH_SIZE"
    verify_batch_canary
}

verify_batch_canary() {
    "$TMAX_ENV/bin/python" "$HERE/tmax_evolution_batch_canary.py" verify \
        --experiment "$EXPERIMENT_DIR" --count "$CANARY_BATCH_SIZE"
    printf '%s count=%s model=%s submission_mode=%s arm=codex\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CANARY_BATCH_SIZE" \
        "$MODEL" "$METAGEN_SUBMISSION_MODE" \
        >"$EXPERIMENT_DIR/preflight/canary-batch.ok"
}

easier_canary() {
    check_root
    [[ -s "$EXPERIMENT_DIR/preflight/metagen.ok" ]] || {
        echo "ERROR: run '$0 probe' before the easier canary" >&2
        return 1
    }
    [[ -s "$EXPERIMENT_DIR/preflight/daytona.ok" ]] || {
        echo "ERROR: run '$0 sandbox-probe' before the easier canary" >&2
        return 1
    }
    "$TMAX_ENV/bin/python" "$HERE/tmax_evolution_batch_canary.py" prepare \
        --experiment "$EXPERIMENT_DIR" --count "$CANARY_BATCH_SIZE" \
        --direction easier \
        --reference-root "$CANARY_REFERENCE_ROOT" \
        --reference-run "$CANARY_REFERENCE_RUN" \
        --rebench-rollouts "$CANARY_REBENCH_ROLLOUTS" \
        --rebench-events "$CANARY_REBENCH_EVENTS"
    run_with_proxy "$CANARY_EASIER_ROOT" \
        "$TMAX_ENV/bin/python" \
        "$HERE/run_evolution_daytona.py" \
        --once --dry --limit "$CANARY_BATCH_SIZE" --workers "$CANARY_BATCH_SIZE"
    verify_easier_canary
}

verify_easier_canary() {
    "$TMAX_ENV/bin/python" "$HERE/tmax_evolution_batch_canary.py" verify \
        --experiment "$EXPERIMENT_DIR" --count "$CANARY_BATCH_SIZE" \
        --direction easier
    printf '%s count=%s model=%s submission_mode=%s arm=codex direction=easier spec_repair=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CANARY_BATCH_SIZE" \
        "$MODEL" "$METAGEN_SUBMISSION_MODE" "$EVOLVE_SPEC_REPAIR" \
        >"$EXPERIMENT_DIR/preflight/canary-easier.ok"
}

serve() {
    check_root
    run_with_proxy "$PRODUCTION_ROOT" \
        "$TMAX_ENV/bin/python" \
        "$HERE/run_evolution_daytona.py" \
        --interval "$EVOLVE_INTERVAL_SEC" --workers "$EVOLVE_WORKERS"
}

start_wandb() {
    local run_id temporary
    if systemctl --user is-active --quiet "$WANDB_UNIT"; then
        echo "W&B autosync is already active: $WANDB_UNIT"
        return 0
    fi
    mkdir -p "$(dirname "$WANDB_RUN_ID_FILE")"
    if [[ ! -s "$WANDB_RUN_ID_FILE" ]]; then
        temporary="${WANDB_RUN_ID_FILE}.incoming"
        "$TMAX_ENV/bin/python" -c 'import wandb; print(wandb.util.generate_id())' \
            >"$temporary"
        mv "$temporary" "$WANDB_RUN_ID_FILE"
    fi
    run_id=$(tr -d '[:space:]' <"$WANDB_RUN_ID_FILE")
    [[ "$run_id" =~ ^[A-Za-z0-9_-]+$ ]] || {
        echo "ERROR: invalid W&B mirror run id in $WANDB_RUN_ID_FILE" >&2
        return 1
    }
    systemctl --user reset-failed "$WANDB_UNIT" 2>/dev/null || true
    systemd-run --user --collect --unit "$WANDB_UNIT" \
        --description "W&B autosync $RUN_TAG" \
        --property Restart=on-failure --property RestartSec=30 \
        --setenv "WANDB_PROJECT=torchtitan" \
        --setenv "WANDB_ENTITY=$TMAX_WANDB_ENTITY" \
        --setenv "WANDB_BIN=$TMAX_ENV/bin/wandb" \
        --setenv "WANDB_PYTHON=$TMAX_ENV/bin/python" \
        --setenv "WANDB_MIRROR_RUN_ID=$run_id" \
        --setenv "WANDB_RESTART_AWARE=${WANDB_RESTART_AWARE:-0}" \
        --setenv "WANDB_MIRROR_CURSOR_FILE=$LOCAL_STATE_ROOT/wandb_mirror_cursor.json" \
        --setenv "WANDB_BUCKET_WANDB=$WANDB_BUCKET_WANDB" \
        /bin/bash "$HERE/wandb_autosync.sh" "$WANDB_RUN_NAME" 60
    echo "W&B mirror: https://meta.wandb.io/$TMAX_WANDB_ENTITY/torchtitan/runs/$run_id"
}

start_lifecycle_watch() {
    local job_name=${1:-}
    local expected_training_steps=${SWE_TRAIN_STEPS:-}
    if [[ ! "$job_name" =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "ERROR: invalid MAST job name: '$job_name'" >&2
        return 2
    fi
    if [[ ! "$expected_training_steps" =~ ^[0-9]+$ ]]; then
        echo "ERROR: SWE_TRAIN_STEPS must be a nonnegative integer" >&2
        return 2
    fi
    if ! systemctl --user is-active --quiet "$UNIT"; then
        echo "ERROR: evolution sidecar is not active: $UNIT" >&2
        return 1
    fi
    if systemctl --user is-active --quiet "$LIFECYCLE_UNIT"; then
        echo "ERROR: lifecycle watcher is already active: $LIFECYCLE_UNIT" >&2
        return 1
    fi
    mkdir -p "$LOCAL_STATE_ROOT"
    systemctl --user reset-failed "$LIFECYCLE_UNIT" 2>/dev/null || true
    systemd-run --user --collect --unit "$LIFECYCLE_UNIT" \
        --description "Stop TMax evolution after optimization or MAST termination" \
        --property Restart=on-failure --property RestartSec=30 \
        "$TMAX_ENV/bin/python" "$HERE/mast_job_lifecycle.py" \
            --job-name "$job_name" \
            --evolution-unit "$UNIT" \
            --poll-seconds "$MAST_STATUS_POLL_SEC" \
            --query-timeout-seconds "$MAST_STATUS_QUERY_TIMEOUT_SEC" \
            --optimization-marker "$OPTIMIZATION_COMPLETE_MARKER" \
            --expected-training-steps "$expected_training_steps" \
            --admission-file "$ADMISSION_FILE" \
            --training-activity "$TRAINING_ACTIVITY" \
            --status-file "$MAST_LIFECYCLE_STATUS_FILE"
    echo "Lifecycle watcher: $LIFECYCLE_UNIT -> $job_name"
}

case "$ACTION" in
    probe) probe ;;
    sandbox-probe) sandbox_probe ;;
    canary) canary ;;
    batch-canary) batch_canary ;;
    batch-canary-verify) verify_batch_canary ;;
    easier-canary) easier_canary ;;
    easier-canary-verify) verify_easier_canary ;;
    serve) serve ;;
    start-wandb) start_wandb ;;
    watch-job) start_lifecycle_watch "${2:-}" ;;
    start)
        check_root
        load_credentials
        [[ -s "$EXPERIMENT_DIR/preflight/canary-batch.ok" ]] || {
            echo "ERROR: run '$0 batch-canary' successfully before starting the loop" >&2
            exit 1
        }
        if ! grep -Fq "model=$MODEL" \
            "$EXPERIMENT_DIR/preflight/canary-batch.ok" || \
            ! grep -Fq "submission_mode=$METAGEN_SUBMISSION_MODE" \
                "$EXPERIMENT_DIR/preflight/canary-batch.ok"; then
            echo "ERROR: rerun '$0 batch-canary' for $MODEL/$METAGEN_SUBMISSION_MODE" >&2
            exit 1
        fi
        if [[ "$SWE_EVOLVE_SIMPLIFY" == 1 && \
            ! -s "$EXPERIMENT_DIR/preflight/canary-easier.ok" ]]; then
            echo "ERROR: run '$0 easier-canary' successfully before enabling simplification" >&2
            exit 1
        fi
        if [[ "$SWE_EVOLVE_SIMPLIFY" == 1 ]]; then
            if [[ "$EVOLVE_SPEC_REPAIR" == 1 ]] && ! grep -Fq 'spec_repair=1' \
                "$EXPERIMENT_DIR/preflight/canary-easier.ok"; then
                echo "ERROR: rerun the easier canary with specification repair enabled" >&2
                exit 1
            fi
            if ! grep -Fq "model=$MODEL" \
                "$EXPERIMENT_DIR/preflight/canary-easier.ok" || \
                ! grep -Fq "submission_mode=$METAGEN_SUBMISSION_MODE" \
                    "$EXPERIMENT_DIR/preflight/canary-easier.ok"; then
                echo "ERROR: rerun '$0 easier-canary' for $MODEL/$METAGEN_SUBMISSION_MODE" >&2
                exit 1
            fi
        fi
        if systemctl --user is-active --quiet "$UNIT"; then
            echo "Evolution sidecar is already active: $UNIT"
        else
            PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$TMAX_ENV/bin/python" -c \
                'import sys; from pathlib import Path; from mast_rl.mast_job_lifecycle import write_admission_status; write_admission_status(Path(sys.argv[1]), allow_admission=False, reason="waiting for submission")' \
                "$ADMISSION_FILE"
            systemctl --user reset-failed "$UNIT" 2>/dev/null || true
            systemd-run --user --collect --unit "$UNIT" \
                --description "Task evolution $RUN_TAG" \
                --property Restart=on-failure --property RestartSec=30 \
                --property "CPUAffinity=$EVOLVE_CPU_AFFINITY" \
                --property Nice=10 --property CPUWeight=10 --property IOWeight=10 \
                --property CPUSchedulingPolicy=batch \
                --setenv "TOKIO_WORKER_THREADS=$EVOLVE_TOKIO_WORKER_THREADS" \
                --setenv "RUN_TAG=$RUN_TAG" \
                --setenv "METAGEN_PROXY_PORT=${EVOLVE_SERVE_PORT:-18080}" \
                --setenv "TMAX_EVOLVE_SEED_PROFILE=$TMAX_EVOLVE_SEED_PROFILE" \
                --setenv "SYNTH_MODEL=$MODEL" \
                --setenv "METAGEN_SUBMISSION_MODE=$METAGEN_SUBMISSION_MODE" \
                --setenv "EVOLVE_WORKERS=$EVOLVE_WORKERS" \
                --setenv "EVOLVE_INTERVAL_SEC=$EVOLVE_INTERVAL_SEC" \
                --setenv "METAGEN_PROXY_MAX_CONCURRENCY=$METAGEN_PROXY_MAX_CONCURRENCY" \
                --setenv "CODEX_STREAM_MAX_RETRIES=$CODEX_STREAM_MAX_RETRIES" \
                --setenv "CODEX_REQUEST_MAX_RETRIES=$CODEX_REQUEST_MAX_RETRIES" \
                --setenv "EVOLVE_AGENT_TIMEOUT=$EVOLVE_AGENT_TIMEOUT" \
                --setenv "EVOLVE_WORK_ROOT=$EVOLVE_WORK_ROOT" \
                --setenv "EVOLVE_REWRITE_BUDGET_SEC=$PRODUCTION_REWRITE_BUDGET_SEC" \
                --setenv "EVOLVE_FINAL_VALIDATION_RESERVE_SEC=$EVOLVE_FINAL_VALIDATION_RESERVE_SEC" \
                --setenv "EVOLVE_STATUS_REFRESH_SEC=$EVOLVE_STATUS_REFRESH_SEC" \
                --setenv "SWE_EVOLVE_SIMPLIFY=$SWE_EVOLVE_SIMPLIFY" \
                --setenv "SWE_SIMPLIFY_HINT=$SWE_SIMPLIFY_HINT" \
                --setenv "EVOLVE_EASIER_MIN_SCORED_ATTEMPTS=$EVOLVE_EASIER_MIN_SCORED_ATTEMPTS" \
                --setenv "SWE_EVOLUTION_MIN_SCORED=${SWE_EVOLUTION_MIN_SCORED:-2}" \
                --setenv "EVOLVE_SPEC_REPAIR=$EVOLVE_SPEC_REPAIR" \
                --setenv "SWE_POLICY_CANARY_K=$SWE_POLICY_CANARY_K" \
                --setenv "SWE_POLICY_CANARY_MAX_CONCURRENCY=$SWE_POLICY_CANARY_MAX_CONCURRENCY" \
                --setenv "SWE_POLICY_CANARY_MAX_VERSION_SPAN=$SWE_POLICY_CANARY_MAX_VERSION_SPAN" \
                --setenv "SWE_POLICY_CANARY_TIMEOUT_SEC=$SWE_POLICY_CANARY_TIMEOUT_SEC" \
                --setenv "SWE_POLICY_CANARY_POLL_SEC=$SWE_POLICY_CANARY_POLL_SEC" \
                --setenv "TMAX_EVOLVE_LOCAL_MOUNT=$LOCAL_MOUNT" \
                --setenv "TMAX_EVOLVE_STORAGE_PREFIX=$STORAGE_PREFIX" \
                --setenv "TMAX_EVOLVE_LOCAL_STATE_ROOT=$LOCAL_STATE_ROOT" \
                --setenv "DAYTONA_ENV_FILE=${DAYTONA_ENV_FILE:-$HOME/.daytona_env}" \
                --setenv "METAGEN_ENV_FILE=${METAGEN_ENV_FILE:-$HOME/.metagen_env}" \
                --setenv "EVOLVE_CODEX_HARNESS=${EVOLVE_CODEX_HARNESS:-}" \
                --setenv "EVOLVE_CODEX_LOCAL_BIN=${EVOLVE_CODEX_LOCAL_BIN:-}" \
                --setenv "EVOLVE_CODEX_MODEL=${EVOLVE_CODEX_MODEL:-}" \
                /bin/bash "$HERE/tmax_evolution_sidecar.sh" serve
        fi
        start_wandb
        ;;
    stop-evolution)
        systemctl --user stop "$LIFECYCLE_UNIT" 2>/dev/null || true
        systemctl --user stop "$UNIT" 2>/dev/null || true
        ;;
    stop)
        systemctl --user stop "$LIFECYCLE_UNIT" 2>/dev/null || true
        systemctl --user stop "$UNIT" 2>/dev/null || true
        systemctl --user stop "$WANDB_UNIT" 2>/dev/null || true
        ;;
    status)
        systemctl --user --no-pager status "$UNIT" 2>/dev/null || true
        systemctl --user --no-pager status "$LIFECYCLE_UNIT" 2>/dev/null || true
        systemctl --user --no-pager status "$WANDB_UNIT" 2>/dev/null || true
        if [[ -s "$MAST_LIFECYCLE_STATUS_FILE" ]]; then
            jq . "$MAST_LIFECYCLE_STATUS_FILE" || true
        fi
        ;;
    storage-check) mount_storage ;;
    logs)
        journalctl --user -u "$UNIT" -n 100 --no-pager
        journalctl --user -u "$LIFECYCLE_UNIT" -n 100 --no-pager
        ;;
    *)
        echo "usage: $0 {probe|sandbox-probe|canary|batch-canary|batch-canary-verify|easier-canary|easier-canary-verify|serve|start-wandb|watch-job|start|stop-evolution|stop|status|storage-check|logs}" >&2
        exit 2
        ;;
esac
