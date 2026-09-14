# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
#
# Source AFTER della/evolveloop_env.sh: routes every model call of the loop
# (Codex sessions and synth_client chat calls) through the local LiteLLM
# proxy to Claude, replacing the OpenAI key evolveloop_env.sh exported.
#   PROXY_ENV=<the proxy's env file> [PROXY_PORT=4000] [SYNTH_MODEL=claude-opus-5]
: "${PROXY_ENV:?the proxy env file, holding LITELLM_MASTER_KEY=}"
export OPENAI_API_KEY=$(sed -n 's/^LITELLM_MASTER_KEY=//p' "$PROXY_ENV")
export SYNTH_API_BASE=http://127.0.0.1:${PROXY_PORT:-4000}/v1
export SYNTH_MODEL=${SYNTH_MODEL:-claude-opus-5}
unset EVOLVE_CODEX_AUTH_FILE
# A Claude turn can think silently for minutes and a busy proxy can stall
# longer than the CLI's default five reconnects a few seconds apart (a stall
# then ends the session, 33% of one batch on 2026-09-14): more reconnects,
# and a 15-minute idle limit instead of the default.
export EVOLVE_CODEX_EXTRA_CONFIG="model_providers.oai.stream_max_retries=20 model_providers.oai.request_max_retries=10 model_providers.oai.stream_idle_timeout_ms=900000"
# Claude at 1.8 turns/min needs more than the 40-minute session default for
# the 50-80 turn verifier and repair stages.
export EVOLVE_AGENT_TIMEOUT=${EVOLVE_AGENT_TIMEOUT:-7200}
