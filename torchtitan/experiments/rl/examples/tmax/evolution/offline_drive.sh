#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
# Evolve a staged subset of signals to completion: rounds of
# `evolve_ondella.py --once` over one root until every selected task has a
# verdict. Between rounds the tasks whose newest rewrite ended failed or
# interrupted (an infrastructure loss) are restaged under a fresh run-dir
# name (offline_stage.py --only-failed), at most MAX_ATTEMPTS per task counted
# from ATTEMPTS_SINCE, so a task failing on its own merits is not retried
# forever. Stops when nothing is left to restage or after MAX_ROUNDS.
#
#   TRL_PROFILE=andy TRL_BASE=<root> TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 TT_DAYTONA_DISK_GB=2 \
#   SOURCE_RUN=<run dir the signals came from> SELECTED=<root>/scripts/selected.json \
#   WORKERS=64 MAX_ROUNDS=6 MAX_ATTEMPTS=2 ATTEMPTS_SINCE=$(date -u +%Y%m%d-%H%M) \
#   [CLAUDE_PROXY=1] [EVOLVE_AGENT_TIMEOUT=7200] bash offline_drive.sh
#
# Run it as a systemd user unit (a process tied to an ssh session dies with
# it), with EVOLVE_SOCK_DIR and TMPDIR off a shared /tmp:
#   systemd-run --user --unit=offline-<root> --collect --working-directory=$TRL_BASE \
#     --setenv=TRL_PROFILE=.. --setenv=TRL_BASE=.. --setenv=SOURCE_RUN=.. --setenv=SELECTED=.. \
#     --setenv=EVOLVE_SOCK_DIR=/dev/shm/$USER-evolve --setenv=TMPDIR=$TRL_BASE/tmp ... \
#     bash <evolution dir>/offline_drive.sh
# Log: $TRL_BASE/logs/offline_drive--<stamp>.log; each round's loop output in
# $TRL_BASE/evolution/offline-round<k>.log, and the loop's own loop.log.
set -uo pipefail
: "${TRL_BASE:?}" "${SOURCE_RUN:?}" "${SELECTED:?}"
WORKERS=${WORKERS:-64}; MAX_ROUNDS=${MAX_ROUNDS:-6}; MAX_ATTEMPTS=${MAX_ATTEMPTS:-2}
ATTEMPTS_SINCE=${ATTEMPTS_SINCE:-$(date -u +%Y%m%d-%H%M)}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# DLOG, not LOG: evolveloop_env.sh sets LOG to the loop log when sourced.
DLOG=$TRL_BASE/logs/offline_drive--$(date -u +%Y%m%d-%H%M%SZ).log
mkdir -p "$TRL_BASE/logs" "$TRL_BASE/tmp" "${EVOLVE_SOCK_DIR:-/tmp}"
L() { echo "[$(date -u +%FT%TZ)] $*" >> "$DLOG"; }
# shellcheck disable=SC1091
. "$HERE/della/evolveloop_env.sh"
if [ "${CLAUDE_PROXY:-0}" = 1 ]; then
  # shellcheck disable=SC1091
  . "$HERE/claude_proxy/claude_env.sh"
fi
L "start workers=$WORKERS max_rounds=$MAX_ROUNDS max_attempts=$MAX_ATTEMPTS since=$ATTEMPTS_SINCE checkout=$TT@$(git -C "$TT" rev-parse --short HEAD) model=${SYNTH_MODEL:-gpt-5.6} api_base=${SYNTH_API_BASE:-openai} agent_timeout=${EVOLVE_AGENT_TIMEOUT:-2400} sock_dir=${EVOLVE_SOCK_DIR:-/tmp} tmpdir=${TMPDIR:-/tmp}"
for k in $(seq 1 "$MAX_ROUNDS"); do
  # a round already running over this root (the lock names its pid) finishes first
  while pid=$(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$TRL_BASE/evolution/loop.lock" 2>/dev/null) \
        && [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; do sleep 60; done
  name=$(basename "$SOURCE_RUN").offline$k-$(date -u +%H%M%S)
  # $PY (the training venv, from evolveloop_env.sh): the login node's python3 is
  # 3.9 and cannot parse offline_stage.py; its traceback went to the journal and
  # the round read "staged 0" (2026-09-14).
  out=$("$PY" "$HERE/offline_stage.py" --run "$SOURCE_RUN" --selected "$SELECTED" --root "$TRL_BASE" \
        --name "$name" --skip-accepted --only-failed --max-attempts "$MAX_ATTEMPTS" --attempts-since "$ATTEMPTS_SINCE")
  L "round $k: $out"
  n=$(printf '%s' "$out" | sed -n 's/^staged \([0-9]*\) signals.*/\1/p')
  if [ "${n:-0}" -eq 0 ]; then
    L "nothing left to restage; done"
    rmdir "$TRL_BASE/runs/$name/signals" "$TRL_BASE/runs/$name/rollouts" "$TRL_BASE/runs/$name" 2>/dev/null
    break
  fi
  L "round $k: running --once --workers $WORKERS"
  "$PY" "$EVO/evolve_ondella.py" --once --workers "$WORKERS" >> "$TRL_BASE/evolution/offline-round$k.log" 2>&1
  L "round $k: exit=$? $(grep 'round done' "$TRL_BASE/evolution/loop.log" | tail -1 | cut -c1-200)"
done
L "finished"
