# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Per-sandbox labels reach the cloud create call beside the owner label.

A BUILD_FAILED sandbox never returns to the harness, and the SDK's list() view
of it carries no Dockerfile, so the labels are the only cheap way to say which
task, group and rollout produced a corpse. ``owner`` is the sweep's tenant key
and must survive whatever the caller passes.
"""

from __future__ import annotations

import pytest

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod
from torchtitan.experiments.rl.harness.sandbox.daytona import DaytonaSandbox
from torchtitan.experiments.rl.tests.test_daytona_ttl import _run_aenter


def _labels_sent(monkeypatch, labels) -> dict:
    captured = _run_aenter_with(monkeypatch, labels)
    assert captured.params is not None, "create was never reached"
    return dict(captured.params.labels)


def _run_aenter_with(monkeypatch, labels):
    """Same drive as the TTL test, with labels handed to the sandbox."""
    original = DaytonaSandbox.__init__

    def _init(self, image, **kwargs):
        original(self, image, labels=labels, **kwargs)

    monkeypatch.setattr(DaytonaSandbox, "__init__", _init)
    return _run_aenter(monkeypatch)


def test_task_labels_ride_beside_owner(monkeypatch):
    sent = _labels_sent(
        monkeypatch, {"task": "tw_1", "group": "3", "rollout": "7", "run": "r"}
    )
    assert sent["owner"] == daytona_mod.HARNESS_LABELS["owner"]
    assert sent["task"] == "tw_1"
    assert sent["group"] == "3"
    assert sent["rollout"] == "7"
    assert sent["run"] == "r"


def test_owner_is_not_overridable(monkeypatch):
    sent = _labels_sent(monkeypatch, {"owner": "someone_else", "task": "tw_1"})
    assert sent["owner"] == daytona_mod.HARNESS_LABELS["owner"]


def test_no_labels_keeps_owner_only(monkeypatch):
    sent = _labels_sent(monkeypatch, None)
    assert sent == dict(daytona_mod.HARNESS_LABELS)


def test_non_string_label_rejected():
    with pytest.raises(ValueError):
        DaytonaSandbox(image="alpine:3.19", labels={"group": 3})
