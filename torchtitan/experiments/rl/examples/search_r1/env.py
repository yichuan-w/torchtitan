# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import re
from dataclasses import dataclass

from renderers import Message

from torchtitan.experiments.rl.environment import (
    MessageEnv,
    MessageEnvInitOutput,
    MessageEnvStepOutput,
)
from torchtitan.experiments.rl.examples.search_r1.data import SearchR1Example
from torchtitan.experiments.rl.examples.search_r1.retrieval import search


# Search-R1 instruction prompt (ported from the Search-R1 / slime data format).
# The model reasons in <think>, searches via <search> query </search> (results come
# back in <information>...</information>), and answers in <answer>...</answer>.
SEARCH_R1_INSTRUCTION = (
    "Answer the given question. You must conduct reasoning inside <think> and "
    "</think> first every time you get new information. After reasoning, if you "
    "find you lack some knowledge, you can call a search engine by <search> query "
    "</search> and it will return the top searched results between <information> "
    "and </information>. You can search as many times as your want. If you find no "
    "further external knowledge needed, you can directly provide the answer inside "
    "<answer> and </answer>, without detailed illustrations. For example, <answer> "
    "Beijing </answer>. Question: "
)

_INVALID_ACTION_MESSAGE = (
    "\nMy previous action is invalid. If I want to search, I should put the query "
    "between <search> and </search>. If I want to give the final answer, I should "
    "put the answer between <answer> and </answer>. Let me try again.\n"
)

# First well-formed <search>...</search> or <answer>...</answer> block.
_ACTION_RE = re.compile(r"<(search|answer)>(.*?)</\1>", re.DOTALL)


class SearchR1Env(MessageEnv):
    """Multi-turn open-domain QA env with a search tool (Search-R1).

    Each assistant turn either issues a ``<search>query</search>`` — which the env
    answers with a ``<information>...</information>`` user message — or gives a final
    ``<answer>...</answer>``, which ends the rollout. Malformed turns get a corrective
    nudge. The per-rollout turn budget is enforced by ``TokenEnv.max_num_turns``.

    Uses the text-tag protocol (not function-calling tools); pair it with a
    renderer configured with ``enable_thinking=False`` so the model's ``<think>``
    tags stay in the completion text.
    """

    @dataclass(kw_only=True, slots=True)
    class Config(MessageEnv.Config):
        search_url: str = "http://127.0.0.1:8000/retrieve"
        """URL of the local dense retrieval server."""

        topk: int = 3
        """Number of passages to retrieve per search query."""

    def __init__(self, config: Config, *, env_input: SearchR1Example) -> None:
        self._question = env_input.question
        self._search_url = config.search_url
        self._topk = config.topk

    async def init(self) -> MessageEnvInitOutput:
        return MessageEnvInitOutput(
            init_prompt_messages=[
                {"role": "user", "content": SEARCH_R1_INSTRUCTION + self._question},
            ]
        )

    async def step(self, completion_message: Message) -> MessageEnvStepOutput:
        # The text-tag protocol only uses plain-text content.
        content = completion_message.get("content")
        content = content if isinstance(content, str) else ""
        match = _ACTION_RE.search(content)
        action = match.group(1) if match else None
        argument = match.group(2).strip() if match else ""

        if action == "answer":
            # Final answer: end the rollout.
            return MessageEnvStepOutput(done=True)

        if action == "search":
            passages = await search(argument, url=self._search_url, topk=self._topk)
            observation = f"\n\n<information>{passages.strip()}</information>\n\n"
            return MessageEnvStepOutput(
                env_messages=[{"role": "user", "content": observation}],
                done=False,
            )

        # No valid action: nudge the model and let it try again.
        return MessageEnvStepOutput(
            env_messages=[{"role": "user", "content": _INVALID_ACTION_MESSAGE}],
            done=False,
        )
