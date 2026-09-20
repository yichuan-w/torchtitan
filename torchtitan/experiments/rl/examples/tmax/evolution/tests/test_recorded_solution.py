import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve
import task_size
import terminus_validation as tv
from recorded_solution import ACTION_PATH, parse_actions
from torchtitan.experiments.rl.harness.agents.spec import AgentRun


def recording():
    return [
        {
            "step_id": 1,
            "offset_sec": 2.0,
            "keystrokes": "read answer\n",
            "duration": 0.1,
            "kind": "terminal_action",
        },
        {
            "step_id": 1,
            "offset_sec": 2.0,
            "keystrokes": "a < b & </keystrokes>\n",
            "duration": 1.0,
            "kind": "terminal_action",
        },
        {
            "step_id": 2,
            "offset_sec": 5.0,
            "keystrokes": "C-c",
            "duration": 0.1,
            "kind": "terminal_action",
        },
        {
            "step_id": 3,
            "offset_sec": 6.0,
            "keystrokes": "",
            "duration": 0,
            "kind": "completion_marker",
        },
        {
            "step_id": 4,
            "offset_sec": 7.0,
            "keystrokes": "",
            "duration": 0,
            "kind": "completion_marker",
        },
    ]


def package(tmp_path):
    files = {
        "instruction.md": "Write the answer.",
        "environment/Dockerfile": "FROM debian:12\n",
        "tests/test.sh": "test -f /answer\n",
        ACTION_PATH: json.dumps(recording()),
    }
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def test_evolution_roundtrip_and_size(tmp_path):
    src = package(tmp_path / "src")
    task = evolve.load(src)
    assert evolve.file_map(task)["solve_sh"] == ACTION_PATH
    actions = json.loads(task["solve_sh"])
    actions[0]["keystrokes"] = "read answer\nprintf '%s' \"$answer\" > /answer\n"
    task["solve_sh"] = json.dumps(actions)
    dest = evolve.save(task, tmp_path / "rewritten")
    assert evolve.load(dest)["solve_sh"] == task["solve_sh"]
    assert not (dest / "solution/solve.sh").exists()
    assert task_size.size_of_package(src, "tests/test.sh")["solution_lines"] == 3
    assert task_size.size_of_package(dest, "tests/test.sh")["solution_lines"] == 4


def test_shell_solution_keeps_precedence(tmp_path):
    src = package(tmp_path)
    (src / "solution/solve.sh").write_text("echo answer > /answer\n")
    assert evolve.file_map(evolve.load(src))["solve_sh"] == "solution/solve.sh"
    assert task_size.size_of_package(src, "tests/test.sh")["solution_lines"] == 1


def test_replay_preserves_turns_keys_waits_and_two_submissions(monkeypatch):
    from harbor.agents.terminus_2.terminus_json_plain_parser import (
        TerminusJSONPlainParser,
    )

    clock = [100.0]
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(tv.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(tv.asyncio, "sleep", sleep)
    adapter = tv.RecordedActionsAdapter(recording())
    parser = TerminusJSONPlainParser()

    async def run():
        parsed = []
        for _ in range(4):
            response = await adapter.complete("s", {"messages": [{"content": "pane"}]})
            result = parser.parse_response(response["content"][0]["text"])
            assert not result.error
            parsed.append(result)
            clock[0] += sum(c.duration for c in result.commands)
        return parsed

    result = asyncio.run(run())
    assert [(c.keystrokes, c.duration) for c in result[0].commands] == [
        (a["keystrokes"], a["duration"]) for a in recording()[:2]
    ]
    assert result[1].commands[0].keystrokes == "C-c"
    assert [r.is_task_complete for r in result] == [False, False, True, True]
    assert sleeps == pytest.approx([2.0, 1.9, 0.9, 1.0])


@pytest.mark.parametrize(
    "finish,submitted,code",
    [
        ("submit", True, 0),
        ("hit_time_budget", False, 124),
        ("stopped_early", False, None),
    ],
)
def test_recording_requires_harness_submission(monkeypatch, finish, submitted, code):
    sb = SimpleNamespace(
        exec=AsyncMock(return_value=(0, "tmux 3.2", "")),
        write_file=AsyncMock(),
        read_file=AsyncMock(return_value="pane"),
    )

    async def run(task, **kwargs):
        assert kwargs["parser_name"] == "json"
        assert isinstance(task.adapter, tv.RecordedActionsAdapter)
        task.adapter.exit_code = 0
        return AgentRun(
            turns=4, submitted=submitted, finish_reason=finish, pane_path="/pane"
        )

    monkeypatch.setattr(tv, "terminus_agent", run)
    result = asyncio.run(tv.run_reference(sb, "", 30, actions=recording()))
    assert result["solve_exit"] == code
    assert result["submitted"] == submitted
    sb.write_file.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration", -1),
        ("duration", 61),
        ("offset_sec", float("nan")),
        ("kind", "unknown"),
    ],
)
def test_invalid_recordings_fail_before_execution(field, value):
    actions = recording()
    actions[0][field] = value
    with pytest.raises(ValueError):
        parse_actions(json.dumps(actions))


def test_missing_confirmation_is_not_synthesized():
    with pytest.raises(ValueError, match="two completion markers"):
        parse_actions(json.dumps(recording()[:-1]))
