# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""LiteLLM pre-call hook for the Responses route the Codex CLI uses.

Two things the route needs that LiteLLM's config cannot express for it:

* Anthropic prompt caching up to the end of every request. Codex resends
  the whole conversation on every turn; with caching, each turn's prefix
  (system prompt, tool list, all earlier turns) is a cache read rather than
  full-price input. The request carries a top-level cache_control, which
  Anthropic's automatic caching turns into a breakpoint on the last block,
  whatever its kind. This hook used to mark the newest text item instead;
  on 2026-09-27 two arms of the same loop on the same 139 signals left
  8.2% of input tokens uncached with that marker (about a fifth of the
  arm's spend, $243 of $1,170) and 0.0% with this one. Which turns missed
  under the old marker is not established: the worst ones read ~28.6k
  tokens from cache, wrote none and paid full price for the rest, and two
  synthetic reproductions cached fully under both hooks. A cache_control
  on the function_call_output item itself does not survive LiteLLM's
  conversion; the top-level one does.
* an output cap. Codex sends none, LiteLLM then defaults Anthropic's
  max_tokens to 4096, and adaptive thinking alone can use that up, which
  ends the turn on an empty message and the session with it.
"""
from litellm.integrations.custom_logger import CustomLogger

CACHE = {"type": "ephemeral"}
MAX_OUTPUT_TOKENS = 32000


class ResponsesRouteHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type in ("aresponses", "responses"):
            data.setdefault("cache_control", CACHE)
            if not data.get("max_output_tokens"):
                data["max_output_tokens"] = MAX_OUTPUT_TOKENS
        return data


proxy_handler_instance = ResponsesRouteHook()
