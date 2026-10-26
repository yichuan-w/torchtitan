# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""A TB4 row's Harbor environment, from the row to the rollout's sandboxes.

Boot keywords (GPU, region, compose), the healthcheck and the non-root agent
user; and that a row without these fields boots and runs exactly as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.examples.tmax import harbor_env, rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import _parse_sample_row, TMaxSample
from torchtitan.experiments.rl.examples.tmax.rollouter import _RolloutIssueGate, TMaxRollouter
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun

_TB4_MD = {
    "instance_id": "kv-live-surgery",
    "image": "harborframework/terminal-bench:kv@sha256:" + "a" * 64,
    "daytona_gpu": 1,
    "daytona_gpu_type": ["H100"],
    "daytona_target": "earth",
    "compose": {"docker_compose": "services: {}\n", "build_timeout_sec": 1200.0},
    "healthcheck": {"command": "probe", "retries": 2, "interval_sec": 0, "timeout_sec": 5},
    "agent_user": "agent",
    "tmax": {"separate_verifier": {"image": "v", "artifacts": ["/app/x"]}},
}


def test_boot_kwargs_map_a_tb4_row_and_nothing_else():
    assert harbor_env.boot_kwargs(_TB4_MD) == {
        "sandbox_options": {"gpu": 1, "gpu_type": ["H100"], "target": "earth"},
        "compose": {
            "docker_compose": "services: {}\n",
            "project": "kv-live-surgery",
            "build_timeout_sec": 1200,
        },
    }
    assert harbor_env.boot_kwargs({"instance_id": "t", "image": "i", "daytona_cpu": 2}) == {}
    assert harbor_env.sandbox_options({"network_block_all": True}) == {
        "network_block_all": True
    }


def test_sample_rows_carry_the_fields_and_old_rows_gain_nothing():
    sample = _parse_sample_row({"prompt": "p", "metadata": dict(_TB4_MD)})
    assert sample.boot_kwargs == harbor_env.boot_kwargs(_TB4_MD)
    assert sample.healthcheck == _TB4_MD["healthcheck"]
    assert sample.agent_user == "agent"
    old = _parse_sample_row(
        {"prompt": "p", "metadata": {"instance_id": "t", "image": "i", "tmax": {"test_sh": "x"}}}
    )
    assert (old.boot_kwargs, old.healthcheck, old.agent_user) == ({}, None, "")


class _Probe:
    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = 0

    async def exec(self, cmd, **kw):
        self.calls += 1
        return (self.codes.pop(0) if self.codes else 0), "", ""


def test_healthcheck_ignores_start_period_failures_then_counts_retries(monkeypatch):
    async def no_sleep(_s):
        return None

    monkeypatch.setattr(harbor_env.asyncio, "sleep", no_sleep)
    probe = _Probe([1, 1, 0])
    asyncio.run(harbor_env.run_healthcheck(probe, {"command": "x", "retries": 3}))
    assert probe.calls == 3
    with pytest.raises(RuntimeError, match="after 2 consecutive retries"):
        asyncio.run(
            harbor_env.run_healthcheck(_Probe([1, 1, 1]), {"command": "x", "retries": 2})
        )
    # Inside the start period failures do not count.
    clock = itertools.chain([0.0, 0.0, 1.0, 2.0], itertools.repeat(100.0))
    monkeypatch.setattr(harbor_env, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    probe = _Probe([1, 1, 1, 0])
    asyncio.run(
        harbor_env.run_healthcheck(
            probe, {"command": "x", "retries": 2, "start_period_sec": 50}
        )
    )
    assert probe.calls == 4
    asyncio.run(harbor_env.run_healthcheck(_Probe([]), None))


class _Sb:
    def __init__(self, *, rc=0):
        self.calls = []
        self.rc = rc
        self.sandbox_id = "sb-1"
        self.allocated_disk_gb = 10

    async def exec(self, cmd, *, user="root", env=None, **kw):
        self.calls.append(("exec", user, cmd, env))
        if cmd.startswith("getent passwd"):
            return 0, "/home/agent\n", ""
        return self.rc, "", ""

    async def write_file(self, path, content, *, user="root"):
        self.calls.append(("write", user, path, None))

    async def read_file(self, path, *, user="root"):
        self.calls.append(("read", user, path, None))
        return "text"


def test_the_agent_user_view_maps_only_the_agents_requests():
    inner = _Sb()
    assert asyncio.run(harbor_env.enter_as_agent_user(inner, "")) is inner
    assert inner.calls == []
    sb = asyncio.run(harbor_env.enter_as_agent_user(inner, "agent"))
    assert inner.calls[0][:3] == (
        "exec",
        "root",
        "mkdir -p /logs/agent /logs/verifier /logs/artifacts && "
        "chmod 777 /logs/agent /logs/verifier /logs/artifacts",
    )
    inner.calls.clear()
    asyncio.run(sb.exec("tmux -V"))
    asyncio.run(sb.exec("id", user="root", env={"A": "1"}))
    asyncio.run(sb.exec("whoami", user="tester"))
    asyncio.run(sb.write_file("/tmp/x", "y"))
    assert asyncio.run(sb.read_file("/app/out")) == "text"
    home = {"USER": "agent", "LOGNAME": "agent", "HOME": "/home/agent"}
    assert inner.calls == [
        ("exec", "agent", "tmux -V", home),
        ("exec", "agent", "id", {**home, "A": "1"}),
        ("exec", "tester", "whoami", None),
        ("write", "agent", "/tmp/x", None),
        ("read", "root", "/app/out", None),
    ]
    assert sb.sandbox_id == "sb-1"
    with pytest.raises(RuntimeError, match="as root failed .rc=1"):
        asyncio.run(harbor_env.enter_as_agent_user(_Sb(rc=1), "agent"))


def test_harbors_log_dirs_match_its_environment_paths():
    from harbor.models.trial.paths import EnvironmentPaths

    paths = EnvironmentPaths()
    assert harbor_env.AGENT_LOG_DIRS == tuple(
        str(p) for p in (paths.agent_dir, paths.verifier_dir, paths.artifacts_dir)
    )


# --------------------------------------------------------------------------- #
# Through the rollouter
# --------------------------------------------------------------------------- #
def _rollouter() -> TMaxRollouter:
    r = object.__new__(TMaxRollouter)
    r._agent_name = "vanillux"
    r._time_budget_sec = 3600
    r._eval_timeout_sec = 600
    r._max_context_tokens = 4096
    r._eval_max_context_tokens = 4096
    r._eval_max_turns = 0
    r._strict_format = r._strict_opening = r._strict_truncation = False
    r._eval_strict_format = r._eval_strict_opening = r._eval_strict_truncation = False
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._shadow_grade_unsubmitted = False
    r._eval_shadow_grade_timeouts = True
    r._eval_grade_unsubmitted = False
    r._grade_unsubmitted = False
    r._eval_context_notice = True
    r._format_streak_limit = 0
    r._rollout_gate = _RolloutIssueGate(1)
    return r


def _rollout(monkeypatch, sample: TMaxSample):
    boots: list = []
    sandbox = _Sb()

    @contextlib.asynccontextmanager
    async def boot(image, **kwargs):
        boots.append((image, kwargs))
        yield sandbox

    seen: list = []

    async def agent(task):
        seen.append(task.sandbox)
        await task.sandbox.exec("echo agent")
        return AgentRun(turns=1, submitted=False, finish_reason="hit_context_limit")

    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    monkeypatch.setenv("TT_SANDBOX_NET_PROBE", "0")
    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
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
    asyncio.run(
        _rollouter()._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=sample,
            group_id=0,
            rollout_idx=0,
            sampling=SimpleNamespace(seed=0),
            renderer=object(),
        )
    )
    return boots, sandbox


def test_a_tb4_rollout_boots_checks_health_and_runs_the_agent_as_its_user(monkeypatch):
    sample = _parse_sample_row({"prompt": "p", "metadata": dict(_TB4_MD)})
    boots, sandbox = _rollout(monkeypatch, sample)
    _image, kwargs = boots[0]
    assert kwargs["sandbox_options"] == {"gpu": 1, "gpu_type": ["H100"], "target": "earth"}
    assert kwargs["compose"]["project"] == "kv-live-surgery"
    users = {cmd: user for kind, user, cmd, _env in sandbox.calls if kind == "exec"}
    assert users["probe"] == "root"  # the healthcheck, as root
    assert users["echo agent"] == "agent"
    agent_env = next(env for _k, _u, cmd, env in sandbox.calls if cmd == "echo agent")
    assert agent_env["HOME"] == "/home/agent"


def test_a_plain_rollout_boots_and_runs_as_before(monkeypatch):
    sample = TMaxSample(
        instance_id="task-1", image="example/image", workdir="/app", problem_statement="x"
    )
    boots, sandbox = _rollout(monkeypatch, sample)
    _image, kwargs = boots[0]
    assert "sandbox_options" not in kwargs and "compose" not in kwargs
    assert all(user == "root" for kind, user, *_ in sandbox.calls if kind == "exec")
    assert not any(cmd.startswith("getent") for _k, _u, cmd, _e in sandbox.calls)


def test_a_sample_pickled_before_the_new_fields_unpickles_with_their_defaults():
    """A controller checkpoint (SWE_CKPT_CONTROLLER_DATA=1) pickles pending
    samples. One written before boot_kwargs/healthcheck/agent_user existed holds
    one value per field it had, in field order; it must load with the new fields
    at their defaults, not with unset slots (AttributeError in the rollouter,
    and in asdict for the lineage record)."""
    import dataclasses
    import pickle

    from torchtitan.experiments.rl.training_lineage import sample_payload

    sample = TMaxSample(
        instance_id="t",
        image="i",
        workdir="/app",
        problem_statement="x",
        tmax={"test_sh": "y"},
    )
    new = ("boot_kwargs", "healthcheck", "agent_user", "version")
    names = [f.name for f in dataclasses.fields(TMaxSample)]
    assert names[-4:] == list(new)  # appended, so the old fields keep their order
    old_fields = [n for n in names if n not in new]

    def old_getstate(self):
        return [getattr(self, n) for n in old_fields]

    current = TMaxSample.__getstate__
    TMaxSample.__getstate__ = old_getstate
    try:
        blob = pickle.dumps(sample)
    finally:
        TMaxSample.__getstate__ = current
    restored = pickle.loads(blob)
    assert (
        restored.boot_kwargs,
        restored.healthcheck,
        restored.agent_user,
        restored.version,
    ) == ({}, None, "", "")
    assert restored == sample
    assert sample_payload(restored)["boot_kwargs"] == {}
    # and a current pickle round-trips unchanged
    tb4 = _parse_sample_row({"prompt": "p", "metadata": dict(_TB4_MD)})
    assert pickle.loads(pickle.dumps(tb4)) == tb4
    assert pickle.loads(pickle.dumps(tb4)).agent_user == "agent"
