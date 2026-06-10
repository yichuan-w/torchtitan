# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Minimal async client for a Search-R1 local dense retrieval server.

Talks to the HTTP retrieval server from Search-R1 / slime
(`local_dense_retriever/retrieval_server.py`), which exposes
``POST {url}`` accepting ``{"queries": [...], "topk": k, "return_scores": false}``
and returning ``{"result": [[{"id", "contents"}, ...]]}`` (one list per query).
"""

from __future__ import annotations

import aiohttp


def _passages_to_string(docs: list[dict]) -> str:
    """Format retrieved docs into the ``Doc i(Title: ...) text`` block Search-R1
    feeds back to the model. Each doc's ``contents`` is ``"<title>\\n<text>"``."""
    out = ""
    for i, doc in enumerate(docs):
        contents = doc.get("contents", "") if isinstance(doc, dict) else ""
        lines = contents.split("\n")
        title = lines[0] if lines else ""
        text = "\n".join(lines[1:])
        out += f"Doc {i + 1}(Title: {title}) {text}\n"
    return out


async def search(query: str, *, url: str, topk: int, timeout_s: float = 60.0) -> str:
    """Retrieve the top-``topk`` passages for ``query`` and format them as a string.

    Returns an empty string on any transport/server error so a single flaky
    retrieval degrades the rollout (empty `<information>`) instead of crashing it.
    """
    payload = {"queries": [query], "topk": topk, "return_scores": False}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
    except Exception:
        return ""
    results = data.get("result") or [[]]
    return _passages_to_string(results[0])
