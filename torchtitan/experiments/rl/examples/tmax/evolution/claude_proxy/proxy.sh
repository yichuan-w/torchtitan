#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
# The Claude proxy for the evolve loop, as a systemd user unit on this host.
#
#   PROXY_ENV=<file with ANTHROPIC_API_KEY= and LITELLM_MASTER_KEY=> \
#   [PROXY_VENV=<venv with litellm[proxy]>] [PROXY_PORT=4000] [PROXY_WORKERS=1] \
#   [PROXY_LOG=<file>] bash proxy.sh start|stop|status
#
# Workers: with several uvicorn workers the supervisor pings each one every
# 0.5 s and SIGKILLs any that does not answer within timeout_worker_healthcheck
# (5 s by default). The pong thread shares the GIL with request handling, and
# 64 concurrent streams of 50-80k-token JSON bodies hold it longer than that:
# on 2026-09-14 four workers were killed together every ~3 min (308 boots in
# 2 h), cutting every in-flight stream each time. One worker has no
# supervisor and carried the same load for 7 h; more than one gets the check
# timeout raised to 60 s.
set -u
PROXY_VENV=${PROXY_VENV:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/litellm-venv}
PROXY_PORT=${PROXY_PORT:-4000}
PROXY_WORKERS=${PROXY_WORKERS:-1}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
UNIT=litellm-claude-proxy-$PROXY_PORT
case "${1:-status}" in
  start)
    : "${PROXY_ENV:?a file holding ANTHROPIC_API_KEY= and LITELLM_MASTER_KEY=}"
    PROXY_LOG=${PROXY_LOG:-$(dirname "$PROXY_ENV")/proxy.log}
    systemctl --user stop "$UNIT" 2>/dev/null; systemctl --user reset-failed "$UNIT" 2>/dev/null
    extra=""; [ "$PROXY_WORKERS" -gt 1 ] && extra="--timeout_worker_healthcheck 60"
    systemd-run --user --unit="$UNIT" --collect -p EnvironmentFile="$PROXY_ENV" -p WorkingDirectory="$HERE" \
      -p Environment=TMPDIR="${TMPDIR:-/tmp}" -p Restart=on-failure -p RestartSec=3 \
      bash -c "exec $PROXY_VENV/bin/litellm --config $HERE/config.yaml --host 127.0.0.1 --port $PROXY_PORT --num_workers $PROXY_WORKERS $extra >> $PROXY_LOG 2>&1"
    sleep 15; systemctl --user is-active "$UNIT"; curl -s -m 5 "http://127.0.0.1:$PROXY_PORT/health/liveliness"; echo ;;
  stop) systemctl --user stop "$UNIT" ;;
  status) systemctl --user is-active "$UNIT"; curl -s -m 5 "http://127.0.0.1:$PROXY_PORT/health/liveliness"; echo ;;
esac
