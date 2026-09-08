# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the in-mesh DP request router (`IntraGeneratorRouter`)."""

from __future__ import annotations

import pytest

from torchtitan.experiments.rl.routing.intra_generator_router import (
    IntraGeneratorRouter,
)
from torchtitan.experiments.rl.routing.strategies import (
    LeastLoadedRoutingStrategy,
    RoundRobinRoutingStrategy,
    StickySessionRoutingStrategy,
)


def _loads(router: IntraGeneratorRouter) -> list[int]:
    return [h.reserved_load for h in router._handles]


def test_round_robin_cycles_across_dp_ranks():
    router = IntraGeneratorRouter.Config(
        strategy=RoundRobinRoutingStrategy.Config()
    ).build(dp_degree=3)
    chosen = [router.reserve(f"r{i}", routing_session_id=None) for i in range(7)]
    assert chosen == [0, 1, 2, 0, 1, 2, 0]


def test_least_loaded_balances_in_flight_count():
    router = IntraGeneratorRouter.Config(
        strategy=LeastLoadedRoutingStrategy.Config()
    ).build(dp_degree=2)
    # Each reserve adds one load unit; idle ties resolve to the lowest index.
    assert router.reserve("r0", routing_session_id=None) == 0
    assert router.reserve("r1", routing_session_id=None) == 1
    # Both ranks now hold one request; the tie again resolves to rank 0.
    assert router.reserve("r2", routing_session_id=None) == 0
    assert _loads(router) == [2, 1]
    # Freeing rank 0's requests makes it the least loaded again.
    router.release("r0")
    router.release("r2")
    assert router.reserve("r3", routing_session_id=None) == 0


def test_release_frees_load():
    router = IntraGeneratorRouter.Config(
        strategy=LeastLoadedRoutingStrategy.Config()
    ).build(dp_degree=2)
    router.reserve("r0", routing_session_id=None)
    router.reserve("r1", routing_session_id=None)
    router.release("r0")
    router.release("r1")
    assert _loads(router) == [0, 0]
    assert router._reservations == {}


def test_duplicate_reserve_asserts():
    router = IntraGeneratorRouter.Config(
        strategy=LeastLoadedRoutingStrategy.Config()
    ).build(dp_degree=2)
    router.reserve("r0", routing_session_id=None)
    with pytest.raises(AssertionError, match="already has a reservation"):
        router.reserve("r0", routing_session_id=None)


def test_release_unknown_request_raises():
    router = IntraGeneratorRouter.Config(
        strategy=LeastLoadedRoutingStrategy.Config()
    ).build(dp_degree=2)
    with pytest.raises(KeyError):
        router.release("missing")


def test_sticky_pins_session_to_dp_rank():
    router = IntraGeneratorRouter.Config(
        strategy=StickySessionRoutingStrategy.Config()
    ).build(dp_degree=3)
    first = router.reserve("r0", routing_session_id="s0")
    # Even though s0's DP rank now has load, the same session sticks to it.
    assert router.reserve("r1", routing_session_id="s0") == first
    # A different session uses the (least-loaded) fallback -> a different DP rank.
    assert router.reserve("r2", routing_session_id="s1") != first


@pytest.mark.parametrize("dp_degree", [0, 1])
def test_rejects_dp_degree_without_routing(dp_degree: int):
    with pytest.raises(ValueError, match="dp_degree must be > 1"):
        IntraGeneratorRouter.Config(strategy=RoundRobinRoutingStrategy.Config()).build(
            dp_degree=dp_degree
        )


def test_sticky_rebalances_a_swamped_pin():
    """A session pinned to a rank that is far more loaded than the least-loaded
    rank moves there on its next request (and stays: it is re-pinned)."""
    router = IntraGeneratorRouter.Config(
        strategy=StickySessionRoutingStrategy.Config(
            fallback_strategy=RoundRobinRoutingStrategy.Config(),
            rebalance_load_ratio=2.0,
            rebalance_min_gap=2,
        )
    ).build(dp_degree=2)
    # Session A pins to rank 0 (first RoundRobin pick).
    assert router.reserve("a0", routing_session_id="A") == 0
    # Swamp rank 0 with unpinned traffic while rank 1 stays idle.
    for i in range(6):
        router._handles[0].reserved_load += 1
    assert _loads(router) == [7, 0]
    # 7 > 2.0 * 0 + 2 -> A's next request breaks the pin and goes to rank 1 ...
    assert router.reserve("a1", routing_session_id="A") == 1
    # ... and the session is re-pinned there for the request after.
    assert router.reserve("a2", routing_session_id="A") == 1
    assert _loads(router) == [7, 2]


def test_sticky_rebalance_off_by_default_keeps_the_pin():
    router = IntraGeneratorRouter.Config(
        strategy=StickySessionRoutingStrategy.Config(
            fallback_strategy=RoundRobinRoutingStrategy.Config(),
        )
    ).build(dp_degree=2)
    assert router.reserve("a0", routing_session_id="A") == 0
    for i in range(50):
        router._handles[0].reserved_load += 1
    # ratio 0 -> a pin is for life, however skewed.
    assert router.reserve("a1", routing_session_id="A") == 0


def test_sticky_rebalance_respects_min_gap():
    """Tiny absolute skews (idle ties) must not bounce sessions."""
    router = IntraGeneratorRouter.Config(
        strategy=StickySessionRoutingStrategy.Config(
            fallback_strategy=RoundRobinRoutingStrategy.Config(),
            rebalance_load_ratio=2.0,
            rebalance_min_gap=8,
        )
    ).build(dp_degree=2)
    assert router.reserve("a0", routing_session_id="A") == 0
    for i in range(3):
        router._handles[0].reserved_load += 1
    # 4 > 2*0 + 8 is false -> stay.
    assert router.reserve("a1", routing_session_id="A") == 0


def test_sticky_rebalance_water_fills_across_three_ranks():
    """Moves go to the CURRENT least-loaded rank and stop once loads are within
    ratio, so a flood of re-pins spreads instead of stampeding one rank."""
    router = IntraGeneratorRouter.Config(
        strategy=StickySessionRoutingStrategy.Config(
            fallback_strategy=RoundRobinRoutingStrategy.Config(),
            rebalance_load_ratio=2.0,
            rebalance_min_gap=0,
        )
    ).build(dp_degree=3)
    # 30 sessions all pinned to rank 0 (simulate the pathology directly).
    sessions = [f"s{i}" for i in range(30)]
    for s in sessions:
        router._handles[0].reserved_load += 1
        router._strategy._sessions[s] = router._handles[0]
    assert _loads(router) == [30, 0, 0]
    # Each session's next request re-routes; loads water-fill across ranks.
    chosen = [router.reserve(f"{s}/t1", routing_session_id=s) for s in sessions]
    loads = _loads(router)
    assert loads[0] == 30  # pins moved; rank 0's OLD reservations are untouched
    assert loads[1] + loads[2] == 30
    assert abs(loads[1] - loads[2]) <= 1
    assert set(chosen) == {1, 2}
