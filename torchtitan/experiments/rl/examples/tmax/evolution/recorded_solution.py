"""The recorded terminal-action solution carried by TMax longlongcheck."""

from __future__ import annotations

import json
import math
from pathlib import Path

ACTION_PATH = "solution/gpt6_actions.json"
SHELL_PATH = "solution/solve.sh"


def solution_path(package: Path) -> str:
    if (package / SHELL_PATH).is_file():
        return SHELL_PATH
    if (package / ACTION_PATH).is_file():
        return ACTION_PATH
    raise FileNotFoundError(f"{package}: no {SHELL_PATH} or {ACTION_PATH}")


def parse_actions(text: str) -> list[dict]:
    actions = json.loads(text)
    if not isinstance(actions, list) or not actions:
        raise ValueError("recorded solution must be a nonempty action list")
    previous = -1.0
    for action in actions:
        if action["kind"] not in ("terminal_action", "completion_marker"):
            raise ValueError(f"unknown recorded action: {action['kind']}")
        if not isinstance(action["keystrokes"], str):
            raise ValueError("recorded keystrokes must be a string")
        offset, duration = action["offset_sec"], action["duration"]
        if not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in (offset, duration)
        ):
            raise ValueError("recorded action times must be finite numbers")
        if offset < previous or duration < 0 or duration > 60:
            raise ValueError(
                "recorded offsets must increase and durations must be in [0, 60]"
            )
        previous = offset
        if action["kind"] == "completion_marker" and (action["keystrokes"] or duration):
            raise ValueError("completion markers cannot carry keystrokes or waits")
    if [a["kind"] for a in actions[-2:]] != ["completion_marker"] * 2:
        raise ValueError("recorded solution must end with two completion markers")
    return actions


def action_lines(text: str) -> str:
    """Text to measure, never a shell script to execute."""
    return "\n".join(
        a["keystrokes"] for a in parse_actions(text) if a["kind"] == "terminal_action"
    )
