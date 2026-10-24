"""Exercise real offline W&B records without making network requests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from wandb.proto import wandb_internal_pb2 as pb
from wandb.sdk.internal.datastore import DataStore

from mast_rl import wandb_history_mirror as mirror
from mast_rl.wandb_history_mirror import (
    CURSOR_KEY,
    _history_rows,
    _restart_rows,
    _resume_cursor,
    _stitch_history,
    _write_cursor,
)


def _offline(tmp_path: Path, segment: str, steps: list[int]) -> Path:
    directory = tmp_path / segment
    directory.mkdir(exist_ok=True)
    path = directory / f"run-{segment.rsplit('-', 1)[-1]}.wandb"
    datastore = DataStore()
    datastore.open_for_write(str(path))
    for step in steps:
        record = pb.Record()
        for key, value in {"_step": step, "rollout/reward": step / 10}.items():
            item = record.history.item.add()
            item.key, item.value_json = key, json.dumps(value)
        datastore.write(record)
    datastore.close()
    return path


def _status(rows):
    return {
        "historyLineCount": len(rows),
        "historyTail": [json.dumps(row) for row in rows[-2:]],
        "summaryMetrics": json.dumps({CURSOR_KEY: rows[-1][CURSOR_KEY]})
        if rows
        else "{}",
    }


def test_empty_online_run_ignores_synthetic_history_tail(tmp_path):
    status = {
        "historyLineCount": 0,
        "historyTail": json.dumps([json.dumps({"_step": 0, "_runtime": 0})]),
        "summaryMetrics": json.dumps({"_runtime": 0}),
    }
    cursor, last_step, awaiting_ack = _resume_cursor(
        status, tmp_path / "cursor.json", "mirror"
    )
    path = _offline(tmp_path, "offline-run-20260101_000000-a", [0, 1])
    missing, _ = _restart_rows([path], cursor, last_step)
    assert last_step == -1 and not awaiting_ack
    assert [row["_step"] for row in missing] == [0, 1]


def test_existing_history_without_recovery_cursor_is_rejected(tmp_path):
    status = {
        "historyLineCount": 1,
        "historyTail": [json.dumps({"_step": 0, "rollout/reward": 0.5})],
        "summaryMetrics": "{}",
    }
    with pytest.raises(ValueError, match="requires an empty run"):
        _resume_cursor(status, tmp_path / "cursor.json", "mirror")


def test_repeated_rollbacks_keep_all_rows_and_resume_without_duplicates(tmp_path):
    paths = [
        _offline(tmp_path, "offline-run-20260101_000000-a", [0, 1, 2]),
        _offline(tmp_path, "offline-run-20260101_010000-b", [1, 2, 3]),
        _offline(tmp_path, "offline-run-20260101_020000-c", [2, 3, 4]),
    ]
    cursor_path = tmp_path / "cursor.json"
    uploaded = []
    for num_attempts in range(1, 4):
        cursor, last_step, awaiting_ack = _resume_cursor(
            _status(uploaded), cursor_path, "mirror"
        )
        assert not awaiting_ack
        missing, cursor = _restart_rows(paths[:num_attempts], cursor, last_step)
        assert len(missing) == 3
        uploaded.extend(missing)
        _write_cursor(cursor_path, cursor)
        assert cursor_path.stat().st_mode & 0o777 == 0o600
    assert [row["_step"] for row in uploaded] == list(range(9))
    assert [row["training/step"] for row in uploaded] == [0, 1, 2, 1, 2, 3, 2, 3, 4]
    cursor, last_step, awaiting_ack = _resume_cursor(
        _status(uploaded), cursor_path, "mirror"
    )
    missing, _ = _restart_rows(paths, cursor, last_step)
    assert not awaiting_ack and not missing
    assert [row["_step"] for row in _stitch_history(paths)] == list(range(5))


def test_remote_cursor_recovers_crash_before_local_cursor_write(tmp_path):
    path = _offline(tmp_path, "offline-run-20260101_000000-a", [0, 1])
    cursor_path = tmp_path / "missing-cursor.json"
    cursor, last_step, _ = _resume_cursor(_status([]), cursor_path, "mirror")
    uploaded, _ = _restart_rows([path], cursor, last_step)
    assert not cursor_path.exists()
    cursor, last_step, awaiting_ack = _resume_cursor(
        _status(uploaded), cursor_path, "mirror"
    )
    assert not awaiting_ack
    assert _restart_rows([path], cursor, last_step)[0] == []


def test_local_cursor_waits_for_remote_acknowledgement(tmp_path):
    path = _offline(tmp_path, "offline-run-20260101_000000-a", [0])
    cursor_path = tmp_path / "cursor.json"
    cursor, last_step, _ = _resume_cursor(_status([]), cursor_path, "mirror")
    uploaded, cursor = _restart_rows([path], cursor, last_step)
    _write_cursor(cursor_path, cursor)
    _, _, awaiting_ack = _resume_cursor(_status([]), cursor_path, "mirror")
    assert awaiting_ack
    _, _, awaiting_ack = _resume_cursor(_status(uploaded), cursor_path, "mirror")
    assert not awaiting_ack


def test_delayed_old_segment_history_can_be_backfilled(tmp_path):
    old = _offline(tmp_path, "offline-run-20260101_000000-a", [0, 1, 2])
    original = old.read_bytes()
    old.write_bytes(original[:-5])
    assert [row["_step"] for row in _history_rows(old)] == [0, 1]
    new = _offline(tmp_path, "offline-run-20260101_010000-b", [1, 2, 3])
    cursor_path = tmp_path / "cursor.json"
    cursor, last_step, _ = _resume_cursor(_status([]), cursor_path, "mirror")
    uploaded, _ = _restart_rows([old, new], cursor, last_step)
    old.write_bytes(original)
    cursor, last_step, _ = _resume_cursor(_status(uploaded), cursor_path, "mirror")
    missing, _ = _restart_rows([old, new], cursor, last_step)
    assert len(missing) == 1
    assert missing[0]["_step"] == 5 and missing[0]["training/step"] == 2


def test_empty_preempted_segment_does_not_hide_later_records(tmp_path):
    empty = tmp_path / "offline-run-empty" / "run-empty.wandb"
    empty.parent.mkdir()
    empty.touch()
    path = _offline(tmp_path, "offline-run-20260101_010000-b", [0, 1])
    cursor, last_step, _ = _resume_cursor(
        _status([]), tmp_path / "cursor.json", "mirror"
    )
    missing, _ = _restart_rows([empty, path], cursor, last_step)
    assert len(missing) == 2


def test_wrong_run_cursor_is_rejected(tmp_path):
    path = tmp_path / "cursor.json"
    _write_cursor(
        path,
        {
            "schema_version": 1,
            "run_id": "other",
            "source_counts": {},
            "remote_step": -1,
        },
    )
    with pytest.raises(ValueError, match="another W&B run"):
        _resume_cursor(_status([]), path, "mirror")


def test_online_mirror_resumes_identity_and_restores_metric_axes(monkeypatch, tmp_path):
    paths = [
        _offline(tmp_path, "offline-run-20260101_000000-a", [0, 1, 2]),
        _offline(tmp_path, "offline-run-20260101_010000-b", [1, 2, 3]),
    ]
    uploaded, definitions, initializations = [], [], []

    def init(**kwargs):
        initializations.append(kwargs)
        return SimpleNamespace(
            define_metric=lambda *a, **kw: definitions.append((a, kw)),
            log=lambda payload, step: uploaded.append({**payload, "_step": step}),
            finish=lambda: None,
        )

    monkeypatch.setattr(mirror.wandb, "init", init)
    monkeypatch.setattr(
        mirror,
        "Api",
        lambda: SimpleNamespace(run_resume_status=lambda *_a: _status(uploaded)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mirror",
            "--entity",
            "owner",
            "--project",
            "project",
            "--run-id",
            "mirror",
            "--restart-aware",
            "--cursor-file",
            str(tmp_path / "cursor.json"),
            *map(str, paths),
        ],
    )
    mirror.main()
    mirror.main()
    assert len(initializations) == 1
    assert (
        initializations[0]["id"] == "mirror" and initializations[0]["resume"] == "must"
    )
    assert len(uploaded) == 6
    assert (("*",), {"step_metric": "training/step", "step_sync": False}) in definitions
    assert (
        ("validation/*",),
        {"step_metric": "validation/policy_version", "step_sync": False},
    ) in definitions
