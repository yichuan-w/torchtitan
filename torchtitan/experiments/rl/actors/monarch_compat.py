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
"""

try:
    from monarch.actor import concurrent_endpoint as endpoint
except ImportError:  # Monarch < 0.7
    from monarch.actor import endpoint

__all__ = ["endpoint"]
