"""Oracle completion must come from a Terminus observation and confirmed submit."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import terminus_validation as tv
from torchtitan.experiments.rl.harness.agents.spec import AgentRun


def body(prompt):
    return {"messages": [{"role": "user", "content": prompt}]}


def test_echoed_command_cannot_finish_reference():
    from harbor.agents.terminus_2.terminus_xml_plain_parser import (
        TerminusXMLPlainParser,
    )

    adapter = tv.ReferenceAdapter("sleep 10; exit 7")
    first = asyncio.run(adapter.complete("s", body("terminal ready")))
    parsed = TerminusXMLPlainParser().parse_response(first["content"][0]["text"])
    assert not parsed.error and len(parsed.commands) == 1
    echoed = parsed.commands[0].keystrokes
    waiting = asyncio.run(adapter.complete("s", body(echoed)))
    assert adapter.exit_code is None
    assert (
        not TerminusXMLPlainParser()
        .parse_response(waiting["content"][0]["text"])
        .is_task_complete
    )
    complete = asyncio.run(adapter.complete("s", body(f"{adapter.marker}=7\nroot# ")))
    assert adapter.exit_code == 7
    assert (
        TerminusXMLPlainParser()
        .parse_response(complete["content"][0]["text"])
        .is_task_complete
    )
    # The second confirmation need not contain the result screen again.
    confirm = asyncio.run(adapter.complete("s", body("Confirm completion.")))
    assert (
        TerminusXMLPlainParser()
        .parse_response(confirm["content"][0]["text"])
        .is_task_complete
    )


@pytest.mark.parametrize(
    "finish,submitted,expected",
    [
        ("submit", True, 0),
        ("hit_time_budget", False, 124),
        ("stopped_early", False, None),
        ("hit_max_turns", False, None),
    ],
)
def test_exit_marker_alone_does_not_pass(monkeypatch, finish, submitted, expected):
    sb = SimpleNamespace(
        exec=AsyncMock(return_value=(0, "tmux 3.2", "")),
        write_file=AsyncMock(),
        read_file=AsyncMock(return_value="pane"),
    )

    async def run(task, *, terminal_session_name):
        assert terminal_session_name.startswith("oracle-")
        assert task.time_budget_sec == 3600 and task.max_turns > 360
        task.adapter.exit_code = 0
        return AgentRun(
            turns=3, submitted=submitted, finish_reason=finish, pane_path="/pane"
        )

    monkeypatch.setattr(tv, "terminus_agent", run)
    result = asyncio.run(tv.run_reference(sb, "printf '<response>'", 3600))
    assert result["solve_exit"] == expected
    assert result["submitted"] is submitted
    assert sb.exec.await_args.args == ("tmux -V",)
    assert "printf '<response>'" in sb.write_file.await_args.args[1]


def test_provider_preserves_terminus_prompt_usage_and_truncation():
    prompt = body("The terminal asks for input. Return Terminus XML.")
    seen = []

    def chat_response(messages, max_tokens):
        seen.append((messages, max_tokens))
        return {
            "model": "configured-model",
            "choices": [
                {"message": {"content": "<response>"}, "finish_reason": "length"}
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 24},
        }

    adapter = tv.ProviderAdapter(
        SimpleNamespace(EFFORT="high", chat_response=chat_response)
    )
    result = asyncio.run(adapter.complete("s", prompt))
    assert seen == [(prompt["messages"], 24000)]
    assert result["stop_reason"] == "length"
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 24}
    assert adapter.transcript[0]["model"] == "configured-model"


def test_declared_budget_is_not_replaced_by_the_sweep_floor(tmp_path):
    from pack_to_dataset import declared_solve_budget

    assert declared_solve_budget(tmp_path, 900) == 900
    (tmp_path / "task.toml").write_text("[agent]\ntimeout_sec = 3600\n")
    assert declared_solve_budget(tmp_path, 900) == 3600
    assert declared_solve_budget(tmp_path, 7200) == 7200
