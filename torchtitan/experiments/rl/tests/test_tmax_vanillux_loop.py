# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import math
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from torchtitan.experiments.rl.examples.tmax import (
    layout,
    rollout_record,
    vanillux_loop,
)
from torchtitan.experiments.rl.examples.tmax.data import TMaxSample
from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _finish_reason_metrics,
    _note_image_without_tmux,
    _sandbox_issue_metrics,
    _SandboxRolloutDiagnostics,
    _write_rollout_record,
    TMaxRollouter,
)
from torchtitan.experiments.rl.harness import CapturedTurn
from torchtitan.experiments.rl.observability.metrics import Mean
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus

_STAMP = re.compile(r"^\d{8}-\d{6}Z$")


_RUN_BASH = vanillux_loop._run_bash


def _patch_self_healing_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path]:
    wrapper_path = tmp_path / "wrapper.sh"
    cwd_path = tmp_path / "cwd"
    env_path = tmp_path / "env"
    wrapper = (
        vanillux_loop._BASH_WRAPPER.replace(vanillux_loop._BASH_CWD_PATH, str(cwd_path))
        .replace(vanillux_loop._BASH_ENV_PATH, str(env_path))
        .replace(vanillux_loop._BASH_DEFAULT_CWD, str(tmp_path))
    )
    monkeypatch.setattr(vanillux_loop, "_BASH_WRAPPER_PATH", str(wrapper_path))
    monkeypatch.setattr(vanillux_loop, "_BASH_CWD_PATH", str(cwd_path))
    monkeypatch.setattr(vanillux_loop, "_BASH_ENV_PATH", str(env_path))
    monkeypatch.setattr(
        vanillux_loop, "_BASH_COMMAND_PATH_PREFIX", str(tmp_path / "command.")
    )
    monkeypatch.setattr(vanillux_loop, "_BASH_DEFAULT_CWD", str(tmp_path))
    monkeypatch.setattr(vanillux_loop, "_BASH_WRAPPER", wrapper)
    return wrapper_path, cwd_path, env_path


class _FakeAdapter:
    def __init__(self, responses: list[dict | None]) -> None:
        self._responses = iter(responses)

    async def complete(self, session_id: str, payload: dict) -> dict | None:
        return next(self._responses, None)


@pytest.fixture(autouse=True)
def _patch_sandbox_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def prepare_runtime(sb: Any) -> None:
        pass

    async def run_bash(sb: Any, command: str, timeout: int) -> tuple[str, int]:
        return "command output", 0

    monkeypatch.setattr(vanillux_loop, "_prepare_runtime", prepare_runtime)
    monkeypatch.setattr(vanillux_loop, "_run_bash", run_bash)


def _tool_response() -> dict:
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "bash",
                "id": "call-1",
                "input": {"command": "echo ok"},
            }
        ],
        "stop_reason": "tool_use",
    }


def _run_loop(
    responses: list[dict | None],
    *,
    time_budget_sec: int = 60,
    max_turns: int = 1,
) -> tuple[int, bool, int, str]:
    sandbox: Any = object()
    adapter: Any = _FakeAdapter(responses)
    return asyncio.run(
        vanillux_loop.run_vanillux_loop(
            sandbox,
            task="test task",
            session_id="group=0/rollout=0",
            adapter=adapter,
            time_budget_sec=time_budget_sec,
            max_turns=max_turns,
        )
    )


def test_persistent_bash_wrapper_avoids_pkill_pattern_self_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    wrapper_path = tmp_path / "wrapper.sh"
    cwd_path = tmp_path / "cwd"
    env_path = tmp_path / "env"
    wrapper = vanillux_loop._BASH_WRAPPER.replace(
        vanillux_loop._BASH_CWD_PATH, str(cwd_path)
    ).replace(vanillux_loop._BASH_ENV_PATH, str(env_path))
    wrapper_path.write_text(wrapper)
    wrapper_path.chmod(0o755)
    cwd_path.write_text(str(tmp_path))
    env_path.touch()
    monkeypatch.setattr(vanillux_loop, "_BASH_WRAPPER_PATH", str(wrapper_path))

    class LocalSandbox:
        command = ""

        async def exec(self, command: str, **kwargs) -> tuple[int, str, str]:
            self.command = command
            completed = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                check=False,
                text=True,
                timeout=kwargs["timeout"] + 2,
            )
            return completed.returncode, completed.stdout, completed.stderr

    sandbox = LocalSandbox()
    pattern = f"tt_vanillux_wrapper_{os.getpid()}_{time.time_ns()}"
    command = f"pkill -f {shlex.quote(pattern)} || :; printf survived"

    output, exit_code = asyncio.run(_RUN_BASH(sandbox, command, 2))

    assert command not in sandbox.command
    assert (output, exit_code) == ("survived", 0)


def test_persistent_bash_wrapper_preserves_command_argument_semantics(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    wrapper_path = tmp_path / "wrapper.sh"
    cwd_path = tmp_path / "cwd"
    env_path = tmp_path / "env"
    wrapper = vanillux_loop._BASH_WRAPPER.replace(
        vanillux_loop._BASH_CWD_PATH, str(cwd_path)
    ).replace(vanillux_loop._BASH_ENV_PATH, str(env_path))
    wrapper_path.write_text(wrapper)
    wrapper_path.chmod(0o755)
    cwd_path.write_text(str(tmp_path))
    env_path.touch()
    monkeypatch.setattr(vanillux_loop, "_BASH_WRAPPER_PATH", str(wrapper_path))

    class LocalSandbox:
        async def exec(self, command: str, **kwargs) -> tuple[int, str, str]:
            completed = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                check=False,
                text=True,
                timeout=kwargs["timeout"] + 2,
            )
            return completed.returncode, completed.stdout, completed.stderr

    command = "printf '%s' \"$1\""

    output, exit_code = asyncio.run(_RUN_BASH(LocalSandbox(), command, 2))

    assert (output, exit_code) == (command, 0)

    sandbox = LocalSandbox()
    assert asyncio.run(_RUN_BASH(sandbox, "export _command=preserved", 2)) == (
        "",
        0,
    )
    assert asyncio.run(_RUN_BASH(sandbox, "printf '%s' \"$_command\"", 2)) == (
        "preserved",
        0,
    )
    assert asyncio.run(_RUN_BASH(sandbox, "export PATH=/tmp/missing", 2)) == ("", 0)
    assert asyncio.run(_RUN_BASH(sandbox, "printf survived", 2)) == ("survived", 0)


def test_persistent_bash_wrapper_recovers_after_tmp_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    wrapper_path, cwd_path, env_path = _patch_self_healing_runtime(
        monkeypatch, tmp_path
    )

    class LocalSandbox:
        async def exec(self, command: str, **kwargs) -> tuple[int, str, str]:
            completed = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                check=False,
                text=True,
                timeout=kwargs["timeout"] + 2,
            )
            return completed.returncode, completed.stdout, completed.stderr

    cleanup = shlex.join(["rm", "-f", str(wrapper_path), str(cwd_path), str(env_path)])
    sandbox = LocalSandbox()

    assert asyncio.run(_RUN_BASH(sandbox, f"{cleanup}; printf first", 2)) == (
        "first",
        0,
    )
    assert not wrapper_path.exists()
    cwd_path.unlink(missing_ok=True)
    env_path.unlink(missing_ok=True)
    assert asyncio.run(_RUN_BASH(sandbox, "printf second", 2)) == ("second", 0)
    assert wrapper_path.is_file() and cwd_path.is_file() and env_path.is_file()


def test_persistent_bash_wrapper_handles_large_command_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _patch_self_healing_runtime(monkeypatch, tmp_path)

    class LocalSandbox:
        async def exec(self, command: str, **kwargs) -> tuple[int, str, str]:
            transport_path = tmp_path / "transport.sh"
            transport_path.write_text(command)
            completed = subprocess.run(
                ["bash", str(transport_path)],
                capture_output=True,
                check=False,
                text=True,
                timeout=kwargs["timeout"] + 2,
            )
            return completed.returncode, completed.stdout, completed.stderr

    command = "printf large-command-ok; # " + "x" * 256_000

    assert asyncio.run(_RUN_BASH(LocalSandbox(), command, 2)) == (
        "large-command-ok",
        0,
    )


def test_finish_reason_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_bash(sb: Any, command: str, timeout: int) -> tuple[str, int]:
        return vanillux_loop.SUBMIT_MARKER, 0

    monkeypatch.setattr(vanillux_loop, "_run_bash", run_bash)

    assert _run_loop([_tool_response()]) == (1, True, 0, "submit")


def test_finish_reason_hit_max_turns() -> None:
    assert _run_loop([_tool_response()]) == (1, False, 0, "hit_max_turns")


def test_finish_reason_hit_time_budget() -> None:
    assert _run_loop([], time_budget_sec=0) == (
        0,
        False,
        0,
        "hit_time_budget",
    )


def test_finish_reason_stopped_early() -> None:
    assert _run_loop([]) == (0, False, 0, "stopped_early")


def test_format_error_stops_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vanillux_loop, "_FORMAT_ERROR_FEEDBACK", False)
    response = {"content": [{"type": "text", "text": "not a tool call"}]}

    assert _run_loop([response]) == (1, False, 1, "stopped_early")


def test_finish_reason_metrics_are_exhaustive_fractions() -> None:
    metrics = _finish_reason_metrics(
        ["submit", "submit", "hit_time_budget", "stopped_early", "error"]
    )
    fractions = {}
    for metric in metrics:
        assert isinstance(metric.value, Mean)
        fractions[metric.key] = metric.value.value / metric.value.count

    assert fractions == {
        "rollout/finish_submit_frac": 0.4,
        "rollout/finish_hit_max_turns_frac": 0.0,
        "rollout/finish_hit_time_budget_frac": 0.2,
        # Terminus-2 only; the vanillux loop trims its own history and never
        # reports it, so the fraction is present and zero rather than absent.
        "rollout/finish_hit_context_limit_frac": 0.0,
        "rollout/finish_stopped_early_frac": 0.2,
        "rollout/finish_error_frac": 0.2,
    }
    assert sum(fractions.values()) == pytest.approx(1.0)


def test_sandbox_issue_metrics_count_events_and_affected_rollouts() -> None:
    metrics = _sandbox_issue_metrics(
        [
            {},
            {"command_disk_exhausted": 1},
            {
                "execute_response_recovered": 1,
                "command_output_retry": 1,
            },
            {},
        ]
    )
    values = {}
    for metric in metrics:
        assert isinstance(metric.value, Mean)
        values[metric.key] = metric.value.value / metric.value.count

    assert values == {
        "rollout/sandbox_issue_frac": 0.5,
        "rollout/sandbox_issue_events_mean": 0.75,
        "rollout/sandbox_disk_full_frac": 0.25,
        "rollout/sandbox_disk_full_events_mean": 0.25,
        "rollout/sandbox_transport_issue_frac": 0.25,
        "rollout/sandbox_transport_issue_events_mean": 0.5,
        "rollout/sandbox_provision_issue_frac": 0.0,
        "rollout/sandbox_timeout_frac": 0.0,
    }


def _run_group_with_one_infra_failure(monkeypatch: pytest.MonkeyPatch, group_id: int):
    """Two siblings, the second an infrastructure failure, scored 1.0 / 0.0."""
    # No run directory: the group-level signal writer has nowhere to write and
    # returns before it reads the placeholder sample.
    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    rollouter = object.__new__(TMaxRollouter)
    rollouter._ensure_adapter = AsyncMock(return_value=object())
    rollouter._read_ctrf = False
    rollouter._reward_mode = "sparse"

    async def run_sibling(**kwargs):
        rollout_idx = kwargs["rollout_idx"]
        infra_failed = rollout_idx == 1
        return (
            Rollout(
                group_id=group_id,
                rollout_id=rollout_idx,
                status=(
                    RolloutStatus.ERROR if infra_failed else RolloutStatus.COMPLETED
                ),
            ),
            not infra_failed,
            0,
            "error" if infra_failed else "submit",
            _SandboxRolloutDiagnostics(
                sandbox_id=f"sandbox-{rollout_idx}",
                disk_gb=6,
                issue_counts={},
                issues=(),
                num_dropped_details=0,
                infra_failed=infra_failed,
            ),
        )

    rollouter._run_agent_rollout = AsyncMock(side_effect=run_sibling)

    async def score_group(rollouts, sample):
        del sample
        return [
            Mock(reward=1.0, reward_breakdown={}),
            Mock(reward=0.0, reward_breakdown={}),
        ]

    rollouter.score_group = AsyncMock(side_effect=score_group)
    rollouter.advantage_estimator = Mock(return_value=[0.5, -0.5])

    group = asyncio.run(
        rollouter.run_group_rollouts(
            generate_fn=AsyncMock(),
            sample=object(),
            group_id=group_id,
            group_size=2,
            sampling=object(),
            renderer=object(),
        )
    )
    return rollouter, group


def test_infra_failure_is_unscored_not_a_zero_in_a_training_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An infrastructure failure is not a verdict, so it must not become one.

    It used to be held at 0.0 to match Open-Instruct, on the reasoning that such a
    rollout has no completion and so no training tokens. That is false for the
    timeouts that dominate -- they carry tens of thousands of completion tokens --
    and centered advantage then trains those turns away from behavior no verdict
    established was wrong. NaN says "unscored" so the baseline skips it.
    """
    rollouter, group = _run_group_with_one_infra_failure(monkeypatch, group_id=7)

    rewards = [rollout.reward for rollout in group.rollouts]
    assert rewards[0] == 1.0
    assert math.isnan(rewards[1])
    rollouter.score_group.assert_awaited_once()
    rollouter.advantage_estimator.assert_called_once()
    metric_values = {
        metric.key: metric.value.value
        for metric in group.metrics
        if metric.key.startswith("rollout/infra")
    }
    assert metric_values == {
        "rollout/infra_failed_frac": 0.5,
        "rollout/infra_failed_group_frac": 1.0,
    }


def test_validation_keeps_an_infra_failure_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation groups carry negative ids. avg@k is defined over attempts, so a NaN
    there would move the denominator and stop the number being comparable to the
    published one -- and index.json cannot encode a NaN at all."""
    _rollouter, group = _run_group_with_one_infra_failure(monkeypatch, group_id=-3)

    assert [rollout.reward for rollout in group.rollouts] == [1.0, 0.0]


def _sample(instance_id: str = "task-123", rev: int = 0) -> TMaxSample:
    return TMaxSample(
        instance_id=instance_id,
        image="example/image",
        workdir="/workspace",
        problem_statement="test",
        rev=rev,
    )


def _run_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> layout.Run:
    run = layout.Run(tmp_path / "runs" / "tmax-9b--20260904-181500Z")
    monkeypatch.setenv("TRL_RUN_DIR", str(run.path))
    return run


def _diagnostics(**overrides) -> _SandboxRolloutDiagnostics:
    kwargs = dict(
        sandbox_id="sandbox-abc",
        disk_gb=6,
        issue_counts={},
        issues=(),
        num_dropped_details=0,
    )
    kwargs.update(overrides)
    return _SandboxRolloutDiagnostics(**kwargs)


def _captured(prompt_token_ids, completion_token_ids, *, extends_previous):
    return CapturedTurn(
        prompt_token_ids=prompt_token_ids,
        completion_token_ids=completion_token_ids,
        completion_logprobs=[0.0] * len(completion_token_ids),
        min_policy_version=0,
        max_policy_version=0,
        finish_reason="stop",
        extends_previous=extends_previous,
    )


class _CharTokenizer:
    """One token per character, so a test can spell the stream it expects."""

    def decode(self, ids, skip_special_tokens=False):
        assert skip_special_tokens is False, "the record keeps the chat markers"
        return "".join(chr(i) for i in ids)


def _ids(text: str) -> list[int]:
    return [ord(c) for c in text]


def test_rollout_record_is_one_header_line_then_one_line_per_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The record header is LAYOUT.md's, key for key and in its order; the turns
    are decoded from the captured tokens, the reply to each from the next
    prompt's extension."""
    run = _run_dir(monkeypatch, tmp_path)
    prompt = _ids("task<think>\n")
    completion_1 = _ids(
        "t</think><response><commands><keystrokes>ls\n</keystrokes></commands>"
        "</response><|im_end|>"
    )
    reply_1 = _ids(
        "\n<|im_start|>user\nout<|im_end|>\n<|im_start|>assistant\n<think>\n"
    )
    completion_2 = _ids(
        "d</think><response><commands></commands>"
        "<task_complete>true</task_complete></response>"
    )
    captured = [
        _captured(prompt, completion_1, extends_previous=False),
        _captured(prompt + completion_1 + reply_1, completion_2, extends_previous=True),
    ]
    exec_trace = [{"t": 1725474121.1, "secs": 0.4, "exit": 0, "cmd": "tmux send-keys"}]

    rel = _write_rollout_record(
        run,
        sample=_sample(rev=2),
        group_id=713,
        rollout_idx=13,
        captured=captured,
        renderer=SimpleNamespace(_tokenizer=_CharTokenizer()),
        status="completed",
        reward=1.0,
        submitted=True,
        fmt_errors=0,
        error_msg="",
        finish_reason="submit",
        sandbox_diagnostics=_diagnostics(issue_counts={"session_disk_exhausted": 1}),
        exec_trace=exec_trace,
        secs=412.34,
        budget_sec=1800,
        started="20260904-182201Z",
    )

    assert rel == "rollouts/task-123/g713-r13.jsonl"
    header, turns = rollout_record.read_record(run.path / rel)
    assert list(header) == [
        "task",
        "rev",
        "run",
        "group",
        "rollout",
        "reward",
        "status",
        "finish_reason",
        "submitted",
        "format_errors",
        "infra_failed",
        "error",
        "sandbox",
        "secs",
        "budget_sec",
        "turns",
        "started",
        "exec",
    ]
    assert header == {
        "task": "task-123",
        "rev": 2,
        "run": "tmax-9b--20260904-181500Z",
        "group": 713,
        "rollout": 13,
        "reward": 1.0,
        "status": "completed",
        "finish_reason": "submit",
        "submitted": True,
        "format_errors": 0,
        "infra_failed": False,
        "error": "",
        "sandbox": {
            "id": "sandbox-abc",
            "disk_gb": 6,
            "issues": {"session_disk_exhausted": 1},
            "dropped_details": 0,
        },
        "secs": 412.3,
        "budget_sec": 1800,
        "turns": 2,
        "started": "20260904-182201Z",
        "exec": exec_trace,
    }
    assert turns == [
        {"turn": 1, "keystrokes": ["ls\n"], "output": "out", "think": "t"},
        {
            "turn": 2,
            "keystrokes": [],
            "task_complete": True,
            "output": "",
            "think": "d",
        },
    ]
    # Renamed into place: no .incoming left beside it.
    assert list((run.rollouts / "task-123").iterdir()) == [run.path / rel]


@pytest.mark.parametrize(
    "group_id, records, expect",
    [(-1, "1", False), (5, "0", False), (5, "1", True)],
)
def test_rollout_record_skips_validation_groups_and_honors_the_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    group_id: int,
    records: str,
    expect: bool,
) -> None:
    """Validation rollouts belong to the controller's validation report; a file
    under rollouts/ would read as a training rollout of a task never trained on."""
    run = _run_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("SWE_ROLLOUT_RECORDS", records)

    rel = _write_rollout_record(
        run,
        sample=_sample(),
        group_id=group_id,
        rollout_idx=0,
        captured=[],
        renderer=object(),
        status="completed",
        reward=0.0,
        submitted=False,
        fmt_errors=0,
        error_msg="",
        finish_reason="hit_max_turns",
        sandbox_diagnostics=_diagnostics(),
        exec_trace=[],
        secs=1.0,
        budget_sec=10,
        started="20260904-182201Z",
    )

    assert (rel is not None) is expect
    assert run.rollouts.exists() is expect


def test_no_tmux_probe_appends_one_advisory_line_per_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _run_dir(monkeypatch, tmp_path)

    _note_image_without_tmux(_sample("task-notmux"), group_id=3, rollout_idx=1)
    _note_image_without_tmux(_sample("task-notmux"), group_id=3, rollout_idx=2)

    lines = layout.read_jsonl(run.advisory("no_tmux"))
    assert [
        (l["task"], l["image"], l["reason"], l["group"], l["rollout"]) for l in lines
    ] == [
        ("task-notmux", "example/image", "no_tmux_in_image", 3, 1),
        ("task-notmux", "example/image", "no_tmux_in_image", 3, 2),
    ]
    assert all(_STAMP.match(l["stamp"]) for l in lines)


def test_without_a_run_dir_nothing_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    rollouter = object.__new__(TMaxRollouter)

    rollouter._maybe_emit_evolution_signal(
        _sample(), [_rollout(1, 0, 1.0), _rollout(1, 1, 1.0)]
    )
    _note_image_without_tmux(_sample(), group_id=1, rollout_idx=0)

    assert list(tmp_path.iterdir()) == []


def test_rollout_carries_its_finish_reason_and_format_errors() -> None:
    """The per-rollout loop outcome must ride on the Rollout: group metrics average it
    away, so the eval trace report has no other source for 'why did THIS trial stop'."""
    fields = {f.name for f in dataclasses.fields(Rollout)}
    assert "diagnostics" in fields

    rollout = Rollout(
        group_id=-1,
        rollout_id=0,
        status=RolloutStatus.COMPLETED,
        diagnostics={
            "finish_reason": "stopped_early",
            "format_errors": 1,
            "submitted": False,
            "infra_failed": False,
        },
    )

    assert rollout.diagnostics["finish_reason"] == "stopped_early"
    assert rollout.diagnostics["format_errors"] == 1
    # The keys TMaxRollouter._run_agent_rollout populates.
    source = inspect.getsource(TMaxRollouter._run_agent_rollout)
    for key in (
        "finish_reason",
        "format_errors",
        "submitted",
        "infra_failed",
        "record",
    ):
        assert f'"{key}"' in source, f"{key} no longer recorded on the Rollout"


def test_worker_info_logging_reaches_a_handler_without_duplicating(
    capsys: pytest.CaptureFixture,
) -> None:
    """Worker INFO must be emitted exactly once, whether or not the process already
    configured logging. Records fall through to logging.lastResort (WARNING-only)
    otherwise, which silently hid every agent-loop stop reason."""
    import logging

    from torchtitan.experiments.rl.actors.rollout_worker import (
        _enable_worker_info_logging,
    )

    titan = logging.getLogger("torchtitan")
    root = logging.getLogger()
    saved = (list(titan.handlers), titan.level, list(root.handlers), root.level)
    try:
        for handlers, root_level in (([], logging.WARNING), ([], logging.INFO)):
            titan.handlers.clear()
            titan.setLevel(logging.NOTSET)
            root.handlers.clear()
            root.setLevel(root_level)
            if root_level == logging.INFO:
                handler = logging.StreamHandler()
                handler.setLevel(logging.INFO)
                root.addHandler(handler)

            _enable_worker_info_logging()
            capsys.readouterr()
            logging.getLogger("torchtitan.experiments.rl.demo").info("marker-line")
            captured = capsys.readouterr()
            emitted = (captured.out + captured.err).count("marker-line")
            assert emitted == 1, f"root_level={root_level}: emitted {emitted} times"
    finally:
        titan.handlers[:] = saved[0]
        titan.setLevel(saved[1])
        root.handlers[:] = saved[2]
        root.setLevel(saved[3])


def _rollout(
    group_id: int, idx: int, reward: float, *, turns: int = 1, record: bool = True
) -> Rollout:
    """A scored sibling as run_group_rollouts hands it to the signal writer: the
    rubric reward on it and, when its record was written, that record's path."""
    return Rollout(
        group_id=group_id,
        rollout_id=idx,
        status=RolloutStatus.COMPLETED,
        reward=reward,
        turns=[Mock()] * turns,
        diagnostics={
            "record": f"rollouts/task-123/g{group_id}-r{idx}.jsonl" if record else None
        },
    )


def test_evolution_signal_names_the_sibling_records_and_the_direction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An all-pass group is a `harder` signal: one JSON under the run's signals/,
    naming the siblings' rollout records relative to the run, renamed into place."""
    run = _run_dir(monkeypatch, tmp_path)
    rollouter = object.__new__(TMaxRollouter)
    rollouts = [_rollout(713, idx, 1.0) for idx in range(3)]

    rollouter._maybe_emit_evolution_signal(_sample("task-123", rev=2), rollouts)

    (path,) = run.signal_files()
    assert path == run.signal("task-123", 713)
    signal = json.loads(path.read_text())
    assert _STAMP.match(signal["created"])
    assert signal == {
        "task": "task-123",
        "rev": 2,
        "run": "tmax-9b--20260904-181500Z",
        "group": 713,
        "direction": "harder",
        "solved": 3,
        "total": 3,
        "created": signal["created"],
        "attempts": [
            "rollouts/task-123/g713-r0.jsonl",
            "rollouts/task-123/g713-r1.jsonl",
            "rollouts/task-123/g713-r2.jsonl",
        ],
    }
    assert not list(run.signals.glob("*.incoming"))


@pytest.mark.parametrize(
    "rewards, group_id, signals",
    [
        # Reward variance: the group still trains, nothing to evolve.
        ([1.0, 0.0], 1, "1"),
        # Validation prompts are never trained, so never evolved.
        ([0.0, 0.0], -1, "1"),
        # Switched off.
        ([0.0, 0.0], 1, "0"),
    ],
)
def test_no_signal_for_in_band_validation_or_switched_off_groups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rewards: list[float],
    group_id: int,
    signals: str,
) -> None:
    run = _run_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("SWE_EVOLUTION_SIGNALS", signals)
    rollouter = object.__new__(TMaxRollouter)
    rollouts = [_rollout(group_id, idx, r) for idx, r in enumerate(rewards)]

    rollouter._maybe_emit_evolution_signal(_sample(), rollouts)

    assert run.signal_files() == []
    assert not run.advisories.exists()


@pytest.mark.parametrize(
    "rewards, expect_direction",
    [
        ([0.0, 0.0, math.nan], "easier"),
        ([0.0, 1.0, math.nan], None),
        ([math.nan, math.nan], None),
        ([1.0, 1.0], "harder"),
    ],
)
def test_zero_variance_is_judged_over_scored_siblings_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rewards: list[float],
    expect_direction: str | None,
) -> None:
    """An infra-failed sibling carries NaN, which is no verdict: it neither makes a
    group mixed nor counts as an attempt, and pstdev would raise on it."""
    run = _run_dir(monkeypatch, tmp_path)
    rollouter = object.__new__(TMaxRollouter)
    rollouts = [_rollout(4, idx, r) for idx, r in enumerate(rewards)]

    rollouter._maybe_emit_evolution_signal(_sample(), rollouts)

    files = run.signal_files()
    if expect_direction is None:
        assert files == []
        return
    (path,) = files
    signal = json.loads(path.read_text())
    scored = [idx for idx, r in enumerate(rewards) if not math.isnan(r)]
    assert signal["direction"] == expect_direction
    assert signal["total"] == len(scored)
    assert signal["solved"] == (len(scored) if expect_direction == "harder" else 0)
    assert signal["attempts"] == [
        f"rollouts/task-123/g4-r{idx}.jsonl" for idx in scored
    ]


def test_all_fail_group_with_zero_turns_is_a_quarantine_advisory_not_a_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No attempt took a turn: that measured the infrastructure, not the task. A
    signal would drive an unearned simplify; the advisory keeps the finding."""
    run = _run_dir(monkeypatch, tmp_path)
    rollouter = object.__new__(TMaxRollouter)
    rollouts = [_rollout(9, idx, 0.0, turns=0) for idx in range(16)]

    rollouter._maybe_emit_evolution_signal(_sample("task-dead"), rollouts)

    assert run.signal_files() == []
    (line,) = layout.read_jsonl(run.advisory("infra_quarantine"))
    assert _STAMP.match(line["stamp"])
    assert line == {
        "stamp": line["stamp"],
        "task": "task-dead",
        "image": "example/image",
        "reason": "all_fail_zero_turns",
        "group": 9,
        "rollouts_lost": 16,
    }


def test_signal_lists_only_records_that_were_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With SWE_ROLLOUT_RECORDS=0 the siblings have no record; the signal still
    carries the verdict and names no file that does not exist."""
    run = _run_dir(monkeypatch, tmp_path)
    rollouter = object.__new__(TMaxRollouter)
    rollouts = [_rollout(2, idx, 1.0, record=False) for idx in range(2)]

    rollouter._maybe_emit_evolution_signal(_sample(), rollouts)

    (path,) = run.signal_files()
    signal = json.loads(path.read_text())
    assert (signal["solved"], signal["total"], signal["attempts"]) == (2, 2, [])
