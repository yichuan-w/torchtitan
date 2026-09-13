# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Frozen pipeline settings and the credential boundary survive resume."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_pipeline as pipeline


def test_resume_refuses_changed_inputs(tmp_path):
    path = tmp_path / "inputs.json"
    original = {"sources": {"seed": 1}, "workers": 1000}
    pipeline.frozen(path, original)
    pipeline.frozen(path, original)
    with pytest.raises(ValueError, match="inputs changed"):
        pipeline.frozen(path, {**original, "sources": {"seed": 2}})
    assert json.loads(path.read_text()) == original


def test_remote_configuration_never_contains_master_credentials():
    config = {
        "sources": {"seed": 1},
        "workers": 1000,
        "image_repository": "ghcr.io/example/tasks",
        "github_token_file": "/local/master",
        "hf_token_file": "/local/hf",
        "daytona_env_file": "/local/daytona",
        "ssh_host": "host",
    }
    assert pipeline.remote_config(config) == {
        "sources": {"seed": 1},
        "workers": 1000,
        "image_repository": "ghcr.io/example/tasks",
    }


def test_completed_publication_resume_does_not_start_worker(tmp_path, monkeypatch):
    config = {"sources": {"seed": 1}, "workers": 1000}
    receipt = {"repo": "example/data", "revision": "published"}
    pipeline.release.write_json(tmp_path / "publication.json", receipt)

    def unexpected(*args):
        raise AssertionError("completed pipeline contacted remote worker")

    monkeypatch.setattr(pipeline, "remote", unexpected)
    pipeline.controller(config, tmp_path)
    assert json.loads((tmp_path / "publication.json").read_text()) == receipt
