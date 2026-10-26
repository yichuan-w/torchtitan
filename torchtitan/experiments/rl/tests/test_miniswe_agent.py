# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The mini-swe-agent harness (``TMAX_AGENT=miniswe``) against a fake sandbox
and a fake policy: parsing, format errors, submission, truncation, timeouts and
the finish reasons the tmax rollouter reads. Nothing here runs a command."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _FINISH_REASONS,
    _finish_reason_metrics,
    _format_breakdown_metrics,
)
from torchtitan.experiments.rl.harness.agents import miniswe, miniswe_prompts
from torchtitan.experiments.rl.harness.agents.spec import (
    AgentTask,
    FORMAT_ERROR_CATEGORIES,
    get_agent,
)

_UNAME = "Linux\n6.8.0\n#1 SMP PREEMPT_DYNAMIC\nx86_64\n"
_SUBMIT = "THOUGHT: done.\n\n```bash\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n```"


@pytest.fixture(autouse=True)
def _upstream_defaults(monkeypatch):
    """Tests describe upstream's loop; the lenient parse and turn cap that tmax
    turns on by default are opted into per test."""
    monkeypatch.setattr(miniswe, "_LENIENT_PARSE", False)
    monkeypatch.setattr(miniswe, "_DEFAULT_MAX_TURNS", 0)


def _reply(text: str, *, stop: str = "end_turn", tokens: tuple[int, int] = (100, 20)):
    return {
        "content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
    }


class _Adapter:
    """Replays scripted replies and keeps every request it was sent."""

    def __init__(self, replies, delay: float = 0.0) -> None:
        self._replies = iter(replies)
        self._delay = delay
        self.requests: list[dict] = []

    async def complete(self, session_id: str, body: dict):
        self.requests.append({**body, "messages": list(body["messages"])})
        if self._delay:
            await asyncio.sleep(self._delay)
        return next(self._replies, None)


class _Sandbox:
    """Answers ``uname`` itself and every other command from ``results``, in
    order, as ``(exit_code, output)`` or ``(exit_code, output, seconds)``."""

    def __init__(self, results=()) -> None:
        self._results = iter(results)
        self.calls: list[dict] = []

    async def exec(self, cmd, *, user="root", env=None, timeout=120, check=False):
        self.calls.append({"cmd": cmd, "env": env, "timeout": timeout})
        if cmd == miniswe._UNAME:
            return 0, _UNAME, ""
        exit_code, output, *secs = next(self._results)
        if secs:
            await asyncio.sleep(secs[0])
        return exit_code, output, ""


def _run(adapter, sandbox, **overrides):
    task = AgentTask(
        sandbox=sandbox,
        instruction="Make /app/out.txt say hi.",
        session_id="s",
        adapter=adapter,
        time_budget_sec=overrides.pop("time_budget_sec", 600),
        **overrides,
    )
    return asyncio.run(miniswe.miniswe_agent(task))


# ---- prompts -----------------------------------------------------------------


def test_registered_under_miniswe():
    assert get_agent("miniswe") is miniswe.miniswe_agent


def test_instance_template_adds_only_the_background_rule():
    upstream = miniswe_prompts._UPSTREAM_INSTANCE_TEMPLATE
    assert upstream.count(miniswe_prompts._RULE_3_END) == 1
    assert miniswe_prompts.INSTANCE_TEMPLATE.replace(
        miniswe_prompts._BACKGROUND_RULE, ""
    ) == upstream


def test_first_request_is_upstreams_prompt_with_the_sandbox_uname():
    adapter = _Adapter([_reply(_SUBMIT)])
    _run(adapter, _Sandbox([(0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")]))
    first = adapter.requests[0]
    # Jinja drops the template's trailing newline, as upstream renders it.
    assert first["system"] == miniswe_prompts.SYSTEM_TEMPLATE.rstrip("\n")
    assert "tools" not in first
    [message] = first["messages"]
    assert message["role"] == "user"
    text = message["content"]
    assert text.startswith("Please solve this issue: Make /app/out.txt say hi.\n")
    assert "<system_information>\nLinux 6.8.0 #1 SMP PREEMPT_DYNAMIC x86_64\n" in text
    assert "Every action is executed in a new subshell." in text
    assert "4. A command that does not exit on its own is killed" in text
    assert "`nohup your_server > /tmp/server.log 2>&1 &`" in text
    assert "You are on MacOS" not in text


# ---- parsing and observations ------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("THOUGHT: look.\n\n```bash\nls -la\n```", ["ls -la"]),
        ("```bash   \ncat <<'EOF' > f\nx\nEOF\n```", ["cat <<'EOF' > f\nx\nEOF"]),
        ("THOUGHT: no fence\n\nls -la", []),
        ("```sh\nls\n```", []),
        ("```bash\nls\n```\nthen\n```bash\npwd\n```", ["ls", "pwd"]),
    ],
)
def test_parse_actions(text, expected):
    assert [a.strip() for a in miniswe.parse_actions(text)] == expected


def test_format_error_says_how_many_actions_were_found():
    rendered = miniswe.render(miniswe_prompts.FORMAT_ERROR_TEMPLATE, actions=["a", "b"])
    assert rendered.startswith(
        "Please always provide EXACTLY ONE action in triple backticks, found 2 actions."
    )
    # The re-ask no longer suggests submitting (see miniswe_prompts).
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in rendered
    assert "Please format your response exactly as follows:" in rendered


@pytest.mark.parametrize(
    "output, exit_code, expected",
    [
        ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 0, True),
        ("\n  COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT  \nrest\n", 0, True),
        ("MINI_SWE_AGENT_FINAL_OUTPUT\n", 0, True),
        ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 1, False),
        ("building\nCOMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 0, False),
        ("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 0, False),
        ("", 0, False),
    ],
)
def test_is_submission(output, exit_code, expected):
    assert miniswe.is_submission(output, exit_code) is expected


def test_short_observation_is_returncode_and_output():
    assert miniswe.observation("ls", "a\nb\n", 2, timed_out=False) == (
        "<returncode>2</returncode>\n<output>\na\nb\n</output>"
    )


def test_long_observation_keeps_5000_chars_each_end():
    output = "h" * 5000 + "m" * 2000 + "t" * 5000
    text = miniswe.observation("cat big", output, 0, timed_out=False)
    assert "<warning>\nThe output of your last command was too long." in text
    assert "<output_head>\n" + "h" * 5000 + "\n</output_head>" in text
    assert "<elided_chars>\n2000 characters elided\n</elided_chars>" in text
    assert "<output_tail>\n" + "t" * 5000 + "\n</output_tail>" in text
    assert "mmm" not in text


def test_timeout_observation_quotes_the_command_and_its_output():
    text = miniswe.observation("python serve.py", "Listening\n", 124, timed_out=True)
    assert text == (
        "The last command <command>python serve.py</command> timed out and has been "
        "killed.\nThe output of the command was:\n<output>\nListening\n\n</output>\n"
        "Please try another command and make sure to avoid those requiring "
        "interactive input."
    )


# ---- the loop ----------------------------------------------------------------


def test_submit_after_one_command():
    first = _reply("THOUGHT: write it.\n\n```bash\necho hi > /app/out.txt\n```")
    adapter = _Adapter([first, _reply(_SUBMIT)])
    sandbox = _Sandbox([(0, ""), (0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")])
    run = _run(adapter, sandbox)
    assert (run.turns, run.submitted, run.finish_reason) == (2, True, "submit")
    assert run.format_errors == 0
    # uname, then the two actions, each with upstream's environment and timeout.
    assert [c["cmd"] for c in sandbox.calls] == [
        miniswe._UNAME,
        "echo hi > /app/out.txt",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    ]
    assert all(c["env"] == miniswe_prompts.ENV for c in sandbox.calls)
    assert sandbox.calls[1]["timeout"] == miniswe._EXEC_TIMEOUT
    assert [set(e) >= {"t", "secs", "exit", "cmd"} for e in run.exec_trace] == [True] * 3
    # The assistant turn is echoed verbatim (thinking included) and the
    # observation follows it as a plain user message.
    second = adapter.requests[1]["messages"]
    assert second[1] == {"role": "assistant", "content": first["content"]}
    assert second[2] == {
        "role": "user",
        "content": "<returncode>0</returncode>\n<output>\n</output>",
    }


def test_format_errors_are_answered_and_counted():
    adapter = _Adapter(
        [
            _reply("I will list the files."),
            _reply("```bash\nls\n```\n```bash\npwd\n```"),
            _reply("```bash\nls\n```"),
            _reply(_SUBMIT),
        ]
    )
    run = _run(
        adapter, _Sandbox([(0, "f\n"), (0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")])
    )
    assert (run.turns, run.finish_reason, run.format_errors) == (4, "submit", 2)
    assert run.format_breakdown["no_commands"] == 1
    assert run.format_breakdown["other"] == 1
    assert run.format_breakdown["opening_streak"] == 2
    assert run.format_breakdown["max_streak"] == 2
    assert run.format_breakdown["replies"] == 4
    assert set(FORMAT_ERROR_CATEGORIES) <= set(run.format_breakdown)
    feedback = [r["messages"][-1]["content"] for r in adapter.requests[1:3]]
    assert "found 0 actions." in feedback[0]
    assert "found 2 actions." in feedback[1]


def test_format_streak_limit_ends_the_episode_on_the_nth_rejected_turn():
    replies = [_reply("Okay"), _reply("```bash\nls\n```")] + [_reply("Okay")] * 5
    run = _run(_Adapter(replies), _Sandbox([(0, "f\n")]), format_streak_limit=3)
    assert (run.turns, run.finish_reason, run.submitted) == (5, "format_error", False)
    assert (run.violation_category, run.violation_turn) == ("no_commands", 5)
    assert run.format_errors == 4


def test_no_format_streak_limit_keeps_answering_rejected_turns():
    run = _run(_Adapter([_reply("Okay")] * 5), _Sandbox())
    assert (run.turns, run.finish_reason, run.violation_turn) == (5, "stopped_early", 0)


def test_a_capped_turn_without_an_action_counts_as_truncated():
    adapter = _Adapter([_reply("THOUGHT: let me think at length", stop="max_tokens")])
    run = _run(adapter, _Sandbox(), max_context_tokens=80_000)
    assert run.finish_reason == "stopped_early"
    assert run.format_breakdown["truncated"] == 1
    assert run.format_breakdown["truncated_turns"] == 1


def test_a_capped_turn_with_one_complete_action_still_runs_it():
    adapter = _Adapter([_reply("```bash\nls\n```\nand then", stop="max_tokens"), _reply(_SUBMIT)])
    sandbox = _Sandbox([(0, "f\n"), (0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")])
    run = _run(adapter, sandbox, max_context_tokens=80_000)
    assert run.finish_reason == "submit"
    assert sandbox.calls[1]["cmd"] == "ls"


def test_the_context_wall_ends_the_episode():
    adapter = _Adapter([_reply("```bash\nls\n```", stop="max_tokens", tokens=(79_990, 10))])
    sandbox = _Sandbox()
    run = _run(adapter, sandbox, max_context_tokens=80_000)
    assert (run.turns, run.submitted, run.finish_reason) == (1, False, "hit_context_limit")
    assert [c["cmd"] for c in sandbox.calls] == [miniswe._UNAME]


def test_a_failed_submit_command_is_shown_like_any_other():
    adapter = _Adapter([_reply(_SUBMIT), _reply(_SUBMIT)])
    sandbox = _Sandbox(
        [(1, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"), (0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")]
    )
    run = _run(adapter, sandbox)
    assert (run.turns, run.finish_reason) == (2, "submit")
    assert adapter.requests[1]["messages"][-1]["content"].startswith(
        "<returncode>1</returncode>"
    )


def test_a_command_at_the_timeout_gets_the_timeout_message_and_cannot_submit():
    adapter = _Adapter([_reply(_SUBMIT)])
    sandbox = _Sandbox([(124, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 1.05)])
    run = _run(adapter, sandbox, exec_timeout=1)
    assert run.finish_reason == "stopped_early"
    assert sandbox.calls[1]["timeout"] == 1
    assert adapter.requests[-1]["messages"][-1]["content"].startswith(
        "The last command <command>echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT</command> "
        "timed out and has been killed."
    )


def test_a_fast_exit_124_is_the_commands_own_code():
    adapter = _Adapter([_reply("```bash\ntimeout 1 sleep 5\n```")])
    run = _run(adapter, _Sandbox([(124, "")]), exec_timeout=60)
    assert run.finish_reason == "stopped_early"


def test_turn_cap():
    adapter = _Adapter([_reply("```bash\nls\n```")] * 5)
    sandbox = _Sandbox([(0, "f\n")] * 5)
    run = _run(adapter, sandbox, max_turns=3)
    assert (run.turns, run.submitted, run.finish_reason) == (3, False, "hit_max_turns")
    assert len(adapter.requests) == 3


def test_default_turn_cap_applies_when_the_rollout_sets_none(monkeypatch):
    monkeypatch.setattr(miniswe, "_DEFAULT_MAX_TURNS", 2)
    run = _run(_Adapter([_reply("```bash\nls\n```")] * 5), _Sandbox([(0, "f\n")] * 5))
    assert (run.turns, run.finish_reason) == (2, "hit_max_turns")


def test_time_budget_interrupts_a_pending_generation():
    adapter = _Adapter([_reply(_SUBMIT)], delay=5)
    run = _run(adapter, _Sandbox(), time_budget_sec=1)
    assert (run.turns, run.submitted, run.finish_reason) == (0, False, "hit_time_budget")


def test_a_sandbox_failure_is_raised_not_scored():
    sandbox = MagicMock()

    async def broken(*_args, **_kwargs):
        raise TimeoutError("toolbox did not answer")

    sandbox.exec = broken
    with pytest.raises(TimeoutError, match="toolbox did not answer"):
        _run(_Adapter([]), sandbox)


def test_a_command_whose_status_never_arrives_reads_as_a_timeout(monkeypatch):
    monkeypatch.setattr(miniswe, "_EXEC_TIMEOUT", 0)
    sandbox = _Sandbox([(0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")])
    real_exec = sandbox.exec
    calls = {"n": 0}

    async def flaky(cmd, **kwargs):
        if cmd != miniswe._UNAME and calls["n"] == 0:
            calls["n"] += 1
            raise TimeoutError(
                "daytona exec status unavailable after 251s without replaying "
                "the command body (claim=present)"
            )
        return await real_exec(cmd, **kwargs)

    sandbox.exec = flaky
    adapter = _Adapter([_reply("```bash\nstrings /bin/ls\n```"), _reply(_SUBMIT)])
    run = _run(adapter, sandbox)
    assert (run.submitted, run.finish_reason) == (True, "submit")
    shown = adapter.requests[1]["messages"][-1]["content"]
    assert "timed out" in shown.lower()


def test_lenient_action_shapes():
    assert miniswe.lenient_action("x", ["ls", " pwd\n"]) == "ls\npwd"
    py = "Run it:\n```python\nprint(1)\n```"
    assert miniswe.lenient_action(py, []) == (
        "python3 - <<'__MINISWE_PYTHON__'\nprint(1)\n__MINISWE_PYTHON__"
    )
    assert miniswe.lenient_action("no block at all", []) is None
    two_py = "```python\na\n```\n```python\nb\n```"
    assert miniswe.lenient_action(two_py, []) is None


def test_lenient_parse_runs_two_blocks_when_on(monkeypatch):
    monkeypatch.setattr(miniswe, "_LENIENT_PARSE", True)
    sandbox = _Sandbox([(0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")])
    adapter = _Adapter([_reply("```bash\ncd /app\n```\n```bash\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n```")])
    run = _run(adapter, sandbox)
    assert run.submitted and run.format_errors == 0
    assert run.format_breakdown["lenient_parses"] == 1
    assert sandbox.calls[-1]["cmd"] == "cd /app\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


# ---- what the rollouter reads ------------------------------------------------


def test_finish_reasons_and_breakdown_feed_the_rollouter_metrics():
    runs = [
        _run(_Adapter([_reply(_SUBMIT)]), _Sandbox([(0, "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")])),
        _run(_Adapter([_reply("no action")]), _Sandbox()),
        _run(
            _Adapter([_reply("```bash\nls\n```", stop="max_tokens", tokens=(79_990, 10))]),
            _Sandbox(),
            max_context_tokens=80_000,
        ),
    ]
    reasons = [run.finish_reason for run in runs]
    assert set(reasons) <= set(_FINISH_REASONS)
    _finish_reason_metrics(reasons)
    names = {metric.key for metric in _format_breakdown_metrics([r.format_breakdown for r in runs])}
    assert "rollout/format_error_no_commands_frac" in names
    assert "rollout/truncated_turns_mean" in names
