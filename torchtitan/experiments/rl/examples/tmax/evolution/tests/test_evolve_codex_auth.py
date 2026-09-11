# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Explicit ChatGPT authentication stays private and independent of API keys."""

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec
from torchtitan.experiments.rl.examples.tmax import layout


def test_explicit_chatgpt_auth_is_private_and_cleaned(tmp_path, monkeypatch):
    root = layout.Root(tmp_path / "root")
    monkeypatch.setenv("TRL_BASE", str(root.path))
    rw = root.evolution.task("task-a").rewrite("harder")
    source = tmp_path / "login.json"
    auth = {"auth_mode": "chatgpt", "tokens": {"access_token": "test-token"}}
    source.write_text(json.dumps(auth))
    monkeypatch.setenv("EVOLVE_CODEX_AUTH_FILE", str(source))
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    with ec.session(rw, "agent", timeout=10) as run:
        env = ec._codex_env(run.dir)
        target = run.dir.codex_home / "auth.json"
        assert "OPENAI_API_KEY" not in env
        assert json.loads(target.read_text()) == auth
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert ec._provider_overrides() == ["model_provider=openai"]
        assert run.meta["authentication"] == "chatgpt"
    assert not target.exists()
    assert json.loads(source.read_text()) == auth


def test_api_key_provider_remains_default(monkeypatch):
    monkeypatch.delenv("EVOLVE_CODEX_AUTH_FILE", raising=False)
    assert "model_provider=oai" in ec._provider_overrides()
    assert "model_providers.oai.env_key=OPENAI_API_KEY" in ec._provider_overrides()
