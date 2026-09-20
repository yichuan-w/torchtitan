# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Daytona cloud sandbox backend.

Boots a remote cloud container from a Docker image. Meant for boxes that cannot
expose an inbound port to the cloud (an internal dev box): the agent inside the
sandbox reaches the on-box Anthropic adapter through a file-relay bridge over the
Daytona ``fs`` API (see ``bridge.py``), not a direct dial-back.

Ported from THUDM/slime ``slime/agent/sandbox.py``.
"""

from __future__ import annotations

import base64
import json

import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from torchtitan.experiments.rl.harness.sandbox.base import (
    _getenv,
    ExecResult,
    FileContent,
    SandboxIssue,
    SandboxIssueTracker,
)

logger = logging.getLogger(__name__)

# Label stamped on every sandbox we create, so cleanup can target ONLY our
# sandboxes (never another tenant's) when sharing a Daytona account.
HARNESS_LABELS = {"owner": _getenv("TT_DAYTONA_LABEL", default="titan_swe_r2e")}

_COMMAND_KILL_GRACE_SEC = 10
_DEFAULT_EXEC_REQUEST_TIMEOUT_SEC = 120
_SESSION_POLL_GRACE_SEC = 120
_SESSION_RPC_TIMEOUT_SEC = 60
_COMMAND_SUBMIT_DELAYS_SEC = (0.0, 1.0, 2.0, 4.0, 8.0)
# Everything the harness itself writes inside the sandbox lives on /dev/shm, a
# tmpfs outside the task's disk quota: the claim that makes a relaunch
# idempotent, the wrapper, the raw output (head and tail, 4 MiB each) and the
# result files. A task that fills its own disk then fails its own commands with
# ENOSPC, which the agent sees, while the harness keeps driving the sandbox.
# Measured 2026-09-14 on a 1 GB tmax sandbox at 100% disk: a one-shot exec
# still ran, a detached wrapper still wrote its status here, and the file API
# still read it; only the Toolbox's own session mkdir failed.
_EXEC_CLAIM_DIR = "/dev/shm/.torchtitan_exec_claims"
_EXEC_OUTPUT_DIR = "/dev/shm/.torchtitan_exec_out"
_EXEC_RESULT_DIR = "/dev/shm/.torchtitan_exec"
_EXEC_STAGING_DIR = "/dev/shm"
# The launch is one short one-shot exec that backgrounds the wrapper and
# returns; this bounds only that request, never the command.
_LAUNCH_RPC_TIMEOUT_SEC = 30
# What a command's caller gets back is its complete output up to 8 MiB; past
# that, the first 4 MiB, one marker line, and the LAST 4 MiB. The tail is kept
# by an awk ring buffer that stops at the receipt, so it is the true end of the
# stream even while a background child still holds stdout. Terminus-2 reads
# the whole tmux history every turn and diffs it against the previous one, so
# what it must see intact is the end, and it applies its own 10 KB limit
# before anything reaches the model; nothing smaller is cut here.
_EXEC_OUTPUT_HEAD_BYTES = 4 * 1024 * 1024
_EXEC_OUTPUT_TAIL_BYTES = 4 * 1024 * 1024
# Daytona ultimately launches this string as one `sh -c` argument. Spill with
# headroom below Linux MAX_ARG_STRLEN (128 KiB).
_EXEC_INLINE_COMMAND_LIMIT_BYTES = 96 * 1024
_FINAL_RESULT_PROBE_TIMEOUT_SEC = 5.0
# Polls for the status file after a launch that did not return the result
# inline: short first, doubling, so a command that finishes in 300 ms is seen
# in ~400 ms and a long one costs a poll every 2 s.
_RESULT_POLL_DELAYS_SEC = (0.1, 0.2, 0.4, 0.8, 1.6)
_RESULT_POLL_INTERVAL_SEC = 2.0
# The launch request waits in the sandbox up to this long for the status file
# and, when the output is at most this many bytes, returns both in its own
# response (base64, so bytes survive the daemon's JSON encoding). Most of what
# Terminus-2 runs -- capture-pane, send-keys, display-message -- finishes in
# tens of milliseconds, so this turns one launch + one poll + one read into a
# single round trip. Larger or slower results go through the file API.
_LAUNCH_INLINE_WAIT_TICKS = 20  # x 50 ms
_LAUNCH_INLINE_MAX_BYTES = 256 * 1024
_LAUNCH_INLINE_PREFIX = "__torchtitan_inline__"
_OBSERVABLE_WRAPPER_ENV = "__TORCHTITAN_OBSERVABLE_WRAPPER_B64"
_MISSING_OUTPUT_MESSAGE = (
    "[torchtitan: command completed, but its captured output is unavailable]"
)


@dataclass(frozen=True, slots=True)
class _ObservableExecCommand:
    inline_command: str
    uploaded_command: str
    wrapper_path: str
    wrapper: bytes
    status_path: str
    output_path: str

    def launch(self, *, uploaded: bool, stale: tuple[str, ...] = ()) -> str:
        """The one-shot exec that starts the wrapper detached and returns.

        The claim inside the wrapper's dispatcher makes this idempotent: a
        relaunch after a lost response finds the claim and exits without
        running the body again. Stdio goes to /dev/null so the Toolbox's
        request does not wait on the detached process group.

        ``stale`` are the previous command's result files, already read: they
        are removed here, in the request this launch makes anyway, so a
        rollout of hundreds of commands does not fill /dev/shm (64 MB) with
        outputs of up to 8 MiB each.
        """
        body = self.uploaded_command if uploaded else self.inline_command
        cleanup = f"rm -f {shlex.join(stale)} 2>/dev/null; " if stale else ""
        status = shlex.quote(self.status_path)
        output = shlex.quote(self.output_path)
        # The wrapper writes the output file before the status file, so a
        # status file means the output is complete.
        wait = (
            "_tt_i=0; while [ $_tt_i -lt "
            f"{_LAUNCH_INLINE_WAIT_TICKS} ]; do "
            f"if [ -f {status} ]; then "
            f"_tt_sz=$(stat -c %s {output} 2>/dev/null || printf 999999999); "
            f'if [ "$_tt_sz" -le {_LAUNCH_INLINE_MAX_BYTES} ]; then '
            f"printf '%s%s\\n' {shlex.quote(_LAUNCH_INLINE_PREFIX)} \"$(cat {status})\"; "
            f"base64 {output} | tr -d '\\n'; exit 0; fi; break; fi; "
            "sleep 0.05; _tt_i=$((_tt_i+1)); done; echo launched"
        )
        return f"{cleanup}( {body} ) </dev/null >/dev/null 2>&1 & {wait}"


def _error_status_code(error: BaseException) -> int | None:
    for value in (
        getattr(error, "status_code", None),
        getattr(error, "status", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _is_sandbox_gone_error(error: BaseException) -> bool:
    if _error_status_code(error) == 404:
        return True
    name = type(error).__name__.lower()
    if "notfound" in name or "not_found" in name:
        return True
    message = str(error).lower()
    return (
        ("sandbox" in message and "not found" in message)
        or "sandbox has been deleted" in message
        or "no such container" in message
        or "no ip address" in message
    )


def _is_transient_rpc_error(error: BaseException) -> bool:
    status_code = _error_status_code(error)
    if status_code == 429 or (status_code is not None and status_code >= 500):
        return True
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    name = type(error).__name__.lower()
    if "connection" in name or "timeout" in name or "throttl" in name:
        return True
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "connection reset",
            "server disconnected",
            "temporarily unavailable",
            "timed out",
            "timeout",
            "too many requests",
            # The provider reports its own 5xx as prose, not a status code, so the
            # numeric checks above miss it. Seen on session create under a wide
            # rollout fanout ("internal server error: failed to create session
            # config directory"), where retrying with a fresh id does succeed.
            "internal server error",
            "service unavailable",
            "bad gateway",
            "status code 429",
            "status code 500",
            "status code 502",
            "status code 503",
            "status code 504",
        )
    )


def _build_exec_command(
    cmd: str,
    *,
    user: str,
    env: dict[str, str] | None,
    timeout: int,
) -> str:
    """Build one shell-safe command for the Daytona session API."""
    # Decode through a tmpfs script rather than argv or the environment. Besides
    # hiding pkill patterns from ancestor argv, this avoids MAX_ARG_STRLEN for
    # large model-generated shell actions while preserving their original stdin.
    encoded_cmd = base64.b64encode(cmd.encode()).decode("ascii")
    script_path_prefix = shlex.quote(f"{_EXEC_STAGING_DIR}/.torchtitan_exec_command.")
    argv = ["bash"]
    if user and user != "root":
        keys = ",".join((env or {}).keys())
        argv = ["runuser", "-u", user]
        if keys:
            argv.append(f"--whitelist-environment={keys}")
        argv.extend(["--", "bash"])
    if env:
        argv = ["env", "--", *(f"{key}={value}" for key, value in env.items()), *argv]
    argv = [
        "timeout",
        # Use the short options shared by GNU coreutils and BusyBox.  Some
        # TerminalWorld images provide BusyBox's timeout, which rejects the
        # GNU-only --signal/--kill-after spellings before running the payload.
        "-s",
        "TERM",
        "-k",
        f"{_COMMAND_KILL_GRACE_SEC}s",
        f"{timeout}s",
        *argv,
    ]
    runner = f'{shlex.join(argv)} "$_tt_script_path"'
    return (
        f"_tt_script_path={script_path_prefix}$$; "
        f"if printf %s {shlex.quote(encoded_cmd)} | base64 -d "
        '   > "$_tt_script_path"; then '
        f"{runner}; _tt_script_rc=$?; "
        "else _tt_script_rc=$?; fi; "
        'rm -f "$_tt_script_path"; '
        '(exit "$_tt_script_rc")'
    )


# awk keeps the last ``cap`` bytes of its input in a ring of lines and writes
# them out the moment a line containing the receipt ``r`` arrives (the text
# before the receipt on that line included), so the tail is the true end of
# the command's output without waiting for EOF. ``drop`` receives the number of
# bytes the ring let go, which is what decides whether the marker is inserted.
AWK_TAIL_PROG = (
    "{ i = index($0, r); "
    "if (i > 0) { piece = substr($0, 1, i - 1); "
    "if (length(piece) > 0) { buf[++n] = piece; tot += length(piece) }; done = 1 } "
    'else { buf[++n] = $0 "\\n"; tot += length($0) + 1 }; '
    "while (tot > cap && h < n) { h++; tot -= length(buf[h]); "
    "dropped += length(buf[h]); delete buf[h] }; "
    "if (done) exit } "
    'END { for (k = h + 1; k <= n; k++) printf "%s", buf[k] > out; close(out); '
    "print dropped + 0 > drop; close(drop); "
    'system("mv -f " out " " fin) }'
)


def _build_observable_exec(full: str, command_key: str) -> _ObservableExecCommand:
    """Build inline and uploaded forms of an observable command."""
    raw_output_path = f"{_EXEC_OUTPUT_DIR}/{command_key}.raw"
    raw_tail_path = f"{_EXEC_OUTPUT_DIR}/{command_key}.tail"
    raw_tail_tmp_path = f"{raw_tail_path}.tmp"
    dropped_path = f"{_EXEC_OUTPUT_DIR}/{command_key}.dropped"
    output_fifo_path = f"{_EXEC_OUTPUT_DIR}/{command_key}.fifo"
    result_prefix = f"{_EXEC_RESULT_DIR}/{command_key}"
    output_path = f"{result_prefix}.output"
    output_tmp_path = f"{output_path}.tmp"
    status_path = f"{result_prefix}.status"
    status_tmp_path = f"{status_path}.tmp"
    wrapper_path = f"{_EXEC_STAGING_DIR}/.torchtitan_exec_{command_key}.wrapper"
    quoted_output_dir = shlex.quote(_EXEC_OUTPUT_DIR)
    quoted_result_dir = shlex.quote(_EXEC_RESULT_DIR)
    quoted_raw_output = shlex.quote(raw_output_path)
    quoted_raw_tail = shlex.quote(raw_tail_path)
    quoted_raw_tail_tmp = shlex.quote(raw_tail_tmp_path)
    quoted_dropped = shlex.quote(dropped_path)
    quoted_output_fifo = shlex.quote(output_fifo_path)
    quoted_output = shlex.quote(output_path)
    quoted_output_tmp = shlex.quote(output_tmp_path)
    quoted_status = shlex.quote(status_path)
    quoted_status_tmp = shlex.quote(status_tmp_path)
    quoted_wrapper = shlex.quote(wrapper_path)
    truncation_marker = shlex.quote("\n[torchtitan: command output truncated]\n")
    # A receipt in the same byte stream orders collection after foreground output.
    # Waiting for EOF would wait for detached children that retain stdout.
    output_receipt = f"__torchtitan_output_end_{command_key}__"
    quoted_receipt = shlex.quote(output_receipt)
    # The tail keeper: a ring buffer of the last _EXEC_OUTPUT_TAIL_BYTES of
    # whatever follows the head, flushed the moment the receipt arrives, so it
    # never waits for EOF (a daemon left running keeps the pipe open). The
    # count of bytes it dropped decides whether the marker line is inserted.
    tail_keeper = shlex.quote(AWK_TAIL_PROG)
    wrapper = (
        f"rm -f {quoted_wrapper}; "
        f"_tt_run() {{ {full}; _tt_exec_rc=$?; }}; "
        f"mkdir -p {quoted_result_dir} 2>/dev/null || :; "
        f"rm -f {quoted_output} {quoted_output_tmp} {quoted_status} "
        f"{quoted_status_tmp}; "
        "_tt_awk=$(command -v awk 2>/dev/null); "
        f"if mkdir -p {quoted_output_dir} 2>/dev/null "
        "&& command -v stdbuf > /dev/null "
        f"&& rm -f {quoted_raw_output} {quoted_raw_tail} {quoted_raw_tail_tmp} "
        f"{quoted_dropped} {quoted_output_fifo} "
        f"&& mkfifo {quoted_output_fifo} 2>/dev/null "
        f"&& : > {quoted_raw_output} "
        f"&& exec 9>> {quoted_raw_output} "
        f"&& exec 7<> {quoted_output_fifo}; then "
        f"(exec 7>&-; exec 8< {quoted_output_fifo}; "
        f"stdbuf -o0 head -c {_EXEC_OUTPUT_HEAD_BYTES} <&8 >&9; "
        "exec 9>&-; "
        'if [ -n "$_tt_awk" ]; then '
        f'"$_tt_awk" -v cap={_EXEC_OUTPUT_TAIL_BYTES} -v r={quoted_receipt} '
        f"-v out={quoted_raw_tail_tmp} -v fin={quoted_raw_tail} "
        f"-v drop={quoted_dropped} {tail_keeper} <&8; fi; "
        # Keep draining after the receipt so a daemon that inherited stdout is
        # never killed by SIGPIPE on its next write.
        "cat <&8 > /dev/null) </dev/null > /dev/null 2>&1 & "
        "_tt_run >&7 2>&1 7>&-; "
        f"printf %s {quoted_receipt} >&7; exec 7>&-; "
        f"_tt_collect_end=$(($(date +%s) + {_SESSION_POLL_GRACE_SEC})); "
        "_tt_last=-1; _tt_mode=; _tt_exec_size=; "
        "while :; do "
        "_tt_raw_size=$(stat -Lc '%s' /proc/$$/fd/9 2>/dev/null || printf '0'); "
        # Search the head only when it grew: the receipt can only be new then.
        'if [ "$_tt_raw_size" != "$_tt_last" ]; then _tt_last=$_tt_raw_size; '
        f"_tt_exec_size=$(grep -aobF {quoted_receipt} /proc/$$/fd/9 "
        "| head -n 1 | cut -d : -f 1); "
        '[ -n "$_tt_exec_size" ] && { _tt_mode=head; break; }; fi; '
        f"if [ -f {quoted_raw_tail} ]; then _tt_mode=tail; break; fi; "
        # Without awk there is no tail keeper: once the head is full the
        # receipt can never be found, so return the head alone, marked.
        f'if [ "$_tt_raw_size" -ge {_EXEC_OUTPUT_HEAD_BYTES} ] '
        '&& [ -z "$_tt_awk" ]; then '
        f"_tt_exec_size={_EXEC_OUTPUT_HEAD_BYTES - len(output_receipt)}; "
        "_tt_mode=head-only; break; fi; "
        'if [ "$(date +%s)" -ge "$_tt_collect_end" ]; then '
        "exit 125; fi; sleep 0.05; "
        "done; "
        f"mkdir -p {quoted_result_dir} 2>/dev/null || :; "
        'if [ "$_tt_mode" = tail ]; then '
        f"cat /proc/$$/fd/9 > {quoted_output_tmp}; "
        f'if [ "$(cat {quoted_dropped} 2>/dev/null || printf 0)" -gt 0 ]; then '
        f"printf %s {truncation_marker} >> {quoted_output_tmp}; fi; "
        f"cat {quoted_raw_tail} >> {quoted_output_tmp}; "
        "else "
        f'head -c "$_tt_exec_size" /proc/$$/fd/9 > {quoted_output_tmp}; '
        'if [ "$_tt_mode" = head-only ]; then '
        f"printf %s {truncation_marker} >> {quoted_output_tmp}; fi; "
        "fi; "
        "exec 9>&-; "
        f"mv -f {quoted_output_tmp} {quoted_output}; "
        f"rm -f {quoted_raw_output} {quoted_raw_tail} {quoted_raw_tail_tmp} "
        f"{quoted_dropped} {quoted_output_fifo}; "
        "else "
        f"rm -f {quoted_raw_output} {quoted_output_fifo}; "
        "_tt_run; "
        "fi; "
        f"mkdir -p {quoted_result_dir} 2>/dev/null || :; "
        f"(printf '%s\\n' \"$_tt_exec_rc\" > {quoted_status_tmp} "
        f"&& mv -f {quoted_status_tmp} {quoted_status}) 2>/dev/null || :; "
        '(exit "$_tt_exec_rc")'
    )
    encoded_wrapper = base64.b64encode(wrapper.encode()).decode("ascii")
    # Claim before materializing or opening the wrapper: a duplicate must not
    # truncate a script that the first shell is still reading. Claims live on
    # /dev/shm and remain until sandbox deletion, even if the launcher dies.
    claim = (
        f"mkdir -p {shlex.quote(_EXEC_CLAIM_DIR)} || exit $?; "
        f"if ! mkdir {shlex.quote(f'{_EXEC_CLAIM_DIR}/{command_key}')} 2>/dev/null; "
        f"then test -d {shlex.quote(f'{_EXEC_CLAIM_DIR}/{command_key}')} "
        "&& exit 0; exit 125; fi; "
    )
    materializer = (
        claim + f"mkdir -p {quoted_result_dir} 2>/dev/null || exit $?; "
        f'printf %s "${_OBSERVABLE_WRAPPER_ENV}" | base64 -d > {quoted_wrapper} '
        "|| exit $?; "
        f"unset {_OBSERVABLE_WRAPPER_ENV}; "
        "if command -v setsid > /dev/null 2>&1; then "
        f"exec setsid sh {quoted_wrapper}; "
        f"else exec sh {quoted_wrapper}; fi"
    )
    inline_command = (
        f"{_OBSERVABLE_WRAPPER_ENV}={shlex.quote(encoded_wrapper)} exec "
        + shlex.join(["sh", "-c", materializer])
    )
    dispatcher = (
        claim + 'if command -v setsid > /dev/null 2>&1; then exec setsid sh "$1"; '
        'else exec sh "$1"; fi'
    )
    uploaded_command = "exec " + shlex.join(
        ["sh", "-c", dispatcher, "sh", wrapper_path]
    )
    return _ObservableExecCommand(
        inline_command=inline_command,
        uploaded_command=uploaded_command,
        wrapper_path=wrapper_path,
        wrapper=wrapper.encode(),
        status_path=status_path,
        output_path=output_path,
    )


def _build_observable_exec_command(full: str, command_key: str) -> tuple[str, str, str]:
    """Wrap a command inline with an atomic result sentinel and bounded output."""
    command = _build_observable_exec(full, command_key)
    return command.inline_command, command.status_path, command.output_path


def _is_missing_file_error(error: BaseException) -> bool:
    message = str(error).lower()
    if (
        ("sandbox" in message and "not found" in message)
        or "sandbox has been deleted" in message
        or "no such container" in message
        or "no ip address" in message
    ):
        return False
    if _error_status_code(error) == 404 or isinstance(error, FileNotFoundError):
        return True
    return any(
        marker in message
        for marker in (
            "file not found",
            "no such file",
            "does not exist",
            "not found",
        )
    )


def _strip_comments_in_continuation(dockerfile: str) -> str:
    """Drop whole-line comments that sit inside an open line-continuation.

    Docker strips every whole-line comment before it joins backslash
    continuations, so a comment between two continued lines does not end the
    join. Callers that flatten continuations textually have to do the same, or
    the RUN block ends at the comment and its remaining lines each parse as a
    fresh instruction. Comments outside a continuation are left alone: they are
    harmless, and the leading block can carry a parser directive.
    """
    kept: list[str] = []
    continued = False
    for line in dockerfile.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#"):
            if not continued:
                kept.append(line)
            continue
        kept.append(line)
        continued = stripped.endswith("\\")
    return "".join(kept)


_FROM_IMAGE = re.compile(
    r"^(?P<prefix>[ \t]*FROM[ \t]+(?:--\S+[ \t]+)*)(?P<image>\S+)"
    r"(?P<suffix>[^\r\n]*)",
    re.IGNORECASE | re.MULTILINE,
)


def _qualify_docker_hub_base_images(dockerfile: str) -> str:
    """Make Docker Hub's implicit registry explicit in FROM instructions."""
    stage_names: set[str] = set()

    def qualify(match: re.Match[str]) -> str:
        image = match.group("image")
        suffix = match.group("suffix")
        alias_match = re.fullmatch(
            r"[ \t]+AS[ \t]+(?P<alias>[A-Za-z0-9_.-]+)[ \t]*",
            suffix,
            re.IGNORECASE,
        )
        if (
            image.lower() != "scratch"
            and not image.startswith("$")
            and image.lower() not in stage_names
        ):
            repository = image.split("@", 1)[0]
            if "/" not in repository:
                image = "docker.io/library/" + image
            else:
                registry = repository.split("/", 1)[0].lower()
                if not (
                    "." in registry or ":" in registry or registry == "localhost"
                ):
                    image = "docker.io/" + image
        if alias_match:
            stage_names.add(alias_match.group("alias").lower())
        return match.group("prefix") + image + suffix

    return _FROM_IMAGE.sub(qualify, dockerfile)


def _prepare_dockerfile_for_daytona(dockerfile: str) -> str:
    source = _strip_comments_in_continuation(dockerfile)
    source = _qualify_docker_hub_base_images(source)
    return re.sub(r"\\\r?\n[ \t]*", " ", source)


_proxy_patch_done = False


def _keep_proxy_for_context_upload() -> None:
    """Keep the process environment while Daytona creates its object-store client.

    The SDK uploads COPY sources to S3 with an obstore client that it constructs
    inside ``isolated_env()``. Daytona 0.203.0 implements that helper by clearing
    and rebuilding the process-wide ``os.environ`` twice. A rollout worker creates
    many sandboxes concurrently and also has Python/HTTP runtime threads; one such
    clear raced a thread doing ``pwd.getpwuid()``, crashing in
    ``_nss_sss_getpwuid_r -> getenv`` with SIGSEGV. It also removes the HTTP proxy
    while constructing obstore, breaking context uploads on proxy-only hosts.

    ObjectStorage passes endpoint and credentials explicitly to ``S3Store``, so it
    does not need to hide the ambient environment. Replace the SDK context manager
    with a no-op and rebind the copies imported by both object-storage modules.
    This keeps proxy variables available and, critically, never mutates process
    environment from a concurrent request.
    """
    global _proxy_patch_done
    if _proxy_patch_done:
        return
    try:
        from daytona._sync import object_storage as sync_store  # type: ignore
        from daytona._utils import environment as env_mod  # type: ignore
    except ImportError:
        return

    from contextlib import contextmanager

    @contextmanager
    def isolated_env(temp_env=None):
        # Daytona's callers pass credentials directly to S3Store. Keep accepting
        # the argument for SDK compatibility, but never rewrite process-wide env.
        del temp_env
        yield

    env_mod.isolated_env = isolated_env
    # object_storage imported the symbol by value, so rebind it there as well.
    sync_store.isolated_env = isolated_env
    try:
        from daytona._async import object_storage as async_store  # type: ignore

        async_store.isolated_env = isolated_env
    except ImportError:
        pass
    _proxy_patch_done = True
    logger.info(
        "[daytona] disabled process-global environment clearing for "
        "build-context upload"
    )


def _eager_rebuild_daytona_models() -> None:
    """Make the Daytona SDK's Pydantic models resolvable in OUR import context.

    The SDK models use ``from __future__ import annotations`` + TYPE_CHECKING typing
    imports, so their module globals lack the forward-ref names (Optional, StrictStr,
    ...) at runtime. When daytona is imported INSIDE the torchtitan/vllm/pydantic
    import chain (the RL controller), pydantic cannot resolve those refs on first
    construct -> ``PydanticUserError: ... is not fully defined`` -> every create/exec
    fails -> turns=0. (In a clean process the refs happen to resolve, which is why the
    curate worker booted sandboxes fine but the controller did not.) Fix: load every
    daytona submodule and inject all typing + pydantic public names into each module's
    globals, so the SDK's own lazy rebuild (triggered later at the construct sites)
    resolves the refs.

    We do NOT call ``model_rebuild`` (force or otherwise): it re-derives the schema and
    DROPS the SDK's field defaults (made SessionExecuteRequest.var_async etc. required).
    Defaults that this context still drops anyway are passed explicitly at the construct
    sites (see ``exec``). Best-effort: a no-op if Daytona isn't installed.
    """
    try:
        import importlib
        import pkgutil
        import sys
        import typing

        import daytona  # type: ignore

        import pydantic

        try:
            for info in pkgutil.walk_packages(daytona.__path__, daytona.__name__ + "."):
                try:
                    importlib.import_module(info.name)
                except Exception:  # noqa: BLE001 -- skip unimportable submodules
                    pass
        except Exception:  # noqa: BLE001 -- pkgutil best-effort
            pass

        inject = {n: getattr(typing, n) for n in dir(typing) if not n.startswith("_")}
        inject.update(
            {n: getattr(pydantic, n) for n in dir(pydantic) if not n.startswith("_")}
        )
        for name, mod in list(sys.modules.items()):
            if name.startswith("daytona") and mod is not None:
                for key, val in inject.items():
                    if not hasattr(mod, key):
                        setattr(mod, key, val)
    except Exception as e:  # noqa: BLE001 -- best-effort
        logger.warning("[daytona] model namespace inject skipped: %s", e)


_eager_rebuild_daytona_models()


# Process-wide AsyncDaytona client, shared by every sandbox in this worker: one
# client = one pooled TLS session reused across all concurrent rollouts. A
# per-sandbox client instead opens its own pool, and the handshake storm under a
# many-way boot fanout over a high-latency link times out the exec requests.
_SHARED_CLIENT = None
_SHARED_CLIENT_LOCK = None

# Cap concurrent sandbox CREATES (held only during create/boot, not the rollout).
# At high rollout concurrency every group tries to boot its siblings at once; the
# resulting create burst trips Daytona's rate limit (ThrottlerException / 429),
# and the whole fanout backs off in lockstep. This throttles creates to a
# Daytona-friendly rate while letting far more sandboxes RUN concurrently. Per
# worker process; the Daytona account limit is shared, so total = num_workers x
# this value -- keep the product well under the observed throttle point.
_CREATE_SEM = None


def _create_sem():
    import asyncio

    global _CREATE_SEM
    if _CREATE_SEM is None:
        _CREATE_SEM = asyncio.Semaphore(
            int(_getenv("TT_DAYTONA_CREATE_CONCURRENCY", default="16"))
        )
    return _CREATE_SEM


async def _get_shared_client(*, api_key: str | None, api_url, target):
    global _SHARED_CLIENT, _SHARED_CLIENT_LOCK
    import asyncio

    from daytona import AsyncDaytona, DaytonaConfig  # type: ignore

    if not api_key:
        raise RuntimeError(
            "DAYTONA_API_KEY is not set; required for the daytona sandbox backend."
        )
    if _SHARED_CLIENT_LOCK is None:
        _SHARED_CLIENT_LOCK = asyncio.Lock()
    async with _SHARED_CLIENT_LOCK:
        if _SHARED_CLIENT is None:
            cfg = DaytonaConfig(api_key=api_key, api_url=api_url, target=target)
            _SHARED_CLIENT = AsyncDaytona(cfg)
    return _SHARED_CLIENT


class DaytonaSandbox:
    """Async sandbox over a Daytona cloud sandbox (https://daytona.io).

    Daytona builds a snapshot wrapping any public image on first ``create`` and
    runs ``process.exec`` as the image's default user. R2E-Gym images default to
    ``root`` with the repo interpreter on ``PATH``, so ``exec`` runs as root and
    drops to an unprivileged user via ``runuser`` when asked. ``fs`` writes as
    root, so ``write_file`` uploads then chowns.

    Env knobs:
      ``DAYTONA_API_KEY``                  -- API key (required).
      ``DAYTONA_API_URL`` / ``DAYTONA_TARGET`` -- override cloud endpoint/region.
      ``TT_DAYTONA_CPU``                   -- vCPUs per sandbox (default 2). Fallback
                                               when no per-task ``cpu`` is given.
      ``TT_DAYTONA_MEM_GB``                -- memory GiB per sandbox (default 4).
                                               Fallback when no per-task ``memory``.
      ``TT_DAYTONA_DISK_GB``               -- disk GiB per sandbox (default 6).
                                               Fallback when no per-task ``disk_gb``.
      ``TT_DAYTONA_MAX_MEM_GB``            -- per-sandbox memory cap the platform
                                               enforces (default 8). A larger
                                               per-task request is clamped to it
                                               rather than refused at create.
      ``TT_DAYTONA_CREATE_TIMEOUT``        -- snapshot-build/boot wait (default 900s).
      ``TT_DAYTONA_EXEC_TIMEOUT_MIN``       -- SDK request timeout floor; does not
                                               extend the command runtime limit.
      ``TT_DAYTONA_RPC_RETRIES``            -- retries for idempotent RPCs; default 2.
      ``TT_DAYTONA_HEARTBEAT_SEC``          -- activity refresh interval; default
                                               180s, or 0 to disable.
      ``TT_DAYTONA_TTL_MIN``                -- hard wall-clock lifetime in minutes,
                                               counted from creation whatever the
                                               sandbox's state; 0 (default)
                                               disables it. The only reaper that
                                               reaches a sandbox that never
                                               started (BUILD_FAILED, ERROR),
                                               which auto-stop and auto-delete
                                               both miss. It deletes LIVE
                                               sandboxes at the same age, so set
                                               it above the longest legitimate
                                               rollout, not near it.
    """

    api_key_env = ("DAYTONA_API_KEY",)
    api_url_env = ("DAYTONA_API_URL",)
    target_env = ("DAYTONA_TARGET",)

    def __init__(
        self,
        image: str,
        *,
        dockerfile: str | None = None,
        build_context: dict[str, str] | None = None,
        timeout: int | None = None,
        cpu: int | None = None,
        memory: int | None = None,
        disk_gb: int | None = None,
        issue_tracker: SandboxIssueTracker | None = None,
        failure_diagnostics_dir: Path | None = None,
        labels: dict[str, str] | None = None,
        **_ignored,
    ) -> None:
        # Per-task overrides for vCPU / memory (GiB) / disk (GiB). None means fall
        # back to the TT_DAYTONA_{CPU,MEM_GB,DISK_GB} env defaults at create time.
        for name, val in (("cpu", cpu), ("memory", memory), ("disk_gb", disk_gb)):
            if val is not None and (
                isinstance(val, bool) or not isinstance(val, int) or val <= 0
            ):
                raise ValueError(
                    f"daytona {name} must be a positive integer, got {val!r}"
                )
        if not image and not dockerfile:
            raise ValueError("daytona sandbox needs either an image or a dockerfile")
        if build_context and not dockerfile:
            raise ValueError("build_context is only meaningful with a dockerfile")
        # Per-sandbox labels (task, group, rollout, run) so a sandbox in the
        # cloud console -- above all a BUILD_FAILED corpse, which the SDK's
        # list() returns without its Dockerfile -- can be traced back to what
        # created it without a per-id get(). Merged under HARNESS_LABELS at
        # create time; ``owner`` stays the sweep's tenant key and is not
        # overridable from here.
        for key, val in (labels or {}).items():
            if not isinstance(key, str) or not isinstance(val, str):
                raise ValueError(
                    f"daytona labels must map str to str, got {key!r}: {val!r}"
                )
        self.labels = dict(labels or {})
        self.image = image
        self.dockerfile = dockerfile
        self.build_context = build_context
        self.timeout = timeout
        self.cpu = cpu
        self.mem = memory
        self.disk_gb = disk_gb
        self.allocated_disk_gb: int | None = None
        self.issue_tracker = issue_tracker or SandboxIssueTracker()
        self._failure_diagnostics_dir = failure_diagnostics_dir
        self._started_at = datetime.now(timezone.utc).isoformat()
        # Daytona is optional and imported lazily, so its SDK types are not
        # available for static annotations in this module.
        self._client: Any = None
        self._sb: Any = None
        self._dockerfile_dir: Any = None
        self.sandbox_id = ""
        self._heartbeat_task: Any = None
        self._lost_error: BaseException | None = None
        # Result files of the last command, deleted by the next launch.
        self._stale_result_paths: tuple[str, ...] = ()
        # Timings and sizes of the last exec, for the adapter's per-command trace.
        self.last_exec_stats: dict[str, Any] = {}

    def _record_issue(
        self,
        kind: str,
        *,
        phase: str,
        error: BaseException | str,
        recovered: bool = False,
        session_id: str = "",
        command_id: str = "",
        attempt: int | None = None,
        max_attempts: int | None = None,
        exit_code: int | None = None,
        emit_log: bool = True,
    ) -> None:
        message = " ".join(str(error).split())[:1000]
        error_type = type(error).__name__ if isinstance(error, BaseException) else ""
        issue = SandboxIssue(
            provider="daytona",
            kind=kind,
            phase=phase,
            recovered=recovered,
            error_type=error_type,
            message=message,
            sandbox_id=self.sandbox_id,
            session_id=session_id,
            command_id=command_id,
            attempt=attempt,
            max_attempts=max_attempts,
            exit_code=exit_code,
        )
        self.issue_tracker.record(issue)
        if not emit_log:
            return
        context = self.issue_tracker.context
        payload = {
            "event": "sandbox_issue",
            "provider": issue.provider,
            "kind": issue.kind,
            "phase": issue.phase,
            "recovered": issue.recovered,
            "instance_id": context.instance_id,
            "group_id": context.group_id,
            "rollout_id": context.rollout_id,
            "sandbox_id": issue.sandbox_id,
            "image": self.image,
            "disk_gb": self.allocated_disk_gb or self.disk_gb,
            "session_id": issue.session_id,
            "command_id": issue.command_id,
            "attempt": issue.attempt,
            "max_attempts": issue.max_attempts,
            "exit_code": issue.exit_code,
            "error_type": issue.error_type,
            "message": issue.message,
        }
        logger.warning("[sandbox_issue] %s", json.dumps(payload, sort_keys=True))

    def _mark_sandbox_lost(self, error: BaseException, *, phase: str) -> None:
        if self._lost_error is not None:
            return
        self._lost_error = error
        self._record_issue("sandbox_lost", phase=phase, error=error)

    def _raise_if_sandbox_lost(self) -> None:
        if self._lost_error is None:
            return
        raise RuntimeError(
            f"daytona sandbox {self.sandbox_id or '<unknown>'} is no longer available"
        ) from self._lost_error

    async def _retry_idempotent_rpc(
        self,
        call: Callable[[], Awaitable[Any]],
        *,
        phase: str,
        retry_kind: str,
        failed_kind: str,
        missing_kind: str | None = None,
        session_id: str = "",
        command_id: str = "",
    ) -> Any:
        import asyncio
        import random

        retries = int(_getenv("TT_DAYTONA_RPC_RETRIES", default="2"))
        if retries < 0:
            raise ValueError(
                f"TT_DAYTONA_RPC_RETRIES must be non-negative, got {retries}"
            )
        max_attempts = retries + 1
        backoff = 0.5
        for attempt in range(1, max_attempts + 1):
            try:
                return await asyncio.wait_for(call(), timeout=_SESSION_RPC_TIMEOUT_SEC)
            except Exception as error:
                if missing_kind is not None and _is_missing_file_error(error):
                    self._record_issue(
                        missing_kind,
                        phase=phase,
                        error=error,
                        recovered=True,
                        session_id=session_id,
                        command_id=command_id,
                    )
                    return None
                if _is_sandbox_gone_error(error):
                    self._mark_sandbox_lost(error, phase=phase)
                    raise
                terminal = attempt >= max_attempts or not _is_transient_rpc_error(error)
                self._record_issue(
                    failed_kind if terminal else retry_kind,
                    phase=phase,
                    error=error,
                    recovered=not terminal,
                    session_id=session_id,
                    command_id=command_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    emit_log=terminal,
                )
                if terminal:
                    raise
                await asyncio.sleep(backoff * (0.5 + random.random()))
                backoff = min(backoff * 2, 5.0)
        raise AssertionError("idempotent RPC retry loop did not return or raise")

    async def _heartbeat_loop(self, interval_sec: float) -> None:
        import asyncio
        import random

        while True:
            await asyncio.sleep(interval_sec * (0.9 + 0.2 * random.random()))
            try:
                await asyncio.wait_for(
                    self._sb.refresh_activity(), timeout=_SESSION_RPC_TIMEOUT_SEC
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if _is_sandbox_gone_error(error):
                    self._mark_sandbox_lost(error, phase="heartbeat")
                    return
                self._record_issue(
                    "heartbeat_retry",
                    phase="heartbeat",
                    error=error,
                    recovered=True,
                    emit_log=False,
                )

    @property
    def daytona(self):
        """Underlying ``daytona.AsyncSandbox`` (for the fs-relay bridge)."""
        return self._sb

    def _declarative_image(self):
        """Build a Daytona ``Image`` from this task's Dockerfile text.

        Tasks that ship a Dockerfile instead of a published image (e.g. the
        Recursive-Task-Synthesis corpus) are built server-side by Daytona, which
        caches the result -- so only the first sandbox per distinct Dockerfile
        pays the build. ``Image.from_dockerfile`` wants a path and resolves COPY
        sources relative to it, so the build context (when the task has one) is
        materialized next to the Dockerfile first.
        """
        import base64
        import tempfile

        from daytona import Image  # type: ignore

        assert self.dockerfile is not None, "only called on the dockerfile path"
        _keep_proxy_for_context_upload()
        # Kept for the sandbox's lifetime: the SDK reads these paths when it
        # serializes the create request, which happens after this returns.
        self._dockerfile_dir = tempfile.TemporaryDirectory(prefix="tt-dockerfile-")
        root = Path(self._dockerfile_dir.name)
        for rel, b64 in (self.build_context or {}).items():
            # Relative paths only: a COPY source outside the context dir would
            # escape the temp tree and pick up an unrelated host file.
            dest = root / rel
            if not dest.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"build_context path escapes the context dir: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(b64))
        path = root / "Dockerfile"
        # Collapse line-continuations before the SDK sees the Dockerfile: some
        # Daytona SDK versions parse COPY sources line-by-line with shlex.split to
        # decide what to upload, and an instruction split across physical lines with a
        # trailing "\" (a multi-line COPY, or the tmux-install RUN block) then makes
        # shlex raise "No escaped character", failing provisioning for every sibling in
        # the group. Joining "\"+newline into a space is exactly what Docker does, so
        # the built image is unchanged.
        # Docker removes whole-line comments BEFORE joining continuations, so a
        # comment inside a backslash-continued RUN keeps the join alive. This regex does
        # not, and such a comment ends the join early: every following physical line
        # of that RUN then parses as its own instruction and the server-side build
        # fails with "unknown instruction: <first word>" (6 of 667 TerminalWorld
        # rows, each burning its create retries and landing as reward-0 infra
        # failures inside a live group). Drop those lines first, matching Docker.
        # Docker resolves unqualified FROM images through Docker Hub. Make that
        # default explicit because non-interactive builders cannot prompt for a
        # short-name registry choice.
        path.write_text(_prepare_dockerfile_for_daytona(self.dockerfile))
        return Image.from_dockerfile(path)

    async def __aenter__(self) -> DaytonaSandbox:
        import asyncio
        import random

        from daytona import CreateSandboxFromImageParams, Resources  # type: ignore

        self._client = await _get_shared_client(
            api_key=_getenv(*self.api_key_env),
            api_url=_getenv(*self.api_url_env) or None,
            target=_getenv(*self.target_env) or None,
        )
        cpu = (
            self.cpu
            if self.cpu is not None
            else int(_getenv("TT_DAYTONA_CPU", default="2"))
        )
        mem = (
            self.mem
            if self.mem is not None
            else int(_getenv("TT_DAYTONA_MEM_GB", default="4"))
        )
        # Clamp to what the platform allows per sandbox. Daytona rejects an
        # oversized request at CREATE, so a task declaring more than the cap never
        # starts, scores an infra failure, and does so on every single attempt --
        # five TerminalWorld tasks declare 16 GiB against an 8 GiB cap, three of
        # them live, and they had burned 704 rollout slots between them before this.
        # Running at the cap may still OOM, but an OOM is a measurement the
        # `oom_suspect` flag already reports; a refused create measures nothing.
        mem_cap = int(_getenv("TT_DAYTONA_MAX_MEM_GB", default="8"))
        if mem > mem_cap:
            logger.warning(
                "sandbox memory request %d GiB exceeds the per-sandbox cap "
                "(%d GiB); requesting the cap instead",
                mem,
                mem_cap,
            )
            mem = mem_cap
        disk = (
            self.disk_gb
            if self.disk_gb is not None
            else int(_getenv("TT_DAYTONA_DISK_GB", default="6"))
        )
        self.allocated_disk_gb = disk
        create_timeout = float(_getenv("TT_DAYTONA_CREATE_TIMEOUT", default="900"))
        # Cloud-side TTL so an orphan (left by a SIGKILL'd run that never reached
        # __aexit__, e.g. MAST preemption) self-reaps: once it goes idle it
        # auto-stops after auto_stop minutes, then auto-deletes immediately
        # (auto_delete=0). The heartbeat below refreshes cloud activity while a live
        # rollout is waiting on model generation and making no Daytona RPCs.
        auto_stop = int(_getenv("TT_DAYTONA_AUTO_STOP_MIN", default="10"))
        auto_delete = int(_getenv("TT_DAYTONA_AUTO_DELETE_MIN", default="0"))
        default_heartbeat_sec = min(180.0, auto_stop * 20.0) if auto_stop > 0 else 0.0
        heartbeat_sec = float(
            _getenv(
                "TT_DAYTONA_HEARTBEAT_SEC",
                default=str(default_heartbeat_sec),
            )
        )
        if heartbeat_sec < 0:
            raise ValueError(
                f"TT_DAYTONA_HEARTBEAT_SEC must be non-negative, got {heartbeat_sec}"
            )
        if int(_getenv("TT_DAYTONA_RPC_RETRIES", default="2")) < 0:
            raise ValueError("TT_DAYTONA_RPC_RETRIES must be non-negative")
        # Neither auto_stop nor auto_delete reaches a sandbox that never started:
        # both are defined on a RUNNING sandbox going idle, and a BUILD_FAILED or
        # ERROR one is already in a terminal state, so it stays for good. That is
        # where a stopped run's residue actually comes from -- a task whose image
        # does not build leaves one corpse per create retry. ttl_minutes is the
        # only cloud-side reaper that covers it: wall-clock from creation
        # regardless of state (verified on this account 2026-09-07, a
        # BUILD_FAILED sandbox with ttl=2 was gone inside 142s while an
        # otherwise identical one without it stayed).
        #
        # It also kills LIVE sandboxes at the same age, so it is off by default
        # (0) and must be set well above the longest a rollout can legitimately
        # take -- boot allowance + SWE_TIME_BUDGET_SEC + the verifier -- or it
        # takes running work with it.
        ttl_min = int(_getenv("TT_DAYTONA_TTL_MIN", default="0"))
        if ttl_min < 0:
            raise ValueError(f"TT_DAYTONA_TTL_MIN must be non-negative, got {ttl_min}")
        # Some Daytona regions reject non-ephemeral creates outright ("Only
        # ephemeral sandboxes are permitted in this region"). Opt-in via
        # TT_DAYTONA_EPHEMERAL. Ephemeral already means delete-on-stop -- the SDK
        # forces auto_delete_interval to 0, which is sooner than any value we
        # could pass, and warns if one is given ("'ephemeral' and
        # 'auto_delete_interval' cannot be used together") -- so send
        # auto_delete_interval only on the non-ephemeral path. ttl_minutes has no
        # such conflict and goes on both.
        create_kwargs = {}
        if ttl_min > 0:
            create_kwargs["ttl_minutes"] = ttl_min
        if _getenv("TT_DAYTONA_EPHEMERAL", default="0") == "1":
            create_kwargs["ephemeral"] = True
        else:
            create_kwargs["auto_delete_interval"] = auto_delete
        params = CreateSandboxFromImageParams(
            image=self._declarative_image() if self.dockerfile else self.image,
            resources=Resources(cpu=cpu, memory=mem, disk=disk),
            labels={**self.labels, **HARNESS_LABELS},
            auto_stop_interval=auto_stop,
            **create_kwargs,
        )
        # Daytona create transiently 401s a valid key under a concurrent boot burst.
        # Retry with jittered backoff so a wide fanout does not retry in lockstep.
        # The create-concurrency semaphore (held only for the create+boot, released
        # before the rollout runs) keeps the create rate under Daytona's throttle.
        retries = int(_getenv("TT_DAYTONA_CREATE_RETRIES", default="5"))
        backoff = 5.0
        for attempt in range(retries + 1):
            # Hold the create-concurrency semaphore ONLY around the actual create
            # call, never during the backoff sleep. A create that hits a bad node
            # (e.g. sysbox-mgr unavailable) and backs off must release its slot so
            # other rollouts can boot; holding it across the exponential backoff
            # (up to ~135s over 5 retries) collapses the effective create
            # concurrency and starves long-tail groups (0 completed rollouts).
            try:
                async with _create_sem():
                    self._sb = await self._client.create(params, timeout=create_timeout)
                break
            except Exception as e:
                terminal = attempt >= retries
                self._record_issue(
                    "create_failed" if terminal else "create_retry",
                    phase="create",
                    error=e,
                    recovered=not terminal,
                    attempt=attempt + 1,
                    max_attempts=retries + 1,
                )
                if attempt >= retries:
                    raise
                await asyncio.sleep(backoff * (0.5 + random.random()))
                backoff = min(backoff * 2, 60.0)
        self.sandbox_id = self._sb.id
        if heartbeat_sec > 0:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(heartbeat_sec),
                name=f"daytona_heartbeat_{self.sandbox_id}",
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if (
                exc is not None
                and self._sb is not None
                and self._failure_diagnostics_dir
            ):
                from torchtitan.experiments.rl.harness.sandbox.daytona_diagnostics import (
                    collect_failure_diagnostics,
                )

                await collect_failure_diagnostics(
                    self._client,
                    self._sb,
                    self._failure_diagnostics_dir,
                    self._started_at,
                    exc,
                )
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        import asyncio
        import random

        # Delete only this sandbox; never close the process-wide shared client --
        # other concurrent rollouts are still using its pooled connections.
        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._sb is None:
            return
        retries = int(_getenv("TT_DAYTONA_RPC_RETRIES", default="2"))
        if retries < 0:
            raise ValueError(
                f"TT_DAYTONA_RPC_RETRIES must be non-negative, got {retries}"
            )
        max_attempts = retries + 1
        backoff = 0.5
        for attempt in range(1, max_attempts + 1):
            try:
                await asyncio.wait_for(
                    self._client.delete(self._sb),
                    timeout=_SESSION_RPC_TIMEOUT_SEC,
                )
                break
            except Exception as error:
                # Delete is idempotent. A 404 means the desired final state already
                # holds, commonly because Daytona auto-deleted an idle sandbox.
                if _is_sandbox_gone_error(error):
                    break
                terminal = attempt >= max_attempts or not _is_transient_rpc_error(error)
                self._record_issue(
                    "delete_failed" if terminal else "delete_retry",
                    phase="delete",
                    error=error,
                    recovered=not terminal,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    emit_log=terminal,
                )
                if terminal:
                    break
                await asyncio.sleep(backoff * (0.5 + random.random()))
                backoff = min(backoff * 2, 5.0)
        self._raise_if_sandbox_lost()

    async def exec(
        self,
        cmd: str,
        *,
        user: str = "root",
        env: dict[str, str] | None = None,
        timeout: int = 120,
        check: bool = False,
    ) -> ExecResult:
        import os

        self._raise_if_sandbox_lost()
        if timeout <= 0:
            raise ValueError(f"daytona exec timeout must be positive, got {timeout}")

        # The SDK timeout only controls the host->Daytona request. Keep its
        # high-latency floor separate from the in-sandbox command deadline.
        request_timeout = max(
            _DEFAULT_EXEC_REQUEST_TIMEOUT_SEC,
            int(os.environ.get("TT_DAYTONA_EXEC_TIMEOUT_MIN", "0")),
        )

        # The original command remains opaque even when it contains quotes or
        # newlines, and is decoded into a tmpfs script inside the sandbox.
        full = _build_exec_command(cmd, user=user, env=env, timeout=timeout)
        num_issues_before = self.issue_tracker.num_events
        try:
            rc, out = await self._detached_exec(
                full,
                command_timeout=timeout,
                request_timeout=request_timeout,
            )
        except Exception as e:
            if self.issue_tracker.num_events == num_issues_before:
                # The Toolbox needs a directory for every command it runs
                # ("failed to create log directory: mkdir ...: no space left on
                # device"); on a full sandbox that surfaces here, after the
                # session exists, as a plain API error. It is the same disk
                # exhaustion the session-create path records, and the rollouter
                # scores it as the agent's outcome only under that kind.
                if "no space left on device" in str(e).lower():
                    self._record_issue(
                        "command_disk_exhausted", phase="exec", error=e
                    )
                else:
                    self._record_issue("exec_failed", phase="exec", error=e)
            raise
        out_lower = out.lower()
        if rc != 0 and (
            "no space left on device" in out_lower or "errno 28" in out_lower
        ):
            self._record_issue(
                "command_disk_exhausted",
                phase="command",
                error=f"command exited {rc}: {out[:400]}",
                exit_code=rc,
            )
        # Match Open-Instruct's backend convention: GNU timeout reserves 124 for
        # the synthetic timeout diagnostic. Forced SIGKILL remains raw 137.
        err = f"Command timed out after {timeout}s." if rc == 124 else ""
        if rc == 124:
            self._record_issue(
                "command_timeout",
                phase="command",
                error=err,
                exit_code=rc,
            )
        if check and rc != 0:
            detail = out + (f"\n{err}" if out and err else err)
            raise RuntimeError(
                f"daytona exec failed (exit={rc}): {cmd[:120]}\n{detail[:400]}"
            )
        return rc, out, err

    async def _detached_exec(
        self,
        full: str,
        *,
        command_timeout: int,
        request_timeout: int,
    ) -> tuple[int, str]:
        """Launch one command detached, then read its result from /dev/shm.

        The launch is a one-shot ``process.exec`` that backgrounds the wrapper
        and returns at once, so no Daytona session is created: the Toolbox
        implements a session as directories on the sandbox's own disk, and a
        task that filled that disk used to cut the harness off from a sandbox
        that was still alive. A relaunch after a lost response is safe because
        the wrapper claims its command key on /dev/shm before doing anything;
        the duplicate exits and the original's status file settles the run.

        The wrapper writes the foreground exit code atomically, so completion
        never depends on the provider's view of a process tree; a foreground
        shell that started a background daemon still completes.
        """
        import asyncio
        import random
        import uuid

        del request_timeout  # the launch returns at once; the command runs detached
        command_key = uuid.uuid4().hex
        observable = _build_observable_exec(full, command_key)
        uploaded = (
            len(observable.inline_command.encode()) > _EXEC_INLINE_COMMAND_LIMIT_BYTES
        )
        if uploaded:
            await self._retry_idempotent_rpc(
                lambda: self._sb.fs.upload_file(
                    observable.wrapper,
                    observable.wrapper_path,
                    _SESSION_RPC_TIMEOUT_SEC,
                ),
                phase="command_upload",
                retry_kind="file_upload_retry",
                failed_kind="file_upload_failed",
            )
        launch = observable.launch(uploaded=uploaded, stale=self._stale_result_paths)
        self._stale_result_paths = ()
        status_path = observable.status_path
        output_path = observable.output_path

        loop = asyncio.get_running_loop()
        deadline = (
            loop.time()
            + command_timeout
            + _COMMAND_KILL_GRACE_SEC
            + _SESSION_POLL_GRACE_SEC
        )

        launched = False
        submission_error: Exception | None = None
        inline: tuple[int, bytes] | None = None
        stats: dict[str, Any] = {"inline": False, "launch_s": 0.0, "wait_s": 0.0, "read_s": 0.0}
        started = loop.time()

        def _parse_inline(response: Any) -> tuple[int, bytes] | None:
            text = getattr(response, "result", None) or ""
            if not text.startswith(_LAUNCH_INLINE_PREFIX):
                return None
            head, _, rest = text[len(_LAUNCH_INLINE_PREFIX):].partition("\n")
            try:
                code = int(head.strip())
                return code, base64.b64decode("".join(rest.split()))
            except ValueError:
                return None

        async def read_status(*, final: bool = False) -> int | None:
            remaining = deadline - loop.time()
            if remaining <= 0 and not final:
                return None
            rpc_timeout = (
                _FINAL_RESULT_PROBE_TIMEOUT_SEC
                if final
                else min(float(_SESSION_RPC_TIMEOUT_SEC), remaining)
            )
            self._raise_if_sandbox_lost()
            try:
                await asyncio.wait_for(
                    self._sb.fs.get_file_info(status_path),
                    timeout=rpc_timeout,
                )
                raw_status = await asyncio.wait_for(
                    self._sb.fs.download_file(
                        status_path,
                        max(1, int(rpc_timeout)),
                    ),
                    timeout=rpc_timeout,
                )
            except asyncio.TimeoutError as e:
                self._record_issue(
                    "poll_transient",
                    phase="command_poll",
                    error=e,
                    recovered=True,
                    command_id=command_key,
                    emit_log=False,
                )
                return None
            except Exception as e:
                if _is_missing_file_error(e):
                    return None
                if _is_sandbox_gone_error(e):
                    self._mark_sandbox_lost(e, phase="command_poll")
                    raise
                if _is_transient_rpc_error(e):
                    self._record_issue(
                        "poll_transient",
                        phase="command_poll",
                        error=e,
                        recovered=True,
                        command_id=command_key,
                        emit_log=False,
                    )
                    return None
                raise
            if isinstance(raw_status, bytes):
                status_text = raw_status.decode("utf-8", errors="replace").strip()
            else:
                status_text = str(raw_status).strip()
            try:
                return int(status_text)
            except ValueError as e:
                self._record_issue(
                    "command_status_invalid",
                    phase="command_poll",
                    error=f"invalid command status {status_text!r}",
                    command_id=command_key,
                )
                raise RuntimeError(
                    f"daytona command wrote invalid status {status_text!r}"
                ) from e

        exit_code = None
        for attempt, delay in enumerate(_COMMAND_SUBMIT_DELAYS_SEC):
            if attempt:
                remaining = deadline - loop.time()
                delay *= 0.5 + random.random()
                if remaining <= delay:
                    break
                await asyncio.sleep(delay)
                # The lost response may have launched the wrapper after all.
                exit_code = await read_status()
                if exit_code is not None:
                    break
                if loop.time() >= deadline:
                    break
            self._raise_if_sandbox_lost()
            try:
                t_launch = loop.time()
                response = await asyncio.wait_for(
                    self._sb.process.exec(launch, timeout=_LAUNCH_RPC_TIMEOUT_SEC),
                    timeout=max(
                        0.001,
                        min(
                            float(_LAUNCH_RPC_TIMEOUT_SEC) + 5.0,
                            deadline - loop.time(),
                        ),
                    ),
                )
                stats["launch_s"] += loop.time() - t_launch
                launched = True
                inline = _parse_inline(response)
                break
            except Exception as e:
                if _is_sandbox_gone_error(e):
                    self._mark_sandbox_lost(e, phase="execute_submit")
                    raise
                if not _is_transient_rpc_error(e):
                    raise
                submission_error = e
            exit_code = await read_status(final=loop.time() >= deadline)
            if exit_code is not None:
                break
            if attempt + 1 < len(_COMMAND_SUBMIT_DELAYS_SEC):
                self._record_issue(
                    "execute_submit_retry",
                    phase="execute_submit",
                    error=submission_error,
                    command_id=command_key,
                    attempt=attempt + 1,
                    max_attempts=len(_COMMAND_SUBMIT_DELAYS_SEC),
                    emit_log=False,
                )
        if inline is not None:
            exit_code, output = inline
            stats["inline"] = True
        if exit_code is None:
            exit_code = await read_status()
        polls = 0
        while exit_code is None:
            if loop.time() >= deadline:
                exit_code = await read_status(final=True)
                if exit_code is not None:
                    break
                if submission_error is not None and not launched:
                    self._record_issue(
                        "execute_response_unconfirmed",
                        phase="execute_submit",
                        error=submission_error,
                        command_id=command_key,
                    )
                    raise submission_error
                self._record_issue(
                    "command_status_timeout",
                    phase="command_poll",
                    error="command status remained unavailable until the deadline",
                    command_id=command_key,
                )
                raise TimeoutError(
                    "daytona exec status unavailable after "
                    f"{command_timeout + _COMMAND_KILL_GRACE_SEC + _SESSION_POLL_GRACE_SEC:.0f}s "
                    f"without replaying the command body; cmd={full[:80]}"
                )
            delay = (
                _RESULT_POLL_DELAYS_SEC[polls]
                if polls < len(_RESULT_POLL_DELAYS_SEC)
                else _RESULT_POLL_INTERVAL_SEC
            )
            await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))
            exit_code = await read_status()
            polls += 1

        if submission_error is not None:
            self._record_issue(
                "execute_response_recovered",
                phase="execute_submit",
                error=submission_error,
                recovered=True,
                command_id=command_key,
                emit_log=False,
            )

        if inline is None:
            stats["wait_s"] = loop.time() - started - stats["launch_s"]
            t_read = loop.time()
            output = await self._retry_idempotent_rpc(
                lambda: self._sb.fs.download_file(output_path, _SESSION_RPC_TIMEOUT_SEC),
                phase="command_output",
                retry_kind="command_output_retry",
                failed_kind="command_output_failed",
                missing_kind="command_output_missing",
                command_id=command_key,
            )
            stats["read_s"] = loop.time() - t_read
            if output is None:
                output = _MISSING_OUTPUT_MESSAGE
        # Both files are read; the next launch removes them from /dev/shm.
        self._stale_result_paths = (status_path, output_path)
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output = str(output)
        stats["output_bytes"] = len(output.encode("utf-8", errors="replace"))
        stats["truncated"] = "[torchtitan: command output truncated]" in output
        stats["total_s"] = loop.time() - started
        self.last_exec_stats = stats
        return exit_code, output

    async def write_file(
        self, sandbox_path: str, content: FileContent, *, user: str = "root"
    ) -> None:
        import os

        parent = os.path.dirname(sandbox_path) or "/"
        await self.exec(f"mkdir -p {shlex.quote(parent)}", user="root", check=False)
        if isinstance(content, Path):
            data = content.read_bytes()
        elif isinstance(content, bytes):
            data = content
        else:
            data = str(content).encode("utf-8")
        # fs.upload_file writes as root; chown afterwards for non-root owners.
        await self._retry_idempotent_rpc(
            lambda: self._sb.fs.upload_file(data, sandbox_path),
            phase="write_file",
            retry_kind="file_upload_retry",
            failed_kind="file_upload_failed",
        )
        if user and user != "root":
            await self.exec(
                f"chown {shlex.quote(user)}:{shlex.quote(user)} {shlex.quote(sandbox_path)}",
                user="root",
                check=False,
            )

    async def read_file(self, sandbox_path: str, *, user: str = "root") -> str:
        # root can read any file; the user arg is accepted for protocol parity.
        #
        # Download the bytes rather than `cat` them out of a shell. exec merges
        # stderr into stdout -- deliberately, since an agent driving a terminal
        # has to see its errors -- so anything the image prints when a shell
        # starts arrives ahead of the file's contents and is indistinguishable
        # from them. grade_tmax compares a nonce it wrote here for exact
        # equality, to refuse a reward whose verifier never ran; on an image
        # whose bash greets every command with a setlocale warning the
        # comparison can never hold, and the task scores 0 before its verifier
        # is even started. Measured across the corpus: one task in 1,062, which
        # is the wrong number to reason from -- the noise can come from anything
        # a login shell touches, and the next such image is silently mis-scored.
        #
        # write_file already uploads through fs, and the exec machinery above
        # downloads its own status and output the same way. This was the only
        # file operation that went out through a shell.
        # The old body returned "" for any non-zero `cat`, so a caller that
        # cannot read a file still gets a string. Keep that contract -- callers
        # were written against it and a raise here would turn a graded rollout
        # into an infra failure -- while letting a lost sandbox propagate, which
        # `cat` did too.
        try:
            data = await self._retry_idempotent_rpc(
                lambda: self._sb.fs.download_file(
                    sandbox_path, _SESSION_RPC_TIMEOUT_SEC
                ),
                phase="read_file",
                retry_kind="file_download_retry",
                failed_kind="file_download_failed",
                missing_kind="file_download_missing",
            )
        except Exception as error:  # noqa: BLE001
            if _is_sandbox_gone_error(error):
                raise
            return ""
        if data is None:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)
