from __future__ import annotations

import json
from pathlib import Path

import pytest

from mast_rl.prepare_tmax_evolution import (
    SeedProfile,
    _check_root,
    _create_canary_signals,
    _create_root,
    _seed_rows,
    _source_paths,
)


def _write_task(release: Path, corpus: str, task_id: str) -> None:
    task = release / "sources" / corpus / "tasks" / task_id
    (task / "environment").mkdir(parents=True)
    (task / "solution").mkdir()
    (task / "tests").mkdir()
    (task / "instruction.md").write_text(f"Fix {task_id}.\n")
    (task / "environment/Dockerfile").write_text("FROM scratch\n")
    (task / "solution/solve.sh").write_text("#!/bin/sh\ntrue\n")
    (task / "tests/test.sh").write_text("#!/bin/sh\ntrue\n")


def _mixed_release(tmp_path: Path) -> tuple[Path, SeedProfile]:
    release = tmp_path / "release"
    rows = []
    for corpus, task_id in (
        ("swe-rebench", "rebench-task"),
        ("tmax", "tmax-task"),
    ):
        _write_task(release, corpus, task_id)
        rows.append(
            {
                "label": task_id,
                "metadata": {"corpus": corpus, "instance_id": task_id},
                "prompt": f"Fix {task_id}.",
            }
        )
    (release / "mix.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    profile = SeedProfile(
        name="mixed-test",
        description="synthetic mixed seed",
        default_release=release,
        release_repo=None,
        release_revision=None,
        release_sha256="unused",
        corpus_counts=(("swe-rebench", 1), ("tmax", 1)),
        solution_formats=(
            ("swe-rebench", "solution/solve.sh"),
            ("tmax", "solution/solve.sh"),
        ),
        row_filter="both corpora",
        default_purpose="test",
    )
    return release, profile


def _tools(tmp_path: Path) -> Path:
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("codex", "jq"):
        path = tools / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    return tools


def test_mixed_seed_retains_both_corpora(tmp_path: Path) -> None:
    release, profile = _mixed_release(tmp_path)

    rows = _seed_rows(release, profile)
    sources = _source_paths(release, profile)

    assert [row["metadata"]["corpus"] for row in rows] == [
        "swe-rebench",
        "tmax",
    ]
    assert set(sources) == {"swe-rebench", "tmax"}


def test_mixed_root_links_and_validates_both_sources(tmp_path: Path) -> None:
    release, profile = _mixed_release(tmp_path)
    rows = _seed_rows(release, profile)
    sources = _source_paths(release, profile)
    tools = _tools(tmp_path)
    root_path = tmp_path / "root"

    _create_root(
        root_path,
        mix=release / "mix.jsonl",
        sources=sources,
        name="mixed-test",
        purpose="test",
        profile=profile,
        release_manifest={"release_sha256": "unused", "config": {}},
        bin_dir=tools,
    )

    result = _check_root(root_path, rows, sources, tools, profile)
    signals = _create_canary_signals(root_path, rows)

    assert result["corpus_counts"] == {"swe-rebench": 1, "tmax": 1}
    assert set(result["source_links"]) == {"swe-rebench", "tmax"}
    assert set(signals) == {"swe-rebench", "tmax"}
    assert all(signal.startswith("prelaunch-canary/") for signal in signals.values())


def test_duplicate_task_id_across_corpora_is_rejected(tmp_path: Path) -> None:
    release, profile = _mixed_release(tmp_path)
    rows = [
        json.loads(line) for line in (release / "mix.jsonl").read_text().splitlines()
    ]
    rows[1]["metadata"]["instance_id"] = rows[0]["metadata"]["instance_id"]
    (release / "mix.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match="empty or duplicated"):
        _seed_rows(release, profile)
