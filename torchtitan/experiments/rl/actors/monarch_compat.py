# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""``endpoint`` for the RL actors that stays concurrent on every Monarch release.

The actors rely on concurrent async endpoints: a generator serves many
``generate`` calls at once and takes ``pull_model_state_dict`` while they are
in flight. Monarch < 0.7 dispatches async endpoints concurrently when
``MONARCH_ACTOR_QUEUE_DISPATCH=0`` (set in ``train.py``). Monarch 0.7 removed
that flag and queues every plain ``@endpoint`` (D116091231), which on a fresh
build serializes generation to one request per engine. There, the same
behavior comes from ``@concurrent_endpoint``, which takes the same options.

The choice is by version, not by whether ``concurrent_endpoint`` imports:
0.6.0 (the pinned release) already ships it, and runs there keep the plain
``@endpoint`` they were validated with.
"""

import importlib.metadata
import re


def _monarch_version() -> tuple[int, int] | None:
    try:
        version = importlib.metadata.version("torchmonarch")
    except importlib.metadata.PackageNotFoundError:
        return None
    match = re.match(r"(\d+)\.(\d+)", version)
    return (int(match[1]), int(match[2])) if match else None


_version = _monarch_version()
if _version is None or _version >= (0, 7):
    try:
        from monarch.actor import concurrent_endpoint as endpoint
    except ImportError:
        from monarch.actor import endpoint
else:
    from monarch.actor import endpoint

__all__ = ["endpoint"]
