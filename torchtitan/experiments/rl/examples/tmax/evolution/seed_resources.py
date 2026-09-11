# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Resource conversions shared by seed exporters and mix construction."""

import math

MEM_CAP_GB = 8
DISK_CAP_GB = 10


def policy_gib(mb: object, cap: int) -> int | None:
    """Convert an existing allocation to GiB with the seed mix floor and cap."""
    if not isinstance(mb, (int, float)) or not math.isfinite(mb) or mb <= 0:
        return None
    return min(max(math.ceil(mb / 1024), 1), cap)


def measured_gib(mb: object, cap: int) -> int | None:
    """Provision a raw peak with 30 percent headroom, applied exactly once."""
    if not isinstance(mb, (int, float)) or not math.isfinite(mb) or mb <= 0:
        return None
    return policy_gib(mb * 1.3, cap)
