#!/usr/bin/env bash
# Near-live mirror of a MAST OFFLINE wandb run to meta.wandb.io.
#
# The MAST job (offline, no egress) writes its wandb run to the Manifold bucket
# under ${DUMP_DIR}/wandb/offline-run-*. The devvm gvfs mount can't *enumerate*
# those dirs (Permission denied on listdir), so we use `manifold ls` to find the
# restart segments, `manifold getr` to download them to local /tmp, then mirror
# their stitched history to meta.wandb.io (reachable only via fwdproxy; auth
# from ~/.netrc).
#
#   WANDB_PROJECT=<project> bash mast_rl/wandb_autosync.sh [<run-name>] [<interval_s>]
#   nohup bash mast_rl/wandb_autosync.sh torchtitan-rl-search-r1-qwen3-0.6b 120 \
#       > /home/yichuan/wandb_autosync.log 2>&1 &
# For runs outside the default yichuan path, set WANDB_BUCKET_WANDB to the
# Manifold directory containing the offline-run-* directory.
set -uo pipefail

RUN_NAME="${1:-torchtitan-rl-search-r1-qwen3-0.6b}"
INTERVAL="${2:-120}"
PROJECT="${WANDB_PROJECT:-torchtitan}"
ENTITY="${WANDB_ENTITY:-yichuan}"
WANDB_BIN="${WANDB_BIN:-/home/yichuan/torchtitan/.venv/bin/wandb}"
WANDB_PYTHON="${WANDB_PYTHON:-$(dirname "${WANDB_BIN}")/python}"
MIRROR_RUN_ID="${WANDB_MIRROR_RUN_ID:-}"
KEEPALIVE_ENABLED="${WANDB_KEEPALIVE:-1}"
RESTART_AWARE="${WANDB_RESTART_AWARE:-0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUCKET_WANDB="${WANDB_BUCKET_WANDB:-torchtrain_datasets/tree/yichuan/mast_runs/${RUN_NAME}/wandb}"
TMP="${TMP_DIR:-/tmp/wandb_sync_${RUN_NAME}}"
OFFLINE_RUN="${WANDB_OFFLINE_RUN:-}"
LS_TIMEOUT_SEC="${WANDB_LS_TIMEOUT_SEC:-60}"
GETR_TIMEOUT_SEC="${WANDB_GETR_TIMEOUT_SEC:-180}"
SYNC_TIMEOUT_SEC="${WANDB_SYNC_TIMEOUT_SEC:-180}"

export WANDB_BASE_URL="${WANDB_BASE_URL:-https://meta.wandb.io}"
export https_proxy="${https_proxy:-http://fwdproxy:8080}"
export http_proxy="${http_proxy:-http://fwdproxy:8080}"
export NO_PROXY="127.0.0.1,localhost"

echo "autosync: run='${RUN_NAME}' project='${PROJECT}' every ${INTERVAL}s -> ${WANDB_BASE_URL}"

clean() { grep -ivE 'ClientExecutor|Gojira|ThreadPool|^I[0-9]{4}|HostResource|^W[0-9]{4}'; }

KEEPALIVE_PID=""
cleanup() {
    if [[ -n "${KEEPALIVE_PID}" ]]; then
        kill "${KEEPALIVE_PID}" 2>/dev/null || true
        wait "${KEEPALIVE_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM

if [[ -n "${MIRROR_RUN_ID}" && "${KEEPALIVE_ENABLED}" == "1" ]]; then
    WANDB_DIR="/tmp/wandb_keepalive_${MIRROR_RUN_ID}" \
        "${WANDB_PYTHON}" "${SCRIPT_DIR}/wandb_keepalive.py" \
        --entity "${ENTITY}" --project "${PROJECT}" \
        --run-id "${MIRROR_RUN_ID}" --name "${RUN_NAME}" &
    KEEPALIVE_PID=$!
fi

LAST_NEWEST=""
FIRST_PASS=1
mkdir -p "${TMP}"
while true; do
    OFFLINE_RUNS=()
    if [[ -n "${OFFLINE_RUN}" ]]; then
        OFFLINE_RUNS=("${OFFLINE_RUN}")
    else
        mapfile -t OFFLINE_RUNS < <(
            timeout --kill-after=5s "${LS_TIMEOUT_SEC}" \
                manifold ls "${BUCKET_WANDB}/" 2>/dev/null \
                | clean | awk '/offline-run-/{print $NF}' | sort
        )
    fi
    if [[ "${#OFFLINE_RUNS[@]}" -gt 0 ]]; then
        if [[ -z "${MIRROR_RUN_ID}" ]]; then
            OFFLINE_RUNS=("${OFFLINE_RUNS[-1]}")
        fi
        CURRENT_NEWEST="${OFFLINE_RUNS[-1]}"
        DOWNLOAD_OK=0
        WANDB_FILES=()
        for OFF in "${OFFLINE_RUNS[@]}"; do
            if [[ ! "${OFF}" =~ ^offline-run-[A-Za-z0-9_-]+$ ]]; then
                echo "$(date +%H:%M:%S) ignoring invalid segment name: ${OFF}"
                DOWNLOAD_OK=1
                continue
            fi
            RUN_ID="${OFF##*-}"
            SYNC_DIR="${TMP}/${OFF}"
            SYNC_FILE="${SYNC_DIR}/run-${RUN_ID}.wandb"
            REFRESH=0
            if [[ "${RESTART_AWARE}" == "1" ]]; then
                # Refresh history objects, not entire run directories. Older
                # attempts may finish uploading after a newer one is visible.
                mkdir -p "${SYNC_DIR}"
                if timeout --kill-after=5s "${GETR_TIMEOUT_SEC}" manifold get \
                    "${BUCKET_WANDB}/${OFF}/run-${RUN_ID}.wandb" \
                    "${SYNC_FILE}.incoming" >/dev/null 2>&1; then
                    mv "${SYNC_FILE}.incoming" "${SYNC_FILE}"
                else
                    echo "$(date +%H:%M:%S) history refresh failed; will retry ${OFF}"
                fi
                if [[ ! -f "${SYNC_FILE}" ]]; then
                    continue
                fi
            elif [[ "${FIRST_PASS}" == "1" || ! -f "${SYNC_FILE}" \
                || "${OFF}" == "${CURRENT_NEWEST}" \
                || ( -n "${LAST_NEWEST}" \
                    && "${CURRENT_NEWEST}" != "${LAST_NEWEST}" \
                    && "${OFF}" == "${LAST_NEWEST}" ) ]]; then
                REFRESH=1
            fi
            if [[ "${REFRESH}" == "1" ]]; then
                rm -rf -- "${SYNC_DIR}"
                if [[ -n "${OFFLINE_RUN}" ]]; then
                    mkdir -p "${SYNC_DIR}"
                    # A direct object read avoids directory enumeration used by getr.
                    timeout --kill-after=5s "${GETR_TIMEOUT_SEC}" manifold get \
                        "${BUCKET_WANDB}/${OFF}/run-${RUN_ID}.wandb" \
                        "${SYNC_FILE}" >/dev/null 2>&1 || DOWNLOAD_OK=$?
                else
                    # getr writes to <dst>/<basename(src)> -> point at parent.
                    timeout --kill-after=5s "${GETR_TIMEOUT_SEC}" manifold getr \
                        "${BUCKET_WANDB}/${OFF}" "${TMP}" >/dev/null 2>&1 \
                        || DOWNLOAD_OK=$?
                fi
            fi
            if [[ ! -f "${SYNC_FILE}" ]]; then
                DOWNLOAD_OK=1
            elif [[ ! -s "${SYNC_FILE}" ]]; then
                # A preemption before W&B initializes can leave an empty segment.
                # It has no history to mirror and must not block later segments.
                echo "$(date +%H:%M:%S) skipping empty segment: ${OFF}"
            else
                WANDB_FILES+=("${SYNC_FILE}")
            fi
        done
        if [[ "${DOWNLOAD_OK}" -eq 0 && "${#WANDB_FILES[@]}" -gt 0 ]]; then
            echo "$(date +%H:%M:%S) syncing ${#WANDB_FILES[@]} segment(s)"
            if [[ -n "${MIRROR_RUN_ID}" ]]; then
                MIRROR_ARGS=()
                if [[ "${RESTART_AWARE}" == "1" ]]; then
                    MIRROR_ARGS=(--restart-aware --cursor-file \
                        "${WANDB_MIRROR_CURSOR_FILE:-${TMP}/mirror-cursor-${MIRROR_RUN_ID}.json}")
                fi
                timeout --kill-after=10s "${SYNC_TIMEOUT_SEC}" \
                    "${WANDB_PYTHON}" "${SCRIPT_DIR}/wandb_history_mirror.py" \
                    --entity "${ENTITY}" --project "${PROJECT}" \
                    --run-id "${MIRROR_RUN_ID}" --manifold-prefix "${BUCKET_WANDB}" \
                    "${MIRROR_ARGS[@]}" "${WANDB_FILES[@]}" || true
            else
                # Re-syncing an active run updates its files and summary. For live
                # history, use WANDB_MIRROR_RUN_ID; completed runs sync normally.
                timeout --kill-after=10s "${SYNC_TIMEOUT_SEC}" "${WANDB_BIN}" sync \
                    --id "${RUN_ID}" -e "${ENTITY}" -p "${PROJECT}" \
                    "${SYNC_FILE}" 2>&1 | grep -iE 'syncing|done|view run|view project|error' || true
            fi
            LAST_NEWEST="${CURRENT_NEWEST}"
            FIRST_PASS=0
        elif [[ "${DOWNLOAD_OK}" -eq 0 ]]; then
            echo "$(date +%H:%M:%S) no non-empty offline run yet"
            LAST_NEWEST="${CURRENT_NEWEST}"
            FIRST_PASS=0
        else
            echo "$(date +%H:%M:%S) download failed for one or more segments"
        fi
    else
        echo "$(date +%H:%M:%S) no offline-run yet under ${BUCKET_WANDB}"
    fi
    sleep "${INTERVAL}"
done
