"""size_from_oracle is the oracle term of derive_sizing.main()'s rule: the loop
sizes a rewritten task with it, and a task sized in the loop has to come out
where the seed campaign would have put it."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import derive_sizing as ds


def test_size_from_oracle_applies_headroom_and_the_cpu_seconds_bound() -> None:
    # 1500 MB * 1.3 = 1.9 GiB -> 2; 3000 MB * 1.3 = 3.8 GiB -> 4; 1800 s / 900 s -> 2 cores
    assert ds.size_from_oracle(1500, 3000, 1800) == {"cpu": 2, "mem_gb": 2, "disk_gb": 4}


def test_size_from_oracle_floors_match_the_campaign() -> None:
    # One source: memory never below UNVERIFIED_MEM_FLOOR_GB; disk never below
    # DISK_FLOOR_GB; a solve that barely used a core still gets one.
    assert ds.size_from_oracle(100, 100, 10) == {
        "cpu": 1, "mem_gb": ds.UNVERIFIED_MEM_FLOOR_GB, "disk_gb": ds.DISK_FLOOR_GB}
    assert ds.size_from_oracle(None, None, None) == {
        "cpu": 1, "mem_gb": ds.UNVERIFIED_MEM_FLOOR_GB, "disk_gb": ds.DISK_FLOOR_GB}


def test_size_from_oracle_caps_at_the_platform_ceiling() -> None:
    assert ds.size_from_oracle(20000, 20000, 9000) == ds.CEILING
