# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""GPU trial admission: at most N trials of GPU rows at once, never a deadlock.

A GPU trial can hold two GPUs (agent sandbox, then its verifier while the agent
sandbox is still up). Admitting whole trials against quota / 2 means every
admitted trial can always get its verifier; admitting sandboxes does not.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from torchtitan.experiments.rl.examples.tmax import rollouter as rollouter_mod
from torchtitan.experiments.rl.examples.tmax.data import _parse_sample_row, TMaxSample
from torchtitan.experiments.rl.examples.tmax.gpu_admission import (
    gpu_trial,
    GpuTrialAdmission,
)
from torchtitan.experiments.rl.examples.tmax.rollouter import (
    _RolloutIssueGate,
    TMaxRollouter,
)
from torchtitan.experiments.rl.harness.adapters.anthropic import CapturedTurn
from torchtitan.experiments.rl.harness.agents.spec import AgentRun

# The subprocesses load gpu_admission.py by path: it needs only the standard
# library, and importing the torchtitan package costs ~700 MB per process.
_MODULE = os.path.join(
    os.path.dirname(__file__), "..", "examples", "tmax", "gpu_admission.py"
)
_LOAD = textwrap.dedent(
    """
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("gpu_admission", sys.argv[1])
    gpu_admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gpu_admission)
    GpuTrialAdmission = gpu_admission.GpuTrialAdmission
    """
)


def _row(task, **md):
    return _parse_sample_row(
        {
            "prompt": "p",
            "metadata": {"instance_id": task, "image": "img", "tmax": {"t": 1}, **md},
        }
    )


def test_which_rows_are_gpu_trials():
    assert gpu_trial(_row("agent-gpu", daytona_gpu=1, daytona_target="earth"))
    assert gpu_trial(
        _row("verifier-gpu", tmax={"separate_verifier": {"image": "v", "daytona_gpu": 1}})
    )
    assert not gpu_trial(_row("cpu", daytona_cpu=4))
    assert not gpu_trial(_row("cpu-verifier", tmax={"separate_verifier": {"image": "v"}}))
    assert not gpu_trial(TMaxSample(instance_id="old", image="i", workdir="/", problem_statement="x"))
    assert not gpu_trial(object())  # no boot_kwargs / tmax at all


def test_slots_up_to_the_limit_and_back(tmp_path):
    admission = GpuTrialAdmission(2, slot_dir=str(tmp_path))
    first, second = admission.try_acquire(), admission.try_acquire()
    assert {first.index, second.index} == {0, 1}
    assert admission.try_acquire() is None
    first.release()
    first.release()  # idempotent
    third = admission.try_acquire()
    assert third is not None and third.index == first.index
    assert admission.try_acquire() is None
    second.release()
    third.release()


@pytest.mark.parametrize("limit", [0, -1])
def test_a_nonpositive_limit_is_refused(limit, tmp_path):
    with pytest.raises(ValueError, match="must be >= 1"):
        GpuTrialAdmission(limit, slot_dir=str(tmp_path))


def test_a_cancelled_wait_holds_nothing(tmp_path):
    async def run():
        admission = GpuTrialAdmission(1, slot_dir=str(tmp_path), poll_sec=0.001)
        held = await admission.acquire()
        waiter = asyncio.create_task(admission.acquire())
        await asyncio.sleep(0.01)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        held.release()
        again = await asyncio.wait_for(admission.acquire(), timeout=1.0)
        again.release()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# A stubbed scheduler with a 10-GPU quota
# --------------------------------------------------------------------------- #
class _Quota:
    """A GPU region: a boot waits while every GPU is taken (as a Daytona boot
    queues for capacity). Tracks the peak to show the quota is never exceeded."""

    def __init__(self, gpus):
        self.free = gpus
        self.gpus = gpus

    async def take(self):
        while self.free == 0:
            await asyncio.sleep(0.001)
        self.free -= 1
        assert 0 <= self.free <= self.gpus

    def give(self, n):
        self.free += n
        assert self.free <= self.gpus


async def _schedule(*, admission, num_gpu, num_cpu, quota, seed=0):
    rng = random.Random(seed)
    running = peak = 0
    order: list[str] = []

    async def gpu(index):
        nonlocal running, peak
        slot = await admission.acquire() if admission is not None else None
        running += 1
        peak = max(peak, running)
        try:
            await quota.take()  # agent sandbox
            await asyncio.sleep(rng.uniform(0, 0.01))
            await quota.take()  # verifier, agent sandbox still up
            await asyncio.sleep(rng.uniform(0, 0.005))
            quota.give(2)
            order.append(f"gpu{index}")
        finally:
            running -= 1
            if slot is not None:
                slot.release()

    async def cpu(index):
        await asyncio.sleep(rng.uniform(0, 0.002))
        order.append(f"cpu{index}")

    trials = [gpu(i) for i in range(num_gpu)] + [cpu(i) for i in range(num_cpu)]
    rng.shuffle(trials)
    await asyncio.gather(*trials)
    return peak, order


@pytest.mark.parametrize("seed", range(5))
def test_admission_never_exceeds_the_limit_and_never_deadlocks(tmp_path, seed):
    quota = _Quota(10)
    admission = GpuTrialAdmission(5, slot_dir=str(tmp_path), poll_sec=0.001)
    peak, order = asyncio.run(
        asyncio.wait_for(
            _schedule(admission=admission, num_gpu=40, num_cpu=40, quota=quota, seed=seed),
            timeout=30,
        )
    )
    assert peak == 5
    assert len(order) == 80 and quota.free == 10


def test_without_trial_admission_agents_take_every_gpu_and_wait_for_verifiers(tmp_path):
    """The case trial-level admission exists for: ten agents boot first, hold all
    ten GPUs, and every verifier waits for a GPU that never frees."""

    async def run():
        quota = _Quota(10)
        return await asyncio.wait_for(
            _schedule(admission=None, num_gpu=10, num_cpu=0, quota=quota), timeout=0.5
        )

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        asyncio.run(run())


def test_rows_without_a_gpu_do_not_wait_for_gpu_slots(tmp_path):
    async def run():
        admission = GpuTrialAdmission(2, slot_dir=str(tmp_path), poll_sec=0.001)
        held = [await admission.acquire(), await admission.acquire()]
        # All GPU slots are taken; CPU trials still run straight through.
        _peak, order = await asyncio.wait_for(
            _schedule(admission=admission, num_gpu=0, num_cpu=20, quota=_Quota(10)),
            timeout=1.0,
        )
        assert len(order) == 20
        for slot in held:
            slot.release()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Across processes: training and eval worker pools share the host's slots
# --------------------------------------------------------------------------- #
_WORKER = textwrap.dedent(
    """
    import asyncio, time

    async def main(slot_dir, log):
        admission = GpuTrialAdmission(2, slot_dir=slot_dir, poll_sec=0.002)
        for _ in range(3):
            slot = await admission.acquire()
            start = time.monotonic()
            await asyncio.sleep(0.03)
            end = time.monotonic()
            slot.release()
            with open(log, "a") as f:
                f.write(f"{start} {end}\\n")

    asyncio.run(main(sys.argv[2], sys.argv[3]))
    """
)


def _python(code, *args):
    return subprocess.Popen(
        [sys.executable, "-I", "-c", _LOAD + code, _MODULE, *args],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_processes_share_one_limit(tmp_path):
    logs = [tmp_path / f"w{i}.log" for i in range(4)]
    workers = [_python(_WORKER, str(tmp_path / "slots"), str(log)) for log in logs]
    assert [worker.wait(timeout=120) for worker in workers] == [0] * 4
    spans = [
        tuple(map(float, line.split()))
        for log in logs
        for line in log.read_text().splitlines()
    ]
    assert len(spans) == 12
    events = sorted([(start, 1) for start, _ in spans] + [(end, -1) for _, end in spans])
    running = peak = 0
    for _time, delta in events:
        running += delta
        peak = max(peak, running)
    assert peak <= 2


def test_a_dead_holder_frees_its_slot(tmp_path):
    code = textwrap.dedent(
        """
        import os
        admission = GpuTrialAdmission(2, slot_dir=sys.argv[2])
        held = [admission.try_acquire(), admission.try_acquire()]
        assert all(held)
        os._exit(0)  # no release
        """
    )
    assert _python(code, str(tmp_path)).wait(timeout=120) == 0
    admission = GpuTrialAdmission(2, slot_dir=str(tmp_path))
    held = [admission.try_acquire(), admission.try_acquire()]
    assert all(held)
    for slot in held:
        slot.release()


# --------------------------------------------------------------------------- #
# Through the rollouter
# --------------------------------------------------------------------------- #
class _Sandbox:
    sandbox_id = "sb"
    allocated_disk_gb = 10

    async def exec(self, cmd, **kwargs):
        return 0, "", ""

    async def write_file(self, path, content, **kwargs):
        return None

    async def read_file(self, path, **kwargs):
        return ""


def _rollouter(admission):
    r = object.__new__(TMaxRollouter)
    r._agent_name = "vanillux"
    r._time_budget_sec = 3600
    r._eval_timeout_sec = 600
    r._max_context_tokens = 4096
    r._eval_max_context_tokens = 4096
    r._eval_max_turns = 0
    r._strict_format = r._strict_opening = r._strict_truncation = False
    r._eval_strict_format = r._eval_strict_opening = r._eval_strict_truncation = False
    r._eval_shadow_grade_timeouts = True
    # Set in __init__, which this bypasses (as in test_tb4_harbor_env's fixture).
    r._eval_grade_unsubmitted = False
    r._grade_unsubmitted = False
    r._eval_context_notice = True
    r._format_streak_limit = 0
    r._reward_mode = "sparse"
    r._read_ctrf = False
    r._shadow_grade_unsubmitted = False
    r._rollout_gate = _RolloutIssueGate(32)
    r._gpu_admission = admission
    return r


def _patch_rollout(monkeypatch, *, fail_task=None):
    active = {"gpu": 0, "peak": 0}

    @contextlib.asynccontextmanager
    async def boot(image, *, labels, **kwargs):
        on_gpu = bool(kwargs.get("sandbox_options", {}).get("gpu"))
        if labels["task"] == fail_task:
            raise RuntimeError("boot failed")
        if on_gpu:
            active["gpu"] += 1
            active["peak"] = max(active["peak"], active["gpu"])
        try:
            await asyncio.sleep(0.02 if on_gpu else 0.001)
            yield _Sandbox()
        finally:
            if on_gpu:
                active["gpu"] -= 1

    async def agent(task):
        return AgentRun(turns=1, submitted=False, finish_reason="hit_context_limit")

    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    monkeypatch.setenv("TT_SANDBOX_NET_PROBE", "0")
    monkeypatch.setattr(rollouter_mod, "boot_agent_sandbox", boot)
    monkeypatch.setattr(rollouter_mod, "get_agent", lambda name: agent)
    monkeypatch.setattr(rollouter_mod, "seed_workspace", AsyncMock())
    return active


def _adapter():
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
    return adapter


async def _run_all(rollouter, samples):
    return await asyncio.gather(
        *(
            rollouter._run_agent_rollout(
                adapter=_adapter(),
                generate_fn=AsyncMock(),
                sample=sample,
                group_id=index,
                rollout_idx=0,
                sampling=SimpleNamespace(seed=0),
                renderer=object(),
            )
            for index, sample in enumerate(samples)
        ),
        return_exceptions=True,
    )


def test_the_rollouter_admits_gpu_rows_and_lets_the_rest_through(monkeypatch, tmp_path):
    active = _patch_rollout(monkeypatch)
    admission = GpuTrialAdmission(2, slot_dir=str(tmp_path), poll_sec=0.002)
    gpu_rows = [_row(f"g{i}", daytona_gpu=1, daytona_target="earth") for i in range(7)]
    cpu_rows = [_row(f"c{i}") for i in range(5)]
    results = asyncio.run(
        asyncio.wait_for(_run_all(_rollouter(admission), gpu_rows + cpu_rows), timeout=30)
    )
    assert not [r for r in results if isinstance(r, BaseException)]
    # Every trial ran its agent: none failed inside the rollout either, so the
    # slots were held across real trials, not error exits.
    assert not [r for r in results if r[-1].infra_failed]
    assert active["peak"] == 2
    held = [admission.try_acquire(), admission.try_acquire()]
    assert all(held)  # every slot came back
    for slot in held:
        slot.release()


def test_a_failed_gpu_trial_returns_its_slot(monkeypatch, tmp_path):
    _patch_rollout(monkeypatch, fail_task="g0")
    admission = GpuTrialAdmission(1, slot_dir=str(tmp_path), poll_sec=0.002)
    rows = [_row(f"g{i}", daytona_gpu=1, daytona_target="earth") for i in range(3)]
    asyncio.run(asyncio.wait_for(_run_all(_rollouter(admission), rows), timeout=30))
    slot = admission.try_acquire()
    assert slot is not None
    slot.release()


def test_no_admission_object_means_no_limit(monkeypatch, tmp_path):
    active = _patch_rollout(monkeypatch)
    rows = [_row(f"g{i}", daytona_gpu=1, daytona_target="earth") for i in range(4)]
    rollouter = _rollouter(None)
    del rollouter._gpu_admission  # a rollouter built before the field existed
    asyncio.run(asyncio.wait_for(_run_all(rollouter, rows), timeout=30))
    assert active["peak"] == 4


def test_slot_wait_is_reported_for_gpu_rows_only():
    from torchtitan.experiments.rl.examples.tmax.rollouter import _timing_metrics

    def keys(timings):
        return {metric.key for metric in _timing_metrics(timings)}

    assert "rollout/gpu_slot_wait_secs_mean" not in keys([{"boot_secs": 1.0}])
    assert "rollout/gpu_slot_wait_secs_mean" in keys(
        [{"boot_secs": 1.0}, {"boot_secs": 1.0, "gpu_slot_wait_secs": 4.0}]
    )



def test_a_negative_limit_is_an_error_not_off(tmp_path):
    rows = str(tmp_path / "rows.jsonl")
    with open(rows, "w") as f:
        f.write(
            '{"prompt": "p", "metadata": {"instance_id": "t", "image": "i", "tmax": {"t": 1}}}\n'
        )
    config = TMaxRollouter.Config(
        train_dataset=rollouter_mod.TMaxDataset.Config(data_path=rows),
        validation_dataset=rollouter_mod.TMaxDataset.Config(data_path=rows),
        gpu_trial_concurrency=-1,
    )
    with pytest.raises(ValueError, match="must be >= 0"):
        config.build()
    assert TMaxRollouter.Config(
        train_dataset=rollouter_mod.TMaxDataset.Config(data_path=rows),
        validation_dataset=rollouter_mod.TMaxDataset.Config(data_path=rows),
        gpu_trial_concurrency=0,
    ).build()._gpu_admission is None


class _FailingGate:
    async def acquire_sibling(self, priority):
        raise RuntimeError("gate broke")

    def release(self):
        raise AssertionError("never acquired")


def test_the_slot_comes_back_when_the_gate_raises(monkeypatch, tmp_path):
    _patch_rollout(monkeypatch)
    admission = GpuTrialAdmission(1, slot_dir=str(tmp_path), poll_sec=0.002)
    rollouter = _rollouter(admission)
    rollouter._rollout_gate = _FailingGate()
    rows = [_row("g0", daytona_gpu=1, daytona_target="earth")]
    (result,) = asyncio.run(_run_all(rollouter, rows))
    assert isinstance(result, RuntimeError)
    slot = admission.try_acquire()
    assert slot is not None
    slot.release()


def test_the_slot_comes_back_when_the_gate_wait_is_cancelled(monkeypatch, tmp_path):
    _patch_rollout(monkeypatch)

    async def run():
        admission = GpuTrialAdmission(1, slot_dir=str(tmp_path), poll_sec=0.002)
        rollouter = _rollouter(admission)
        rollouter._rollout_gate = _RolloutIssueGate(1)
        await rollouter._rollout_gate.acquire_sibling((-1, 0))  # the only seat is taken
        trial = asyncio.create_task(
            _run_all(rollouter, [_row("g0", daytona_gpu=1, daytona_target="earth")])
        )
        for _ in range(200):  # until it holds the slot and waits at the gate
            await asyncio.sleep(0.005)
            probe = admission.try_acquire()
            if probe is None:
                break
            probe.release()
        else:
            raise AssertionError("the trial never took the GPU slot")
        trial.cancel()
        await asyncio.gather(trial, return_exceptions=True)
        slot = admission.try_acquire()
        assert slot is not None
        slot.release()
        rollouter._rollout_gate.release()

    asyncio.run(asyncio.wait_for(run(), timeout=30))
