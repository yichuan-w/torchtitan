# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Integrity baseline for a task's protected paths (reaudit).

A data row may carry ``metadata.tmax["protected_paths"]``: a list of paths the policy must
not alter. The harness digests them in the sandbox AFTER setup and BEFORE the agent's first
action (the rollouter seam), keeps the digests harness-side, re-digests them before the
verifier runs (grading), and any difference scores 0. No stamp travels with the data and
there is no capture step at prep time -- the baseline is whatever the booted environment
holds when the agent is handed it.

One shell command, built HERE and used by BOTH sides, so the two digests can only differ
because the filesystem did. Per path, in list order, one output line ``<digest> <index>``:

  a regular file      -> its sha256
  a directory         -> sha256 of the LC_ALL=C-sorted ``find -type f | xargs sha256sum``
                         listing (null-separated, so a filename cannot split a line)
  absent              -> the literal token ABSENT
  present, but neither a regular file nor a directory -> the literal token OTHER

Output is keyed by INDEX, never by the path text, so no character a path may contain can
break the parse. Relative paths resolve against the task's workdir.

A harness failure is never the policy's fault and never a 0: a nonzero exit, a truncated
output (the sandbox appends a visible marker past its output limit), a malformed or missing
line, or a baseline that was never captured all raise ``IntegrityHarnessError`` so the
episode is void rather than silently skipped or silently scored.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Awaitable, Callable

ABSENT = "ABSENT"
OTHER = "OTHER"
# The visible marker the Daytona backend appends when a command's output exceeds its limit
# (harness/sandbox/daytona.py). Its presence means the digest listing is incomplete.
TRUNCATION_MARKER = "[torchtitan: command output truncated]"
COMMAND_TIMEOUT_CAP_SEC = 120

_LINE = re.compile(r"^([0-9a-f]{64}|ABSENT|OTHER) (\d+)$")


class IntegrityHarnessError(RuntimeError):
    """The baseline could not be established or re-measured. Void the episode; never score it."""


def protected_paths_of(tmax: dict) -> list[str]:
    """The row's protected paths, validated: a list of non-empty strings, or [] when the key is
    absent. Anything else is a data defect and raises -- a malformed list must not read as
    "no protected paths"."""
    raw = tmax.get("protected_paths")
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(
        isinstance(p, str) and p.strip() for p in raw
    ):
        raise IntegrityHarnessError(
            f"protected_paths must be a list of non-empty strings, got {type(raw).__name__}"
        )
    return list(raw)


def resolve_path(path: str, workdir: str) -> str:
    """Absolute paths stand; relative ones resolve against the task's workdir."""
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(workdir, path))


def build_digest_command(paths: list[str], workdir: str) -> str:
    """One ``sh`` command emitting ``<digest> <index>`` per protected path, in list order."""
    parts = []
    for i, p in enumerate(paths):
        q = shlex.quote(resolve_path(p, workdir))
        parts.append(
            f"if [ -f {q} ]; then "
            f"printf '%s {i}\\n' \"$(sha256sum -- {q} | cut -d' ' -f1)\"; "
            f"elif [ -d {q} ]; then "
            f"printf '%s {i}\\n' \"$(cd -- {q} && find . -type f -print0 | LC_ALL=C sort -z "
            f"| xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1)\"; "
            f"elif [ -e {q} ]; then printf '{OTHER} {i}\\n'; "
            f"else printf '{ABSENT} {i}\\n'; fi"
        )
    return "; ".join(parts)


def parse_digest_output(stdout: str, paths: list[str]) -> dict[str, str]:
    """{path: digest} from the command's stdout. Every path must be answered exactly once by a
    well-formed line; a truncation marker anywhere means the listing is incomplete."""
    if TRUNCATION_MARKER in stdout:
        raise IntegrityHarnessError("digest output was truncated by the sandbox")
    got: dict[int, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            raise IntegrityHarnessError(f"malformed digest line: {line[:80]!r}")
        idx = int(m.group(2))
        if idx >= len(paths) or idx in got:
            raise IntegrityHarnessError(
                f"digest line for unexpected or repeated index {idx}"
            )
        got[idx] = m.group(1)
    missing = [paths[i] for i in range(len(paths)) if i not in got]
    if missing:
        raise IntegrityHarnessError(
            f"no digest line for {len(missing)} path(s): {missing[:5]}"
        )
    return {paths[i]: got[i] for i in range(len(paths))}


ExecFn = Callable[..., Awaitable[tuple]]


async def compute_digests(
    exec_fn: ExecFn, paths: list[str], *, workdir: str, timeout: int
) -> dict[str, str]:
    """Run the digest command through ``exec_fn(cmd, check=False, timeout=...)`` -- the sandbox's
    ``exec`` (or a root-forcing wrapper of it) -- and parse it. The caller decides the user."""
    cmd = build_digest_command(paths, workdir)
    rc, stdout, stderr = await exec_fn(
        cmd, check=False, timeout=min(COMMAND_TIMEOUT_CAP_SEC, timeout)
    )
    if rc != 0:
        raise IntegrityHarnessError(
            f"digest command exited {rc}: {(stderr or '').strip()[:200]!r}"
        )
    return parse_digest_output(stdout or "", paths)


def differences(baseline: dict[str, str], current: dict[str, str]) -> list[str]:
    """Paths whose digest differs, including ABSENT<->present and OTHER transitions, in the
    baseline's order. A path missing from either side counts as different."""
    keys = list(baseline) + [k for k in current if k not in baseline]
    return [k for k in keys if baseline.get(k) != current.get(k)]


async def capture_integrity_baseline(
    exec_fn: ExecFn, tmax: dict, *, workdir: str, timeout: int
) -> dict[str, str] | None:
    """The rollouter's half: ``None`` when the row has no protected paths, else the digests to
    hold harness-side until grading."""
    paths = protected_paths_of(tmax)
    if not paths:
        return None
    return await compute_digests(exec_fn, paths, workdir=workdir, timeout=timeout)


async def integrity_differences(
    exec_fn: ExecFn,
    tmax: dict,
    baseline: dict[str, str] | None,
    *,
    workdir: str,
    timeout: int,
) -> list[str]:
    """The grader's half: re-digest and return the differing paths ([] means intact). A row
    with protected paths and no baseline is a harness bug and raises."""
    paths = protected_paths_of(tmax)
    if not paths:
        return []
    if baseline is None:
        raise IntegrityHarnessError(
            f"{len(paths)} protected path(s) but no integrity baseline was captured at rollout"
        )
    current = await compute_digests(exec_fn, paths, workdir=workdir, timeout=timeout)
    return differences(baseline, current)
