"""What may sit under tests/ beside the verifier. Grading fixtures ride inside
the mix row as text and are uploaded at grade time, so they are bounded the way
the Dockerfile's COPY sources already are, and a binary one is refused with its
name rather than dropped on the floor and found later as an oracle failure."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pack_to_dataset as pack


@pytest.fixture(autouse=True)
def _checkout(monkeypatch):
    monkeypatch.setenv("TRL_TT", str(Path(__file__).resolve().parents[7]))


def _package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "environment").mkdir(parents=True)
    (pkg / "tests").mkdir()
    (pkg / "solution").mkdir()
    (pkg / "environment/Dockerfile").write_text("FROM scratch\n")
    (pkg / "instruction.md").write_text("Do the thing.\n")
    (pkg / "tests/test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/sh\n")
    return pkg


def test_a_small_text_fixture_rides_on_the_row(tmp_path) -> None:
    pkg = _package(tmp_path)
    (pkg / "tests/expected.txt").write_text("1880,-0.55\n")
    row = pack.to_row(str(pkg), task_id="t")
    assert row["metadata"]["tmax"]["fixtures"] == {"tests/expected.txt": "1880,-0.55\n"}


def test_fixtures_over_the_context_ceiling_are_refused(tmp_path) -> None:
    pkg = _package(tmp_path)
    (pkg / "tests/big.csv").write_text("x" * (pack.fixture_ceiling() + 1))
    with pytest.raises(ValueError, match="tests_fixtures_too_large"):
        pack.to_row(str(pkg), task_id="t")


def test_fixtures_are_bounded_together_not_one_by_one(tmp_path) -> None:
    pkg = _package(tmp_path)
    half = pack.fixture_ceiling() // 2 + 1
    (pkg / "tests/a.csv").write_text("a" * half)
    (pkg / "tests/b.csv").write_text("b" * half)
    with pytest.raises(ValueError, match="tests_fixtures_too_large"):
        pack.to_row(str(pkg), task_id="t")


def test_a_binary_fixture_is_refused_by_name(tmp_path) -> None:
    pkg = _package(tmp_path)
    (pkg / "tests/ref.bin").write_bytes(b"\x00\xff\xfe binary \x80")
    with pytest.raises(ValueError, match="tests_fixture_binary.*tests/ref.bin"):
        pack.to_row(str(pkg), task_id="t")
