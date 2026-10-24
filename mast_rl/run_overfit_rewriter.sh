#!/usr/bin/env bash
# The overfit bench's reference rewriter (evolve loop, codex + GPT-5.6) on this
# devvm against one bench job on the mount. Settings follow
# overfit_bench/reference_rewriter.env.example; the model is reached through the
# local MetaGen proxy (as tmax_evolution_sidecar.sh does) instead of OpenAI.
#
#   JOB=tb21/torch-tensor-parallelism--gpt56-ref bash mast_rl/run_overfit_rewriter.sh start
#   JOB=... bash mast_rl/run_overfit_rewriter.sh {serve|stop|status|logs}
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
TMAX_ENV=${TMAX_ENV:-$HOME/.conda/envs/rlmast}
TMAX_LOCAL_ROOT=${TMAX_LOCAL_ROOT:-$HOME/rl}
FBSOURCE=${TMAX_FBSOURCE:-/data/users/$(id -un)/fbsource_mg}
: "${JOB:?set JOB=<bench>/<task>--<rewriter>}"
ROOT=$TMAX_LOCAL_ROOT/.torchtrain_datasets/tree/$(id -un)/overfit_bench/jobs/$JOB
[ -f "$ROOT/job.json" ] || { echo "no job.json in $ROOT" >&2; exit 2; }
KEY=$(printf '%s' "$JOB" | tr -c 'A-Za-z0-9_.-' '-')
UNIT=overfit-rewriter-$KEY
STATE=$TMAX_LOCAL_ROOT/.overfit_rewriter_state/$KEY
PORT=${METAGEN_PROXY_PORT:-18090}
MODEL=${SYNTH_MODEL:-gpt-5-6-sol-genai-responses}

serve() {
    mkdir -p "$STATE"
    set -a
    # shellcheck disable=SC1090
    . "${METAGEN_ENV_FILE:-$HOME/.metagen_env}"
    # shellcheck disable=SC1090
    . "${DAYTONA_ENV_FILE:-$HOME/.daytona_env}"
    set +a
    : "${METAGEN_API_KEY:?}" "${DAYTONA_API_KEY:?}"
    export DAYTONA_ENV_FILE=${DAYTONA_ENV_FILE:-$HOME/.daytona_env}
    export PATH="$ROOT/bin:$TMAX_ENV/bin:/usr/local/bin:/usr/bin:/bin"
    export CONDA_PREFIX=$TMAX_ENV PYTHONPATH=$REPO_ROOT
    export TRL_PROFILE=$(id -un)-mast TRL_TT=$REPO_ROOT TRL_BASE=$ROOT
    # reference_rewriter.env.example
    # effort medium (user, 2026-10-18; the example says high).
    export SWE_RETUNE_AGENT=codex SYNTH_EFFORT=${SYNTH_EFFORT:-medium}
    export SWE_EVOLVE_SIMPLIFY=1 SWE_SIMPLIFY_HINT=vague EVOLVE_SPEC_REPAIR=1
    export EVOLVE_REWRITE_BUDGET_SEC=${EVOLVE_REWRITE_BUDGET_SEC:-10800}
    export TT_SANDBOX_BACKEND=daytona TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2
    export TT_DAYTONA_DISK_GB=2 TT_DAYTONA_EPHEMERAL=1 TT_DAYTONA_AUTO_DELETE_MIN=15
    export TT_DAYTONA_CREATE_CONCURRENCY=8 TT_DAYTONA_CREATE_RETRIES=8
    # codex through the local MetaGen facade (tmax_evolution_sidecar.sh).
    export EVOLVE_CODEX_DRIVER=exec
    export EVOLVE_WORK_ROOT=$STATE/work EVOLVE_CODEX_LOCAL_STATE_ROOT=$STATE/codex
    export EVOLVE_AGENT_TIMEOUT=7200 EVOLVE_REPAIR_ROUNDS=3 EVOLVE_HARDER_OPERATORS=0
    # A deadlocked probe witness must not hold a rewrite for its whole budget.
    export EVOLVE_PROBE_COMMAND_TIMEOUT_SEC=${EVOLVE_PROBE_COMMAND_TIMEOUT_SEC:-1200}
    export SYNTH_API_BASE="http://127.0.0.1:$PORT/v1" SYNTH_MODEL=$MODEL
    export SYNTH_TIMEOUT_SEC=7500 SYNTH_MAX_RETRIES=0 SYNTH_CONSISTENCY=1 SYNTH_ORACLE_REPAIR=1
    export METAGEN_MAX_OUTPUT_TOKENS=32768
    export EVOLVE_CODEX_EXTRA_CONFIG="model_providers.oai.wire_api=responses model_providers.responses.name=metagen model_providers.responses.base_url=$SYNTH_API_BASE model_providers.responses.env_key=OPENAI_API_KEY model_providers.responses.wire_api=responses model_providers.responses.stream_max_retries=5 model_providers.responses.request_max_retries=3 model_providers.responses.stream_idle_timeout_ms=7500000 model_provider=responses web_search=disabled"
    export OPENAI_API_KEY=local-metagen-proxy
    # OFB_HARNESS=local: rewrite sessions on this devvm's own codex CLI (its own
    # sign-in and endpoint; no provider override), model OFB_CODEX_MODEL. The
    # loop's chat calls still go through the local MetaGen proxy above.
    if [[ "${OFB_HARNESS:-}" == local ]]; then
        # Default: the devvm codex in its sanctioned --dangerously-enable-internet-mode
        # (wrapper below); without it the agent's egress filter blocks the sandbox provider.
        export EVOLVE_CODEX_HARNESS=local EVOLVE_CODEX_LOCAL_BIN=${EVOLVE_CODEX_LOCAL_BIN:-$HOME/rl/.cache/codex-local-internet/codex}
        export EVOLVE_CODEX_MODEL=${OFB_CODEX_MODEL:-gpt-6.1-sol}
        unset EVOLVE_CODEX_EXTRA_CONFIG
    fi
    unset EVOLVE_CODEX_AUTH_FILE EVOLVE_CODEX_ACCOUNT_HOME CODEX_BASE_URL || true
    export https_proxy=${SWE_PROXY:-http://fwdproxy:8080} http_proxy=${SWE_PROXY:-http://fwdproxy:8080}
    export no_proxy="127.0.0.1,localhost,$(hostname)" NO_PROXY="127.0.0.1,localhost,$(hostname)"
    mkdir -p "$ROOT/evolution" "$EVOLVE_WORK_ROOT" "$EVOLVE_CODEX_LOCAL_STATE_ROOT"
    # OilFS has no ftruncate: the loop's lock lives locally, linked from the root.
    touch "$STATE/loop.lock"
    [ -L "$ROOT/evolution/loop.lock" ] || { rm -f "$ROOT/evolution/loop.lock"; ln -s "$STATE/loop.lock" "$ROOT/evolution/loop.lock"; }

    local target=fbcode//gen_ai/web_search/serving_v2/scripts:metagen_cli artifact
    artifact=$(cd "$FBSOURCE" && /usr/local/bin/buck2 build "$target" --show-output | awk -v t="$target" '$1==t{print $2}')
    METAGEN_PROXY_STATE_DIR=$STATE/metagen_requests METAGEN_PROXY_MAX_CONCURRENCY=16 \
        METAGEN_REQUEST_TIMEOUT_SEC=7200 \
        "$FBSOURCE/${artifact%.par}#link-tree/_bootstrap.sh" "$HERE/metagen_chat_proxy.py" \
        --host 127.0.0.1 --port "$PORT" --model "$MODEL" --submission-mode foreground \
        >>"$STATE/metagen_proxy.log" 2>&1 &
    local proxy=$!
    trap 'kill $proxy 2>/dev/null || true' EXIT INT TERM
    for _ in $(seq 120); do
        curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
        kill -0 $proxy 2>/dev/null || { echo "MetaGen proxy died; $STATE/metagen_proxy.log" >&2; exit 1; }
        sleep 1
    done
    if [[ "${OFB_REWRITER_MODE:-reference}" == ladder ]]; then
        # The scheduler's socket lives on the MAST host; the front end of
        # ofb_socket_relay.py serves it here (main.py starts the back end there).
        "$TMAX_ENV/bin/python" "$HERE/ofb_socket_relay.py" front "$ROOT" >>"$STATE/relay_front.log" 2>&1 &
        local relay=$!
        trap 'kill $proxy $relay 2>/dev/null || true' EXIT INT TERM
        "$TMAX_ENV/bin/python" "$HERE/run_evolution_daytona.py" --ladder "$ROOT" --workers 16 \
            >>"$ROOT/evolution/loop.log" 2>&1
    else
        "$TMAX_ENV/bin/python" "$HERE/run_evolution_daytona.py" --interval 120 --workers 16 \
            >>"$ROOT/evolution/loop.log" 2>&1
    fi
}

case "${1:-status}" in
    serve) serve ;;
    start)
        systemctl --user reset-failed "$UNIT" 2>/dev/null || true
        systemd-run --user --collect --unit "$UNIT" --description "overfit rewriter $JOB" \
            --property Restart=on-failure --property RestartSec=30 --property Nice=10 \
            --setenv JOB="$JOB" --setenv HOME="$HOME" --setenv METAGEN_PROXY_PORT="$PORT" \
            --setenv OFB_HARNESS="${OFB_HARNESS:-}" --setenv OFB_REWRITER_MODE="${OFB_REWRITER_MODE:-reference}" --setenv OFB_CODEX_MODEL="${OFB_CODEX_MODEL:-}" \
            --setenv EVOLVE_CODEX_LOCAL_BIN="${EVOLVE_CODEX_LOCAL_BIN:-}" \
            /bin/bash "$HERE/run_overfit_rewriter.sh" serve
        echo "started $UNIT; log: $ROOT/evolution/loop.log"
        ;;
    stop) systemctl --user stop "$UNIT" ;;
    status) systemctl --user --no-pager status "$UNIT" || true ;;
    logs) tail -n 50 "$ROOT/evolution/loop.log" ;;
    *) echo "usage: $0 {start|serve|stop|status|logs}" >&2; exit 2 ;;
esac
