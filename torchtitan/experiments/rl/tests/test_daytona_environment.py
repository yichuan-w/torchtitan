# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Daytona context uploads must not rewrite a live rollout worker's environment."""

from __future__ import annotations

import os

from daytona._async import object_storage as async_store
from daytona._sync import object_storage as sync_store
from daytona._utils import environment as env_mod

from torchtitan.experiments.rl.harness.sandbox import daytona as daytona_mod


def test_context_upload_does_not_clear_process_environment(monkeypatch):
    originals = (
        env_mod.isolated_env,
        sync_store.isolated_env,
        async_store.isolated_env,
    )
    monkeypatch.setattr(daytona_mod, "_proxy_patch_done", False)
    monkeypatch.setenv("TT_ENV_RACE_SENTINEL", "still-here")

    try:
        daytona_mod._keep_proxy_for_context_upload()
        before = dict(os.environ)
        with sync_store.isolated_env({"AWS_SHOULD_NOT_REPLACE_PROCESS_ENV": "1"}):
            assert os.environ["TT_ENV_RACE_SENTINEL"] == "still-here"
            assert dict(os.environ) == before
        assert dict(os.environ) == before
        assert env_mod.isolated_env is sync_store.isolated_env
        assert env_mod.isolated_env is async_store.isolated_env
    finally:
        env_mod.isolated_env, sync_store.isolated_env, async_store.isolated_env = originals
