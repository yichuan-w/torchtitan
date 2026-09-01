# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import json
from dataclasses import dataclass

from torchtitan.experiments.rl.training_lineage import TrainingLineageRecorder


@dataclass
class _Sample:
    instance_id: str
    prompt: str


def _jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_recorder_appends_events_and_deduplicates_complete_inputs(tmp_path) -> None:
    recorder = TrainingLineageRecorder(dump_dir=str(tmp_path))
    sample = _Sample(instance_id="task-a", prompt="full prompt")
    lineage = recorder.describe_sample(sample=sample, group_id=4)

    recorder.record_sample(lineage=lineage, sample=sample)
    recorder.record_sample(lineage=lineage, sample=sample)
    recorder.record_event("admitted", lineage=lineage)
    recorder.record_event("trained", lineage=lineage, train_step=9)

    samples = _jsonl(recorder.samples_path)
    events = _jsonl(recorder.events_path)
    assert len(samples) == 1
    assert samples[0]["input"] == {
        "instance_id": "task-a",
        "prompt": "full prompt",
    }
    assert [event["event"] for event in events] == [
        "session_started",
        "admitted",
        "trained",
    ]
    assert events[-1]["train_step"] == 9
    assert events[-1]["occurrence_id"] == lineage["occurrence_id"]
