# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Strict environment rules for the Terminus-2 harness.

Strict format rule: a turn the parser rejects ends the episode at that turn with
no grading (reward 0); the turn stays the trajectory's last and no re-ask is
generated. Strict truncation rule: a turn cut at the per-turn generation cap ends
it the same way. The end-to-end tests drive harbor's real Terminus-2 loop against
a scripted adapter, so the swallowing ``except Exception`` and the retrying
``_query_llm`` are the real ones.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from torchtitan.experiments.rl.harness.agents.terminus import (
    _AdapterLLM,
    _CountingParser,
    _FormatViolation,
)
from torchtitan.experiments.rl.harness.agents.terminus_xml import (
    SafeTerminusXMLParser,
)

_ANALYSIS = "<analysis>The file needs a fix before tests pass.</analysis>"
_PLAN = "<plan>Inspect the file, then edit it.</plan>"


def _resp(commands: str, *, wrap: bool = True) -> str:
    body = f"{_ANALYSIS}\n{_PLAN}\n{commands}"
    return f"<response>\n{body}\n</response>" if wrap else body


VALID = _resp('<commands><keystrokes duration="0.1">ls -la\n</keystrokes></commands>')
DONE = _resp("<commands></commands><task_complete>true</task_complete>")
REJECTED = {
    "missing_wrapper": _resp(
        '<commands><keystrokes duration="0.1">ls\n</keystrokes></commands>',
        wrap=False,
    ),
    "plain_text": "I will now look at the repository and run ls to see the files.",
    "no_commands": f"<response>{_ANALYSIS}{_PLAN}<commands><keystrokes>ls\n",
}
WARNING_ONLY = {
    "empty_commands": _resp("<commands></commands>"),
    "silent_noop": _resp("<commands>\nwc -l < a.py\n</commands>"),
}
TRUNCATED = f"<response>{_ANALYSIS}{_PLAN}<commands><keystrokes>cat big"


class _ScriptedAdapter:
    """``adapter.complete`` replaying (text, stop_reason, input, output) replies."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def session_max_tokens(self, _session_id):
        return 512

    async def complete(self, _session_id, _payload):
        self.calls += 1
        if not self._replies:
            return None
        reply = self._replies.pop(0)
        if isinstance(reply, str):
            reply = (reply, "end_turn", 100, 50)
        text, stop, in_tok, out_tok = reply
        return {
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
        }


def _llm(replies=(), **kwargs) -> _AdapterLLM:
    return _AdapterLLM(
        _ScriptedAdapter(replies),
        session_id="group=0/rollout=0",
        max_context=63488,
        turn_max_tokens=512,
        **kwargs,
    )


def _parse(reply: str, *, strict: bool, llm: _AdapterLLM | None = None):
    llm = llm or _llm()
    llm.calls += 1  # the reply arrived through the LLM
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_format=strict)
    parser.parse_response(reply)
    return llm


# ---- parser level ---------------------------------------------------------


@pytest.mark.parametrize("category", sorted(REJECTED))
def test_a_rejected_turn_sets_the_pending_violation(category):
    llm = _parse(REJECTED[category], strict=True)
    assert (llm.violation, llm.violation_category, llm.violation_turn) == (
        "format",
        category,
        1,
    )
    # The next call raises before reaching the model.
    with pytest.raises(_FormatViolation):
        asyncio.run(llm.call(prompt="next"))
    assert llm._adapter.calls == 0


@pytest.mark.parametrize("category", sorted(WARNING_ONLY))
def test_warning_only_turns_do_not_trigger(category):
    assert _parse(WARNING_ONLY[category], strict=True).violation is None


@pytest.mark.parametrize("reply", [*REJECTED.values(), *WARNING_ONLY.values()])
def test_lenient_mode_never_flags(reply):
    assert _parse(reply, strict=False).violation is None


def test_degenerate_triggers_only_with_a_parser_error():
    # Short and wrapper-only: the parser also reports a missing <commands>.
    assert _parse("<response><analysis>ok</analysis></response>", strict=True).violation
    # A repeat of a warning-only reply is degenerate but carries no error.
    llm = _llm()
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_format=True)
    parser.parse_response(WARNING_ONLY["empty_commands"])
    parser.parse_response(WARNING_ONLY["empty_commands"])
    assert parser.categories["degenerate"] == 1
    assert llm.violation is None


def test_valid_and_complete_turns_do_not_trigger():
    llm = _llm()
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_format=True)
    for reply in (VALID, DONE, DONE):
        parser.parse_response(reply)
    assert llm.violation is None


@pytest.mark.parametrize("category", sorted(REJECTED))
def test_opening_rule_ends_a_rejected_first_turn(category):
    llm = _llm()
    llm.calls += 1
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_opening=True)
    parser.parse_response(REJECTED[category])
    assert (llm.violation, llm.violation_category) == ("format", category)


@pytest.mark.parametrize("category", sorted(REJECTED))
def test_opening_rule_ignores_rejected_turns_after_the_first_usable_one(category):
    llm = _llm()
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_opening=True)
    for reply in (VALID, REJECTED[category]):
        llm.calls += 1
        parser.parse_response(reply)
    assert llm.violation is None


def test_opening_rule_still_counts_warning_only_turns_as_opening():
    """Empty commands move nothing, so a rejected turn after them is still the
    opening; the warning-only turn itself does not trigger."""
    llm = _llm()
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_opening=True)
    llm.calls += 1
    parser.parse_response(WARNING_ONLY["empty_commands"])
    assert llm.violation is None
    llm.calls += 1
    parser.parse_response(REJECTED["plain_text"])
    assert (llm.violation, llm.violation_turn) == ("format", 2)


def test_reparsing_a_capped_turn_is_left_to_the_truncation_rule():
    """Lenient truncation re-parses the capped text for warnings; that is not a
    rejected turn of its own, even with the format rule on."""
    from harbor.llms.base import OutputLengthExceededError

    llm = _llm([(TRUNCATED, "max_tokens", 100, 512)])
    with pytest.raises(OutputLengthExceededError):
        asyncio.run(llm.call(prompt="go"))
    parser = _CountingParser(SafeTerminusXMLParser(), llm=llm, strict_format=True)
    parser.parse_response(TRUNCATED)
    assert parser.categories["truncated"] == 1
    assert llm.violation is None


def test_a_capped_turn_in_strict_mode_terminates():
    llm = _llm([(TRUNCATED, "max_tokens", 100, 512), VALID], strict_truncation=True)
    with pytest.raises(_FormatViolation):
        asyncio.run(llm.call(prompt="go"))
    assert (llm.violation, llm.violation_category, llm.violation_turn) == (
        "truncation",
        "truncated",
        1,
    )
    assert llm.truncated_turns == 1
    # Sticky: harbor's retries and any later call end without generating.
    with pytest.raises(_FormatViolation):
        asyncio.run(llm.call(prompt="again"))
    assert llm._adapter.calls == 1


def test_the_context_wall_is_unchanged_in_strict_mode():
    from harbor.llms.base import ContextLengthExceededError

    llm = _llm([(TRUNCATED, "max_tokens", 63000, 488)], strict_truncation=True)
    with pytest.raises(ContextLengthExceededError):
        asyncio.run(llm.call(prompt="go"))
    assert llm.violation is None


# ---- terminus_agent end to end, through harbor's real Terminus-2 loop -----


class _FakeSession:
    _session_name = "test-session"

    def __init__(self):
        self.keys: list[str] = []

    async def is_session_alive(self):
        return True

    async def send_keys(self, keys, block=False, min_timeout_sec=0.0, **_kwargs):
        self.keys.append(keys)

    async def capture_pane(self, capture_entire=False):
        return "$ "

    async def get_incremental_output(self):
        return "Current Terminal Screen:\n$ "


def _run_terminus(
    monkeypatch,
    replies,
    *,
    strict_format,
    strict_truncation,
    strict_opening=False,
    max_turns=8,
):
    from harbor.agents.terminus_2 import Terminus2

    from torchtitan.experiments.rl.harness.agents import terminus
    from torchtitan.experiments.rl.harness.agents.spec import AgentTask
    from torchtitan.experiments.rl.harness.agents.terminus_terminal import (
        TerminalLifecycle,
    )

    for name in ("prepare", "bind", "isolate"):
        monkeypatch.setattr(TerminalLifecycle, name, AsyncMock())
    session = _FakeSession()

    async def setup(self, _env):
        self._session = session

    async def observe(self, _session):
        return "Current Terminal Screen:\n$ "

    monkeypatch.setattr(Terminus2, "setup", setup)
    monkeypatch.setattr(terminus._SandboxEnvironment, "observe_turn", observe)
    adapter = _ScriptedAdapter(replies)
    sandbox = MagicMock()
    sandbox.exec = AsyncMock(return_value=(1, "", ""))
    run = asyncio.run(
        terminus.terminus_agent(
            AgentTask(
                sandbox=sandbox,
                instruction="do the thing",
                session_id="group=0/rollout=0",
                adapter=adapter,
                time_budget_sec=600,
                max_turns=max_turns,
                max_context_tokens=63488,
                strict_format=strict_format,
                strict_opening=strict_opening,
                strict_truncation=strict_truncation,
            )
        )
    )
    return run, adapter, session


def test_strict_a_missing_response_tag_ends_the_episode_on_that_turn(monkeypatch):
    run, adapter, session = _run_terminus(
        monkeypatch,
        [VALID, REJECTED["missing_wrapper"], DONE, DONE],
        strict_format=True,
        strict_truncation=True,
    )
    assert (run.finish_reason, run.submitted) == ("format_error", False)
    assert (run.violation_category, run.violation_turn) == ("missing_wrapper", 2)
    # The rejected turn was the last one generated: no re-ask turn.
    assert adapter.calls == 2
    assert run.turns == 2
    assert session.keys == ["ls -la\n"]


def test_strict_an_opening_rejected_turn_is_turn_one(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [REJECTED["plain_text"], DONE, DONE],
        strict_format=True,
        strict_truncation=False,
    )
    assert (run.finish_reason, run.violation_turn, adapter.calls) == (
        "format_error",
        1,
        1,
    )


def test_opening_rule_ends_a_rejected_first_turn_in_the_harness(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [REJECTED["missing_wrapper"], DONE, DONE],
        strict_format=False,
        strict_truncation=False,
        strict_opening=True,
    )
    assert (run.finish_reason, run.submitted, run.violation_turn) == (
        "format_error",
        False,
        1,
    )
    assert adapter.calls == 1


def test_opening_rule_re_asks_after_the_first_usable_turn(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [VALID, REJECTED["plain_text"], DONE, DONE],
        strict_format=False,
        strict_truncation=False,
        strict_opening=True,
    )
    assert (run.finish_reason, run.submitted) == ("submit", True)
    assert run.violation_turn == 0
    assert adapter.calls == 4


def test_strict_a_rejected_turn_on_the_last_episode_still_counts(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [VALID, REJECTED["no_commands"]],
        strict_format=True,
        strict_truncation=True,
        max_turns=2,
    )
    assert (run.finish_reason, run.submitted, run.violation_turn) == (
        "format_error",
        False,
        2,
    )
    assert adapter.calls == 2


def test_lenient_mode_re_asks_as_before(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [REJECTED["missing_wrapper"], DONE, DONE],
        strict_format=False,
        strict_truncation=False,
    )
    assert (run.finish_reason, run.submitted) == ("submit", True)
    assert (run.violation_category, run.violation_turn) == (None, 0)
    assert adapter.calls == 3
    assert run.format_breakdown["missing_wrapper"] == 1


def test_strict_warning_only_turns_keep_going(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [WARNING_ONLY["empty_commands"], WARNING_ONLY["silent_noop"], DONE, DONE],
        strict_format=True,
        strict_truncation=True,
    )
    assert (run.finish_reason, run.submitted, adapter.calls) == ("submit", True, 4)


def test_strict_truncation_ends_on_the_capped_turn(monkeypatch):
    run, adapter, session = _run_terminus(
        monkeypatch,
        [VALID, (TRUNCATED, "max_tokens", 1000, 512), DONE, DONE],
        strict_format=True,
        strict_truncation=True,
    )
    assert (run.finish_reason, run.submitted) == ("truncated", False)
    assert (run.violation_category, run.violation_turn) == ("truncated", 2)
    assert adapter.calls == 2
    assert run.turns == 2
    assert run.format_breakdown["truncated_turns"] == 1


def test_lenient_truncation_re_asks_even_with_strict_format(monkeypatch):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [(TRUNCATED, "max_tokens", 1000, 512), DONE, DONE],
        strict_format=True,
        strict_truncation=False,
    )
    assert (run.finish_reason, run.submitted, adapter.calls) == ("submit", True, 3)


@pytest.mark.parametrize("strict", [False, True])
def test_the_context_limit_ending_is_unchanged(monkeypatch, strict):
    run, adapter, _ = _run_terminus(
        monkeypatch,
        [VALID, (TRUNCATED, "max_tokens", 63000, 488)],
        strict_format=strict,
        strict_truncation=strict,
    )
    assert (run.finish_reason, run.submitted) == ("hit_context_limit", False)
    assert (run.violation_category, run.violation_turn) == (None, 0)
    assert adapter.calls == 2


# ---- rollouter --------------------------------------------------------------


def test_the_new_finish_reasons_are_accepted_by_the_metrics():
    from torchtitan.experiments.rl.examples.tmax.rollouter import (
        _finish_reason_metrics,
    )

    fractions = {
        metric.key: metric.value.value / metric.value.count
        for metric in _finish_reason_metrics(
            ["format_error", "truncated", "submit", "disk_exhausted"]
        )
    }
    assert fractions["rollout/finish_format_error_frac"] == 0.25
    assert fractions["rollout/finish_truncated_frac"] == 0.25
    assert fractions["rollout/finish_disk_exhausted_frac"] == 0.25


def _rollout(i, reward, finish_reason="submit", violation_turn=0):
    from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus

    return Rollout(
        group_id=3,
        rollout_id=i,
        status=RolloutStatus.COMPLETED,
        reward=reward,
        turns=[object()],
        diagnostics={
            "record": f"attempt-{i}.jsonl",
            "finish_reason": finish_reason,
            "violation_turn": violation_turn,
        },
    )


def test_rule_termination_metrics():
    from torchtitan.experiments.rl.examples.tmax.rollouter import (
        _rule_termination_metrics,
    )

    rollouts = [
        _rollout(0, 0.0, "format_error", 1),
        _rollout(1, 0.0, "truncated", 5),
        _rollout(2, 1.0),
        _rollout(3, 0.0, "hit_max_turns"),
    ]
    values = {
        metric.key: metric.value.value / metric.value.count
        for metric in _rule_termination_metrics(rollouts)
    }
    assert values == {
        "rollout/format_terminated_frac": 0.25,
        "rollout/truncation_terminated_frac": 0.25,
        "rollout/rule_terminated_opening_frac": 0.25,
        "rollout/rule_terminated_turn_mean": 3.0,
    }
    clean = _rule_termination_metrics([_rollout(0, 1.0), _rollout(1, 0.0)])
    assert "rollout/rule_terminated_turn_mean" not in {m.key for m in clean}


def _emit(monkeypatch, tmp_path, rollouts):
    from torchtitan.experiments.rl.examples.tmax import rollouter

    monkeypatch.setenv("TRL_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SWE_EVOLUTION_SIGNALS", "1")
    worker = object.__new__(rollouter.TMaxRollouter)
    worker._reward_mode = "sparse"
    worker._evolution_harder_ratio = 1.0
    worker._evolution_easier_ratio = 0.0
    worker._evolution_min_scored = 2
    worker._maybe_emit_evolution_signal(
        SimpleNamespace(instance_id="task", rev=0, image="image"), rollouts
    )
    return [json.loads(p.read_text()) for p in tmp_path.glob("signals/*.json")]


def test_evolution_signal_ignores_rule_terminated_rollouts(monkeypatch, tmp_path):
    # Every graded attempt solved; the terminated zeros must not hide that.
    signals = _emit(
        monkeypatch,
        tmp_path,
        [
            _rollout(0, 1.0),
            _rollout(1, 0.0, "format_error", 1),
            _rollout(2, 1.0),
            _rollout(3, 0.0, "truncated", 4),
        ],
    )
    assert len(signals) == 1
    signal = signals[0]
    assert (signal["direction"], signal["solved"], signal["total"]) == ("harder", 2, 2)
    assert signal["attempts"] == ["attempt-0.jsonl", "attempt-2.jsonl"]


def test_an_all_terminated_group_is_not_an_easier_signal(monkeypatch, tmp_path):
    signals = _emit(
        monkeypatch,
        tmp_path,
        [
            _rollout(0, 0.0, "format_error", 1),
            _rollout(1, 0.0, "format_error", 1),
            _rollout(2, 0.0, "hit_max_turns"),
        ],
    )
    assert signals == []


def _rollouter_for_agent_task(*, strict_format, strict_truncation, eval_format, eval_truncation):
    from torchtitan.experiments.rl.examples.tmax.rollouter import (
        _RolloutIssueGate,
        TMaxRollouter,
    )

    r = object.__new__(TMaxRollouter)
    r._agent_name = "terminus"
    r._time_budget_sec = 3600
    r._agent_budget_floor_sec = 0
    r._eval_timeout_sec = 600
    r._max_context_tokens = 4096
    r._eval_max_context_tokens = 4096
    r._eval_max_turns = 0
    r._eval_agent_budget_cap_sec = 0
    r._eval_verifier_budget_cap_sec = 0
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._shadow_grade_unsubmitted = True
    r._eval_shadow_grade_timeouts = True
    r._eval_grade_unsubmitted = False
    r._grade_unsubmitted = False
    r._eval_context_notice = True
    r._length_penalty = False
    r._strict_format = strict_format
    r._strict_opening = False
    r._strict_truncation = strict_truncation
    r._eval_strict_format = eval_format
    r._eval_strict_opening = False
    r._eval_strict_truncation = eval_truncation
    r._rollout_gate = _RolloutIssueGate(1)
    return r


def _run_rollout(monkeypatch, rollouter, *, group_id, agent_run):
    import contextlib

    from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
    from torchtitan.experiments.rl.examples.tmax.data import TMaxSample
    from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn

    tasks = []
    grades = []

    @contextlib.asynccontextmanager
    async def fake_boot(*_args, **_kwargs):
        sandbox = AsyncMock()
        sandbox.sandbox_id = "sandbox-0"
        yield sandbox

    async def fake_agent(task):
        tasks.append(task)
        return agent_run

    async def fake_grade(*_args, **kwargs):
        grades.append(kwargs)
        return 1.0

    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", fake_boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda _name: fake_agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
    monkeypatch.setattr(rollouter_mod, "capture_baseline", AsyncMock(return_value=None))
    monkeypatch.setattr(rollouter_mod, "_net_probe", AsyncMock(return_value={}))
    monkeypatch.setattr(rollouter_mod, "grade_tmax", fake_grade)
    adapter = AsyncMock()
    adapter.open_session = lambda *a, **k: None
    adapter.finish_session = AsyncMock(
        return_value=[
            CapturedTurn(
                prompt_token_ids=[1, 2],
                completion_token_ids=[3],
                completion_logprobs=[-0.1],
                min_policy_version=0,
                max_policy_version=0,
                finish_reason="stop",
                extends_previous=False,
            )
        ]
    )
    rollout, *_ = asyncio.run(
        rollouter._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=TMaxSample(
                instance_id="task-1",
                image="example/image",
                workdir="/app",
                problem_statement="do the thing",
            ),
            group_id=group_id,
            rollout_idx=0,
            sampling=SimpleNamespace(seed=0),
            renderer=object(),
        )
    )
    return rollout, tasks[0], grades


@pytest.mark.parametrize(
    "group_id,expected", [(0, (True, True)), (-1, (False, False))]
)
def test_validation_groups_are_lenient_by_default(monkeypatch, group_id, expected):
    from torchtitan.experiments.rl.harness.agents.spec import AgentRun

    rollouter = _rollouter_for_agent_task(
        strict_format=True, strict_truncation=True, eval_format=False, eval_truncation=False
    )
    _, task, _ = _run_rollout(
        monkeypatch,
        rollouter,
        group_id=group_id,
        agent_run=AgentRun(turns=1, submitted=True, finish_reason="submit"),
    )
    assert (task.strict_format, task.strict_truncation) == expected


def test_eval_switches_reach_validation_groups(monkeypatch):
    from torchtitan.experiments.rl.harness.agents.spec import AgentRun

    rollouter = _rollouter_for_agent_task(
        strict_format=False, strict_truncation=False, eval_format=True, eval_truncation=True
    )
    _, task, _ = _run_rollout(
        monkeypatch,
        rollouter,
        group_id=-1,
        agent_run=AgentRun(turns=1, submitted=True, finish_reason="submit"),
    )
    assert (task.strict_format, task.strict_truncation) == (True, True)


def test_a_rule_terminated_rollout_scores_zero_and_is_not_shadow_graded(monkeypatch):
    from torchtitan.experiments.rl.harness.agents.spec import AgentRun

    rollouter = _rollouter_for_agent_task(
        strict_format=True, strict_truncation=True, eval_format=False, eval_truncation=False
    )
    rollout, _, grades = _run_rollout(
        monkeypatch,
        rollouter,
        group_id=0,
        agent_run=AgentRun(
            turns=3,
            submitted=False,
            finish_reason="format_error",
            violation_category="missing_wrapper",
            violation_turn=3,
        ),
    )
    assert grades == []
    assert rollout.turns[-1].env_rewards == {"tmax_reward": 0.0}
    assert rollout.diagnostics["finish_reason"] == "format_error"
    assert rollout.diagnostics["violation_turn"] == 3
    assert rollout.diagnostics["shadow_reward"] is None
    assert rollout.diagnostics["infra_failed"] is False


def test_other_unsubmitted_endings_are_still_shadow_graded(monkeypatch):
    from torchtitan.experiments.rl.harness.agents.spec import AgentRun

    rollouter = _rollouter_for_agent_task(
        strict_format=True, strict_truncation=True, eval_format=False, eval_truncation=False
    )
    rollout, _, grades = _run_rollout(
        monkeypatch,
        rollouter,
        group_id=0,
        agent_run=AgentRun(turns=3, submitted=False, finish_reason="hit_context_limit"),
    )
    assert len(grades) == 1
    assert rollout.diagnostics["shadow_reward"] == 1.0


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, (False, True, False, False, False, False)),
        (
            {
                "SWE_STRICT_FORMAT": "1",
                "SWE_STRICT_OPENING": "0",
                "SWE_STRICT_TRUNCATION": "1",
                "SWE_EVAL_STRICT_FORMAT": "1",
                "SWE_EVAL_STRICT_OPENING": "1",
                "SWE_EVAL_STRICT_TRUNCATION": "1",
            },
            (True, False, True, True, True, True),
        ),
    ],
)
def test_config_registry_defaults(monkeypatch, env, expected):
    """Training checks only the opening, validation is lenient, and each switch
    is read from its own env."""
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        _tmax_rollouter,
    )

    for name in (
        "SWE_STRICT_FORMAT",
        "SWE_STRICT_OPENING",
        "SWE_STRICT_TRUNCATION",
        "SWE_EVAL_STRICT_FORMAT",
        "SWE_EVAL_STRICT_OPENING",
        "SWE_EVAL_STRICT_TRUNCATION",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    r = _tmax_rollouter(validation_from_caller=True)
    assert (
        r.strict_format,
        r.strict_opening,
        r.strict_truncation,
        r.eval_strict_format,
        r.eval_strict_opening,
        r.eval_strict_truncation,
    ) == expected
