# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The overfit bench's version pool, scheduler, events and rollouter switches."""

from __future__ import annotations

import asyncio
import contextlib
import json
import pickle
import random
import socket
import subprocess
import sys
import threading
import time
import types
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.examples.tmax import layout
from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import _parse_sample_row, TMaxSample
from torchtitan.experiments.rl.examples.tmax.overfit_bench import bench, events
from torchtitan.experiments.rl.examples.tmax.overfit_bench.pool import (
    PoolError,
    VersionPool,
)
from torchtitan.experiments.rl.examples.tmax.overfit_bench.rewriter_client import (
    RewriterClient,
    RewriterError,
    run_reference,
)
from torchtitan.experiments.rl.examples.tmax.overfit_bench.scheduler import (
    BenchScheduler,
)
from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _BenchMode,
    _RolloutIssueGate,
    _SandboxRolloutDiagnostics,
    _TURN_CAP_GUARD_SEC,
    TMaxRollouter,
)
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun
from torchtitan.experiments.rl.rollout.types import Rollout, RolloutStatus, RolloutTurn
from torchtitan.experiments.rl.training_lineage import content_revision
from torchtitan.experiments.rl.types import RolloutTurnID


def _row(task: str = "a", rev: int = 0, test: str = "exit 0", **md) -> dict:
    return {
        "prompt": f"solve {task}",
        "label": task,
        "metadata": {
            "instance_id": task,
            "image": "ubuntu:24.04",
            "rev": rev,
            "tmax": {"test_sh": test, "fixtures": {}, "reward_path": "/logs/verifier/reward.txt"},
            **md,
        },
    }


def _live(root: layout.Root) -> list[dict]:
    return [json.loads(ln) for ln in root.mix.read_live().splitlines() if ln.strip()]


def _until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def root(tmp_path):
    root = layout.Root(tmp_path / "job")
    root.mix.publish([json.dumps(_row("a"))])
    return root


@pytest.fixture
def run(root, monkeypatch):
    run = root.run("tmax-9b--20261021-000000Z")
    monkeypatch.setenv("TRL_RUN_DIR", str(run.path))
    monkeypatch.setenv("TRL_BASE", str(root.path))
    monkeypatch.delenv("TRL_ROLLOUT_ATTEMPT_ID", raising=False)
    monkeypatch.delenv(events.SOCKET_ENV, raising=False)
    return run


@pytest.fixture
def sched(root, run):
    s = BenchScheduler(root, run=run, hook_timeout_sec=0.3, import_interval_sec=0.05)
    s.start()
    yield s
    s.stop()


@pytest.fixture
def rewriter_events(sched):
    """Every event the scheduler fans out, as a connected rewriter sees it."""
    client = RewriterClient(sched.root.path)
    client.connect()
    assert _until(lambda: any(c.role == "rewriter" for c in sched._clients))
    yield client
    client.close()


# -- the pool ---------------------------------------------------------------


def test_pool_weighted_sampling_follows_weights_and_skips_retired():
    pool = VersionPool()
    a = pool.add(_row("a"), weight=3.0)["id"]
    b = pool.add(_row("b"), weight=1.0)["id"]
    c = pool.add(_row("c"), weight=5.0)["id"]
    pool.retire(c)
    rng = random.Random(0)
    drawn = [pool.sample(rng)[0].id for _ in range(4000)]
    assert c not in drawn
    assert abs(drawn.count(a) / len(drawn) - 0.75) < 0.03
    assert drawn.count(b) > 0
    with pytest.raises(PoolError):
        pool.reweight(c, 1.0)
    with pytest.raises(PoolError):
        pool.reweight(a, -1)
    with pytest.raises(PoolError):
        pool.add({"prompt": "x", "metadata": {"instance_id": "x"}})


def test_pool_version_is_the_row_sha_and_pool_fields_stay_out_of_it():
    pool = VersionPool()
    info = pool.add(_row("a", rev=2), weight=0.5)
    assert info["id"] == content_revision(_row("a", rev=2))
    assert info["label"] == "a@r2"
    pool.reweight(info["id"], 4.0)
    assert pool.get(info["id"]).row == _row("a", rev=2)
    # Two rows of one task at one rev are two versions.
    other = pool.add(_row("a", rev=2, test="exit 1"))["id"]
    assert other != info["id"] and len(pool.active()) == 2


def test_pool_drained_fires_once_when_a_task_reaches_zero_weight():
    drained = []
    pool = VersionPool(on_drained=drained.append)
    a0 = pool.add(_row("a"))["id"]
    a1 = pool.add(_row("a", rev=1), parent=a0)["id"]
    pool.retire(a0)
    assert drained == []
    pool.reweight(a1, 0.0)
    assert drained == ["a"]
    pool.retire(a1)
    assert drained == ["a"]
    # A drained pool still draws: zero-weight actives, then the last retired.
    assert pool.sample(random.Random(0))[1] == "no_active"


def test_pool_state_round_trip(tmp_path):
    log = tmp_path / "pool.jsonl"
    pool = VersionPool(log_path=log, state_path=tmp_path / "pool.json")
    a = pool.add(_row("a"))["id"]
    b = pool.add(_row("a", rev=1), parent=a, weight=2.0)["id"]
    pool.retire(a)
    copy = VersionPool()
    copy.load_state_dict(json.loads((tmp_path / "pool.json").read_text()))
    assert copy.get_state() == pool.get_state()
    assert copy.get(b).parent == a and copy.seq == 3
    assert [line["op"] for line in layout.read_jsonl(log)] == ["add", "add", "retire"]
    restored = VersionPool()
    restored.load_state_dict(pickle.loads(pickle.dumps(pool.state_dict())))
    assert restored.get_state(with_rows=True) == pool.get_state(with_rows=True)


def test_version_is_excluded_from_sample_equality_and_old_pickles_restore():
    sample = _parse_sample_row(_row("a"))
    assert sample.version == ""
    assert replace(sample, version="x") == sample
    state = list(replace(sample, version="x").__getstate__())[:-1]
    restored = object.__new__(TMaxSample)
    restored.__setstate__(state)
    assert restored.version == ""


# -- the scheduler ----------------------------------------------------------


def test_draw_without_a_rewriter_is_the_weighted_pool_draw(sched, run):
    sample = sched.draw()
    vid = content_revision(_row("a"))
    assert sample.version == vid and sample.lineage.sample_revision == vid
    assert [d["by"] for d in layout.read_jsonl(run.scheduler / "draws.jsonl")] == [
        "weighted"
    ]
    assert json.loads(sched.root.scheduler_json.read_text())["socket"] == str(
        sched.socket_path
    )


def test_rewriter_pool_calls_republish_the_mix(sched, run):
    client = RewriterClient(sched.root.path)
    client.connect()
    parent = content_revision(_row("a"))
    added = client.add(_row("a", rev=1), parent=parent)
    client.retire(parent)
    assert [r["metadata"]["rev"] for r in _live(sched.root)] == [1]
    state = client.get_state()
    assert {v["id"]: v["status"] for v in state["versions"]} == {
        parent: "retired",
        added["id"]: "active",
    }
    with pytest.raises(Exception, match="not an active version"):
        client.reweight(parent, 1.0)
    assert [x["op"] for x in layout.read_jsonl(run.scheduler / "pool.jsonl")] == [
        "add",
        "add",
        "retire",
    ]
    assert sched.draw().version == added["id"]
    client.close()


def test_which_version_answer_is_used(sched):
    b = sched.pool.add(_row("a", rev=1))["id"]
    client = RewriterClient(sched.root.path, which_version=lambda c: b)
    client.connect()
    assert _until(lambda: any(c.hooks for c in sched._clients))
    assert all(sched.draw().version == b for _ in range(5))
    client.close()


def test_slow_hook_falls_back_to_the_weighted_draw(sched, run):
    client = RewriterClient(
        sched.root.path, which_version=lambda c: time.sleep(2) or c[0]["id"]
    )
    client.connect()
    assert _until(lambda: any(c.hooks for c in sched._clients))
    started = time.monotonic()
    sched.draw()
    assert time.monotonic() - started < 1.0
    assert layout.read_jsonl(run.scheduler / "draws.jsonl")[-1]["by"] == "weighted"
    client.close()


def test_a_hung_or_dead_rewriter_never_blocks_a_draw(sched):
    hung = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    hung.connect(str(sched.socket_path))
    hung.sendall(b'{"op": "hello", "role": "rewriter", "hooks": ["which_version"]}\n')
    assert _until(lambda: any(c.hooks for c in sched._clients))
    started = time.monotonic()
    assert sched.draw().version
    assert time.monotonic() - started < 1.0
    hung.close()
    started = time.monotonic()
    for _ in range(3):
        sched.draw()
    assert time.monotonic() - started < 3 * 0.3 + 1.0


def test_producer_events_reach_the_rewriter_and_the_log(sched, run, rewriter_events, monkeypatch):
    # A RolloutWorker has no local scheduler: it finds the socket from scheduler.json.
    monkeypatch.setattr(events, "_local_sink", None)
    monkeypatch.setattr(events, "_producer", None)
    events.emit(
        events.ROLLOUT_DONE,
        version="v",
        task="a",
        rev=0,
        group=3,
        idx=1,
        reward=1.0,
        finish_reason="submit",
        record_path="rollouts/a/g3-r1.jsonl",
        policy_versions=[0, 1],
    )
    event = rewriter_events.wait_for({"rollout_done"}, timeout=5)
    assert event["group"] == 3 and event["run"] == run.name
    assert _until(lambda: (run.scheduler / "events.jsonl").exists())
    assert layout.read_jsonl(run.scheduler / "events.jsonl")[0]["event"] == "rollout_done"


def test_emit_with_no_listener_is_a_no_op(monkeypatch):
    monkeypatch.setattr(events, "_local_sink", None)
    monkeypatch.setattr(events, "_producer", None)
    monkeypatch.delenv(events.SOCKET_ENV, raising=False)
    monkeypatch.delenv("TRL_BASE", raising=False)
    events.emit(events.STEP_DONE, step=1, policy_version=1)
    events.emit(events.STEP_DONE, step=1)  # a malformed event is dropped too
    assert events._producer is None


def test_pool_drained_reaches_the_rewriter(sched, rewriter_events):
    rewriter_events.reweight(content_revision(_row("a")), 0.0)
    assert rewriter_events.wait_for({"pool_drained"}, timeout=5)["task"] == "a"
    assert sched.draw().version  # still draws


def test_a_file_publish_is_imported(sched):
    old = content_revision(_row("a"))
    sched.pool.reweight(old, 3.0)
    versions = len(sched.root.mix.versions())
    sched.root.mix.publish([json.dumps(_row("a", rev=1)), json.dumps(_row("b"))])
    new = content_revision(_row("a", rev=1))
    assert _until(lambda: sched.pool.is_active(new))
    assert not sched.pool.is_active(old)
    assert sched.pool.get(new).parent == old and sched.pool.get(new).weight == 3.0
    assert sched.pool.get(content_revision(_row("b"))).parent is None
    # The import itself publishes nothing: the file already says it.
    assert len(sched.root.mix.versions()) == versions + 1


def test_an_unparseable_file_publish_is_ignored(sched):
    bad = _row("a", rev=1)
    del bad["metadata"]["tmax"]
    sched.root.mix.publish([json.dumps(bad)])
    time.sleep(0.3)
    assert [v.rev for v in sched.pool.active()] == [0]


def test_scheduler_state_round_trip_restores_the_draw_stream(root, run):
    first = BenchScheduler(root, run=run, seed=7)
    first.start()
    first.pool.add(_row("a", rev=1))
    first.draw()
    state = pickle.loads(pickle.dumps(first.state_dict()))
    expected = [first.draw().version for _ in range(10)]
    first.stop()
    second = BenchScheduler(root, run=run, seed=0)
    second.start()
    second.load_state_dict(state)
    assert [second.draw().version for _ in range(10)] == expected
    second.stop()


def test_a_seeded_job_starts_with_every_seeded_version(root, run, tmp_path):
    rows = [_row("a", rev=1), _row("a", rev=1, test="exit 1"), _row("a", rev=2)]
    seed_file = tmp_path / "seed.jsonl"
    seed_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert bench.seed(root.path, seed_file, weight=2.0) == {
        "active": 3,
        "added": 3,
        "retired": 1,
    }
    assert _live(root) == rows
    ids = {content_revision(r) for r in rows}
    s = BenchScheduler(root, run=run, seed=0)
    s.start()
    try:
        assert {v.id for v in s.pool.active()} == ids
        assert all(v.weight == 2.0 for v in s.pool.active())
        assert not s.pool.is_active(content_revision(_row("a")))
        assert {s.draw().version for _ in range(30)} == ids
        with pytest.raises(SystemExit, match="scheduler is serving"):
            bench.seed(root.path, seed_file)
    finally:
        s.stop()


def test_seed_refuses_a_file_with_any_unparseable_row(root, tmp_path):
    bad = _row("a", rev=1)
    del bad["metadata"]["tmax"]
    seed_file = tmp_path / "seed.jsonl"
    seed_file.write_text(json.dumps(_row("a", rev=2)) + "\n" + json.dumps(bad) + "\n")
    with pytest.raises(SystemExit, match="row 2 does not parse"):
        bench.seed(root.path, seed_file)
    assert _live(root) == [_row("a")] and len(root.mix.versions()) == 1
    assert not root.mix.pool_state.exists()


# -- connecting before the scheduler exists -------------------------------------


def _stale_scheduler_json(root: layout.Root) -> None:
    """What a killed scheduler leaves behind: its pid gone, its socket too."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    layout.write_json_atomic(
        root.scheduler_json,
        {
            "socket": "/tmp/no-such-bench/sched.sock",
            "protocol": 1,
            "host": socket.gethostname(),
            "pid": dead.pid,
        },
    )


def test_connect_waits_past_a_stale_scheduler_json_for_the_scheduler(root, run):
    _stale_scheduler_json(root)
    client = RewriterClient(root.path)
    failed = []

    def connect():
        try:
            client.connect(timeout_sec=20, poll_sec=0.05)
        except RewriterError as e:
            failed.append(e)

    waiter = threading.Thread(target=connect)
    waiter.start()
    time.sleep(0.5)
    assert waiter.is_alive() and not client.connected and not failed
    s = BenchScheduler(root, run=run)
    s.start()
    try:
        waiter.join(timeout=10)
        assert client.connected and not failed
        assert _until(lambda: any(c.role == "rewriter" for c in s._clients))
        assert client.get_state()["versions"][0]["id"] == content_revision(_row("a"))
    finally:
        client.close()
        s.stop()


def test_connect_gives_up_at_its_timeout(root):
    with pytest.raises(RewriterError, match="no scheduler.json yet"):
        RewriterClient(root.path).connect(timeout_sec=0.2, poll_sec=0.05)
    _stale_scheduler_json(root)
    with pytest.raises(RewriterError, match="pid .* is gone"):
        RewriterClient(root.path).connect(timeout_sec=0)


def test_reference_adapter_started_first_connects_when_the_scheduler_comes_up(
    root, run, monkeypatch
):
    seen: dict = {}

    def fold(root, accepted):
        accepted[0]["status"] = "accepted"
        version, _ = root.mix.publish([json.dumps(_row("a", rev=1))])
        return version

    def main():
        # The loop is already running file-based when the trainer starts.
        s = BenchScheduler(root, run=run, import_interval_sec=600)
        s.start()
        try:
            seen["connected"] = _until(
                lambda: any(c.role == "rewriter" for c in s._clients), timeout=10
            )
            fake.fold(root, [{"meta": {"task": "a"}}])
            new = content_revision(_row("a", rev=1))
            seen["active"] = s.pool.is_active(new)
            seen["parent"] = s.pool.get(new).parent if seen["active"] else None
        finally:
            s.stop()

    fake = types.ModuleType("evolve_ondella")
    fake.load_ledger = lambda root: {}
    fake.admission_is_open = lambda: True
    fake._pending = lambda root, ledger: True
    fake._wait_for_signals = None
    fake.fold = fold
    fake.main = main
    monkeypatch.setitem(sys.modules, "evolve_ondella", fake)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    run_reference(root.path, [])
    # The importer (600 s interval) never ran, so the fold reached the pool
    # through the socket the adapter connected after starting.
    assert seen == {
        "connected": True,
        "active": True,
        "parent": content_revision(_row("a")),
    }


# -- the reference rewriter adapter -------------------------------------------


def test_reference_adapter_wakes_on_group_done_and_mirrors_folds(sched, monkeypatch):
    root = sched.root
    signal_written = threading.Event()
    seen: dict = {}

    def fold(root, accepted):
        rows = _live(root)
        rows[0] = _row("a", rev=1)
        accepted[0]["status"] = "accepted"
        version, _ = root.mix.publish([json.dumps(r) for r in rows])
        return version

    def main():
        started = time.monotonic()
        threading.Timer(
            0.2,
            lambda: (
                signal_written.set(),
                events.emit(
                    events.GROUP_DONE,
                    version=None,
                    task="a",
                    rev=0,
                    group=0,
                    passed=0,
                    scored=12,
                    policy_version=0,
                ),
            ),
        ).start()
        fake._wait_for_signals(root, 30)
        seen["woke_after"] = time.monotonic() - started
        fake.fold(root, [{"meta": {"task": "a"}}])

    fake = types.ModuleType("evolve_ondella")
    fake.load_ledger = lambda root: {}
    fake.admission_is_open = lambda: True
    fake._pending = lambda root, ledger: signal_written.is_set()
    fake._wait_for_signals = None
    fake.fold = fold
    fake.main = main
    monkeypatch.setitem(sys.modules, "evolve_ondella", fake)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    run_reference(root.path, [])
    assert seen["woke_after"] < 2.0
    new, old = content_revision(_row("a", rev=1)), content_revision(_row("a"))
    assert sched.pool.is_active(new) and not sched.pool.is_active(old)
    assert sched.pool.get(new).parent == old


# -- the controller ---------------------------------------------------------


def test_step_done_once_per_step_after_the_pull(monkeypatch):
    from torchtitan.experiments.rl.controller import Controller
    from torchtitan.experiments.rl.examples.tmax.overfit_bench.bench_controller import (
        BenchController,
    )

    got = []
    monkeypatch.setattr(events, "_local_sink", got.append)
    monkeypatch.setattr(Controller, "_pull_generator_weights", AsyncMock())
    controller = object.__new__(BenchController)
    controller.start_step = 3

    async def pulls():
        for version in (3, 4, 4, 5):
            await controller._pull_generator_weights(policy_version=version)

    asyncio.run(pulls())
    assert [(e["event"], e["step"], e["policy_version"]) for e in got] == [
        ("step_done", 4, 4),
        ("step_done", 5, 5),
    ]


@pytest.fixture
def recipe_env(monkeypatch):
    from pathlib import Path

    from torchtitan.experiments.rl.examples.tmax import config_registry

    recipe = (
        Path(config_registry.__file__).parent / "overfit_bench" / "recipe.env"
    ).read_text()
    for line in recipe.splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("TRL_BASE", "/data/job")
    monkeypatch.delenv("SWE_POLICY_CANARY_K", raising=False)
    monkeypatch.setattr(config_registry, "_TB2_VAL_DATA", "/data/original.jsonl")


def test_the_recipe_builds_a_bench_controller_that_passes_the_capacity_check(recipe_env):
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_9b_tmax,
    )
    from torchtitan.experiments.rl.examples.tmax.overfit_bench.bench_controller import (
        BenchController,
    )

    config = rl_grpo_qwen3_5_9b_tmax()
    assert isinstance(config, BenchController.Config)
    assert config.bench_root == "/data/job"
    r = config.rollouter
    assert (r.bench_scheduler, r.bench_max_turns, r.bench_sandbox_cpu) == (True, 60, 4)
    assert (r.bench_sandbox_mem_gb, r.bench_sandbox_disk_gb) == (0, 0)
    loop = config.async_loop
    assert (loop.group_size, loop.max_active_rollout_groups, loop.max_offpolicy_steps) == (12, 8, 8)
    assert loop.initial_active_rollout_groups == 8


def test_the_bench_refuses_the_policy_canary(recipe_env, monkeypatch):
    from torchtitan.experiments.rl.examples.tmax.config_registry import (
        rl_grpo_qwen3_5_9b_tmax,
    )

    monkeypatch.setenv("SWE_POLICY_CANARY_K", "8")
    with pytest.raises(ValueError, match="policy canary"):
        rl_grpo_qwen3_5_9b_tmax()


# -- the rollouter ----------------------------------------------------------


def _rollouter(**bench) -> TMaxRollouter:
    r = object.__new__(TMaxRollouter)
    r._agent_name = "terminus"
    r._time_budget_sec = 2400
    r._agent_budget_floor_sec = 7200
    r._eval_timeout_sec = 600
    r._max_context_tokens = 4096
    r._eval_max_context_tokens = 4096
    r._eval_max_turns = 0
    r._eval_agent_budget_cap_sec = 0
    r._eval_verifier_budget_cap_sec = 0
    r._strict_format = r._strict_opening = r._strict_truncation = False
    r._eval_strict_format = r._eval_strict_opening = r._eval_strict_truncation = False
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._shadow_grade_unsubmitted = False
    r._eval_shadow_grade_timeouts = False
    r._eval_grade_unsubmitted = False
    r._grade_unsubmitted = False
    r._eval_context_notice = True
    r._format_streak_limit = 0
    r._length_penalty = False
    r._evolution_min_scored = 2
    r._evolution_harder_ratio = 1.0
    r._evolution_easier_ratio = 0.0
    r._rollout_gate = _RolloutIssueGate(4)
    r._bench = _BenchMode(**bench)
    return r


def _rollout(r, monkeypatch, sample, *, group_id=0, reward=1.0):
    seen: dict = {}

    @contextlib.asynccontextmanager
    async def fake_boot(*args, **kwargs):
        seen.setdefault("boots", []).append(kwargs)
        sandbox = AsyncMock()
        sandbox.sandbox_id = "sandbox-0"
        sandbox.allocated_disk_gb = 1
        yield sandbox

    async def fake_agent(task):
        seen["task"] = task
        return AgentRun(turns=1, submitted=True, finish_reason="submit")

    async def grade(sandbox, tmax, *, workdir, **kwargs):
        return reward

    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", fake_boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: fake_agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
    monkeypatch.setattr(rollouter_mod, "capture_baseline", AsyncMock(return_value=None))
    monkeypatch.setattr(rollouter_mod, "grade_tmax", grade)
    adapter = AsyncMock()
    adapter.open_session = lambda *a, **k: None
    adapter.finish_session = AsyncMock(
        return_value=[
            CapturedTurn(
                prompt_token_ids=[1, 2],
                completion_token_ids=[3],
                completion_logprobs=[-0.1],
                min_policy_version=2,
                max_policy_version=3,
                finish_reason="stop",
                extends_previous=False,
            )
        ]
    )
    result = asyncio.run(
        r._run_agent_rollout(
            adapter=adapter,
            generate_fn=AsyncMock(),
            sample=sample,
            group_id=group_id,
            rollout_idx=0,
            sampling=SimpleNamespace(seed=0),
            renderer=object(),
        )
    )
    return result, seen


@pytest.fixture
def captured(monkeypatch):
    got: list[dict] = []
    monkeypatch.setattr(events, "_local_sink", got.append)
    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    return got


def test_turn_cap_mode_has_no_deadline_and_only_the_sanity_guard(monkeypatch, captured):
    r = _rollouter(max_turns=60)
    guards = []
    r._guard_for = lambda b, v=None: (guards.append(b), TMaxRollouter._guard_for(r, b, v))[1]
    sample = replace(_parse_sample_row(_row("a")), agent_timeout_sec=900)
    _, seen = _rollout(r, monkeypatch, sample)
    assert seen["task"].time_budget_sec == 0 and seen["task"].max_turns == 60
    assert guards and set(guards) == {_TURN_CAP_GUARD_SEC}
    # The readout runs under the same attempt budget as training.
    guards.clear()
    _, seen = _rollout(r, monkeypatch, sample, group_id=-1)
    assert seen["task"].time_budget_sec == 0 and seen["task"].max_turns == 60
    assert set(guards) == {_TURN_CAP_GUARD_SEC}


def test_rollout_done_is_training_only(monkeypatch, captured):
    r = _rollouter(scheduler=True)
    sample = replace(_parse_sample_row(_row("a", rev=2)), version="vid")
    _rollout(r, monkeypatch, sample, group_id=5)
    assert len(captured) == 1
    e = captured[0]
    assert (e["event"], e["version"], e["task"], e["rev"], e["group"], e["idx"]) == (
        "rollout_done",
        "vid",
        "a",
        2,
        5,
        0,
    )
    assert e["reward"] == 1.0 and e["policy_versions"] == [2, 3]
    captured.clear()
    _rollout(r, monkeypatch, sample, group_id=-1)
    assert captured == []


def _canned(group_id: int, rewards: list[float]):
    async def fake(self, *, sample, group_id, rollout_idx, **kwargs):
        rollout = Rollout(
            group_id=group_id,
            rollout_id=rollout_idx,
            status=RolloutStatus.COMPLETED,
            turns=[
                RolloutTurn(
                    rollout_id=RolloutTurnID(group_id=group_id, rollout_id=rollout_idx, turn_id=0),
                    prompt_token_ids=[1],
                    completion_token_ids=[2],
                    completion_logprobs=[0.0],
                    min_policy_version=4,
                    max_policy_version=4 + rollout_idx,
                )
            ],
            diagnostics={"finish_reason": "submit", "format_breakdown": {}},
        )
        diagnostics = _SandboxRolloutDiagnostics(
            sandbox_id="s", disk_gb=1, issue_counts={}, issues=(), num_dropped_details=0
        )
        return rollout, True, 0, "submit", diagnostics

    return fake


@pytest.mark.parametrize("group_id, emitted", [(7, True), (-3, False)])
def test_group_done_is_emitted_for_training_groups_only(monkeypatch, captured, group_id, emitted):
    r = _rollouter(scheduler=True)
    r._adapter = object()
    rewards = [1.0, 0.0, 1.0]
    monkeypatch.setattr(TMaxRollouter, "_run_agent_rollout", _canned(group_id, rewards))

    async def score_group(rollouts, sample):
        return [SimpleNamespace(reward=x, reward_breakdown={}) for x in rewards]

    r.score_group = score_group
    r.advantage_estimator = lambda group: [0.0] * len(group.rollouts)
    sample = replace(_parse_sample_row(_row("a", rev=1)), version="vid")
    asyncio.run(
        r.run_group_rollouts(
            generate_fn=AsyncMock(),
            sample=sample,
            group_id=group_id,
            group_size=3,
            sampling=SimpleNamespace(seed=None),
            renderer=object(),
        )
    )
    if not emitted:
        assert captured == []
        return
    assert [e["event"] for e in captured] == ["group_done"]
    e = captured[0]
    assert (e["version"], e["task"], e["rev"], e["group"]) == ("vid", "a", 1, 7)
    assert (e["passed"], e["scored"], e["policy_version"]) == (2, 3, 6)


def test_sandbox_override_reaches_boot_verifier_and_readout(monkeypatch, captured):
    r = _rollouter(sandbox=(("daytona_cpu", 4),))
    separate = {"image": "verifier:1", "daytona_cpu": 1, "daytona_mem_gb": 2}
    row = _row("a", daytona_cpu=1, daytona_mem_gb=3)
    row["metadata"]["tmax"]["separate_verifier"] = separate
    base = _parse_sample_row(row)
    r._train_dataset = iter([base])
    r._validation_dataset = iter([base])
    train = r.get_training_sample()
    readout = r.get_validation_sample()
    for sample in (train, readout):
        assert (sample.daytona_cpu, sample.daytona_mem_gb) == (4, 3)
        assert sample.tmax["separate_verifier"] == {**separate, "daytona_cpu": 4}
    assert base.tmax["separate_verifier"]["daytona_cpu"] == 1  # the row is untouched
    _, seen = _rollout(r, monkeypatch, train)
    # The agent's sandbox, then grade_separate's verifier sandbox.
    assert [(b["cpu"], b["memory"]) for b in seen["boots"]] == [(4, 3), (4, 2)]


def test_bench_rollouter_draws_from_the_scheduler_and_checkpoints_it(sched):
    r = _rollouter(scheduler=True, sandbox=(("daytona_cpu", 4),))
    sched.pool.add(_row("b"))
    sample = r.get_training_sample()
    assert sample.version in {content_revision(_row(t)) for t in "ab"}
    assert sample.daytona_cpu == 4
    assert r.drain_training_data_events() == []
    state = r.training_data_state_dict()
    expected = [r.get_training_sample().version for _ in range(20)]
    assert len(set(expected)) == 2
    r.load_training_data_state_dict(state)
    assert [r.get_training_sample().version for _ in range(20)] == expected
    with pytest.raises(ValueError, match="dataset cursor"):
        r.load_training_data_state_dict({"order": []})


def test_bench_rollouter_without_a_scheduler_refuses_to_draw(monkeypatch):
    with pytest.raises(RuntimeError, match="no bench scheduler"):
        _rollouter(scheduler=True).get_training_sample()


def _build(monkeypatch, **fields):
    monkeypatch.setattr(rollouter_mod.Rollouter, "__init__", lambda self, config: None)
    return TMaxRollouter(TMaxRollouter.Config(**fields))


def test_sync_wait_is_refused_under_the_scheduler(monkeypatch):
    monkeypatch.setenv("TMAX_AGENT", "vanillux")
    monkeypatch.setattr(rollouter_mod, "_SYNC_WAIT_FOR_REWRITES", True)
    with pytest.raises(ValueError, match="SWE_SYNC_WAIT_FOR_REWRITES"):
        _build(monkeypatch, bench_scheduler=True)
    assert _build(monkeypatch)._bench == _BenchMode()


def test_turn_cap_mode_needs_terminus(monkeypatch):
    monkeypatch.setenv("TMAX_AGENT", "vanillux")
    with pytest.raises(ValueError, match="terminus"):
        _build(monkeypatch, bench_max_turns=60)
