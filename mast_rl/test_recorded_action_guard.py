import json
from types import SimpleNamespace

import pytest
from recorded_action_guard import ACTION_PATH, install, normalize_task

from torchtitan.experiments.rl.examples.tmax.evolution.recorded_solution import (
    parse_actions,
)


def _actions(duration=0.1, keystrokes="printf 'ok\\n'\n"):
    return [
        {
            "step_id": 1,
            "offset_sec": 1.0,
            "keystrokes": keystrokes,
            "duration": duration,
            "kind": "terminal_action",
        },
        {
            "step_id": 2,
            "offset_sec": 200.0,
            "keystrokes": "",
            "duration": 0,
            "kind": "completion_marker",
        },
        {
            "step_id": 3,
            "offset_sec": 201.0,
            "keystrokes": "",
            "duration": 0,
            "kind": "completion_marker",
        },
    ]


def test_valid_recording_is_unchanged():
    text = json.dumps(_actions())
    task = {"_solution_rel": ACTION_PATH, "solve_sh": text}
    assert normalize_task(task, parse_actions) == []
    assert task["solve_sh"] == text


def test_invalid_shell_escape_is_preserved_as_a_literal_backslash():
    text = json.dumps(_actions(keystrokes="find /app -name '*.md'\n"))
    text = text.replace("-name", "\\( -name")
    task = {"_solution_rel": ACTION_PATH, "solve_sh": text}
    assert normalize_task(task, parse_actions) == ["invalid_json_backslashes=1"]
    assert json.loads(task["solve_sh"])[0]["keystrokes"] == (
        "find /app \\( -name '*.md'\n"
    )


def test_overlong_wait_is_split_without_changing_total_wait():
    task = {"_solution_rel": ACTION_PATH, "solve_sh": json.dumps(_actions(180.0))}
    assert normalize_task(task, parse_actions) == ["overlong_waits=1"]
    actions = parse_actions(task["solve_sh"])
    terminal = [action for action in actions if action["kind"] == "terminal_action"]
    assert [action["duration"] for action in terminal] == [60, 60, 60]
    assert terminal[0]["keystrokes"]
    assert not terminal[1]["keystrokes"]
    assert not terminal[2]["keystrokes"]


def test_unrelated_invalid_recording_still_fails():
    actions = _actions()
    actions[-1]["kind"] = "unknown"
    task = {"_solution_rel": ACTION_PATH, "solve_sh": json.dumps(actions)}
    with pytest.raises(ValueError, match="unknown recorded action"):
        normalize_task(task, parse_actions)


def test_shell_solution_is_not_touched():
    task = {"_solution_rel": "solution/solve.sh", "solve_sh": "echo ok\n"}
    assert normalize_task(task, parse_actions) == []
    assert task["solve_sh"] == "echo ok\n"


def test_install_normalizes_before_write_back(tmp_path):
    written = []
    feedback_loop = SimpleNamespace(
        _write_back=lambda _work, task: written.append(task["solve_sh"]),
    )
    install(feedback_loop, parse_actions)
    task = {
        "_solution_rel": ACTION_PATH,
        "solve_sh": json.dumps(_actions(duration=180.0)),
    }
    feedback_loop._write_back(tmp_path / "package", task)
    assert [action["duration"] for action in json.loads(written[0])[:3]] == [60, 60, 60]
