# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Worker setup runs on its own loop, before the generator router is built."""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.actors import rollout_worker


@pytest.mark.parametrize("cpu_count", [64, None])
def test_setup_installs_a_working_pool_on_the_worker_loop(
    monkeypatch, caplog, cpu_count
):
    monkeypatch.setattr(rollout_worker.os, "cpu_count", lambda: cpu_count)
    caplog.set_level(logging.INFO, logger="torchtitan")
    loop = asyncio.new_event_loop()
    generators = [object()]
    router = object()

    def build(*, generators):
        assert generators is supplied_generators
        assert asyncio.get_running_loop() is loop
        # Setup must install the executor before building the router.
        assert isinstance(loop._default_executor, ThreadPoolExecutor)
        return router

    supplied_generators = generators
    worker = SimpleNamespace(
        config=SimpleNamespace(generator_router=SimpleNamespace(build=build))
    )

    async def exercise():
        # Invoke the real endpoint implementation without spawning model actors.
        await rollout_worker.RolloutWorker.setup._method(worker, generators)
        assert worker._generator_router is router
        executor = loop._default_executor
        assert executor._max_workers > 0
        if cpu_count is not None:
            assert executor._max_workers == cpu_count
        assert f"installed={executor._max_workers}" in caplog.text
        assert "was None" in caplog.text
        tid = await asyncio.wait_for(asyncio.to_thread(threading.get_ident), 3)
        assert tid != threading.get_ident()

    try:
        loop.run_until_complete(exercise())
    finally:
        if loop._default_executor is not None:
            loop._default_executor.shutdown(wait=True)
        loop.close()


def test_worker_setup_leaves_the_controller_loop_pool_unchanged(monkeypatch):
    monkeypatch.setattr(rollout_worker.os, "cpu_count", lambda: 64)
    controller = asyncio.new_event_loop()
    worker_loop = asyncio.new_event_loop()
    controller_pool = ThreadPoolExecutor(max_workers=2)
    controller.set_default_executor(controller_pool)
    worker = SimpleNamespace(
        config=SimpleNamespace(
            generator_router=SimpleNamespace(build=lambda **kwargs: object())
        )
    )
    try:
        worker_loop.run_until_complete(
            rollout_worker.RolloutWorker.setup._method(worker, [])
        )
        assert controller._default_executor is controller_pool
        assert controller_pool._max_workers == 2
        assert worker_loop._default_executor is not controller_pool
        assert worker_loop._default_executor._max_workers == 64
    finally:
        controller_pool.shutdown(wait=True)
        if worker_loop._default_executor is not None:
            worker_loop._default_executor.shutdown(wait=True)
        controller.close()
        worker_loop.close()
