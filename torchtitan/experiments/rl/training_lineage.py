# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Append-only audit records for the asynchronous RL training-data path."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from torchtitan.experiments.rl.types import SampleLineage


_SCHEMA_VERSION = 1


def canonical_json(value: object) -> str:
    """Return the stable JSON representation used for content revisions."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_revision(value: object) -> str:
    """SHA-256 of a complete JSON-serializable input."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sample_payload(sample: object) -> object:
    """Serialize the complete environment input while excluding occurrence metadata."""
    if dataclasses.is_dataclass(sample):
        payload: object = dataclasses.asdict(sample)
    elif isinstance(sample, dict):
        payload = dict(sample)
    else:
        payload = {"type": type(sample).__qualname__, "repr": repr(sample)}
    if isinstance(payload, dict):
        payload.pop("lineage", None)
    return payload


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class TrainingLineageRecorder:
    """Durable JSONL source of truth for sample identity and lifecycle events.

    Each call opens and flushes one append. A controller crash can lose at most a
    partially written final line; prior events and the deduplicated sample catalog
    remain intact and are reused when the same output directory is resumed.
    """

    def __init__(self, *, dump_dir: str) -> None:
        self.root = Path(dump_dir) / "training_lineage"
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.samples_path = self.root / "samples.jsonl"
        self.session_id = uuid.uuid4().hex
        self._sequence = 0
        self._known_sample_revisions = self._read_known_sample_revisions()
        self.record_event("session_started", pid=os.getpid())

    def _read_known_sample_revisions(self) -> set[str]:
        revisions: set[str] = set()
        if not self.samples_path.exists():
            return revisions
        with self.samples_path.open() as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                revision = record.get("sample_revision")
                if isinstance(revision, str):
                    revisions.add(revision)
        return revisions

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        with path.open("a") as f:
            f.write(canonical_json(record) + "\n")
            f.flush()

    def describe_sample(self, *, sample: object, group_id: int) -> dict[str, Any]:
        """Return a stable occurrence identity, with a generic fallback."""
        declared = getattr(sample, "lineage", None)
        if isinstance(declared, SampleLineage):
            lineage = dataclasses.asdict(declared)
        elif dataclasses.is_dataclass(declared):
            lineage = dataclasses.asdict(declared)
        elif isinstance(declared, dict):
            lineage = dict(declared)
        else:
            payload = sample_payload(sample)
            task_id = (
                getattr(sample, "instance_id", None)
                or getattr(sample, "id", None)
                or (sample.get("instance_id") if isinstance(sample, dict) else None)
                or f"group-{group_id}"
            )
            lineage = {
                "occurrence_id": f"{self.session_id}:{group_id}",
                "task_id": str(task_id),
                "sample_revision": content_revision(payload),
                "mix_revision": None,
                "dataset_epoch": None,
                "dataset_position": None,
                "stream_position": group_id,
                "stream_id": self.session_id,
            }
        lineage["group_id"] = group_id
        return lineage

    def record_sample(self, *, lineage: dict[str, Any], sample: object) -> None:
        """Store a complete input once per content revision."""
        revision = lineage.get("sample_revision")
        if not isinstance(revision, str) or revision in self._known_sample_revisions:
            return
        self._append(
            self.samples_path,
            {
                "schema_version": _SCHEMA_VERSION,
                "record_type": "sample",
                "sample_revision": revision,
                "task_id": lineage.get("task_id"),
                "recorded_at": _utc_now(),
                "input": sample_payload(sample),
            },
        )
        self._known_sample_revisions.add(revision)

    def record_event(
        self,
        event: str,
        *,
        lineage: dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        """Append one timestamped lifecycle or dataset event."""
        self._sequence += 1
        record = {
            "schema_version": _SCHEMA_VERSION,
            "record_type": "event",
            "event": event,
            "timestamp": _utc_now(),
            "time_unix_ns": time.time_ns(),
            "session_id": self.session_id,
            "sequence": self._sequence,
        }
        if lineage:
            record.update(lineage)
        record.update(fields)
        self._append(self.events_path, record)

    def record_dataset_event(self, event: dict[str, Any]) -> None:
        """Append an event emitted by a hot-reloadable dataset."""
        payload = dict(event)
        event_name = str(payload.pop("event"))
        self.record_event(event_name, **payload)
