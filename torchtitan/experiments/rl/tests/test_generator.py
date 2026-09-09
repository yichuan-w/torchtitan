# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the `VLLMGenerator` continuous-batching mechanics.

Exercises the per-request pieces in isolation with a fake vLLM engine — no Monarch,
no GPU, no real model, and no broadcast (the engine loop's broadcast/step is a TP collective,
not unit-tested here; `test_engine_loop.py` covers the decision logic in `_decide_next_action`).
Covers completion (token-out + the metrics that ride with it),
the SamplingParams contract, and the vLLM metric timing math.
"""

import asyncio
from types import SimpleNamespace

import pytest
from vllm.sampling_params import RequestOutputKind

from torchtitan.config import DebugConfig
from torchtitan.experiments.rl.actors.generator import (
    _extract_request_metrics_inputs,
    _prepare_generation_request_metrics,
    _resolve_max_num_batched_tokens,
    GenerationFuture,
    RequestDispatcher,
    SamplingConfig,
    VLLMCudagraphConfig,
    VLLMGenerator,
)
from torchtitan.experiments.rl.models.vllm_registry import InferenceParallelismConfig
from torchtitan.experiments.rl.observability import metrics as m
from torchtitan.experiments.rl.routing.intra_generator_router import (
    IntraGeneratorRouter,
)
from torchtitan.experiments.rl.routing.strategies import LeastLoadedRoutingStrategy


class _FakeRenderer:
    """Stub for vLLM's Renderer.render_cmpl: token-id dicts in, typed EngineInputs out."""

    def render_cmpl(self, prompts):
        return [
            {
                "type": "token",
                "prompt_token_ids": p["prompt_token_ids"],
                "arrival_time": 0.0,
            }
            for p in prompts
        ]


class _FakeEngine:
    def __init__(self):
        self.add_requests = []
        self.renderer = _FakeRenderer()

    def add_request(self, *args, **kwargs):
        self.add_requests.append((args, kwargs))


def _sample(*, token_ids=(10, 11), finish_reason="stop", stop_reason=None):
    return SimpleNamespace(
        token_ids=list(token_ids),
        logprobs=[{tok: SimpleNamespace(logprob=-0.1)} for tok in token_ids],
        finish_reason=finish_reason,
        stop_reason=stop_reason,
    )


def _request_output(*, request_id="r0", outputs=None, num_generation_tokens=4):
    return SimpleNamespace(
        request_id=request_id,
        num_cached_tokens=0,
        metrics=SimpleNamespace(
            first_token_latency=0.012,
            queued_ts=1.0,
            scheduled_ts=1.005,
            first_token_ts=1.017,
            last_token_ts=1.047,
            num_generation_tokens=num_generation_tokens,
        ),
        outputs=list(outputs or [_sample()]),
    )


def _generator():
    """A bare generator (no __init__ / engine build) with just the state the
    per-request helpers (`_build_sampling_params`) read."""
    generator = VLLMGenerator.__new__(VLLMGenerator)
    generator._engine = _FakeEngine()
    generator._rank = 0
    generator.policy_version = 7
    generator.config = SimpleNamespace(
        sampling=SamplingConfig(temperature=0.0, top_p=1.0, max_tokens=4),
        debug=SimpleNamespace(seed=None),
    )
    return generator


def _dispatcher(*, rank=0, dp_degree=1, tp_degree=1, dp_routing_strategy=None):
    """A bare RequestDispatcher; broadcast_group is unused unless ``setup`` runs.

    Passes a vLLM parallel config that matches the layout so the construction-time
    assert holds.
    """
    parallelism = SimpleNamespace(
        data_parallel_degree=dp_degree, tensor_parallel_degree=tp_degree
    )
    vllm_parallel_config = SimpleNamespace(
        tensor_parallel_size=tp_degree,
        data_parallel_size=dp_degree,
        data_parallel_rank=rank // tp_degree,
    )
    return RequestDispatcher(
        rank=rank,
        parallelism=parallelism,
        broadcast_group=None,
        vllm_parallel_config=vllm_parallel_config,
        intra_generator_router=IntraGeneratorRouter.Config(
            strategy=dp_routing_strategy or LeastLoadedRoutingStrategy.Config()
        ),
    )


# --- completion (token-out) ---


@pytest.mark.parametrize(
    ("max_model_len", "has_gdn_layers", "prefix_caching_enabled", "parity", "expected"),
    [
        (1024, True, True, False, 2048),
        (8192, True, True, False, 8192),
        # Long context under align-mode prefix caching: the budget is CAPPED so vLLM
        # chunks the prefill (which align mode requires) instead of sizing its
        # non-KV reserve from a 65536-token dummy forward.
        (65536, True, True, False, 8192),
        (1024, False, True, False, None),
        (1024, True, False, False, None),
        (1024, True, False, True, 2048),
    ],
)
def test_resolve_max_num_batched_tokens(
    max_model_len,
    has_gdn_layers,
    prefix_caching_enabled,
    parity,
    expected,
):
    assert (
        _resolve_max_num_batched_tokens(
            max_model_len=max_model_len,
            has_gdn_layers=has_gdn_layers,
            prefix_caching_enabled=prefix_caching_enabled,
            gdn_trainer_parity=parity,
        )
        == expected
    )


def test_process_finished_requests_resolves_future_with_completion():
    async def main():
        # DP=1: rank 0 is the single replica's leader, so it builds and resolves locally.
        dispatcher = _dispatcher()
        future = asyncio.get_running_loop().create_future()
        # Admitted (sampled) under v7 (the min); a weight pull then advanced the live version to 8 (the max).
        generation_future = GenerationFuture(future=future, metrics_prefix="generator")
        generation_future.min_policy_version = 7
        dispatcher._rank0_generation_futures = {"r0": generation_future}

        dispatcher.process_finished_requests(
            [
                _request_output(
                    outputs=[
                        _sample(
                            token_ids=(10, 11),
                            finish_reason="length",
                            stop_reason=99,
                        )
                    ]
                )
            ],
            policy_version=8,
        )

        completion = await future
        assert completion.request_id == "r0"
        assert completion.token_ids == [10, 11]
        assert completion.token_logprobs == [-0.1, -0.1]
        assert completion.finish_reason == "length"
        assert completion.stop_reason == 99
        assert completion.min_policy_version == 7  # min = version it was admitted under
        assert completion.max_policy_version == 8  # max = live version at finish
        # The request is popped from the in-flight map.
        assert dispatcher._rank0_generation_futures == {}
        # The per-generation metrics ride on the completion (built on rank 0).
        assert (
            m.MetricsProcessor._aggregate_metrics(completion.metrics)[
                "generator/inflight_requests_at_completion/max"
            ]
            == 1
        )

    asyncio.run(main())


@pytest.mark.parametrize("settlement", ["abort", "cancel"])
def test_peer_result_drain_survives_late_completion(settlement):
    async def main():
        dispatcher = _dispatcher(dp_degree=5)
        queue = asyncio.Queue()
        dispatcher._rank0_result_receiver = SimpleNamespace(recv=queue.get)
        futures = {}
        for rid in ("late", "healthy", "next"):
            futures[rid] = dispatcher.rank0_register_future(rid, "generator")
            dispatcher._rank0_generation_futures[rid].min_policy_version = 7
            dispatcher._rank0_dp_router.reserve(rid, routing_session_id=rid)

        # A peer finished and sent its output, but rank 0 handles cancellation
        # before the fan-in task receives that already queued completion.
        if settlement == "abort":
            assert dispatcher.rank0_settle_aborted("late")
        else:
            futures["late"].cancel()
        drain = asyncio.create_task(dispatcher._rank0_drain_results())
        try:
            await queue.put(
                dispatcher._build_completions(
                    [_request_output(request_id=rid) for rid in ("late", "healthy")], 7
                )
            )
            await asyncio.sleep(0)
            assert (
                not drain.done()
            ), f"peer completion drain died: {drain.exception()!r}"
            assert futures["healthy"].done()
            assert futures["healthy"].result().request_id == "healthy"
            await queue.put(
                dispatcher._build_completions([_request_output(request_id="next")], 7)
            )
            await asyncio.sleep(0)
            assert futures["next"].result().request_id == "next"
            assert not dispatcher.rank0_has_pending_futures()
            assert dispatcher._rank0_dp_router._reservations == {}
            assert all(
                h.reserved_load == 0 for h in dispatcher._rank0_dp_router._handles
            )
        finally:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)

    asyncio.run(main())


def test_peer_result_drain_failure_is_logged_and_rejects_new_requests(caplog):
    async def main():
        dispatcher = _dispatcher(dp_degree=5)
        pending = dispatcher.rank0_register_future("pending", "generator")

        async def broken_recv():
            raise RuntimeError("result channel unavailable")

        dispatcher._rank0_result_receiver = SimpleNamespace(recv=broken_recv)
        dispatcher._rank0_drain_task = asyncio.create_task(
            dispatcher._rank0_drain_results()
        )
        with pytest.raises(RuntimeError, match="result channel unavailable"):
            await dispatcher._rank0_drain_task
        with pytest.raises(RuntimeError, match="result channel unavailable"):
            await pending
        with pytest.raises(RuntimeError, match="peer result drain has stopped"):
            dispatcher.rank0_register_future("new", "generator")
        assert "generator peer result drain crashed" in caplog.text

    asyncio.run(main())


def test_metric_failure_settles_the_current_future_and_all_siblings(monkeypatch):
    import torchtitan.experiments.rl.actors.generator as mod

    def fail_metrics(*args, **kwargs):
        raise RuntimeError("invalid request metrics")

    async def main():
        dispatcher = _dispatcher(dp_degree=5)
        futures = [
            dispatcher.rank0_register_future(rid, "generator")
            for rid in ("bad", "sibling", "queued")
        ]
        for rid in ("bad", "sibling"):
            dispatcher._rank0_generation_futures[rid].min_policy_version = 7
            dispatcher._rank0_dp_router.reserve(rid, routing_session_id=rid)
        queue = asyncio.Queue()
        dispatcher._rank0_result_receiver = SimpleNamespace(recv=queue.get)
        await queue.put(
            dispatcher._build_completions([_request_output(request_id="bad")], 7)
        )
        monkeypatch.setattr(mod, "_prepare_generation_request_metrics", fail_metrics)
        with pytest.raises(RuntimeError, match="invalid request metrics"):
            await dispatcher._rank0_drain_results()
        for future in futures:
            assert future.done()
            with pytest.raises(RuntimeError, match="invalid request metrics"):
                await future
        assert dispatcher._rank0_dp_router._reservations == {}

    asyncio.run(main())


def test_failed_engine_loop_rejects_new_work():
    async def main():
        generator = _generator()

        async def fail():
            raise RuntimeError("engine failed")

        generator._engine_loop_task = asyncio.create_task(fail())
        await asyncio.gather(generator._engine_loop_task, return_exceptions=True)
        with pytest.raises(RuntimeError, match="engine loop has stopped") as raised:
            await generator._ensure_engine_loop()
        assert str(raised.value.__cause__) == "engine failed"

    asyncio.run(main())


def _abort_collective_rank(rank, rendezvous):
    from datetime import timedelta

    import torch.distributed as dist

    dist.init_process_group(
        "gloo",
        init_method=f"file://{rendezvous}",
        rank=rank,
        world_size=2,
        timeout=timedelta(seconds=30),
    )
    try:

        async def main():
            generator = _generator()
            generator._rank = rank
            generator._broadcast_group = dist.group.WORLD
            generator._request_dispatcher = _dispatcher(rank=rank, dp_degree=2)

            def abort(ids):
                if rank == 1:
                    raise RuntimeError("peer abort failed")

            generator._engine.abort_request = abort
            with pytest.raises(RuntimeError, match="abort failed on at least one rank"):
                await generator._abort_requests(["r0"])

        asyncio.run(main())
    finally:
        dist.destroy_process_group()


def test_peer_abort_failure_reaches_all_ranks_over_gloo(tmp_path):
    import torch.multiprocessing as mp

    mp.spawn(
        _abort_collective_rank, args=(str(tmp_path / "gloo"),), nprocs=2, join=True
    )


@pytest.mark.parametrize("failed_rank", [None, "local", "peer"])
def test_abort_requires_success_on_every_rank(monkeypatch, failed_rank):
    import torchtitan.experiments.rl.actors.generator as mod

    async def main():
        generator = _generator()
        dispatcher = generator._request_dispatcher = _dispatcher(dp_degree=2)
        generator._broadcast_group = object()
        future = dispatcher.rank0_register_future("r0", "generator")
        dispatcher._rank0_dp_router.reserve("r0", routing_session_id="r0")

        def abort(ids):
            assert ids == ["r0"]
            if failed_rank == "local":
                raise RuntimeError("abort failed")

        def collective(failed, *, op, group):
            assert failed.item() == int(failed_rank == "local")
            assert group is generator._broadcast_group
            if failed_rank == "peer":
                failed.fill_(1)

        generator._engine.abort_request = abort
        monkeypatch.setattr(mod.dist, "all_reduce", collective)
        if failed_rank is None:
            await generator._abort_requests(["r0"])
            assert await future is None
            assert dispatcher._rank0_dp_router._reservations == {}
        else:
            with pytest.raises(RuntimeError, match="abort failed on at least one rank"):
                await generator._abort_requests(["r0"])
            assert not future.done()
            assert "r0" in dispatcher._rank0_dp_router._reservations
            dispatcher.fail_generation_futures(RuntimeError("generator failed"))
            with pytest.raises(RuntimeError, match="generator failed"):
                await future

    asyncio.run(main())


def test_process_finished_requests_noop_on_nonzero_tp_rank():
    # tp_rank != 0 hold no finished outputs, so processing returns before building or sending.
    dispatcher = _dispatcher(rank=1, dp_degree=1, tp_degree=2)
    assert dispatcher._tp_rank != 0
    dispatcher.process_finished_requests(
        [_request_output(request_id="r0")], policy_version=7
    )
    assert dispatcher._rank0_generation_futures == {}


def test_process_finished_requests_releases_dp_router_load():
    async def main():
        dispatcher = _dispatcher(dp_degree=2)
        assert dispatcher._rank0_dp_router is not None
        future = asyncio.get_running_loop().create_future()
        generation_future = GenerationFuture(future=future, metrics_prefix="generator")
        generation_future.min_policy_version = 7
        dispatcher._rank0_generation_futures = {"r0": generation_future}
        dispatcher._rank0_dp_router.reserve("r0", routing_session_id=None)
        # The reservation is recorded (least-loaded picks DP rank 0) and loads it.
        assert dispatcher._rank0_dp_router._reservations == {"r0": 0}
        assert [h.reserved_load for h in dispatcher._rank0_dp_router._handles] == [1, 0]

        dispatcher.process_finished_requests(
            [_request_output(request_id="r0")], policy_version=7
        )

        await future
        # Resolving the completion releases the reservation and its load.
        assert dispatcher._rank0_dp_router._reservations == {}
        assert [h.reserved_load for h in dispatcher._rank0_dp_router._handles] == [0, 0]

    asyncio.run(main())


# --- SamplingParams contract (must match the batched path exactly) ---


def test_build_sampling_params_matches_contract():
    # seed and stop_token_ids are carried on the SamplingConfig (the rollouter
    # offsets the seed per sample); _build_sampling_params just reads them.
    generator = _generator()
    params = generator._build_sampling_params(
        SamplingConfig(
            temperature=0.3,
            top_p=0.9,
            max_tokens=64,
            seed=44,
            stop_token_ids=[99],
        )
    )
    assert params.temperature == 0.3 and params.top_p == 0.9
    assert params.max_tokens == 64
    assert params.n == 1
    assert params.logprobs == 0
    assert params.output_kind == RequestOutputKind.FINAL_ONLY
    assert params.stop_token_ids == [99]
    assert params.seed == 44


def test_build_sampling_params_seed_and_stop_default_to_none():
    generator = _generator()
    params = generator._build_sampling_params(
        SamplingConfig(temperature=0.8, top_p=0.95, max_tokens=8)
    )
    assert params.seed is None
    assert not params.stop_token_ids  # vLLM normalizes None -> []


# --- vLLM metric timing math (the `_prepare_generation_request_metrics` helper) ---


def test_metric_timing_math_and_prefix_override():
    metrics = _prepare_generation_request_metrics(
        _extract_request_metrics_inputs(_request_output()),
        prefix="validation_generator",
    )
    aggregate = m.MetricsProcessor._aggregate_metrics(metrics)
    assert all(key.startswith("validation_generator/") for key in aggregate)
    assert aggregate["validation_generator/queue_time_ms/mean"] == pytest.approx(5)
    assert aggregate["validation_generator/time_to_first_token_ms/mean"] == 12
    assert aggregate["validation_generator/prefill_time_ms/mean"] == pytest.approx(12)
    assert aggregate["validation_generator/decode_time_ms/mean"] == pytest.approx(30)
    assert aggregate[
        "validation_generator/inter_token_latency_ms/mean"
    ] == pytest.approx(10)


def test_decode_metrics_absent_for_single_generated_token():
    metrics = _prepare_generation_request_metrics(
        _extract_request_metrics_inputs(_request_output(num_generation_tokens=1)),
        prefix="generator",
    )
    keys = {metric.key for metric in metrics}
    assert "generator/prefill_time_ms" in keys
    assert "generator/decode_time_ms" not in keys
    assert "generator/inter_token_latency_ms" not in keys


# --- config guards (weight-sync invariants) ---

# A valid inference parallelism; the weight-sync guards run after it is accepted.
_PARALLELISM = InferenceParallelismConfig()


def test_batch_invariant_requires_prefix_cache_reset():
    with pytest.raises(ValueError, match="reset_prefix_cache_on_weight_sync"):
        VLLMGenerator.Config(
            parallelism=_PARALLELISM,
            debug=DebugConfig(batch_invariant=True),
            reset_prefix_cache_on_weight_sync=False,
        )


def test_reset_running_requests_requires_prefix_cache_reset():
    with pytest.raises(ValueError, match="reset_prefix_cache_on_weight_sync"):
        VLLMGenerator.Config(
            parallelism=_PARALLELISM,
            reset_running_requests_on_weight_sync=True,
            reset_prefix_cache_on_weight_sync=False,
        )


def test_salt_prefix_cache_accepted_with_batch_invariant():
    config = VLLMGenerator.Config(
        parallelism=_PARALLELISM,
        debug=DebugConfig(batch_invariant=True),
        salt_prefix_cache_on_weight_sync=True,
        reset_prefix_cache_on_weight_sync=False,
        reset_running_requests_on_weight_sync=False,
    )
    assert config.salt_prefix_cache_on_weight_sync


def test_salt_prefix_cache_accepted_with_resets_off():
    # The intended combo: salt on, both reset knobs off (hot-swap keeps in-flight KV).
    config = VLLMGenerator.Config(
        parallelism=_PARALLELISM,
        salt_prefix_cache_on_weight_sync=True,
        reset_prefix_cache_on_weight_sync=False,
        reset_running_requests_on_weight_sync=False,
    )
    assert config.salt_prefix_cache_on_weight_sync


def test_trainer_requires_prefix_cache_reset_when_hotswap_off():
    # Strict drain (hot_swap=False) needs the prefix cache reset so post-pull requests don't reuse old-weight KV.
    import dataclasses

    from torchtitan.experiments.rl.examples.alphabet_sort.config_registry import (
        rl_grpo_qwen3_0_6b_varlen,
    )

    config = rl_grpo_qwen3_0_6b_varlen()
    # hot_swap defaults True; the guard fires only in drain mode (hot_swap=False) with reset also off.
    with pytest.raises(ValueError, match="reset_prefix_cache_on_weight_sync"):
        dataclasses.replace(
            config,
            generator_router=dataclasses.replace(
                config.generator_router, hot_swap=False
            ),
            generator=dataclasses.replace(
                config.generator, reset_prefix_cache_on_weight_sync=False
            ),
        )


# --- CUDA graph config (VLLMCudagraphConfig.get_vllm_compilation_config) ---


def test_cudagraph_disabled_returns_none():
    assert (
        VLLMCudagraphConfig(enable=False).get_vllm_compilation_config(max_num_seqs=256)
        is None
    )


def test_cudagraph_default_mode_is_full_decode_only():
    # Default mode; decode-only graphs avoid the mixed-batch corruption (#3668),
    # with no inductor compile (CompilationMode.NONE == 0).
    cfg = VLLMCudagraphConfig(enable=True).get_vllm_compilation_config(max_num_seqs=256)
    assert cfg.cudagraph_mode.name == "FULL_DECODE_ONLY"
    assert int(cfg.mode) == 0


def test_cudagraph_full_mode_no_compile():
    # FULL captures the whole forward (incl. attention) with no inductor compile.
    cfg = VLLMCudagraphConfig(enable=True, mode="FULL").get_vllm_compilation_config(
        max_num_seqs=256
    )
    assert cfg.cudagraph_mode.name == "FULL"
    assert int(cfg.mode) == 0


def test_cudagraph_decode_only_capture_sizes_cover_max_num_seqs():
    # FULL_DECODE_ONLY only graphs decode, so capture up to max_num_seqs (plus
    # max_num_seqs itself when not a power of 2).
    cfg = VLLMCudagraphConfig(enable=True).get_vllm_compilation_config(max_num_seqs=500)
    assert cfg.cudagraph_capture_sizes == [1, 2, 4, 8, 16, 32, 64, 128, 256, 500]


def test_cudagraph_full_mode_extends_capture_sizes_to_chunk():
    # FULL also graphs prefill, so sizes extend to the chunked-prefill chunk
    # (max_num_batched_tokens, 2048) on top of max_num_seqs.
    cfg = VLLMCudagraphConfig(enable=True, mode="FULL").get_vllm_compilation_config(
        max_num_seqs=500
    )
    assert cfg.cudagraph_capture_sizes[-1] == 2048
    assert 500 in cfg.cudagraph_capture_sizes  # decode batch captured exactly


def test_cudagraph_rejects_nonpositive_max_num_seqs():
    with pytest.raises(ValueError, match="max_num_seqs must be positive"):
        VLLMCudagraphConfig(enable=True).get_vllm_compilation_config(max_num_seqs=0)
