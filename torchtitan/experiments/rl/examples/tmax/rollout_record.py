"""One rollout, one JSONL file: the rollout on line 1, then one line per turn.
LAYOUT.md gives the schema; this module is the only reader and writer.

The turns are decoded from the token stream, not from parsed messages: a
turn's completion is the model's whole answer and the next turn's prompt
extends this turn's prompt+completion by the terminal reply, so the growing
prefix recovers each reply (the TITO invariant). The completion is Terminus-2's
XML, thinking first, so ``parse_completion`` splits it into fields and puts
the command first in the line.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, Protocol

TURN_KEYS = ("turn", "keystrokes", "raw", "task_complete", "output", "analysis", "plan", "think")
_CHAT_MARKERS = re.compile(r"<\|im_(?:start|end)\|>(?:assistant|user|system)?\n?")
_KEYSTROKES = re.compile(r"<keystrokes[^>]*>(.*?)</keystrokes>", re.S)
_RESPONSE = re.compile(r"<(?:response|analysis|commands)>")


class _Turn(Protocol):
    prompt_token_ids: list[int]
    completion_token_ids: list[int]


def strip_markers(text: str) -> str:
    """Drop what the chat template wraps a turn in: ``<|im_end|>``, the
    ``<|im_start|>user`` before a terminal reply, and the
    ``<|im_start|>assistant\\n<think>\\n\\n`` opener the decoded delta carries
    at its tail."""
    text = _CHAT_MARKERS.sub("", text)
    return re.sub(r"<think>\s*$", "", text).strip()


def parse_completion(completion: str) -> dict:
    """Terminus-2's answer into fields. The template opens ``<think>`` for the
    model, so the text starts inside it; ``</think>`` then ``<response>`` with
    ``<analysis>``, ``<plan>`` and ``<commands>`` holding ``<keystrokes>``
    blocks, optionally ``<task_complete>``. A response with no keystrokes is
    the closing turn and keeps an empty list; a completion with no response
    at all keeps its text under ``raw``."""
    text = strip_markers(completion or "")
    think, closed, rest = text.partition("</think>")
    if not closed:
        think, rest = "", text
    think = think.removeprefix("<think>").strip()
    out: dict = {}
    if _RESPONSE.search(rest):
        out["keystrokes"] = _KEYSTROKES.findall(rest)
    else:
        out["raw"] = rest.strip()
    if "<task_complete>true</task_complete>" in rest:
        out["task_complete"] = True
    for tag in ("analysis", "plan"):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", rest, re.S)
        if m:
            out[tag] = m.group(1).strip()
    if think:
        out["think"] = think
    return out


def turn_line(index: int, completion: str, output: str) -> dict:
    """One turn line in reading order: what ran, what came back, then the prose."""
    fields = parse_completion(completion)
    line: dict = {"turn": index}
    if "keystrokes" in fields:
        line["keystrokes"] = fields["keystrokes"]
    else:
        line["raw"] = fields.get("raw", "")
    if fields.get("task_complete"):
        line["task_complete"] = True
    line["output"] = strip_markers(output or "")
    for key in ("analysis", "plan", "think"):
        if key in fields:
            line[key] = fields[key]
    return line


def turns_from_tokens(turns: Iterable[_Turn], decode) -> list[dict]:
    """Turn lines from captured turns. ``decode(ids) -> str`` is the tokenizer
    with special tokens kept. The reply to turn i is the part of turn i+1's
    prompt beyond turn i's prompt+completion; a turn whose prompt does not
    extend the previous one (a re-render) gets no reply text."""
    turns = list(turns)
    out = []
    for i, t in enumerate(turns):
        completion = decode(list(t.completion_token_ids or []))
        reply = ""
        if i + 1 < len(turns):
            cur = list(t.prompt_token_ids or []) + list(t.completion_token_ids or [])
            nxt = list(turns[i + 1].prompt_token_ids or [])
            if nxt[: len(cur)] == cur:
                reply = decode(nxt[len(cur):])
        out.append(turn_line(i + 1, completion, reply))
    return out


def write_record(path: Path, header: dict, turns: list[dict]) -> None:
    """Whole file beside, then renamed in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".incoming")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for t in turns:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_header(path: Path) -> dict:
    """Line 1 only; what a tally over thousands of records needs."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return json.loads(line)
    raise ValueError(f"empty rollout record: {path}")


def read_record(path: Path) -> tuple[dict, list[dict]]:
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    if not lines:
        raise ValueError(f"empty rollout record: {path}")
    return lines[0], lines[1:]
