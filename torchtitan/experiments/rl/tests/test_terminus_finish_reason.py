# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""How a Terminus-2 trajectory ended, and why "ended early" is not "submitted".

Terminus-2's episode loop has three exits and only one is a submit:

1. it runs the episodes out (``_n_episodes == max_turns``);
2. it returns early on a CONFIRMED ``<task_complete>true</task_complete>`` -- the
   second consecutive one, at which point ``_pending_completion`` is still set;
3. it returns early because ``is_session_alive()`` went false, i.e. the tmux
   session died under it, with no completion claimed at all.

Reading "ended before the cap" as the submit signal folds 3 into 2 and reports a
dead session as a real attempt, which then scores 0 and looks like a model failure.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeParser:
    """The one method Terminus-2 reaches its parser through."""

    def __init__(self, results=()):
        self._results = list(results)

    def parse_response(self, _content):
        return self._results.pop(0) if self._results else MagicMock()


class _FakeTerminus2:
    """Stands in for harbor's Terminus2, replaying one of the three exits."""

    def __init__(
        self,
        *,
        episodes: int,
        pending_completion: bool,
        raises=None,
        parse_results=(),
        subagent_calls=0,
        **kwargs,
    ):
        self._episodes = episodes
        self._pending_completion = pending_completion
        self._raises = raises
        self._subagent_calls = subagent_calls
        self._n_episodes = 0
        self._llm = None
        # The real Terminus2 builds one in __init__; terminus.py wraps it to count
        # the turns it could not turn into an action.
        self._parser = _FakeParser(parse_results)
        self._parse_results = list(parse_results)
        # Record what terminus.py asked for, so a test can assert on the config it
        # passes upstream (summarization in particular).
        self.init_kwargs = kwargs

    async def _run_subagent(self, **_kwargs):
        """Terminus-2 routes its summarization subagents through here, and through
        the same self._llm, which is why terminus.py wraps it to count them."""
        return MagicMock(), MagicMock()

    async def setup(self, _env) -> None:
        return None

    async def run(self, _instruction, _env, _context) -> None:
        # The real loop assigns _n_episodes at the top of each iteration, so the
        # count survives an exception raised mid-episode.
        self._n_episodes = self._episodes
        # Terminus-2 parses one response per episode; replay that so the wrapper
        # sees the same call pattern.
        for _ in range(len(self._parse_results)):
            self._parser.parse_response("<response/>")
        for _ in range(self._subagent_calls):
            await self._run_subagent()
        if self._raises is not None:
            raise self._raises


def _install_fake_harbor(monkeypatch, built: list, **agent_kwargs) -> None:
    """Import terminus.py against a stub harbor (the real one needs a sandbox).

    ``built`` collects the fake agents that get constructed, so a test can assert on
    the configuration terminus.py passed upstream.
    """
    # The real exception types: terminus.py branches on them, so substituting
    # look-alikes would let a wrong branch pass.
    from harbor.llms.base import ContextLengthExceededError

    for name in ("harbor", "harbor.agents", "harbor.agents.terminus_2"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    def _build(**kwargs):
        agent = _FakeTerminus2(**agent_kwargs, **kwargs)
        built.append(agent)
        return agent

    monkeypatch.setattr(
        sys.modules["harbor.agents.terminus_2"], "Terminus2", _build, raising=False
    )
    llms_base = types.ModuleType("harbor.llms.base")
    llms_base.ContextLengthExceededError = ContextLengthExceededError  # pyrefly: ignore
    for name in ("harbor.llms",):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "harbor.llms.base", llms_base)
    context_mod = types.ModuleType("harbor.models.agent.context")
    context_mod.AgentContext = MagicMock  # pyrefly: ignore
    for name in ("harbor.models", "harbor.models.agent"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "harbor.models.agent.context", context_mod)


def _run(monkeypatch, *, max_turns: int, built: list | None = None, **agent_kwargs):
    from torchtitan.experiments.rl.harness.agents.spec import AgentTask
    from torchtitan.experiments.rl.harness.agents.terminus import terminus_agent

    _install_fake_harbor(
        monkeypatch, built if built is not None else [], **agent_kwargs
    )
    sandbox = MagicMock()

    async def _exec(*_args, **_kwargs):
        return 0, "", ""

    sandbox.exec = _exec
    return asyncio.run(
        terminus_agent(
            AgentTask(
                sandbox=sandbox,
                instruction="do the thing",
                session_id="sess-1",
                adapter=MagicMock(),
                time_budget_sec=60,
                max_turns=max_turns,
                workdir=str(Path("/app")),
            )
        )
    )


def test_confirmed_task_complete_is_a_submit(monkeypatch):
    run = _run(monkeypatch, max_turns=10, episodes=4, pending_completion=True)
    assert (run.finish_reason, run.submitted, run.turns) == ("submit", True, 4)


def test_a_dead_session_is_stopped_early_not_a_submit(monkeypatch):
    """The regression: exit 3 used to report submit just for ending under the cap."""
    run = _run(monkeypatch, max_turns=10, episodes=4, pending_completion=False)
    assert (run.finish_reason, run.submitted, run.turns) == ("stopped_early", False, 4)


def test_running_the_episodes_out_hits_max_turns(monkeypatch):
    run = _run(monkeypatch, max_turns=10, episodes=10, pending_completion=False)
    assert (run.finish_reason, run.submitted, run.turns) == ("hit_max_turns", False, 10)


def test_max_turns_wins_over_a_pending_completion(monkeypatch):
    """One unconfirmed task_complete on the last episode is not a submit."""
    run = _run(monkeypatch, max_turns=10, episodes=10, pending_completion=True)
    assert run.finish_reason == "hit_max_turns"
    assert run.submitted is False


def test_an_error_keeps_the_episodes_it_got_through(monkeypatch):
    """Reporting 0 turns here misattributes turns that are still trained on."""
    run = _run(
        monkeypatch,
        max_turns=10,
        episodes=6,
        pending_completion=False,
        raises=RuntimeError("adapter returned no completion"),
    )
    assert (run.finish_reason, run.submitted, run.turns) == ("error", False, 6)


def test_sandbox_api_error_is_not_returned_as_an_unsuccessful_attempt(monkeypatch):
    from torchtitan.experiments.rl.harness.agents.terminus import (
        _SandboxEnvironment,
        _SandboxExecutionError,
    )

    provider_error = RuntimeError("Failed to create session:")
    sandbox = MagicMock()
    sandbox.exec = AsyncMock(side_effect=provider_error)
    env = _SandboxEnvironment(sandbox, agent_dir=Path("."))
    with pytest.raises(_SandboxExecutionError) as raised:
        asyncio.run(env.exec("which firewall-cmd"))
    assert raised.value.__cause__ is provider_error
    with pytest.raises(_SandboxExecutionError):
        _run(
            monkeypatch,
            max_turns=10,
            episodes=6,
            pending_completion=False,
            raises=raised.value,
        )


def test_nonzero_command_exit_remains_a_command_result():
    from torchtitan.experiments.rl.harness.agents.terminus import _SandboxEnvironment

    sandbox = MagicMock()
    sandbox.exec = AsyncMock(return_value=(1, "", "command failed"))
    env = _SandboxEnvironment(sandbox, agent_dir=Path("."))
    result = asyncio.run(env.exec("false"))
    assert result.return_code == 1
    assert result.stderr == "command failed"


@pytest.mark.parametrize(
    "reason",
    [
        "submit",
        "stopped_early",
        "hit_max_turns",
        "hit_time_budget",
        "hit_context_limit",
        "error",
    ],
)
def test_every_reported_reason_is_one_the_rollouter_accepts(reason):
    """The rollouter validates finish_reason; an unknown one would fail a rollout."""
    from torchtitan.experiments.rl.examples.tmax.rollouter import _FINISH_REASONS

    assert reason in _FINISH_REASONS


def test_a_full_context_is_its_own_ending_not_an_error(monkeypatch):
    """With summarization off, running out of context is the ordinary way a
    trajectory ends. Pooling it into "error" hides how often the context, rather
    than the task, is what stopped the agent."""
    from harbor.llms.base import ContextLengthExceededError

    run = _run(
        monkeypatch,
        max_turns=150,
        episodes=23,
        pending_completion=False,
        raises=ContextLengthExceededError("full"),
    )
    assert (run.finish_reason, run.submitted, run.turns) == (
        "hit_context_limit",
        False,
        23,
    )


def test_summarization_is_off_unless_asked_for(monkeypatch):
    """Upstream defaults it on, where it fires below 8k free context and runs a
    three-subagent prompt chain through our adapter. Measured on a 9B, every trial
    that summarized scored zero."""
    built: list = []
    _run(monkeypatch, max_turns=150, episodes=1, pending_completion=False, built=built)

    assert built[0].init_kwargs["enable_summarize"] is False
    # Belt and braces: the proactive path is thresholded separately, and 0 disables
    # it, so an upstream default flipping would not quietly re-enable it.
    assert built[0].init_kwargs["proactive_summarization_threshold"] == 0


def test_the_summarize_flag_restores_upstream_behavior(monkeypatch):
    """Kept switchable so fidelity against published numbers can be A/B'd."""
    import torchtitan.experiments.rl.harness.agents.terminus as terminus

    monkeypatch.setattr(terminus, "_SUMMARIZE", True)
    monkeypatch.setattr(terminus, "_PROACTIVE_SUMMARIZE_THRESHOLD", 8192)
    built: list = []
    _run(monkeypatch, max_turns=150, episodes=1, pending_completion=False, built=built)

    assert built[0].init_kwargs["enable_summarize"] is True
    assert built[0].init_kwargs["proactive_summarization_threshold"] == 8192


# --------------------------------------------------------------------------
# format_errors. Nothing was ever filling this in -- every rollout reported 0,
# which reads as "the policy speaks the XML fine" but meant "never measured".
# Over a 9B Terminal-Bench 2.0 pass, 9.4% of turns emitted no keystrokes at all.
# --------------------------------------------------------------------------


def _parse_result(*, commands=(), is_task_complete=False):
    result = MagicMock()
    result.commands = list(commands)
    result.is_task_complete = is_task_complete
    return result


def test_turns_that_moved_the_terminal_are_not_format_errors(monkeypatch):
    run = _run(
        monkeypatch,
        max_turns=150,
        episodes=3,
        pending_completion=True,
        parse_results=[
            _parse_result(commands=["ls"]),
            _parse_result(commands=["cd /app", "make"]),
            _parse_result(is_task_complete=True),
        ],
    )
    assert run.format_errors == 0


def test_a_turn_with_no_parsable_action_is_counted(monkeypatch):
    """Neither a command nor a completion signal: whatever the model emitted, the
    terminal did not move."""
    run = _run(
        monkeypatch,
        max_turns=150,
        episodes=3,
        pending_completion=False,
        parse_results=[
            _parse_result(commands=["ls"]),
            _parse_result(),
            _parse_result(),
        ],
    )
    assert run.format_errors == 2


def test_format_errors_survive_a_crash(monkeypatch):
    """They describe turns already captured for training, so an exception later in
    the trajectory must not discard them."""
    run = _run(
        monkeypatch,
        max_turns=150,
        episodes=2,
        pending_completion=False,
        parse_results=[_parse_result(), _parse_result()],
        raises=RuntimeError("boom"),
    )
    assert (run.finish_reason, run.format_errors) == ("error", 2)


def test_captured_subagent_calls_are_reported(monkeypatch, caplog):
    """A counter nothing reads is the same defect as format_errors was. With
    summarization on, those calls are prose in the training data and a run has to be
    able to see how many it took."""
    import logging

    import torchtitan.experiments.rl.harness.agents.terminus as terminus

    monkeypatch.setattr(terminus, "_SUMMARIZE", True)
    built: list = []
    with caplog.at_level(logging.WARNING, logger=terminus.logger.name):
        _run(
            monkeypatch,
            max_turns=150,
            episodes=1,
            pending_completion=False,
            built=built,
            subagent_calls=3,
        )

    assert "3 summarization subagent call(s)" in caplog.text


def test_no_subagent_calls_stays_quiet(monkeypatch, caplog):
    import logging

    import torchtitan.experiments.rl.harness.agents.terminus as terminus

    with caplog.at_level(logging.WARNING, logger=terminus.logger.name):
        _run(monkeypatch, max_turns=150, episodes=1, pending_completion=False)

    assert "subagent call" not in caplog.text


def test_the_counting_parser_delegates_everything_else():
    """Terminus-2 feature-detects salvage_truncated_response on the parser, so the
    wrapper has to be transparent."""
    from torchtitan.experiments.rl.harness.agents.terminus import _CountingParser

    class _Inner:
        def salvage_truncated_response(self, _text):
            return "<response/>", False

        def parse_response(self, _content):
            return _parse_result(commands=["ls"])

    wrapped = _CountingParser(_Inner())
    assert hasattr(wrapped, "salvage_truncated_response")
    assert wrapped.salvage_truncated_response("x") == ("<response/>", False)


# --------------------------------------------------------------------------
# The wall-clock budget. The vanillux loop stops at a turn boundary once the
# rollout's budget is spent; Terminus-2's loop is inside harbor, so the one hook
# that runs per episode is the LLM call. Without this Terminus-2 runs until the
# rollouter's outer guard kills it, which lands as an infra failure -- 28 of the
# 445 trials in a 9B pass ended that way -- rather than a graded attempt.
# --------------------------------------------------------------------------


def test_a_spent_budget_stops_before_the_next_episode():
    from torchtitan.experiments.rl.harness.agents.terminus import (
        _AdapterLLM,
        _TimeBudgetExhausted,
    )

    class _Adapter:
        async def complete(self, _session_id, _payload):
            raise AssertionError("must not ask the policy past the deadline")

    llm = _AdapterLLM(
        _Adapter(),
        session_id="group=-1/rollout=0",
        max_context=63488,
        turn_max_tokens=16384,
        deadline=0.0,
    )
    with pytest.raises(_TimeBudgetExhausted):
        asyncio.run(llm.call(prompt="go"))


def test_no_budget_means_no_deadline():
    """time_budget_sec is optional on AgentTask; absent, nothing should expire."""
    llm = _adapter_llm(_reply("<response/>", "end_turn"))
    assert llm._deadline is None
    assert asyncio.run(llm.call(prompt="go")).content == "<response/>"


def test_running_out_of_budget_is_reported_as_such(monkeypatch):
    from torchtitan.experiments.rl.harness.agents.terminus import _TimeBudgetExhausted

    run = _run(
        monkeypatch,
        max_turns=150,
        episodes=9,
        pending_completion=False,
        raises=_TimeBudgetExhausted("spent"),
    )
    assert (run.finish_reason, run.submitted, run.turns) == (
        "hit_time_budget",
        False,
        9,
    )


def test_budget_cancels_an_in_flight_episode(monkeypatch):
    import torchtitan.experiments.rl.harness.agents.terminus as terminus

    cancelled = []

    async def pending(self, *_args):
        self._n_episodes = 3
        try:
            await asyncio.wait_for(asyncio.Future(), timeout=0.05)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(_FakeTerminus2, "run", pending)
    # The existing helper supplies a 60-second budget. Advance only the harness
    # clock after setup, leaving the event loop clock alone.
    ticks = iter([0.0, 60.0])
    monkeypatch.setattr(
        terminus, "time", types.SimpleNamespace(monotonic=lambda: next(ticks))
    )
    run = _run(monkeypatch, max_turns=10, episodes=3, pending_completion=False)
    assert cancelled == [True]
    assert (run.finish_reason, run.submitted, run.turns) == (
        "hit_time_budget",
        False,
        3,
    )


def test_external_cancellation_is_not_agent_exhaustion(monkeypatch, caplog):
    with pytest.raises(asyncio.CancelledError):
        _run(
            monkeypatch,
            max_turns=10,
            episodes=2,
            pending_completion=False,
            raises=asyncio.CancelledError(),
        )
    assert "cancelled stage=run episodes=2" in caplog.text


def test_underlying_timeout_is_not_agent_exhaustion(monkeypatch):
    run = _run(
        monkeypatch,
        max_turns=10,
        episodes=2,
        pending_completion=False,
        raises=TimeoutError("sandbox transport"),
    )
    assert run.finish_reason == "error"


# --------------------------------------------------------------------------
# The truncation seam. Terminus-2 handles a turn cut off at max_tokens INSIDE its
# LLM call -- salvage a complete action from the truncated text, else re-ask for a
# shorter one -- and neither costs an episode. That only fires if the backend
# RAISES OutputLengthExceededError; returning the truncated text as a normal reply
# sends it to the XML parser instead, which fails and burns an episode. Both of
# harbor's own backends raise, so ours has to as well.
# --------------------------------------------------------------------------


def _adapter_llm(reply, *, turn_max_tokens=16384):
    from torchtitan.experiments.rl.harness.agents.terminus import _AdapterLLM

    class _Adapter:
        def session_max_tokens(self, _session_id):
            # No session to report on, so the module default stands in.
            return None

        async def complete(self, _session_id, _payload):
            return reply

    return _AdapterLLM(
        _Adapter(),
        session_id="group=-1/rollout=0",
        max_context=63488,
        turn_max_tokens=turn_max_tokens,
    )


def _reply(text, stop_reason, *, output_tokens=None, blocks=None):
    if blocks is None:
        blocks = [{"type": "text", "text": text}]
    if output_tokens is None:
        output_tokens = len(text)
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 100, "output_tokens": output_tokens},
    }


@pytest.mark.parametrize("stop_reason", ["max_tokens", "length"])
def test_a_truncated_turn_raises_output_length_exceeded(stop_reason):
    from harbor.llms.base import OutputLengthExceededError

    llm = _adapter_llm(_reply("<response><commands>ls", stop_reason))

    with pytest.raises(OutputLengthExceededError) as excinfo:
        asyncio.run(llm.call(prompt="go"))

    # Terminus-2 reads truncated_response off the exception to try the salvage.
    assert excinfo.value.truncated_response == "<response><commands>ls"


@pytest.mark.parametrize("stop_reason", ["end_turn", "tool_use", None])
def test_a_complete_turn_is_returned_normally(stop_reason):
    llm = _adapter_llm(
        _reply("<response><commands>ls</commands></response>", stop_reason)
    )

    response = asyncio.run(llm.call(prompt="go"))

    assert response.content == "<response><commands>ls</commands></response>"


def test_output_limit_comes_from_the_session_not_the_module_default():
    """Terminus-2 interpolates this into 'you exceeded N tokens, break it into
    chunks', so it has to be the cap generation actually ran under.

    The regression it guards: the adapter never reads max_tokens out of the request
    body -- generation runs on the SamplingConfig the rollouter opened the session
    with -- and a recipe sets that independently. The 27B one raises it to 32768
    against this module's 16384 default, so quoting the module default told the model
    to chunk under half the budget it had.
    """
    from torchtitan.experiments.rl.harness.agents.terminus import _AdapterLLM

    class _Adapter:
        def session_max_tokens(self, _session_id):
            return 32768

        async def complete(self, _session_id, _payload):
            raise AssertionError("not called")

    llm = _AdapterLLM(
        _Adapter(),
        session_id="group=-1/rollout=0",
        max_context=63488,
        turn_max_tokens=16384,
    )
    assert llm.get_model_output_limit() == 32768


def test_the_module_default_is_only_a_fallback():
    """None means the adapter has no session to report on (not yet open, or closed),
    which is not a reason to tell the model nothing."""
    assert _adapter_llm(_reply("x", "end_turn")).get_model_output_limit() == 16384


def test_the_request_body_carries_no_max_tokens():
    """Sending one reads as though it sets the cap; the adapter ignores it."""
    from torchtitan.experiments.rl.harness.agents.terminus import _AdapterLLM

    seen: dict = {}

    class _Adapter:
        def session_max_tokens(self, _session_id):
            return None

        async def complete(self, _session_id, payload):
            seen.update(payload)
            return _reply("<response/>", "end_turn")

    llm = _AdapterLLM(
        _Adapter(),
        session_id="group=-1/rollout=0",
        max_context=63488,
        turn_max_tokens=16384,
    )
    asyncio.run(llm.call(prompt="go"))

    assert "max_tokens" not in seen


def test_an_exhausted_context_raises_its_own_error():
    """The adapter reports "max_tokens" for a prompt that no longer fits the context
    too, with an EMPTY completion. That is not a truncation and must not be re-asked
    (every retry appends to a history already over budget, so the reply is empty
    again). Terminus-2 answers ContextLengthExceededError by unwinding and
    summarizing, which frees budget; returning an empty reply instead leaves it
    spinning to the turn cap on parser retries."""
    from harbor.llms.base import ContextLengthExceededError, OutputLengthExceededError

    llm = _adapter_llm(_reply("", "max_tokens", output_tokens=0))

    with pytest.raises(ContextLengthExceededError):
        asyncio.run(llm.call(prompt="go"))
    # Specifically NOT the truncation error, whose handler re-asks.
    assert not issubclass(ContextLengthExceededError, OutputLengthExceededError)


def test_a_clamped_turn_is_the_context_wall_not_a_truncation():
    """The regression this replaced: keying on output_tokens > 0.

    The adapter clamps the per-turn cap down to the context that is left, so a turn
    with a few hundred tokens of headroom stops at "max_tokens" after a few hundred
    tokens and looks exactly like a capped generation -- but re-asking for a shorter
    answer cannot help, because the retry has the same headroom. Taken from a real
    trace: 63,388 tokens of prompt, ~100 tokens of prose cut off mid-sentence.
    """
    from harbor.llms.base import ContextLengthExceededError

    llm = _adapter_llm(
        {
            "content": [{"type": "text", "text": "In the debug session, I computed:"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 63388, "output_tokens": 100},
        }
    )
    with pytest.raises(ContextLengthExceededError):
        asyncio.run(llm.call(prompt="go"))


def test_with_no_context_budget_a_stop_is_the_per_turn_cap():
    """The adapter only clamps when a context budget is configured, so with none
    there is no wall to hit and "max_tokens" can only be the generation cap. Sending
    it down the context branch would end every truncated rollout instead."""
    from harbor.llms.base import OutputLengthExceededError

    llm = _adapter_llm(
        {
            "content": [{"type": "text", "text": "<response><commands>"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 999999, "output_tokens": 4096},
        }
    )
    llm._max_context = 0

    with pytest.raises(OutputLengthExceededError):
        asyncio.run(llm.call(prompt="go"))


def test_headroom_left_is_still_a_re_askable_truncation():
    """The other side of the same discriminator: the turn really did hit the
    generation cap, so Terminus-2's salvage / re-ask is worth an attempt."""
    from harbor.llms.base import OutputLengthExceededError

    llm = _adapter_llm(
        {
            "content": [{"type": "text", "text": "<response><commands>"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 2000, "output_tokens": 16384},
        }
    )
    with pytest.raises(OutputLengthExceededError):
        asyncio.run(llm.call(prompt="go"))


def test_a_turn_burned_entirely_inside_thinking_still_raises():
    """The failure this seam exists for: the whole per-turn budget goes into reasoning,
    so there is no text block at all -- but output_tokens is the full cap. Keying the
    raise on empty text would miss exactly this case."""
    from harbor.llms.base import OutputLengthExceededError

    llm = _adapter_llm(
        _reply(
            "",
            "max_tokens",
            output_tokens=16384,
            blocks=[{"type": "thinking", "thinking": "round and round"}],
        )
    )

    with pytest.raises(OutputLengthExceededError):
        asyncio.run(llm.call(prompt="go"))
