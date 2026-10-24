"""Normalize two observed chat-output defects in recorded TMax solutions."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import time
from pathlib import Path

ACTION_PATH = "solution/gpt6_actions.json"
_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t"}
_HEX = frozenset("0123456789abcdefABCDEF")


def _escape_invalid_string_backslashes(text: str) -> tuple[str, int]:
    """Escape backslashes that are invalid only inside JSON strings."""
    output: list[str] = []
    in_string = False
    repaired = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            in_string = not in_string
            output.append(char)
            index += 1
            continue
        if char != "\\" or not in_string:
            output.append(char)
            index += 1
            continue

        following = text[index + 1] if index + 1 < len(text) else ""
        if following in _JSON_ESCAPES:
            output.extend((char, following))
            index += 2
            continue
        if (
            following == "u"
            and index + 5 < len(text)
            and all(value in _HEX for value in text[index + 2 : index + 6])
        ):
            output.append(text[index : index + 6])
            index += 6
            continue
        output.append("\\\\")
        repaired += 1
        index += 1
    return "".join(output), repaired


def _split_long_waits(actions: list[dict]) -> tuple[list[dict], int]:
    normalized: list[dict] = []
    split = 0
    for action in actions:
        duration = action.get("duration")
        if (
            action.get("kind") != "terminal_action"
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 60
        ):
            normalized.append(action)
            continue
        remaining = duration
        first = True
        while remaining > 0:
            part = dict(action)
            part["keystrokes"] = action.get("keystrokes", "") if first else ""
            part["duration"] = min(60, remaining)
            normalized.append(part)
            remaining -= part["duration"]
            first = False
        split += 1
    return normalized, split


def normalize_task(task: dict, parse_actions) -> list[str]:
    """Normalize a recorded solution in place and return applied operations."""
    if task.get("_solution_rel") != ACTION_PATH:
        return []

    original = task["solve_sh"]
    escaped, num_escapes = _escape_invalid_string_backslashes(original)
    actions = json.loads(escaped)
    if not isinstance(actions, list):
        raise TypeError("recorded solution must be a JSON list")
    actions, num_long_waits = _split_long_waits(actions)

    changes = []
    if num_escapes:
        changes.append(f"invalid_json_backslashes={num_escapes}")
    if num_long_waits:
        changes.append(f"overlong_waits={num_long_waits}")
    if not changes:
        parse_actions(original)
        return []

    normalized = json.dumps(actions, indent=2, ensure_ascii=False) + "\n"
    parse_actions(normalized)
    task["solve_sh"] = normalized
    return changes


def install(feedback_loop, parse_actions) -> None:
    """Install the guard immediately before a generated package is written."""
    original = feedback_loop._write_back
    if getattr(original, "_recorded_action_guard", False):
        return

    @functools.wraps(original)
    def guarded_write_back(work: Path, task: dict) -> None:
        changes = task.pop("_recorded_action_normalizations", [])
        direct = normalize_task(task, parse_actions)
        if direct:
            changes.append(direct)
        if changes:
            record = {
                "timestamp": time.time(),
                "changes": changes,
                "sha256": hashlib.sha256(task["solve_sh"].encode()).hexdigest(),
            }
            with (work.parent / "recorded_action_normalization.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
            logging.getLogger(__name__).warning(
                "normalized generated recorded actions: %s",
                "; ".join(", ".join(group) for group in changes),
            )
        original(work, task)

    guarded_write_back._recorded_action_guard = True
    feedback_loop._write_back = guarded_write_back
