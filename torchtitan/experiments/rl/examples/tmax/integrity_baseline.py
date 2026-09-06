# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Integrity baseline for a task's protected paths and protected commands (reaudit).

A data row may carry, in ``metadata.tmax``:

  ``protected_paths``  a list of paths the policy must not alter;
  ``protected_cmds``   a list of command strings whose OUTPUT the policy must not alter.

The harness digests every entry in the sandbox AFTER setup and BEFORE the agent's first action
(the rollouter seam), keeps the digests harness-side, re-digests before the verifier runs
(grading), and any difference scores 0. No stamp travels with the data and there is no capture
step at prep time -- the baseline is whatever the booted environment holds when the agent is
handed it.

ONE SHELL STRING, BUILT HERE AND RUN BY BOTH SIDES, so the two digests can only differ because
the sandbox did. Digests are computed IN THE SANDBOX -- the executor returns decoded text with
stderr merged into stdout, so the harness never sees real bytes and must not try to hash them.
The string prints exactly one line per entry, ``<digest> <index>``, index = position in the
combined list (paths first, then commands); NOTHING else may reach stdout, and every entry's
stderr is discarded inside the string so a tool warning cannot enter the parse. Per entry:

  a regular file      -> its sha256 (``sha256sum -- "$path"``, first field)
  a directory         -> sha256 of the LC_ALL=C-sorted, null-separated ``find -type f | xargs
                         sha256sum`` listing (none are pinned today; kept as specified)
  absent              -> the literal token ABSENT
  present, neither    -> the literal token OTHER
  a command           -> sha256 of its stdout, computed with the hook exporter's arithmetic
                         reproduced exactly (tools/pretest_export.py in the reaudit repo):
                         env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/nonexistent LC_ALL=C
                         bash -c "$cmd" </dev/null 2>/dev/null, captured with $( ) -- which
                         strips TRAILING NEWLINES -- then printf '%s' "$out" | sha256sum.
                         A command that exits nonzero -> the literal token FAIL.

THE COMMAND STRING IS ONE ARGV ELEMENT AND IS NEVER INTERPOLATED INTO A LARGER SHELL STRING.
Every shipped entry contains a quote character; interpolated, it would mis-parse. The entry
enters the generated string only as a single-quoted assignment (``'\\''`` escaping) to a shell
variable that is then referenced as ``"$_cmd"`` -- the hook's own shape. The command's
ENVIRONMENT IS PART OF THE PROTECTED VALUE: a runner that used its own environment would get a
different stdout and grade an honest episode 0, so the wrapper above is not paraphrased.

Output is keyed by INDEX, never by the entry text, so no character an entry may contain can
break the parse; lists are iterated AS LISTS and never joined and re-split (five shipped paths
contain spaces). Relative paths resolve against the task's workdir; commands run in the
sandbox's default cwd, as the hook does.

A harness failure is never the policy's fault and never a 0: a nonzero exit of the whole
string, a truncated output (the sandbox appends a visible marker past its output limit), a
malformed or missing line, or a baseline that was never captured all raise
``IntegrityHarnessError`` so the episode is void rather than silently skipped or scored.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Awaitable, Callable

ABSENT = "ABSENT"
OTHER = "OTHER"
FAIL = "FAIL"
# The visible marker the Daytona backend appends when a command's output exceeds its limit
# (harness/sandbox/daytona.py). Its presence means the digest listing is incomplete.
TRUNCATION_MARKER = "[torchtitan: command output truncated]"
COMMAND_TIMEOUT_CAP_SEC = 120
# The hook exporter's sanitised PATH, verbatim (tools/pretest_export.py, _PIN_SAFE_PATH).
PIN_SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# The hook's wrapper for a command entry, verbatim in shape: empty environment, fixed locale,
# closed stdin, stderr discarded, the command as ONE argv element via "$_cmd".
CMD_WRAPPER = (
    f"env -i PATH={PIN_SAFE_PATH} HOME=/nonexistent LC_ALL=C "
    'bash -c "$_cmd" </dev/null 2>/dev/null'
)

_LINE = re.compile(r"^([0-9a-f]{64}|ABSENT|OTHER|FAIL) (\d+)$")


class IntegrityHarnessError(RuntimeError):
    """The baseline could not be established or re-measured. Void the episode; never score it."""


def _string_list(tmax: dict, key: str) -> list[str]:
    raw = tmax.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(
        isinstance(p, str) and p.strip() for p in raw
    ):
        raise IntegrityHarnessError(
            f"{key} must be a list of non-empty strings, got {type(raw).__name__}"
        )
    return list(raw)


def protected_paths_of(tmax: dict) -> list[str]:
    """The row's protected paths, validated: a list of non-empty strings, or [] when the key
    is absent. A malformed value is a data defect and raises -- it must not read as "none"."""
    return _string_list(tmax, "protected_paths")


def protected_cmds_of(tmax: dict) -> list[str]:
    """The row's protected commands, same contract. A newline inside an entry is refused: the
    hook's manifest is line-based and no shipped entry carries one."""
    cmds = _string_list(tmax, "protected_cmds")
    bad = [c for c in cmds if "\n" in c or "\r" in c]
    if bad:
        raise IntegrityHarnessError(
            f"{len(bad)} protected command(s) contain a newline"
        )
    return cmds


def protected_entries_of(tmax: dict) -> list[tuple[str, str]]:
    """[(kind, entry)] in digest order: every path, then every command. The order is the index
    space of the generated string, so both sides derive it from the same function."""
    return [("path", p) for p in protected_paths_of(tmax)] + [
        ("cmd", c) for c in protected_cmds_of(tmax)
    ]


def resolve_path(path: str, workdir: str) -> str:
    """Absolute paths stand; relative ones resolve against the task's workdir."""
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(workdir, path))


def _path_clause(i: int, resolved: str) -> str:
    q = shlex.quote(resolved)
    return (
        f"if [ -f {q} ]; then "
        f"printf '%s {i}\\n' \"$(sha256sum -- {q} 2>/dev/null | cut -d' ' -f1)\"; "
        f"elif [ -d {q} ]; then "
        f"printf '%s {i}\\n' \"$(cd -- {q} 2>/dev/null && find . -type f -print0 2>/dev/null "
        f"| LC_ALL=C sort -z | xargs -0 -r sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1)\"; "
        f"elif [ -e {q} ]; then printf '{OTHER} {i}\\n'; "
        f"else printf '{ABSENT} {i}\\n'; fi"
    )


def _cmd_clause(i: int, cmd: str) -> str:
    # The command enters the string ONLY here, as a single-quoted assignment; the wrapper
    # references it as "$_cmd", one argv element to bash -c, exactly as the hook does.
    return (
        f"_cmd={shlex.quote(cmd)}; "
        f'if _out="$({CMD_WRAPPER})"; then '
        f"printf '%s {i}\\n' \"$(printf '%s' \"$_out\" | sha256sum | cut -d' ' -f1)\"; "
        f"else printf '{FAIL} {i}\\n'; fi"
    )


def build_digest_command(entries: list[tuple[str, str]], workdir: str) -> str:
    """One shell string emitting ``<digest> <index>`` per (kind, entry), in list order."""
    parts = []
    for i, (kind, entry) in enumerate(entries):
        if kind == "path":
            parts.append(_path_clause(i, resolve_path(entry, workdir)))
        elif kind == "cmd":
            parts.append(_cmd_clause(i, entry))
        else:
            raise IntegrityHarnessError(f"unknown protected entry kind {kind!r}")
    return "; ".join(parts)


def parse_digest_output(stdout: str, entries: list[tuple[str, str]]) -> dict[str, str]:
    """{entry: digest} from the string's output. Every entry must be answered exactly once by a
    well-formed line; a truncation marker anywhere means the listing is incomplete. Keys are the
    entry texts (paths and commands never collide in practice; the kind is not part of the key
    so the baseline is a plain dict of strings)."""
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
        if idx >= len(entries) or idx in got:
            raise IntegrityHarnessError(
                f"digest line for unexpected or repeated index {idx}"
            )
        got[idx] = m.group(1)
    missing = [entries[i][1] for i in range(len(entries)) if i not in got]
    if missing:
        raise IntegrityHarnessError(
            f"no digest line for {len(missing)} entr(y/ies): {missing[:5]}"
        )
    return {entries[i][1]: got[i] for i in range(len(entries))}


ExecFn = Callable[..., Awaitable[tuple]]


async def compute_digests(
    exec_fn: ExecFn, entries: list[tuple[str, str]], *, workdir: str, timeout: int
) -> dict[str, str]:
    """Run the digest string through ``exec_fn(cmd, check=False, timeout=...)`` -- the sandbox's
    ``exec`` (or a root-forcing wrapper of it) -- and parse it. The caller decides the user."""
    cmd = build_digest_command(entries, workdir)
    rc, stdout, stderr = await exec_fn(
        cmd, check=False, timeout=min(COMMAND_TIMEOUT_CAP_SEC, timeout)
    )
    if rc != 0:
        raise IntegrityHarnessError(
            f"digest command exited {rc}: {(stderr or '').strip()[:200]!r}"
        )
    return parse_digest_output(stdout or "", entries)


def differences(baseline: dict[str, str], current: dict[str, str]) -> list[str]:
    """Entries whose digest differs -- including ABSENT<->present, OTHER, and FAIL<->hex -- in
    the baseline's order. An entry missing from either side counts as different; FAIL on both
    sides is equal."""
    keys = list(baseline) + [k for k in current if k not in baseline]
    return [k for k in keys if baseline.get(k) != current.get(k)]


def tmax_protected_fields(
    paths: list[str] | None, cmds: list[str] | None
) -> dict[str, list[str]]:
    """The ``tmax`` keys for a row's protected lists, validated: ``protected_paths`` then
    ``protected_cmds``, each present only when its list is non-empty (never an empty list),
    each a list of non-empty strings, no newline inside a command. Every producer of a row --
    the dataset prep, the loop's packer -- builds the keys HERE, so a folded row and a prepared
    row cannot disagree on the shape grading reads. A malformed list raises
    ``IntegrityHarnessError``; the caller refuses the row by id."""
    fields: dict[str, list[str]] = {}
    if paths:
        fields["protected_paths"] = protected_paths_of({"protected_paths": paths})
    if cmds:
        fields["protected_cmds"] = protected_cmds_of({"protected_cmds": cmds})
    return fields


async def capture_integrity_baseline(
    exec_fn: ExecFn, tmax: dict, *, workdir: str, timeout: int
) -> dict[str, str] | None:
    """The rollouter's half: ``None`` when the row has no protected entries, else the digests
    to hold harness-side until grading."""
    entries = protected_entries_of(tmax)
    if not entries:
        return None
    return await compute_digests(exec_fn, entries, workdir=workdir, timeout=timeout)


async def capture_baseline(
    sb, tmax: dict, *, workdir: str, timeout: int
) -> dict[str, str] | None:
    """``capture_integrity_baseline`` over a sandbox object. The rollouter's root sandbox and
    the loop's ``_Root`` wrapper both expose an ``exec`` that already runs as root; every seam
    (training rollout, the loop's sandbox tool, the loop's revalidator) calls THIS, so the
    digests are taken one way everywhere."""
    return await capture_integrity_baseline(
        sb.exec, tmax, workdir=workdir, timeout=timeout
    )


async def integrity_differences(
    exec_fn: ExecFn,
    tmax: dict,
    baseline: dict[str, str] | None,
    *,
    workdir: str,
    timeout: int,
) -> list[str]:
    """The grader's half: re-digest and return the differing entries ([] means intact). A row
    with protected entries and no baseline is a harness bug and raises."""
    entries = protected_entries_of(tmax)
    if not entries:
        return []
    if baseline is None:
        raise IntegrityHarnessError(
            f"{len(entries)} protected entr(y/ies) but no integrity baseline was captured "
            "at rollout"
        )
    current = await compute_digests(exec_fn, entries, workdir=workdir, timeout=timeout)
    return differences(baseline, current)
