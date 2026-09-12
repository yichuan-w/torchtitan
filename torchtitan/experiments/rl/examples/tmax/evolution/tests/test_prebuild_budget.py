import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prebuild_batch import Ledger, reservation


def test_concurrent_reservations_never_exceed_budget(tmp_path):
    ledger = Ledger(tmp_path / "budget.json", 1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(
            pool.map(lambda key: ledger.reserve(str(key), 0.25), range(100))
        )
    assert sum(admitted) == 4
    restored = Ledger(ledger.path, 1)
    assert not restored.reserve("another", 0.25)
    key = next(iter(restored.entries))
    restored.finish(key, "verified", 0.05)
    assert restored.reserve("another", 0.2)
    assert not restored.reserve(key, 0.01)


def test_reservation_covers_both_sandbox_lifetimes():
    row = {"metadata": {"daytona_cpu": 4, "daytona_mem_gb": 8, "daytona_disk_gb": 10}}
    amount, rate = reservation(row)
    assert amount >= (2 * 0.0504 + 4 * 0.0162 + 10 * 0.000108) * 2
    assert amount >= rate * 0.25
