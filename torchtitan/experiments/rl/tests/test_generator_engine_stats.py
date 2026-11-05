"""_EngineStatsLogger: vLLM engine stats reported through our logger."""

import logging
from types import SimpleNamespace

from torchtitan.experiments.rl.actors import generator


def _sched(running, waiting, kv):
    return SimpleNamespace(
        num_running_reqs=running,
        num_waiting_reqs=waiting,
        num_skipped_waiting_reqs=0,
        kv_cache_usage=kv,
    )


def test_one_line_per_interval_with_peak_kv_and_preemptions(monkeypatch, caplog):
    monkeypatch.setenv("RL_VLLM_STATS_INTERVAL_SEC", "60")
    clock = [0.0]
    monkeypatch.setattr(generator.time, "monotonic", lambda: clock[0])
    stats = generator._EngineStatsLogger(vllm_config=None, engine_index=3)
    stats.record(_sched(10, 2, 0.5), SimpleNamespace(num_preempted_reqs=1))
    stats.record(_sched(12, 5, 0.9), SimpleNamespace(num_preempted_reqs=2))
    stats.record(_sched(11, 4, 0.7), None)
    with caplog.at_level(logging.INFO, logger=generator.logger.name):
        clock[0] = 30.0
        stats.log()  # inside the interval: nothing
        clock[0] = 61.0
        stats.log()
        clock[0] = 125.0
        stats.log()
    lines = [r.getMessage() for r in caplog.records if "[vllm_stats]" in r.getMessage()]
    assert lines[0] == (
        "[vllm_stats] engine=3 running=11 waiting=4 kv_peak=90.0% "
        "preempted=3 preempted_total=3 prefix_hit=n/a window_s=61"
    )
    assert "kv_peak=0.0% preempted=0 preempted_total=3" in lines[1]
    assert len(lines) == 2


def test_engine_stats_ride_on_request_metrics(monkeypatch):
    monkeypatch.setattr(generator, "_ENGINE_STATS", {})
    stats = generator._EngineStatsLogger(vllm_config=None)
    stats.record(_sched(5, 3, 0.25), SimpleNamespace(num_preempted_reqs=4))
    stats.record(_sched(5, 1, 0.5), SimpleNamespace(num_preempted_reqs=1))
    assert generator._ENGINE_STATS == {
        "kv_cache_usage": 0.5,
        "waiting_reqs": 1,
        "preempted_total": 5,
    }


def test_window_prefix_hit_rate_counts_preempted_reprefills(monkeypatch, caplog):
    monkeypatch.setenv("RL_VLLM_STATS_INTERVAL_SEC", "60")
    clock = [0.0]
    monkeypatch.setattr(generator.time, "monotonic", lambda: clock[0])
    stats = generator._EngineStatsLogger(vllm_config=None)

    def sched_with_prefix(queries, hits, preempted_queries=0, preempted_hits=0):
        sched = _sched(1, 0, 0.1)
        sched.prefix_cache_stats = SimpleNamespace(
            queries=queries,
            hits=hits,
            preempted_queries=preempted_queries,
            preempted_hits=preempted_hits,
        )
        return sched

    stats.record(sched_with_prefix(1000, 900), None)
    stats.record(sched_with_prefix(0, 0, preempted_queries=1000, preempted_hits=100), None)
    with caplog.at_level(logging.INFO, logger=generator.logger.name):
        clock[0] = 61.0
        stats.log()
        clock[0] = 122.0
        stats.log()
    lines = [r.getMessage() for r in caplog.records if "[vllm_stats]" in r.getMessage()]
    assert "prefix_hit=50.0%" in lines[0]
    assert "prefix_hit=n/a" in lines[1]


def test_request_prefix_cache_hit_rate(monkeypatch):
    monkeypatch.setattr(generator, "_ENGINE_STATS", {})
    inputs = generator._RequestMetricsInputs(
        num_cached_tokens=750, has_stats=False, num_prompt_tokens=1000
    )
    metrics = generator._prepare_generation_request_metrics(inputs, prefix="generator")
    values = {
        (metric.key, type(metric.value).__name__): metric.value.value
        for metric in metrics
    }
    assert values[("generator/prefix_cache_hit_rate", "Mean")] == 0.75
    assert values[("generator/num_cached_tokens", "Max")] == 750
