#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
# One task's rewrite in a systemd user unit of its own: what offline_dispatch.py
# starts for each pending task when offline_drive.sh runs with DISPATCH=unit.
#
#   systemd-run --user --unit=bench-rw-<root>-<task> --collect --working-directory=<root> \
#     --setenv=RW_TASK=<task> --setenv=RW_ENV_FILE=<root>/tmp/rw-unit.env0 bash offline_rewrite.sh
#
# RW_ENV_FILE is the drive's environment as its unit received it (env -0,
# written before anything is sourced), so the loop here gets the same
# settings, then the same files sourced in the same order, as the default
# drive's `--once` round would. Output: <root>/evolution/rw/<task>.log; the
# loop's own records go to loop.log as always.
set -uo pipefail
: "${RW_TASK:?}" "${RW_ENV_FILE:?}"
# Per-invocation values of the drive's unit stay this unit's own.
while IFS= read -r -d '' kv; do
  name=${kv%%=*}
  case "$name" in
    INVOCATION_ID|JOURNAL_STREAM|SYSTEMD_EXEC_PID|MANAGERPID|MANAGERPIDFDID|PWD|OLDPWD|SHLVL|_|RW_TASK|RW_ENV_FILE) continue ;;
  esac
  [[ $name =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  # shellcheck disable=SC2163  # NAME=value, exported as such
  export "$kv"
done < "$RW_ENV_FILE"
: "${TRL_BASE:?}"
DRIVE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The drive's sourcing, in the drive's order (offline_drive.sh says why LOOP_ENV is last).
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
mkdir -p "$TRL_BASE/evolution/rw"
out=$TRL_BASE/evolution/rw/$RW_TASK.log
echo "[$(date -u +%FT%TZ)] start unit=${INVOCATION_ID:-} task=$RW_TASK checkout=$TT@$(git -C "$TT" rev-parse --short HEAD)" >> "$out"
"$PY" "$EVO/evolve_ondella.py" --task "$RW_TASK" >> "$out" 2>&1
rc=$?
echo "[$(date -u +%FT%TZ)] exit=$rc task=$RW_TASK" >> "$out"
exit $rc
