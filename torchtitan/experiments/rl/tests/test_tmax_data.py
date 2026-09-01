# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import json

import pytest

from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset


def _write_row(tmp_path, *, disk_gb):
    metadata = {
        "instance_id": "task-1",
        "image": "example/image",
        "tmax": {"test_sh": "true"},
    }
    if disk_gb is not None:
        metadata["daytona_disk_gb"] = disk_gb
    path = tmp_path / "tmax.jsonl"
    path.write_text(json.dumps({"metadata": metadata}) + "\n")
    return path


def _write_rows(tmp_path, num_rows: int):
    path = tmp_path / "tmax.jsonl"
    rows = []
    for index in range(num_rows):
        rows.append(
            json.dumps(
                {
                    "metadata": {
                        "instance_id": f"task-{index}",
                        "image": "example/image",
                        "tmax": {"test_sh": "true"},
                    }
                }
            )
        )
    path.write_text("\n".join(rows) + "\n")
    return path


def test_dataset_reads_per_task_daytona_disk(tmp_path) -> None:
    path = _write_row(tmp_path, disk_gb=20)
    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))

    assert next(dataset).daytona_disk_gb == 20


def test_dataset_defaults_per_task_daytona_disk_to_none(tmp_path) -> None:
    path = _write_row(tmp_path, disk_gb=None)
    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))

    assert next(dataset).daytona_disk_gb is None


def test_dataset_stamps_exact_occurrence_and_epoch_lineage(tmp_path) -> None:
    path = _write_rows(tmp_path, 2)
    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))

    first = next(dataset)
    second = next(dataset)
    wrapped = next(dataset)

    assert first.lineage is not None
    assert second.lineage is not None
    assert wrapped.lineage is not None
    assert first.lineage.task_id == "task-0"
    assert first.lineage.dataset_epoch == 0
    assert first.lineage.dataset_position == 0
    assert second.lineage.dataset_position == 1
    assert wrapped.lineage.dataset_epoch == 1
    assert wrapped.lineage.dataset_position == 0
    assert len({first.lineage.occurrence_id, wrapped.lineage.occurrence_id}) == 2
    assert first.lineage.sample_revision == wrapped.lineage.sample_revision


def test_dataset_hot_reload_records_task_revision_transition(
    tmp_path, monkeypatch
) -> None:
    path = _write_row(tmp_path, disk_gb=4)
    monkeypatch.setenv("SWE_DATA_HOT_RELOAD", "1")
    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))
    before = next(dataset)

    row = json.loads(path.read_text())
    row["metadata"]["daytona_disk_gb"] = 8
    incoming = path.with_suffix(".incoming")
    incoming.write_text(json.dumps(row) + "\n")
    incoming.replace(path)
    # Some test filesystems preserve the old file's nanosecond mtime across a
    # rapid atomic replace. Force the existing mtime-based fallback to observe it.
    dataset._data_mtime = -1
    dataset._maybe_reload(min_interval_sec=0)
    events = dataset.drain_lineage_events()
    after = next(dataset)

    assert len(events) == 1
    event = events[0]
    assert event["event"] == "hot_reload"
    assert event["replaced"] == 1
    assert event["changes"] == [
        {
            "task_id": "task-1",
            "change": "replaced",
            "previous_sample_revision": before.lineage.sample_revision,
            "sample_revision": after.lineage.sample_revision,
        }
    ]
    assert before.lineage.sample_revision != after.lineage.sample_revision
    assert before.lineage.mix_revision != after.lineage.mix_revision


@pytest.mark.parametrize("disk_gb", [0, -1, True, 1.5, "20"])
def test_dataset_rejects_invalid_per_task_daytona_disk(tmp_path, disk_gb) -> None:
    path = _write_row(tmp_path, disk_gb=disk_gb)

    with pytest.raises(ValueError, match="invalid daytona_disk_gb"):
        TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))


def _write_row_resources(tmp_path, **resources):
    """One row carrying arbitrary daytona_* resource fields (omitting None)."""
    metadata = {
        "instance_id": "task-1",
        "image": "example/image",
        "tmax": {"test_sh": "true"},
    }
    for key, val in resources.items():
        if val is not None:
            metadata[key] = val
    path = tmp_path / "tmax.jsonl"
    path.write_text(json.dumps({"metadata": metadata}) + "\n")
    return path


def test_dataset_reads_per_task_daytona_cpu_and_mem(tmp_path) -> None:
    path = _write_row_resources(tmp_path, daytona_cpu=1, daytona_mem_gb=2)
    sample = next(TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False)))

    assert sample.daytona_cpu == 1
    assert sample.daytona_mem_gb == 2


def test_dataset_defaults_per_task_daytona_cpu_and_mem_to_none(tmp_path) -> None:
    path = _write_row_resources(tmp_path)
    sample = next(TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False)))

    assert sample.daytona_cpu is None
    assert sample.daytona_mem_gb is None


@pytest.mark.parametrize("field", ["daytona_cpu", "daytona_mem_gb"])
@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "2"])
def test_dataset_rejects_invalid_per_task_daytona_cpu_mem(tmp_path, field, bad) -> None:
    path = _write_row_resources(tmp_path, **{field: bad})

    with pytest.raises(ValueError, match=f"invalid {field}"):
        TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))


def test_dataset_skips_initial_samples_and_preserves_wraps(tmp_path) -> None:
    path = _write_rows(tmp_path, 3)
    baseline = TMaxDataset(
        TMaxDataset.Config(data_path=str(path), seed=7, shuffle=True)
    )
    for _ in range(4):
        next(baseline)

    resumed = TMaxDataset(
        TMaxDataset.Config(
            data_path=str(path),
            seed=7,
            shuffle=True,
            initial_skip_samples=4,
        )
    )

    assert [next(resumed) for _ in range(6)] == [next(baseline) for _ in range(6)]


def test_dataset_rejects_negative_initial_skip_samples(tmp_path) -> None:
    path = _write_rows(tmp_path, 3)

    with pytest.raises(ValueError, match="initial_skip_samples must be non-negative"):
        TMaxDataset(
            TMaxDataset.Config(
                data_path=str(path),
                shuffle=False,
                initial_skip_samples=-1,
            )
        )


def test_dataset_include_ids_preserves_canonical_shuffled_order(tmp_path) -> None:
    path = _write_rows(tmp_path, 6)
    include_path = tmp_path / "include.txt"
    include_path.write_text("task-1\ntask-4\n")

    baseline = TMaxDataset(
        TMaxDataset.Config(data_path=str(path), seed=7, shuffle=True)
    )
    expected = [
        sample
        for sample in [next(baseline) for _ in range(6)]
        if sample.instance_id in {"task-1", "task-4"}
    ]
    filtered = TMaxDataset(
        TMaxDataset.Config(
            data_path=str(path),
            seed=7,
            shuffle=True,
            include_ids_path=str(include_path),
        )
    )

    assert [next(filtered) for _ in range(2)] == expected


def test_dataset_applies_skip_after_include(tmp_path) -> None:
    path = _write_rows(tmp_path, 4)
    include_path = tmp_path / "include.txt"
    include_path.write_text("task-1\ntask-2\n")
    skip_path = tmp_path / "skip.txt"
    skip_path.write_text("task-2\n")
    dataset = TMaxDataset(
        TMaxDataset.Config(
            data_path=str(path),
            shuffle=False,
            include_ids_path=str(include_path),
            skip_ids_path=str(skip_path),
        )
    )

    assert [next(dataset).instance_id for _ in range(3)] == ["task-1"] * 3


@pytest.mark.parametrize("contents", [None, "", "unknown-task\n"])
def test_dataset_rejects_invalid_include_ids(tmp_path, contents: str | None) -> None:
    path = _write_rows(tmp_path, 4)
    include_path = tmp_path / "include.txt"
    if contents is not None:
        include_path.write_text(contents)

    with pytest.raises(
        ValueError, match="instance ID|contains no instance IDs|outside"
    ):
        TMaxDataset(
            TMaxDataset.Config(
                data_path=str(path),
                shuffle=False,
                include_ids_path=str(include_path),
            )
        )


def test_dataset_applies_holdout_before_include_ids(tmp_path) -> None:
    path = _write_rows(tmp_path, 6)
    include_path = tmp_path / "include.txt"
    include_path.write_text("task-1\ntask-5\n")

    with pytest.raises(ValueError, match="outside the train split"):
        TMaxDataset(
            TMaxDataset.Config(
                data_path=str(path),
                shuffle=False,
                holdout_n=2,
                split="train",
                include_ids_path=str(include_path),
            )
        )
