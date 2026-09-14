# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""LiteLLM pre-call hook for the Responses route the Codex CLI uses.

Two things the route needs that LiteLLM's config cannot express for it:

* an Anthropic cache breakpoint on the newest text item of every request.
  Codex resends the whole conversation on every turn; with the breakpoint,
  each turn's prefix (system prompt, tool list, all earlier turns) is a
  cache read rather than full-price input. Measured on 2026-09-14: 97% of
  input tokens served from cache, $704 of a $2,796 run. LiteLLM's own
  cache_control_injection_points only apply on the chat route.
* an output cap. Codex sends none, LiteLLM then defaults Anthropic's
  max_tokens to 4096, and adaptive thinking alone can use that up, which
  ends the turn on an empty message and the session with it.
"""
from litellm.integrations.custom_logger import CustomLogger

CACHE = {"type": "ephemeral"}
MAX_OUTPUT_TOKENS = 32000


def mark_newest_text(items: list) -> bool:
    """Put the cache breakpoint on the last text-bearing input item; tool
    outputs after it are small and stay uncached."""
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str) and content and item.get("role"):
            kind = "output_text" if item.get("role") == "assistant" else "input_text"
            item["content"] = [{"type": kind, "text": content, "cache_control": CACHE}]
            return True
        if isinstance(content, list):
            for part in reversed(content):
                if (
                    isinstance(part, dict)
                    and part.get("type") in ("input_text", "output_text")
                    and part.get("text")
                ):
                    part["cache_control"] = CACHE
                    return True
    return False


class ResponsesRouteHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type in ("aresponses", "responses"):
            items = data.get("input")
            if isinstance(items, list):
                mark_newest_text(items)
            if not data.get("max_output_tokens"):
                data["max_output_tokens"] = MAX_OUTPUT_TOKENS
        return data


proxy_handler_instance = ResponsesRouteHook()
