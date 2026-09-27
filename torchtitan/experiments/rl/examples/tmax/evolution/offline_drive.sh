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
#   [CLAUDE_PROXY=1] [LOOP_ENV=<file>] [EVOLVE_AGENT_TIMEOUT=7200] bash offline_drive.sh
#
# LOOP_ENV names a file sourced last, after evolveloop_env.sh and claude_env.sh,
# for a provider neither of them knows (OPENAI_API_KEY / SYNTH_API_BASE /
# SYNTH_MODEL for another OpenAI-compatible endpoint). It has to come last:
# evolveloop_env.sh sources ~/.config/daytona/env, which exports its own
# OPENAI_API_KEY over whatever the caller set.
#
# On a host with linger (the della login node) run it as a systemd user unit;
# on one without (della-vis1/2) under setsid nohup, since a unit there dies with
# the ssh session. The drive refuses the other combination. Either way, with
# EVOLVE_SOCK_DIR and TMPDIR off a shared /tmp:
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
# DRIVE_DIR, not HERE: evolveloop_env.sh sets HERE to its own directory when sourced.
DRIVE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# DLOG, not LOG: evolveloop_env.sh sets LOG to the loop log when sourced.
DLOG=$TRL_BASE/logs/offline_drive--$(date -u +%Y%m%d-%H%M%SZ).log
mkdir -p "$TRL_BASE/logs" "$TRL_BASE/tmp" "${EVOLVE_SOCK_DIR:-/tmp}"
L() { echo "[$(date -u +%FT%TZ)] $*" >> "$DLOG"; }
# shellcheck disable=SC1091
. "$DRIVE_DIR/della/evolveloop_env.sh"
if [ "${CLAUDE_PROXY:-0}" = 1 ]; then
  # shellcheck disable=SC1091
  . "$DRIVE_DIR/claude_proxy/claude_env.sh"
fi
if [ -n "${LOOP_ENV:-}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$LOOP_ENV"
  set +a
fi
# How the drive was launched has to fit this host. On the login node (linger on)
# processes outside a systemd user unit get killed: two setsid'd proxies died
# ~20 min in on 2026-09-27, as agent trees did on 2026-09-14. On della-vis1/2
# (no linger for this account) a user unit dies with the ssh session that
# started it, so a setsid'd process is the one that lives.
LINGER=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)
if [ "$LINGER" = yes ] && [ -z "${INVOCATION_ID:-}" ]; then
  L "refusing: $(hostname) has linger and this drive is not a systemd user unit; start it with systemd-run --user"
  echo "offline_drive: start it as a systemd user unit on $(hostname) (see the header)" >&2; exit 2
fi
if [ "$LINGER" != yes ] && [ -n "${INVOCATION_ID:-}" ]; then
  L "refusing: $(hostname) has no linger, so this systemd unit dies with the session that started it; start it under setsid nohup"
  echo "offline_drive: no linger on $(hostname); start it under setsid nohup, not as a unit" >&2; exit 2
fi
# One tiny request with the credential, endpoint and model the round's sessions
# will use, before each round is staged. A dead key, an empty balance or a dead
# proxy then stops the drive with the provider's own error instead of failing a
# round's worth of sessions and burning their MAX_ATTEMPTS (2026-09-27: the
# Anthropic balance ran out mid-run; OpenAI scopes, a revoked ChatGPT login and
# a Gemini tier drop did the same before it). The Claude Code arm is asked
# through the CLI itself, on the sessions' model and the shared credential
# store, so a subscription at its usage limit stops the drive too. A ChatGPT
# auth file has no endpoint to ask, and is logged as not probed.
probe_provider() {
  if [ "${SWE_RETUNE_AGENT:-}" = claude ] || [ "${EVOLVE_AGENT:-}" = claude ]; then
    local home=$TRL_BASE/tmp/provider-probe-claude out
    mkdir -p "$home"
    out=$(cd "$TRL_BASE/tmp" && CLAUDE_CONFIG_DIR=$home \
      CLAUDE_SECURESTORAGE_CONFIG_DIR=${EVOLVE_CLAUDE_ACCOUNT_HOME:-$HOME/.claude} \
      timeout 300 "$TRL_BASE/bin/claude" -p "Reply with exactly: ok" \
      --model "${EVOLVE_CLAUDE_MODEL:-opus}" --output-format json --setting-sources "" < /dev/null 2>&1)
    echo "claude: $(printf '%s' "$out" | head -c 300 | tr '\n' ' ')"
    printf '%s' "$out" | grep -q '"is_error":false'
    return
  fi
  if [ -n "${EVOLVE_CODEX_AUTH_FILE:-}" ]; then
    echo "not probed (ChatGPT login)"; return 0
  fi
  local key=${OPENAI_API_KEY:-$(sed -n 's/^OPENAI_API_KEY=//p' "${SYNTH_ENV_FILE:-/dev/null}" 2>/dev/null)}
  local body=$TRL_BASE/tmp/provider-probe.json code
  local req
  req=$(printf '{"model":"%s","input":"ping","max_output_tokens":16}' "${SYNTH_MODEL:-gpt-5.6-sol}")
  code=$(curl -s -m 180 -o "$body" -w '%{http_code}' "${SYNTH_API_BASE:-https://us.api.openai.com/v1}/responses" \
    -H "Authorization: Bearer $key" -H 'Content-Type: application/json' -d "$req")
  echo "http=$code $(head -c 300 "$body" 2>/dev/null | tr '\n' ' ')"
  [ "$code" = 200 ]
}
L "start workers=$WORKERS max_rounds=$MAX_ROUNDS max_attempts=$MAX_ATTEMPTS since=$ATTEMPTS_SINCE checkout=$TT@$(git -C "$TT" rev-parse --short HEAD) model=${SYNTH_MODEL:-gpt-5.6-sol} api_base=${SYNTH_API_BASE:-openai} agent_timeout=${EVOLVE_AGENT_TIMEOUT:-2400} sock_dir=${EVOLVE_SOCK_DIR:-/tmp} tmpdir=${TMPDIR:-/tmp}"
for k in $(seq 1 "$MAX_ROUNDS"); do
  # a round already running over this root (the lock names its pid) finishes first
  while pid=$(sed -n 's/.* pid=\([0-9]*\) .*/\1/p' "$TRL_BASE/evolution/loop.lock" 2>/dev/null) \
        && [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; do sleep 60; done
  if ! probe=$(probe_provider); then
    L "round $k: provider probe failed ($probe); stopping. Accepted rewrites are folded; relaunch with a fresh ATTEMPTS_SINCE once the provider answers"
    exit 3
  fi
  L "round $k: provider probe ok ($(printf '%s' "$probe" | cut -c1-60))"
  name=$(basename "$SOURCE_RUN").offline$k-$(date -u +%Y%m%d-%H%M%S)
  # $PY (the training venv, from evolveloop_env.sh): the login node's python3 is
  # 3.9 and cannot parse offline_stage.py; its traceback went to the journal and
  # the round read "staged 0" (2026-09-14).
  out=$("$PY" "$DRIVE_DIR/offline_stage.py" --run "$SOURCE_RUN" --selected "$SELECTED" --root "$TRL_BASE" \
        --name "$name" --skip-accepted --only-failed --max-attempts "$MAX_ATTEMPTS" --attempts-since "$ATTEMPTS_SINCE" 2>>"$DLOG")
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
