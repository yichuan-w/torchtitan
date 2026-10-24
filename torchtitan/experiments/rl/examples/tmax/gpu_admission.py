# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Trial-level admission for rows that hold Daytona GPU sandboxes.

The GPU region has a fixed quota (10 H100 on ``earth``), and one trial of a GPU
row can hold two of them at once: its agent sandbox and, while grading, its
verifier. Limiting sandboxes would let N agents take the whole quota and then
wait forever for their verifiers; limiting *trials* cannot deadlock, because a
trial takes its slot before it boots anything and keeps it until it has finished
grading, so whatever runs inside the slot always has room for its verifier.

A slot is an exclusive ``flock`` on one of ``limit`` lock files. Every rollout
worker process runs on the controller host, so one directory there counts the
training and validation pools together, and the kernel drops a lock the moment
its holder dies, so a crashed worker cannot leak a slot. Rows without a GPU
never touch it.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import tempfile
from pathlib import Path


def gpu_trial(sample) -> bool:
    """Whether a trial of ``sample`` boots a GPU sandbox (agent or verifier).

    Reads what ``harbor_env`` puts on a TB4 row: ``boot_kwargs.sandbox_options.gpu``
    for the agent sandbox and ``tmax.separate_verifier.daytona_gpu`` for a separate
    verifier. Every other row has neither.
    """
    options = (getattr(sample, "boot_kwargs", None) or {}).get("sandbox_options")
    if (options or {}).get("gpu"):
        return True
    separate = (getattr(sample, "tmax", None) or {}).get("separate_verifier")
    return bool((separate or {}).get("daytona_gpu"))


def default_slot_dir() -> str:
    """Host-local, per-user directory for the slot files."""
    return os.environ.get("TT_GPU_TRIAL_SLOT_DIR") or str(
        Path(tempfile.gettempdir()) / f"tt_gpu_trial_slots_{os.getuid()}"
    )


class GpuTrialSlot:
    """One held slot; ``release`` is idempotent."""

    def __init__(self, fd: int, index: int) -> None:
        self._fd: int | None = fd
        self.index = index

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class GpuTrialAdmission:
    """At most ``limit`` GPU trials at once across every process on this host
    that uses the same ``slot_dir``."""

    def __init__(
        self, limit: int, *, slot_dir: str | None = None, poll_sec: float = 2.0
    ) -> None:
        if limit < 1:
            raise ValueError(f"GPU trial limit must be >= 1, got {limit}")
        self.limit = limit
        self._slot_dir = Path(slot_dir or default_slot_dir())
        self._poll_sec = poll_sec

    def try_acquire(self) -> GpuTrialSlot | None:
        """Take a free slot without waiting, or return None if all are held."""
        self._slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for index in range(self.limit):
            fd = os.open(self._slot_dir / f"gpu-{index}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            return GpuTrialSlot(fd, index)
        return None

    async def acquire(self) -> GpuTrialSlot:
        """Wait for a slot. Cancelling the wait holds nothing."""
        while True:
            slot = self.try_acquire()
            if slot is not None:
                return slot
            await asyncio.sleep(self._poll_sec)
