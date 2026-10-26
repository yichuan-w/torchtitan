#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Agentic retune: change a task with a Codex or Claude CLI session.

The agent reads the full rollout traces as files in the package and follows
the role in AGENTS.md. It can inspect the task, test changes and revise them.

The session takes a task dict and returns the rewritten package as a task dict.
The loop then revalidates that package before accepting it.

Where it works: the rewrite's own `package/` (LAYOUT.md). The loop copies the
input revision there and hardlinks the rollout records under `traces/`; the
agent's cwd is that directory and its edits land in place. Every codex
invocation is one `sessions/<stamp>--<kind>/` beside it: the prompt, both
streams, `session.json`, and a private `codex/` home pruned to the CLI's
session jsonl. Nothing the harness records about a session is in the agent's
view, and nothing the agent writes is outside the package.

Runs Codex LOCALLY on della (the agent reads and rewrites here; the task's own
container it reaches through ./sandbox, on Daytona). Auth mirrors
solve_daytona's verified incantation: a fresh CODEX_HOME (a stray ChatGPT token
otherwise wins and 401s), a model_provider whose base_url is the same us.api
endpoint synth_client uses, and the key injected via env.
"""

from __future__ import annotations

import contextlib
import difflib
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import logging
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import evolve as ev
import simplify_operators as so
import synth_client as llm
import task_size as ts
import verifier_literals as vl
from rewrite_deadline import RewriteDeadline
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record
from torchtitan.experiments.rl.examples.tmax.evolution_metrics import REPAIR_KINDS
from verifier_probes import (
    run_executable,
    SemanticProbeContract,
    SemanticProbeMisses,
    load_probe_contract,
    verify_probes,
)


class Filtered(RuntimeError):
    """The provider's cybersecurity classifier stopped the session.

    Codex exits 1 with "flagged for possible cybersecurity risk" on stderr and
    writes nothing to stdout. It is not a verdict on the task: the flag fires
    late, once the session's context carries the task's own material (a
    crackme's disassembly, angr's symbolic-execution output), and it is
    probabilistic -- over the three seeds it has ever hit, 11 of 21
    sessions were stopped and 10 ran to the end, four of those producing a
    usable rewrite. So the answer is a fresh session, not a dead task; a
    resumed one would carry the flagged context straight back.
    """


class AgentSessionError(RuntimeError):
    """The agent session ended without satisfying its execution contract."""

    def __init__(
        self,
        *,
        agent: str,
        failure_type: str,
        returncode: int,
        stderr_path: Path,
        result: subprocess.CompletedProcess | None = None,
        detail: str | None = None,
    ) -> None:
        self.agent = agent
        self.failure_type = failure_type
        self.returncode = returncode
        self.stderr_path = stderr_path
        self.result = result
        self.detail = detail
        detail_text = f": {detail}" if detail else ""
        super().__init__(
            f"{agent} session failed ({failure_type}, exit {returncode}); "
            f"{agent} exited {returncode}{detail_text}; see {stderr_path}"
        )


class AgentValidationError(RuntimeError):
    """The agent stopped without a usable passing self-check."""

    def __init__(self, failure_type: str, detail: str, *, returncode: int = 0) -> None:
        self.failure_type = failure_type
        self.returncode = returncode
        super().__init__(detail)


CYBER_FLAG = "flagged for possible cybersecurity risk"
CYBER_RETRIES = int(os.environ.get("CODEX_CYBER_RETRIES", "2"))


class Blocked(Exception):  # noqa: N818 -- existing exception name used by callers
    """The agent declined the job rather than forcing a pass.

    Distinct from a crash: nothing went wrong, the task simply stays as it is.
    `report` is the whole verdict; the message keeps its first 200 characters.
    """

    report = ""


# EVOLVE_CODEX_HARNESS=local ("local-harness"): sessions run the codex CLI
# installed on this machine (EVOLVE_CODEX_LOCAL_BIN, else `codex` on PATH) with
# its own sign-in and provider. No provider override is passed, so the CLI
# reaches whatever endpoint it is configured for; the chat calls keep
# SYNTH_API_BASE / SYNTH_MODEL. EVOLVE_CODEX_MODEL names the sessions' model
# when it differs from SYNTH_MODEL.
LOCAL_HARNESS = os.environ.get("EVOLVE_CODEX_HARNESS", "") == "local"
CODEX_MODEL = os.environ.get("EVOLVE_CODEX_MODEL") or os.environ.get(
    "SYNTH_MODEL", "gpt-5.6-sol"
)
# Same knob as the chat calls: high unless SYNTH_EFFORT says otherwise. Left
# unset, the CLI ran the sessions at its own default, which the session log
# records as reasoning_effort=null.
CODEX_EFFORT = llm.EFFORT
API_BASE = os.environ.get("SYNTH_API_BASE", "https://us.api.openai.com/v1")
ACCOUNT_HOME = os.environ.get("EVOLVE_CODEX_ACCOUNT_HOME")
# Turn budget the agent is told to respect; the hard cap is the subprocess
# timeout below. "deadline in the prompt" per the design.
MAX_TOOL_CALLS = int(os.environ.get("CODEX_RETUNE_MAX_CALLS", "25"))
# Which driver runs a session. "exec" is `codex exec`, a batch: whatever the
# agent got wrong is found by the caller afterwards and costs another session.
# "sdk" drives the app-server protocol through codex_session.py, which watches
# the package as it changes and steers the running turn when the rewrite
# leaves a rule -- the correction arrives at minute three instead of in the
# next session. It needs the openai-codex SDK, which lives in its own
# virtualenv (TRL_SDK_PY) rather than the training one.
CODEX_DRIVER = os.environ.get("EVOLVE_CODEX_DRIVER", "exec")
# Which CLI runs a session. "codex" is the original and the default. "claude"
# drives Claude Code headlessly (`claude -p`), which authenticates with the
# user's own subscription rather than an API key -- the reason it exists: the
# claude_proxy/ route reaches Claude too, but only through LiteLLM with an
# Anthropic API key. Both arms share this module's session directory, prompt,
# stream-to-disk and resume contract; only the argv, the environment and the
# way the role reaches the model differ.
def _default_agent() -> str:
    """Which CLI a run drives, from the environment.

    ``SWE_RETUNE_AGENT=claude`` is the ordinary spelling: one name picks the
    agentic arm (feedback_loop) and the CLI it runs (here). EVOLVE_AGENT
    overrides it for a caller that wants the arm named one way and the CLI
    another.
    """
    if agent := os.environ.get("EVOLVE_AGENT"):
        return agent
    return "claude" if os.environ.get("SWE_RETUNE_AGENT") == "claude" else "codex"


EVOLVE_AGENT = _default_agent()
# Claude Code's own model selection: an alias ("opus", "sonnet") or a full
# name. SYNTH_MODEL names an OpenAI model for the codex arm, so this is a
# separate knob rather than a shared one.
CLAUDE_MODEL = os.environ.get("EVOLVE_CLAUDE_MODEL", "opus")
# A model per session kind, over the arm's default above:
# "verifier=claude-sonnet-5,probe=claude-sonnet-5". Unset, every kind runs the
# default. A session that resumes another keeps that session's model, so a
# repair continues its thread on the model that started it.
MODEL_BY_KIND = dict(
    kv.split("=", 1)
    for kv in os.environ.get("EVOLVE_MODEL_BY_KIND", "").split(",")
    if kv.strip()
)
SDK_PY = os.environ.get(
    "TRL_SDK_PY", "/scratch/gpfs/TRIDAO/al9080/terminal-rl/sdkvenv/bin/python"
)
SESSION_DRIVER = Path(__file__).resolve().parent / "codex_session.py"
TIMEOUT_SEC = int(os.environ.get("CODEX_RETUNE_TIMEOUT", "600"))
# What the harness puts into the package and takes out again before a revision
# is kept: the role file, the container tool, the harness's scratch directory
# and the hardlinked rollout records. Everything else in the package travels.
HARNESS = ("AGENTS.md", "sandbox", "run", "traces")
SCAFFOLD = {"AGENTS.md", "sandbox"}


def _tool_bin() -> Path:
    """The pinned codex and jq directory, staged locally when configured."""
    override = os.environ.get("EVOLVE_TOOL_BIN")
    return Path(override) if override else layout.Root.from_env().bin


def _codex_bin() -> Path:
    if LOCAL_HARNESS:
        local = os.environ.get("EVOLVE_CODEX_LOCAL_BIN") or shutil.which("codex")
        if not local:
            raise FileNotFoundError(
                "EVOLVE_CODEX_HARNESS=local but no codex CLI: set EVOLVE_CODEX_LOCAL_BIN"
            )
        return Path(local)
    return _tool_bin() / "codex"


def _claude_bin() -> Path:
    return _tool_bin() / "claude"


def _agent_bin() -> Path:
    """The CLI this run drives, from the same ``$TRL_BASE/bin`` convention."""
    return _claude_bin() if EVOLVE_AGENT == "claude" else _codex_bin()


def _require_codex() -> None:
    if EVOLVE_AGENT not in ("codex", "claude"):
        raise ValueError(f"EVOLVE_AGENT must be codex or claude, got {EVOLVE_AGENT!r}")
    if not _agent_bin().exists():
        raise RuntimeError(f"{EVOLVE_AGENT} binary not found at {_agent_bin()}")


# --------------------------------------------------------------------------
# One codex invocation, one session directory
# --------------------------------------------------------------------------


@dataclass
class SessionRun:
    """A session while it runs: its directory and the record being built."""

    dir: layout.SessionDir
    meta: dict = field(default_factory=dict)


def _new_session_dir(rewrite: layout.RewriteDir, kind: str) -> layout.SessionDir:
    """The next session name. Stamps are seconds; a second session of one kind
    inside the same second takes the next second's name rather than a random
    suffix, so names stay sortable and say what they are."""
    t = time.time()
    s = rewrite.session(kind, layout.stamp(t))
    while s.path.exists():
        t += 1
        s = rewrite.session(kind, layout.stamp(t))
    return s


def _prune_private_home(home: Path) -> None:
    """Keep the CLI's session jsonl and discard its transient client state.

    Each invocation has a private ``CODEX_HOME``. Files outside ``sessions/``
    are caches the client rebuilds; the jsonl is the transcript.

    Claude Code keeps its transcript somewhere else under the config dir, so
    the same rule would delete the thread a later `--resume` needs. Its home
    is left alone; it is small, and the whole session directory is the record.

    The caches (~4k files) are renamed out of the home at once and deleted in
    the background: deleting them inline took up to 80 s on the rewrite's
    critical path. ``drain_cache_cleanup`` waits for them before publication.
    """
    if EVOLVE_AGENT == "claude":
        return
    if not home.is_dir():
        return
    trash = None
    for child in home.iterdir():
        if child.name == "sessions":
            continue
        if child.is_dir() and not child.is_symlink():
            if trash is None:
                trash = home.parent / f"{CACHE_TRASH_PREFIX}{uuid.uuid4().hex}"
                trash.mkdir()
            child.rename(trash / child.name)
        else:
            child.unlink(missing_ok=True)
    if trash is not None:
        _delete_in_background(trash)


# Session-directory entries holding a pruned home's caches until deleted.
CACHE_TRASH_PREFIX = ".codex-cache-"
_TRASH_LOCK = threading.Lock()
_TRASH: dict[Path, Future] = {}
# Caps concurrent cache deletions; each walks ~4k small files.
_TRASH_SLOTS = threading.BoundedSemaphore(
    int(os.environ.get("EVOLVE_CACHE_DELETE_CONCURRENCY", "8"))
)


def _delete_in_background(trash: Path) -> None:
    """Delete one trash directory on its own thread. A shared pool queued every
    rewrite's deletions behind every other's, so a rewrite's drain waited for
    the whole backlog."""
    done: Future = Future()

    def delete() -> None:
        try:
            with _TRASH_SLOTS:
                shutil.rmtree(trash, ignore_errors=True)
        finally:
            done.set_result(None)

    with _TRASH_LOCK:
        _TRASH[trash] = done
    threading.Thread(target=delete, name="codex-cache").start()


def drain_cache_cleanup(under: Path) -> None:
    """Wait for the background cache deletions of session homes below ``under``
    (this rewrite's own; other rewrites' deletions are not waited for)."""
    with _TRASH_LOCK:
        pending = [(p, f) for p, f in _TRASH.items() if p.is_relative_to(under)]
    for path, future in pending:
        future.result()
        with _TRASH_LOCK:
            _TRASH.pop(path, None)


def _link_session_jsonl(src: layout.SessionDir, dst: layout.SessionDir) -> None:
    """Hardlink the recorded session into the resuming session's home.

    ``codex exec resume <id>`` looks the thread up under its own CODEX_HOME
    and appends to the same rollout file, so the resumed session has to find
    it there. A hardlink keeps both directories on one inode: the original
    session's ``codex/`` and the repair's read the same transcript as it
    grows, and each session directory still holds the jsonl of the thread it
    ran.
    """
    sessions = src.codex_home / "sessions"
    for f in sessions.rglob("*.jsonl"):
        layout.link_or_copy(f, dst.codex_home / "sessions" / f.relative_to(sessions))


def cyber_filtered(session: layout.SessionDir) -> bool:
    """Whether the provider's classifier stopped this session.

    Both streams, since codex prints the refusal on stderr today and would
    carry it as an `item.type: "error"` event on stdout under `--json`.
    """
    for stream in (session.stderr, session.stdout):
        try:
            if CYBER_FLAG in stream.read_text(errors="replace"):
                return True
        except OSError:
            pass
    return False


def _claude_usage(sd: layout.SessionDir) -> dict | None:
    """What one Claude Code session spent, from the CLI's own result object.

    ``claude -p --output-format json`` prints a single object whose `usage`
    and `total_cost_usd` describe the whole session. Nothing else records it:
    the `usage` on a rewrite comes from synth_client, which an agentic arm
    never calls, so without this a run cannot say what a rewrite cost.

    `cost_usd` is the CLI's own figure. On a subscription it is what the same
    tokens would have cost at list price, not money billed -- useful for
    comparing arms, not for an invoice.

    Returns None when the stream is absent, truncated or not the result
    object: a session that already ran must not fail over its accounting.
    """
    try:
        data = json.loads(sd.stdout.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    u = data.get("usage")
    u = u if isinstance(u, dict) else {}
    out = {
        "cost_usd": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
        "duration_ms": data.get("duration_ms"),
        "input_tokens": u.get("input_tokens"),
        "output_tokens": u.get("output_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
    }
    return out if any(v is not None for v in out.values()) else None


@contextlib.contextmanager
def session(
    rewrite: layout.RewriteDir,
    kind: str,
    *,
    timeout: int,
    resumes: layout.SessionDir | None = None,
):
    """One codex invocation: create its directory, record it at start and end.

    ``kind`` is agent, repair, verifier or oracle (LAYOUT.md). ``session.json``
    is written with status ``running`` before anything else, so a loop killed
    mid-session leaves a record that says so and finalize_interrupted_traces
    can mark it. ``resumes`` names the session whose thread this one
    continues; its jsonl is hardlinked in (see ``_link_session_jsonl``).

    The block covers the invocation and its teardown only. What the caller
    decides afterwards -- verdict, checks, whether the rewrite is kept -- is
    the rewrite's record, not the session's.
    """
    sd = _new_session_dir(rewrite, kind)
    default_model = CLAUDE_MODEL if EVOLVE_AGENT == "claude" else CODEX_MODEL
    if resumes is not None:
        model = json.loads(resumes.meta.read_text()).get("model") or default_model
    else:
        model = MODEL_BY_KIND.get(kind, default_model)
    sd.path.mkdir(parents=True, mode=0o700)
    # GPFS default ACLs can widen the requested mode. These directories hold
    # the prompt (with the verifier in the package it describes) and the
    # transcript of a session that read the reference solution.
    sd.path.chmod(0o700)
    sd.codex_home.mkdir(mode=0o700)
    meta = {
        "kind": kind,
        "agent": EVOLVE_AGENT,
        "model": model,
        "reasoning_effort": CODEX_EFFORT,
        "driver": "claude-print" if EVOLVE_AGENT == "claude" else CODEX_DRIVER,
        "authentication": _authentication(),
        "started": layout.stamp(),
        "finished": None,
        "status": "running",
        "exit_code": None,
        "error": None,
        "timeout_sec": timeout,
        "filtered": False,
    }
    if EVOLVE_AGENT == "claude":
        # Claude Code names the conversation rather than reporting one back:
        # `--session-id` takes a UUID we choose, so resume does not have to
        # find a transcript on disk the way `_session_id` does for codex.
        meta["claude_session_id"] = str(uuid.uuid4())
    if resumes is not None:
        meta["resumed"] = f"sessions/{resumes.path.name}"
        if EVOLVE_AGENT == "claude":
            # Claude Code looks a conversation up inside CLAUDE_CONFIG_DIR and
            # keeps its own layout there. Rather than copy internals into a
            # fresh home the way _link_session_jsonl does for codex, continue
            # in the home that already holds the thread; this session
            # directory still gets its own prompt, streams and record.
            prior_meta = json.loads(resumes.meta.read_text())
            meta["claude_session_id"] = _session_id(resumes)
            meta["claude_config_dir"] = prior_meta.get("claude_config_dir") or str(
                resumes.codex_home
            )
        else:
            _link_session_jsonl(resumes, sd)
    layout.write_json_atomic(sd.meta, meta)
    run = SessionRun(sd, meta)
    try:
        yield run
    except BaseException as exc:
        if isinstance(exc, Blocked):
            meta["status"] = "blocked"
        elif isinstance(exc, subprocess.TimeoutExpired):
            meta["status"] = "timed_out"
        else:
            meta["status"] = "failed"
        meta["error"] = f"{type(exc).__name__}: {exc}"[:500]
        if failure_type := getattr(exc, "failure_type", None):
            meta["failure_type"] = str(failure_type)
        raise
    else:
        meta["status"] = "completed"
    finally:
        meta["finished"] = layout.stamp()
        meta["filtered"] = cyber_filtered(sd)
        if EVOLVE_AGENT == "claude":
            # After the streams are closed and before the private home is
            # pruned: the result object is in sd.stdout either way, but this
            # keeps the record complete for a session that raised.
            usage = _claude_usage(sd)
            if usage is not None:
                meta["usage"] = usage
        try:
            _prune_private_home(sd.codex_home)
        except OSError as exc:
            meta["cache_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        layout.write_json_atomic(sd.meta, meta)


# --------------------------------------------------------------------------
# Running the CLI
# --------------------------------------------------------------------------


def _harness_env() -> dict:
    """What ./sandbox needs from the environment, codex or not."""
    env = dict(os.environ)
    # ./sandbox is a copy of agent_sandbox.sh dropped into the package, so from
    # there the script cannot find agent_sandbox.py beside itself. Tell it
    # where the harness lives. Without this every session ends in BLOCKED
    # and the loop records it as `kept`.
    env["EVOLVE_HARNESS_DIR"] = str(Path(__file__).resolve().parent)
    # jq sits beside the codex binary, for reading traces/; the loop's own
    # PATH (a systemd unit's) does not carry that directory.
    env["PATH"] = str(_tool_bin()) + os.pathsep + env.get("PATH", "")
    # ./sandbox prefers TRL_VENV_PY over TRL_VENV. An inherited value can name a
    # system Python too old for the harness (a shell profile that exports
    # $TRL_VENV/bin/python before TRL_VENV is set yields /bin/python), and then
    # every sandbox the loop itself boots dies on import while the ones a codex
    # session boots work. The loop already runs on the harness venv, so pin it.
    env["TRL_VENV_PY"] = sys.executable
    return env


def _authentication() -> str:
    """How this run's sessions authenticate, for the session record."""
    if EVOLVE_AGENT == "claude":
        # Claude Code reads its own credentials from CLAUDE_CONFIG_DIR: the
        # subscription's OAuth login when there is one, ANTHROPIC_API_KEY
        # otherwise. The CLI decides; the record only says where it looked.
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic_api_key"
        return "claude_subscription"
    if os.environ.get("EVOLVE_CODEX_AUTH_FILE"):
        return "chatgpt"
    return "api_key"


def _claude_account_home() -> Path:
    """Where Claude Code keeps the login this arm spends.

    ``EVOLVE_CLAUDE_ACCOUNT_HOME`` names it; otherwise the CLI's own default,
    which is the user's ``CLAUDE_CONFIG_DIR`` when they set one and
    ``~/.claude`` when they did not. Read from *this* process's environment,
    which is the human's -- the child's is the private home below.
    """
    return Path(
        os.environ.get("EVOLVE_CLAUDE_ACCOUNT_HOME")
        or os.environ.get("CLAUDE_CONFIG_DIR")
        or (Path.home() / ".claude")
    )


def _claude_env(home: Path) -> dict:
    """The environment for one Claude Code session, run out of ``home``.

    ``CLAUDE_CONFIG_DIR`` is a session's own directory rather than the user's,
    for the same reason ``CODEX_HOME`` is: a session must not inherit another
    one's state, and must not write into the home a human is using. A resuming
    session is the exception and gets the home holding the thread it
    continues.

    The subscription's login does not move into that directory:
    ``CLAUDE_SECURESTORAGE_CONFIG_DIR`` points every session's credential store
    at the account home, so all of them read and refresh one file under the
    CLI's own refresh and write locks, and N concurrent sessions make one
    refresh. A symlinked ``.credentials.json`` does not do that: the CLI writes
    a refresh by temp file and rename, which replaces the link with a private
    file, and the next session's refresh then spends a refresh token the
    server has already rotated away -- which can log the account itself out.
    Verified on Claude Code 2.1.283 (2026-09-27): a session with a private
    ``CLAUDE_CONFIG_DIR`` and this variable refreshed the account's own file
    and left no credential in its home.
    """
    env = _harness_env()
    env["CLAUDE_CONFIG_DIR"] = str(home)
    # The loop's own OpenAI key has no business in a Claude session, and an
    # ANTHROPIC_API_KEY left in the environment would silently bill the API
    # instead of the subscription this arm exists to use.
    env.pop("OPENAI_API_KEY", None)
    if os.environ.get("EVOLVE_CLAUDE_USE_API_KEY") == "1":
        return env
    env.pop("ANTHROPIC_API_KEY", None)
    creds = _claude_account_home() / ".credentials.json"
    if not creds.is_file():
        raise RuntimeError(
            f"no Claude Code login at {creds}: run `claude` and /login, point "
            "EVOLVE_CLAUDE_ACCOUNT_HOME at the home that has one, or set "
            "EVOLVE_CLAUDE_USE_API_KEY=1 with ANTHROPIC_API_KEY to bill the API"
        )
    home.mkdir(parents=True, exist_ok=True)
    env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(creds.parent.resolve())
    return env


def _claude_cmd(cwd: Path, session_id: str, *, resume: bool, model: str) -> list[str]:
    """`claude -p` over the package, with the loop's own settings only.

    ``--setting-sources ''`` keeps a human's user/project settings out of an
    unattended session, the way ``--ignore-user-config`` does for codex.
    ``--permission-prompts none`` makes anything the permission mode would
    still ask about a denial rather than a hang: nobody is at the terminal.
    """
    cmd = [
        str(_claude_bin()),
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--permission-prompts",
        "none",
        "--setting-sources",
        "",
        "--model",
        model,
        "--effort",
        str(CODEX_EFFORT),
        "--add-dir",
        str(cwd),
    ]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    return cmd


def _codex_env(sd: layout.SessionDir) -> dict:
    env = _harness_env()
    env["CODEX_HOME"] = str(sd.codex_home)
    if ACCOUNT_HOME:
        auth = Path(ACCOUNT_HOME).resolve() / "auth.json"
        if not auth.is_file():
            raise RuntimeError(f"account authentication missing: {auth}")
        sd.codex_home.mkdir(parents=True, exist_ok=True)
        # Codex 0.153.4 writes through this link; one credential file owns refresh.
        (sd.codex_home / "auth.json").symlink_to(auth)
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_THREAD_ID", None)
        env.pop("CODEX_SESSION_ID", None)
    elif auth_file := os.environ.get("EVOLVE_CODEX_AUTH_FILE"):
        auth = json.loads(Path(auth_file).read_text())
        if auth.get("auth_mode") != "chatgpt" or not auth.get("tokens", {}).get(
            "access_token"
        ):
            raise ValueError("EVOLVE_CODEX_AUTH_FILE must contain a ChatGPT login")
        target = sd.codex_home / "auth.json"
        target.write_text(json.dumps(auth))
        target.chmod(0o600)
        env.pop("OPENAI_API_KEY", None)
    else:
        env["OPENAI_API_KEY"] = llm._api_key()
    return env


def _extra_config() -> list[str]:
    """Further `-c key=value` overrides for every session, from
    EVOLVE_CODEX_EXTRA_CONFIG, whitespace-separated.

    The CLI's own retry budget is five stream reconnects a few seconds
    apart, which does not survive a proxy that is busy for a minute; the
    knobs for that are provider config, so this is where a run raises them
    (see claude_proxy/claude_env.sh). Whitespace-separated so the value
    survives a systemd EnvironmentFile line."""
    return os.environ.get("EVOLVE_CODEX_EXTRA_CONFIG", "").split()


# EVOLVE_CODEX_WEB_SEARCH=live (the default) gives sessions codex's web tool
# (web__run: search_query / open), which the real hardening guidance uses to
# find sources; set it empty to run sessions without the tool.
WEB_SEARCH = os.environ.get("EVOLVE_CODEX_WEB_SEARCH", "live")


def _provider_overrides() -> list[str]:
    """The provider settings both drivers pass; the SDK takes them as a list."""
    search = [f"web_search={WEB_SEARCH}"] if WEB_SEARCH else []
    if LOCAL_HARNESS:
        # The local CLI's own provider; any override here would bypass it.
        return search + _extra_config()
    if ACCOUNT_HOME or os.environ.get("EVOLVE_CODEX_AUTH_FILE"):
        return ["model_provider=openai"] + search + _extra_config()
    return [
        # codex 0.149 offers the web tool to a custom provider only when its
        # name is exactly "OpenAI" (captured requests, 2026-10-17); "openai"
        # gets none, whatever web_search says.
        f"model_providers.oai.name={'OpenAI' if WEB_SEARCH else 'openai'}",
        f"model_providers.oai.base_url={API_BASE}",
        "model_providers.oai.env_key=OPENAI_API_KEY",
        # The CLI's defaults give up after a few 429s a few seconds apart; a
        # pooled ChatGPT account under a large run rate-limits for longer, and
        # each lost session is a lost rewrite (10-17: ~150 per r100 arm at 72
        # concurrent). EVOLVE_CODEX_EXTRA_CONFIG, passed after, can override.
        "model_providers.oai.request_max_retries=10",
        "model_providers.oai.stream_max_retries=20",
        "model_provider=oai",
    ] + search + _extra_config()


def _session_cmd(run: SessionRun, cwd: Path, *, resume: str | None) -> list[str]:
    cmd = [
        SDK_PY,
        str(SESSION_DRIVER),
        "--pkg",
        str(cwd),
        "--codex-home",
        str(run.dir.codex_home),
        "--events",
        str(run.dir.path / "events.jsonl"),
        "--prompt-file",
        str(run.dir.prompt),
        "--timeout",
        str(run.meta["timeout_sec"]),
        "--model",
        run.meta["model"],
        "--effort",
        str(CODEX_EFFORT),
    ]
    if resume:
        cmd += ["--resume", resume]
    return cmd


def _codex_cmd(cwd: Path, model: str, resume: str | None = None) -> list[str]:
    cmd = [str(_codex_bin()), "exec"]
    if resume:
        cmd += ["resume", resume]
    cmd += [
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
    ]
    for override in _provider_overrides() + [f"model_reasoning_effort={CODEX_EFFORT}"]:
        cmd += ["-c", override]
    if ACCOUNT_HOME:
        # Use the account's authentication, but never its personal configuration.
        cmd += [
            "--ignore-user-config",
            "--json",
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "features.skip_host_skill_discovery=true",
            "-c",
            "features.plugins=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.hooks=false",
            "-c",
            "skills.include_instructions=false",
        ]
    # `exec resume` takes no -C (codex-cli 0.149: it continues in the
    # session's recorded cwd); the subprocess is started in the package
    # either way.
    if not resume:
        cmd += ["-C", str(cwd)]
    cmd += ["-m", model, "-"]
    return cmd


def _read_stream(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _agent_failure_type(stdout: str, stderr: str) -> str:
    """Classify a nonzero agent exit from the streams retained on disk."""
    text = f"{stderr}\n{stdout}".lower()
    if "rate_limit_exceeded" in text or "rate limit exceeded" in text:
        return "provider_rate_limit"
    if "selected model is at capacity" in text:
        return "provider_capacity"
    if (
        "failed to retrieve response from third-party api after" in text
        and "polling attempts" in text
    ):
        return "provider_poll_exhausted"
    if "server_error" in text or "server error" in text:
        return "provider_server_error"
    if any(
        marker in text
        for marker in (
            "transport",
            "connection error",
            "connection reset by peer",
        )
    ):
        return "provider_transport_error"
    if "stream disconnected before completion" in text:
        return "provider_stream_error"
    return f"{EVOLVE_AGENT}_exit_nonzero"


_RUNTIME_INTERFACE = """

Package runtime interface:
- The image builds from environment/Dockerfile. Local COPY/ADD sources are
  relative to environment/, not the package root. A top-level setup.sh is not
  automatically executed; environment setup must be in the image recipe.
- Host edits stay on the host unless copied by the Dockerfile or loaded by a
  sandbox command. oracle reloads solution/; grade reloads tests/. Dockerfile
  and build-context edits require reset. exec preserves container state.
- check, oracle, grade, reset and up can each take minutes. Start them with a
  long wait on the command (codex: yield_time_ms 600000) so the call returns
  with the result; polling a running command spends a turn per poll.
- Once oracle has passed, re-check verifier edits with grade rather than check,
  which rebuilds and replays everything; run check again when solution/ or
  environment/ changed, and to confirm at the end. oracle and check reuse an
  earlier passing result whose inputs are byte-identical, and say so; --fresh
  forces a rerun.
- When the reference solution is present, ./sandbox size reports the current
  size, seed bounds and size violations locally, without booting a container.
  It does not replace ./sandbox check.
- Use these interfaces for routine package work. Inspect harness source only
  when an observed failure cannot be explained by its documented behavior.
"""


def _run_codex(
    run: SessionRun,
    cwd: Path,
    prompt: str,
    *,
    resume: str | None = None,
    _provider_try: int = 0,
) -> subprocess.CompletedProcess:
    """Run codex over ``cwd`` with the session's private CODEX_HOME.

    A stray ChatGPT token in the shared home otherwise wins and 401s, which is
    why the home is per-invocation rather than shared. `resume` continues a
    recorded session (its id) instead of starting one. The prompt goes to
    ``prompt.md`` and to the CLI's stdin; both streams go to disk as the
    process runs.
    """
    sd = run.dir
    timeout = int(run.meta["timeout_sec"])
    prompt += (
        "\n\nEdit files in this package directly. Read back files you "
        "create, including run/verdict.txt, before reporting that they "
        "exist.\n"
    )
    prompt += _RUNTIME_INTERFACE
    if (ACCOUNT_HOME or EVOLVE_AGENT == "claude") and (cwd / "AGENTS.md").is_file():
        # Automatic document discovery is off (codex), or the role file has a
        # name this CLI does not look for (claude): either way the experiment's
        # own role has to arrive in the prompt rather than by discovery.
        prompt = (cwd / "AGENTS.md").read_text() + "\n\n" + prompt
    if (cwd / ev.ACTION_PATH).is_file() and not (cwd / "solution/solve.sh").is_file():
        prompt = _recorded_solution_prompt(prompt)
    sd.prompt.write_text(prompt)
    if (cwd / "sandbox").is_file():
        _sandbox_boot(cwd)
    if EVOLVE_AGENT == "claude":
        env = _claude_env(Path(run.meta.get("claude_config_dir") or sd.codex_home))
        # A resume names the thread the caller wants continued; a fresh
        # session names the one this record was stamped with.
        cmd = _claude_cmd(
            cwd,
            resume or run.meta["claude_session_id"],
            resume=bool(resume),
            model=run.meta["model"],
        )
    else:
        if ACCOUNT_HOME and CODEX_DRIVER != "exec":
            raise ValueError(
                "account authentication currently requires the exec driver"
            )
        env = _codex_env(sd)
        if CODEX_DRIVER == "sdk":
            cmd = _session_cmd(run, cwd, resume=resume)
            env["CODEX_BIN"] = str(_codex_bin())
            env["EVOLVE_CODEX_OVERRIDES"] = "\n".join(_provider_overrides())
        else:
            cmd = _codex_cmd(cwd, run.meta["model"], resume=resume)
    # Streamed to disk rather than captured in memory. A session runs for tens
    # of minutes and used to write nothing until it ended, so a killed one
    # (SIGKILL leaves no record at all) took its log with it, and there was no
    # way to watch a live one. Now `tail -f` works, a kill keeps whatever ran,
    # and the provider's refusal line lands on disk the moment it is printed.
    try:
        with sd.stdout.open("w") as out_f, sd.stderr.open("w") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                text=True,
                cwd=str(cwd),
                env=env,
            )
            try:
                proc.communicate(input=prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            finally:
                if ACCOUNT_HOME and EVOLVE_AGENT != "claude":
                    (sd.codex_home / "auth.json").unlink(missing_ok=True)
    except subprocess.TimeoutExpired as exc:
        run.meta["exit_code"] = proc.returncode
        # The partial streams are on disk; hand them to the caller too, which
        # is where the old in-memory capture put them.
        raise subprocess.TimeoutExpired(
            cmd, timeout, output=_read_stream(sd.stdout), stderr=_read_stream(sd.stderr)
        ) from exc
    run.meta["exit_code"] = proc.returncode
    result = subprocess.CompletedProcess(
        cmd, proc.returncode, _read_stream(sd.stdout), _read_stream(sd.stderr)
    )
    if result.returncode:
        failure_type = _agent_failure_type(result.stdout, result.stderr)
        if failure_type.startswith("provider_") and _provider_try < PROVIDER_RESUMES:
            # The provider refused or dropped the thread (EVOLVE_PROVIDER_RESUMES,
            # off by default): continue the same thread rather than lose its work.
            try:
                sid = resume or _session_id(sd)
            except RuntimeError:
                sid = None
            if sid:
                wait = min(PROVIDER_RESUME_SEC * 2**_provider_try, PROVIDER_RESUME_MAX_SEC)
                log.info(
                    "%s: %s; resuming thread %s in %ds (%d of %d)",
                    sd.path, failure_type, sid, wait, _provider_try + 1, PROVIDER_RESUMES,
                )
                # Keep this invocation's record beside the next one's.
                for f in (sd.prompt, sd.stdout, sd.stderr):
                    if f.exists():
                        f.rename(f.with_name(f"{f.stem}.{_provider_try}{f.suffix}"))
                time.sleep(wait)
                return _run_codex(
                    run,
                    cwd,
                    _PROVIDER_RESUME_JOB.format(failure=failure_type),
                    resume=sid,
                    _provider_try=_provider_try + 1,
                )
        run.meta["failure_type"] = failure_type
        errors = [
            line for line in result.stderr.splitlines() if line.startswith("ERROR:")
        ]
        raise AgentSessionError(
            agent=EVOLVE_AGENT,
            failure_type=failure_type,
            returncode=result.returncode,
            stderr_path=sd.stderr,
            result=result,
            detail=errors[-1][:350] if errors else None,
        )
    return result


@contextlib.contextmanager
def _checked_output(agent_error: AgentSessionError | None):
    """Restore a deferred process failure only when output validation fails."""
    try:
        yield
    except Blocked:
        raise
    except Exception as output_error:
        if agent_error is not None:
            raise agent_error from output_error
        raise


def _session_id(sd: layout.SessionDir) -> str:
    """The id of the thread recorded under this session's private home.

    Codex reports an id only in the transcript it writes, so it is read back
    from there. Claude Code takes the id as an argument, so the one this
    session was started with is already in its record -- read that, rather
    than a transcript whose location is the CLI's business.
    """
    if EVOLVE_AGENT == "claude":
        try:
            sid = json.loads(sd.meta.read_text()).get("claude_session_id")
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"no session record at {sd.meta}") from exc
        if not sid:
            raise RuntimeError(f"no claude_session_id in {sd.meta}")
        return str(sid)
    newest = None
    for f in (sd.codex_home / "sessions").rglob("*.jsonl"):
        if newest is None or f.stat().st_mtime > newest.stat().st_mtime:
            newest = f
    if newest is None:
        raise RuntimeError(f"no recorded session under {sd.codex_home}")
    with newest.open() as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "session_meta":
                sid = (rec.get("payload") or {}).get("id")
                if sid:
                    return str(sid)
    raise RuntimeError(f"no session_meta in {newest}")


# --------------------------------------------------------------------------
# The package as the agent sees it
# --------------------------------------------------------------------------


def _write_seed_literals(
    pkg: Path, verifier_rel: str, *, require_growth: bool = True
) -> None:
    """What the seed's verifier already depends on unseen, for `./sandbox
    check`'s names audit to subtract: the agent answers for the names its
    rewrite added, not for the seed's. Written once, before the session,
    while the package on disk is still the input revision."""
    path = pkg / "run" / "seed_literals.json"
    if path.exists():
        return
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(vl.audit_package(pkg, verifier_rel)) + "\n")
    # And the seed's size, for the one-rung check the same tool runs. A seed
    # with no reference solution has no length to count a rung from.
    size = ts.size_of_package(pkg, verifier_rel)
    try:
        ev.solution_path(pkg)
    except FileNotFoundError:
        size["solution_lines"] = None
    if not require_growth:
        size["require_growth"] = False
    (pkg / "run" / "seed_size.json").write_text(json.dumps(size) + "\n")


def _write_resources(pkg: Path, task: dict) -> None:
    """Tell the sandbox tool what size box training gives this task.

    `_resources` is the row's own daytona_cpu/mem_gb/disk_gb filled out with
    the trainer's fleet defaults, resolved by the loop. Without this file the
    tool opens the harness default box (2/4/6), which on this corpus is a size
    up from what training runs at (1/2/2): the agent's check would pass in a
    box the task never gets, and the fold would inherit the seed's size for a
    task that had outgrown it.
    """
    res = task.get("_resources")
    if not res:
        return
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / "resources.json").write_text(json.dumps(res, sort_keys=True) + "\n")


def _write_pretest(pkg: Path, task: dict) -> None:
    """Tell the sandbox tool the row's pin hook, so `./sandbox check` grades
    the way training does: pins first, verifier second.

    `_pretest` is what the loop read off the row into the rewrite's
    pretest.json. This copy under run/ is the agent's, like resources.json:
    it feeds the agent's own check and is stripped at the fold, while the
    loop's probe reads the rewrite's file, which the session cannot reach. A
    task without a hook writes nothing and the tool grades with the verifier
    alone.
    """
    hook = task.get("_pretest")
    lists = task.get("_protected") or {}
    if not (hook and hook[0]) and not lists:
        return
    layout.write_pretest(
        pkg / "run" / "pretest.json",
        *(hook or ("", "")),
        protected_paths=lists.get("protected_paths"),
        protected_cmds=lists.get("protected_cmds"),
    )


def _prepare_package(pkg: Path, task: dict) -> dict:
    """The package as the loop left it, plus what the agent needs: the seed's
    literals and size for `./sandbox check`, the training box, the role file
    and the container tool. Returns the file map the task was loaded with."""
    # copytree applied the source revision's root mode. Restore the private
    # mode before a prompt or a session sits beside it.
    pkg.chmod(0o700)
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / "result-cache.json").unlink(missing_ok=True)
    fmap = ev.file_map(task)
    _write_seed_literals(
        pkg, fmap["test_state_py"], require_growth=False
    )
    _write_resources(pkg, task)
    _write_pretest(pkg, task)
    shutil.copy2(SPEC, pkg / "AGENTS.md")
    if ev.file_map(task)["solve_sh"] == ev.ACTION_PATH:
        role = pkg / "AGENTS.md"
        role.write_text(_recorded_solution_prompt(role.read_text()))
    shutil.copy2(SANDBOX, pkg / "sandbox")
    os.chmod(pkg / "sandbox", 0o755)
    return fmap


def support_changes(pkg: Path, seed_dir: Path | None) -> list[str]:
    """Files other than the four that round-trip which differ from the input
    revision: added, changed, or removed. The harness's own files are not
    the package's.

    The agent validated the package WITH its fixtures, so a Dockerfile whose
    COPY source is a file the agent wrote has to count as a change: a package
    whose only edit was a new fixture would otherwise take the instruction-only
    fast path and never rebuild.
    """
    if not seed_dir or not Path(seed_dir).is_dir():
        return []
    seed_dir = Path(seed_dir)

    def files(root: Path) -> dict[str, Path]:
        out = {}
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(root)
            if rel.parts[0] in HARNESS or "__pycache__" in rel.parts:
                continue
            out[str(rel)] = f
        return out

    now, before = files(pkg), files(seed_dir)
    mapped = set(ev.FILES.values()) | set(ev.VERIFIER_CANDIDATES) | {ev.ACTION_PATH}
    changed = []
    for rel in sorted(set(now) | set(before)):
        if rel in mapped:
            continue
        if (
            rel not in now
            or rel not in before
            or now[rel].read_bytes() != before[rel].read_bytes()
        ):
            changed.append(rel)
    return changed


def _last_check(pkg: Path) -> dict | None:
    """The last record `./sandbox check` appended, or None."""
    path = pkg / "run" / "checks.jsonl"
    if not path.exists():
        return None
    last = None
    for line in path.read_text().splitlines():
        if line.strip():
            last = line
    if not last:
        return None
    try:
        record = json.loads(last)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _agent_checked(pkg: Path) -> bool:
    """Whether the agent's last `./sandbox check` passed."""
    return (_last_check(pkg) or {}).get("verdict") == "pass"


def _require_checked(
    pkg: Path, result: subprocess.CompletedProcess | None = None
) -> None:
    """Raise a typed error unless the newest sandbox-check record passes.

    The initial author gets one bounded resume for this error. Other callers
    are already repair attempts, so they propagate it instead of looping.
    """
    check = _last_check(pkg)
    final = (result.stdout or "").strip() if result is not None else None
    ending = "empty final response; " if final == "" else ""
    if final == "[System: Empty message content sanitised to satisfy protocol]":
        ending = "placeholder final response; "
    if check is None or "verdict" not in check:
        raise AgentValidationError(
            "agent_missing_validation",
            ending + "agent finished without a readable ./sandbox check result "
            "in run/checks.jsonl",
        )
    if check.get("verdict") != "pass":
        raise AgentValidationError(
            "agent_check_failed",
            ending + "agent's last ./sandbox check did not pass "
            f"(verdict={check.get('verdict')!r})",
        )


def _check_verdict(pkg: Path) -> None:
    verdict = pkg / "run" / "verdict.txt"
    if verdict.exists():
        text = verdict.read_text().strip()
        head = text.upper()
        # Both are legitimate endings, and both mean the same thing here:
        # leave the task alone. Giving up honestly has to be at least as
        # easy as passing, or the only way to finish is to make the check
        # weaker until it passes.
        if head.startswith("BLOCKED") or head.startswith("GIVE UP"):
            blocked = Blocked(text[:200])
            blocked.report = text
            raise blocked


def _verifier_on_disk(pkg: Path, preferred: str) -> str:
    if (pkg / preferred).exists():
        return preferred
    alt = next((c for c in ev.VERIFIER_CANDIDATES if (pkg / c).exists()), None)
    if alt is None:
        raise RuntimeError(f"no verifier on disk ({ev.VERIFIER_CANDIDATES})")
    return alt


def _collect(task: dict, pkg: Path, fmap: dict) -> dict:
    """Read the package back: the four round-tripping files, which support
    files moved, and what the agent's last passing check measured.

    The agent may rewrite the verifier as the other corpus's form -- grading
    always runs tests/test.sh, and a TW task carrying tests/test_state.py can
    legitimately grow a test.sh and drop the helper -- so read back whichever
    verifier is on disk now, not the path the source happened to carry, and
    carry that path forward so the writeback and the row builder follow the
    file the agent kept. A task that passed ./sandbox check with a switched
    verifier used to crash here on the deleted file and be discarded whole.
    """
    fmap = dict(fmap)
    if fmap["solve_sh"] == ev.ACTION_PATH:
        if (pkg / "solution/solve.sh").exists():
            raise ValueError(
                "recorded solution cannot be overridden by solution/solve.sh"
            )
        ev.parse_actions((pkg / ev.ACTION_PATH).read_text())
    if not (pkg / fmap["test_state_py"]).exists():
        fmap["test_state_py"] = _verifier_on_disk(pkg, fmap["test_state_py"])
    out = {**task, **{key: (pkg / rel).read_text() for key, rel in fmap.items()}}
    out["_verifier_rel"] = fmap["test_state_py"]
    # What the reference solution cost in the agent's own container, from the
    # last check that passed. The caller sizes the rewritten row from this --
    # a measurement, never the agent's estimate -- and verifies at that size.
    chk = _last_check(pkg) or {}
    if chk.get("verdict") == "pass":
        out["_measured"] = chk.get("measured")
        out["_box"] = chk.get("resources")
        out["_at_max"] = bool(chk.get("at_max"))
    out["_support_changed"] = support_changes(pkg, task.get("_seed_dir"))
    return out


def _sandbox_boot(pkg: Path) -> None:
    """Start the package's container as the session starts.

    The container is the harness's to provide, not the agent's to manage: it
    comes up while the agent reads the task, and exec, grade and oracle wait
    for it. Agents that ran `./sandbox up` themselves backgrounded it to keep
    working, and codex kills a command's background jobs when the command
    returns, which left them watching a log that had stopped. Best effort: a
    boot that fails here is retried, and its error shown, by the agent's first
    command that needs the container.
    """
    try:
        p = run_executable(
            [str(pkg / "sandbox"), "up", "--detach"],
            cwd=pkg,
            env=_harness_env(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        log.info("sandbox boot for %s: %s", pkg, (p.stdout + p.stderr).strip()[-300:])
    except Exception as e:  # noqa: BLE001 -- the session runs without a head start
        log.warning("sandbox boot for %s did not start: %s", pkg, e)


def _sandbox_down(pkg: Path) -> None:
    """Delete the container a session left running. Best effort."""
    state = pkg / "run" / "sandbox.json"
    if not state.exists():
        return
    try:
        if json.loads(state.read_text()).get("status") == "down":
            return
    except ValueError:
        pass
    try:
        run_executable(
            [str(pkg / "sandbox"), "down"],
            cwd=pkg,
            env=_harness_env(),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception:  # noqa: BLE001 -- cleanup must not turn a verdict into a crash
        pass


def _harness_check(
    pkg: Path, name: str = "check", *, timeout: int | None = None
) -> str:
    """Run `./sandbox check` in the author's package from the harness rather
    than from a session, and return what it printed. Used when the verifier
    was written elsewhere: the record it appends to run/checks.jsonl is the
    same one _require_checked reads, and the text is kept as run/<name>.txt
    for whoever reads a rejected rewrite.

    Always --fresh: run/result-cache.json sits where the agent writes, so a
    reused entry is the agent's word, not a run."""
    timeout = AGENT_TIMEOUT if timeout is None else timeout
    try:
        p = run_executable(
            [str(pkg / "sandbox"), "check", "--fresh"],
            cwd=pkg,
            env=_harness_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        text = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    except subprocess.TimeoutExpired as exc:
        text = f"./sandbox check timed out after {timeout}s\n{exc.stdout or ''}"
    finally:
        _sandbox_down(pkg)
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / f"{name}.txt").write_text(text)
    return text


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_ORACLE_AGENTS_MD = """# Your job: make the reference solution pass the verifier

This task was just rewritten to be harder. The rewrite regenerated the
instruction, the solution and the verifier together, and they do not agree: the
reference solution was run against the verifier and FAILED. Fix that.

Files in this directory:

- `solution/solve.sh` — the reference solution. Usually the file to fix.
- `{verifier}` — the verifier that grades it. Fix only where it is unfair.
- `instruction.md` — what the agent is told (context; edit only if it promises
  something the verifier does not check, or omits something the verifier needs).
- `environment/Dockerfile` — how the container is built (context, read-only).
- `run/failure.txt` — what actually happened when the solution ran, exit code
  {exit_code}. Start here. You are not guessing about behaviour: this is the
  behaviour.

## Method
Read `run/failure.txt` first and let it decide where you look — an impression of
what the code should do is what produced this failure. Identify the specific
check that failed, find the lines of `solve.sh` responsible for it, and fix
those. Do not rewrite what the run shows is already working.

## Which side to change
Prefer `solve.sh`. The verifier encodes what the task asks for, and weakening it
to fit an incomplete solution is how a task becomes worthless. Change a check
only when it demands something the instruction never promised and the workspace
never reveals — such a check would fail a capable agent too. If a check re-runs
the workflow after changing an input, the solution has to recompute from the
inputs as they are at that moment; a hard-coded answer passes the first run and
fails the re-run.

If the failure is environmental rather than logical — a missing tool, no room
for the verifier's own dependencies, a lock or process the setup leaves behind —
do not code around it in `solve.sh`: fix the environment it comes from (the
Dockerfile, the setup), which is part of this job. Write `BLOCKED: <reason>` to
`run/verdict.txt` and stop only when the platform itself fails -- the sandbox
does not start, the network is down -- and nothing in the package can fix it.

## Work efficiently
At most {max_calls} tool calls. Save your edits in place; do not print the files.
"""

_ORACLE_PROMPT = (
    "Read AGENTS.md, then read run/failure.txt and fix the task so "
    "the reference solution passes the verifier. Save your edits "
    "in place and stop."
)


_TRACES_SPEC = r"""

TRACES. `traces/attempt-NN.jsonl`, one file per attempt (NN counts from 01,
in the order the attempts ran), one JSON object per line. Line 1 is the
rollout: reward, turns, secs, and how it ended (status, finish_reason,
submitted, format_errors, infra_failed), with the task, run and group it
came from. Every later line is one turn, in order: turn, keystrokes (a list:
what the agent typed; empty on a closing turn), task_complete, output (the
terminal after it; empty on the last turn, where the episode ended),
analysis, plan, think. A turn with no parseable response has raw instead of
keystrokes. The header may contain verifier diagnostics and a per-test report.
If `traces/previous-simplify.json` exists, read the prior intervention and its
measured outcome before editing. An all-failed variant needs a stronger or
different intervention at the remaining obstacle; an all-solved variant needs
part of the removed difficulty restored, not an unrelated extra deliverable.
The current attempts show whether the prior prediction held.
These attempts are one group, all solved or all failed, so they cannot show
which decision separates a pass from a fail. When `traces/revision/` exists it
holds that contrast for the revision being rewritten: `trajectory.jsonl`, one
line per group the student finished on it in this run, oldest first (solved,
total, finish reasons), and `mixed/gG-rK.jsonl`, the attempts of its latest
mixed groups in the same format. Compare a passing and a failing attempt from
one mixed group to find the decision that differs, and read the trajectory
for how fast the student moved on this revision. jq is on PATH;
these are enough:

  head -qn1 traces/*.jsonl | jq -c '{rollout, reward, turns, finish_reason}'
                                                              # every attempt's outcome
  jq -r 'select(.turn) | "\(.turn)\t" + ((.keystrokes // [.raw]) | join(""))' traces/attempt-01.jsonl
                                                              # the commands alone, turn number first
  tail -n 4 traces/attempt-03.jsonl | jq -r '.turn, .output'  # what the last four turns saw
  jq -r 'select(.turn) | select((((.keystrokes // [.raw]) | join("")) + (.output // "")) | test("metrics")) | .turn' traces/attempt-02.jsonl
                                                              # the turns whose commands or output mention "metrics"
A line number from grep -n over the joined keystrokes is not a turn number."""


# The same paragraph for attempts the mini-swe-agent harness produced: no
# keystrokes, every turn a THOUGHT plus one ```bash block run as its own
# non-interactive process. The record header's "agent" says which harness ran.
_MINISWE_TRACES_SPEC = (
    _TRACES_SPEC.replace(
        """Every later line is one turn, in order: turn, keystrokes (a list:
what the agent typed; empty on a closing turn), task_complete, output (the
terminal after it; empty on the last turn, where the episode ended),
analysis, plan, think. A turn with no parseable response has raw instead of
keystrokes.""",
        """These attempts ran under mini-swe-agent: each turn
the agent wrote its reasoning and exactly one ```bash block, which ran as its
own non-interactive bash process (no shell state carried between turns, no
terminal, a 120 s limit per command). Every later line is one turn, in order:
turn, raw (the agent's reply: reasoning, then the bash block), output (the
command's <returncode> and <output>; empty on the last turn), think. A reply
without exactly one bash block ran nothing and got a format error back. The
episode ends when a command prints COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT.
The student meets the rewritten task the same way, so keep it solvable like
that: nothing that only an interactive terminal can do (an editor, a REPL, a
prompt waiting for an answer) and no single step that must run in the
foreground for more than 120 s; long work has to be able to run in the
background and be checked later. The reference solution may still be replayed
as keystrokes in a terminal; that does not change what the student can do.""",
    )
    .replace("(.keystrokes // [.raw])", "[.raw]")
    .replace("joined keystrokes", "joined replies")
)
assert _MINISWE_TRACES_SPEC.count("mini-swe-agent") == 1


# The same paragraph for attempts the vanillux harness produced
# (vanillux_loop.py): every turn a reply carrying one native tool call to the
# single bash tool, kept under "raw" as the model wrote it, and the tool
# response under "output". The jq recipes pull the command out of raw with the
# same pattern rollout_record.vanillux_command uses.
_JQ_VANILLUX_CMD = (
    r"""def cmd: (.raw // "") | (capture("<parameter=command>\n?(?<c>[\\s\\S]*?)\n?</parameter>").c // ""); """
)
_VANILLUX_TRACES_SPEC = (
    _TRACES_SPEC.replace(
        """Every later line is one turn, in order: turn, keystrokes (a list:
what the agent typed; empty on a closing turn), task_complete, output (the
terminal after it; empty on the last turn, where the episode ended),
analysis, plan, think. A turn with no parseable response has raw instead of
keystrokes.""",
        """These attempts ran under vanillux (mini-swe-agent's
tool-calling loop): each turn the agent may write some prose, then calls its
one tool, bash, with a command. Each command runs as its own non-interactive
bash process with no terminal; the working directory (starting at /app) and
exported variables carry over to the next command, and a command still running
after 120 s is killed ("Command timed out after 120s.", exit_code=124). Every
later line is one turn, in order: turn, raw (the reply as the model wrote it:
any prose, then the call: <tool_call><function=bash><parameter=command>, the
command, </parameter></function></tool_call>), output (what came back, inside
<tool_response>: the command's output, cut to its head and tail when long,
then (exit_code=N); or a format error), think (its reasoning before the
reply). Only the first call in a reply runs. A call without a command
parameter ran nothing and got a format error back. A reply with no call at
all ran nothing: by default it ends the attempt (finish_reason
stopped_early), and format_errors counts such replies. The student submits
by running echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT; that turn is the last
and its output is empty, and only a submitted attempt is graded; writing the
marker in prose submits nothing. hit_context_limit means the last reply was
cut off at the context limit, so its raw may be empty or partial.
The student meets the rewritten task the same way, so keep it solvable like
that: nothing that only an interactive terminal can do (an editor, a REPL, a
prompt waiting for an answer) and no single step that must run in the
foreground for more than 120 s; long work has to be able to run in the
background and be checked later. The reference solution may still be replayed
as keystrokes in a terminal; that does not change what the student can do.""",
    )
    .replace("jq -r 'select(.turn)", "jq -r '" + _JQ_VANILLUX_CMD + "select(.turn)")
    .replace("((.keystrokes // [.raw]) | join(\"\"))", "cmd")
    .replace("the commands alone", "the commands alone (\"\" where none ran)")
    .replace("joined keystrokes", "joined commands")
)
assert _VANILLUX_TRACES_SPEC.count("vanillux") == 1
assert "keystrokes //" not in _VANILLUX_TRACES_SPEC
assert _VANILLUX_TRACES_SPEC.count("def cmd:") == 2

# Which paragraph a package's attempts get, by the header's "agent"; anything
# else (terminus, or a record from before the field existed) gets _TRACES_SPEC.
_AGENT_TRACES_SPECS = {
    "miniswe": _MINISWE_TRACES_SPEC,
    "vanillux": _VANILLUX_TRACES_SPEC,
}


def _attempts_agent(traces: Path) -> str:
    """The harness named in the first attempt record's header ("" if none)."""
    for path in sorted(traces.glob("attempt-*.jsonl")):
        try:
            with path.open() as f:
                return str(json.loads(f.readline()).get("agent") or "")
        except (OSError, ValueError, AttributeError):
            return ""
    return ""


def _traces_spec(traces: Path) -> str:
    """The format paragraph, only when the package carries attempt records."""
    if not any(traces.glob("attempt-*.jsonl")):
        return ""
    return _AGENT_TRACES_SPECS.get(_attempts_agent(traces), _TRACES_SPEC)


# --------------------------------------------------------------------------
# Instruction-only easier rewrite, and the fresh-session oracle repair
# --------------------------------------------------------------------------


def simplify_codex(
    rewrite: layout.RewriteDir,
    task: dict,
    solved: int = 0,
    attempts: int = 16,
    hint: str = "vague",
    *,
    deadline: RewriteDeadline | None = None,
    handoff=None,
    timings: dict | None = None,
) -> dict:
    """Choose one trace-supported operator and validate the resulting package."""
    so.prompt(hint)
    if not any(rewrite.traces.glob("attempt-*.jsonl")):
        raise Blocked("simplify requires attempt traces")
    for path in sorted(rewrite.traces.glob("attempt-*.jsonl")):
        try:
            head, turns = rollout_record.read_record(path)
        except (OSError, ValueError):
            continue
        if (
            head.get("reward") == 0
            and not head.get("infra_failed")
            and any(turn.get("turn") for turn in turns)
        ):
            break
    else:
        raise Blocked(
            "simplify requires a failed student execution, "
            "not empty or infrastructure-only traces"
        )
    return evolve_agentic(
        rewrite,
        {**task, "_solved": solved, "_attempts": attempts, "_simplify_hint": hint},
        "easier",
        deadline=deadline,
        handoff=handoff,
        timings=timings,
    )


def repair_oracle_codex(
    rewrite: layout.RewriteDir,
    task: dict,
    observed: str,
    exit_code: int = 1,
    *,
    deadline: RewriteDeadline | None = None,
) -> dict:
    """Fresh-session repair of a task whose reference solution failed the run.

    The chat repair reads the two files and guesses which side is wrong. Here the
    agent has both files on disk at full length and the run output that proves
    they disagree, and can grep between them instead of holding a 500-line
    solution and a 500-line verifier in one prompt. `resume_agentic` is the
    better tool when the session that wrote the files is on disk; this is for
    when it is not. Raises on failure.
    """
    _require_codex()
    deadline = deadline or rewrite_deadline(rewrite)
    pkg = rewrite.package
    fmap = ev.file_map(task)
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    (pkg / "run" / "verdict.txt").unlink(missing_ok=True)
    (pkg / "AGENTS.md").write_text(
        _ORACLE_AGENTS_MD.format(
            verifier=ev._verifier_rel(task),
            exit_code=exit_code,
            max_calls=MAX_TOOL_CALLS,
        )
    )
    if fmap["solve_sh"] == ev.ACTION_PATH:
        role = pkg / "AGENTS.md"
        role.write_text(_recorded_solution_prompt(role.read_text()))
    agent_error = None
    timeout = _session_timeout(deadline, "oracle repair", TIMEOUT_SEC)
    try:
        with session(rewrite, "oracle", timeout=timeout) as run:
            p = _run_codex(run, pkg, _ORACLE_PROMPT)
    except AgentSessionError as error:
        assert error.result is not None
        p, agent_error = error.result, error
    with _checked_output(agent_error):
        _check_verdict(pkg)
        out = {**task, **{key: (pkg / rel).read_text() for key, rel in fmap.items()}}
    if all(out[key] == task[key] for key in fmap):
        raise RuntimeError(
            f"codex changed nothing (exit {p.returncode}): " f"{p.stdout[-200:]}"
        )
    out["_repaired"] = "codex_oracle_observed"
    out["_session"] = str(run.dir.path)
    if agent_error is not None:
        out["_agent_failure_type"] = agent_error.failure_type
        out["_agent_exit_code"] = agent_error.returncode
    return out


# --------------------------------------------------------------------------
# Agentic evolution: one session, with the task's own container as a tool
# --------------------------------------------------------------------------

SPEC = Path(__file__).resolve().parent / "agents" / "task_evolution.md"
SANDBOX = Path(__file__).resolve().parent / "agent_sandbox.sh"
# Who writes the verifier of a harder rewrite. "same": the session that wrote
# the solution, as it always has. "blind": a second session that is shown the
# instruction, the environment and the seed's verifier, and not the solution,
# so it cannot inherit the solution's private vocabulary -- the hidden
# contract that failed five of eight reviewed 0/16 tasks is then impossible
# by construction rather than caught by a heuristic afterwards. It costs a
# second session and one more ./sandbox check per rewrite: on the first paired
# round (wd-evolve-dev/ab/20260904-170942, six 16/16 signals each way) the
# per-task time went from a median of 430 s to 974 s and the round's wall
# clock from 1323 s to 2320 s on three workers, with the same six of six
# folded, the same sizes, and the hidden solution passing the blind verifier
# at first meeting on all six. The verifiers it wrote recompute the expected
# result from the container instead of asserting the solution's strings,
# which is what the split was for. "same" remains for comparison.
VERIFIER_AUTHOR = os.environ.get("SWE_VERIFIER_AUTHOR", "blind")
VERIFIER_SPEC = Path(__file__).resolve().parent / "agents" / "verifier_author.md"
# Hide author-only files; run metadata is filtered separately in _blind_layout.
HIDDEN_FROM_VERIFIER = (
    "solution",
    "traces",
    "AGENTS.md",
    "sandbox",
)
AGENT_TIMEOUT = int(os.environ.get("EVOLVE_AGENT_TIMEOUT", "2400"))
# The resume that asks an author with no check record to run `./sandbox check`
# once: a check plus a container boot, not another authoring session.
AGENT_VALIDATION_TIMEOUT = int(os.environ.get("EVOLVE_AGENT_VALIDATION_TIMEOUT", "900"))
# When a check fails after the author and the blind verifier have both
# written, the two sides repair in turn -- the author first, with the failure
# and without the verifier, then the verifier's author -- and that pair is one
# iteration. Up to this many iterations fit inside the shared rewrite deadline:
# three iterations of two sessions each is the most a task is worth.
REPAIR_ROUNDS = int(os.environ.get("EVOLVE_REPAIR_ROUNDS", "3"))
# Keep one full Daytona revalidation attempt available after authoring and
# repair sessions. The final probe itself is capped by the shared deadline.
FINAL_VALIDATION_RESERVE_SEC = int(
    os.environ.get("EVOLVE_FINAL_VALIDATION_RESERVE_SEC", "2400")
)
# The budget is one training epoch: a rewrite that outlives one pass over the
# mix lands after the task has been sampled again against the old revision,
# so the group that is measuring it is already stale. Measured from the run
# (rows in the live mix / groups per step x the median step interval), so it
# follows the box: about 14 h on hip's MI350Xs, a few hours on B300s.
# EVOLVE_REWRITE_BUDGET_SEC overrides it with a fixed number.
_EPOCH_STEPS_DEFAULT_SEC = int(os.environ.get("EVOLVE_STEP_SEC_DEFAULT", "3600"))
log = logging.getLogger("evolve")
_budget_cache: dict = {"at": 0.0, "sec": 0.0, "why": ""}


def _step_starts(run_dir: Path) -> list[float]:
    """First timestamp per training step in the controller's structured log."""
    first: dict[int, float] = {}
    for f in sorted((run_dir / "trainer" / "structured_logs").glob("rl_controller.global_rank_0.*.jsonl")):
        try:
            with f.open() as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    step, t = d.get("step"), d.get("time")
                    if isinstance(step, int) and step > 0 and t is not None and step not in first:
                        first[step] = float(t)
        except OSError:
            continue
    return [first[k] for k in sorted(first)]


def rewrite_budget_sec(
    root: "layout.Root | None" = None, *, refresh: bool = False
) -> float:
    """End-to-end seconds for one rewrite: one epoch of the run this root is
    training, recomputed every ten minutes from the newest run's step cadence."""
    fixed = os.environ.get("EVOLVE_REWRITE_BUDGET_SEC")
    if fixed:
        return float(fixed)
    now = time.monotonic()
    # A node-local round snapshots this with an explicit root before it starts
    # workers. Calls from an active rewrite pass no root and must use that
    # snapshot rather than returning to remote storage mid-session.
    if not refresh and _budget_cache["sec"] and (
        root is None or now - _budget_cache["at"] < 600
    ):
        return _budget_cache["sec"]
    root = root or layout.Root.from_env()
    try:
        rows = sum(1 for line in root.mix.read_live().splitlines() if line.strip())
    except FileNotFoundError:
        rows = 0
    groups = int(os.environ.get("SWE_NUM_GROUPS_PER_TRAIN_STEP", "32"))
    runs = root.run_dirs()
    starts = _step_starts(runs[-1].path) if runs else []
    gaps = [b - a for a, b in zip(starts, starts[1:])][-5:]
    step_sec = statistics.median(gaps) if gaps else float(_EPOCH_STEPS_DEFAULT_SEC)
    epoch_steps = (rows / groups) if rows and groups else 14.0
    sec = epoch_steps * step_sec
    why = (
        f"{sec / 3600:.1f} h = one epoch: {rows} rows / {groups} groups per step "
        f"= {epoch_steps:.1f} steps x {step_sec / 60:.0f} min per step"
        + ("" if gaps else " (no step cadence measured yet; default step time)")
    )
    if why != _budget_cache["why"]:
        log.info("rewrite budget %s", why)
    _budget_cache.update(at=now, sec=sec, why=why)
    return sec


def rewrite_deadline(rewrite: layout.RewriteDir) -> RewriteDeadline:
    """Freeze this rewrite's one-epoch budget into one absolute deadline."""
    try:
        started_at = layout.parse_stamp(rewrite.path.name.split("--")[0])
    except (ValueError, IndexError) as error:
        raise ValueError(f"rewrite path has no valid timestamp: {rewrite.path}") from error
    budget_sec = rewrite_budget_sec()
    return RewriteDeadline(
        started_at=started_at,
        expires_at=started_at + budget_sec,
        final_validation_reserve_sec=FINAL_VALIDATION_RESERVE_SEC,
    )


def rewrite_budget_why() -> str:
    """How the last rewrite_budget_sec value was derived, for the loop's log."""
    return _budget_cache["why"] or "EVOLVE_REWRITE_BUDGET_SEC"


def _session_timeout(
    deadline: RewriteDeadline, stage: str, maximum_sec: int = AGENT_TIMEOUT
) -> int:
    """Cap one model session without spending final-validation time."""
    return deadline.timeout(stage, maximum_sec, reserve_final_validation=True)


_STUDENT_HARDER_GUIDANCE = """Choose one change from the student's actual attempts
in `traces/`. Start by identifying the successful strategy and a task-relevant
judgment it currently bypasses. Inspect failures as well: distinguish a missing
skill from unclear requirements or infrastructure trouble.

A strategy not exercised in these attempts is not evidence that the student
cannot use it. When prior measured feedback is supplied, compare its prediction
with the actual attempts. A previous change solved consistently is demonstrated
student capability; use that result to revise the difficulty hypothesis.

Before editing, compare two candidate changes in `run/hardening.md`, then
implement only one. For each candidate, cite attempt filenames and concrete
actions or observations; explain the current strategy, the changed condition,
and the new inference or decision needed to reach the original goal. Sketch
the smallest correct adaptation from the observed strategy,
distinguishing demonstrated skills from new decisions and dependencies between
steps. Using the student's observed checking behavior, explain whether feedback
available in the task would expose mistakes in that adaptation and allow their
correction. Use this evidence to choose the change's magnitude; failure of the
unchanged strategy alone does not establish difficulty of adaptation. State why
the old strategy with a routine post-processing step would be insufficient.
Choose the candidate whose required decisions are least covered by demonstrated
student skills while remaining a modest step from those skills; explain the
comparison before implementing it. If both reduce to routine adaptations, revise
the candidates before selecting one. This comparison is proposal reasoning,
not permission to generate or evaluate multiple task versions.
For each strategy predicted to fail, include a concrete proposed input, the
correct observable result, and the result that strategy would produce. If the
results agree, that case does not support the prediction. For a strategy that
discards information, include cases requiring different outcomes and check
that the grader distinguishes them; a wrong reason for the right output is
not a measured failure.
Use that analysis to design the patch; no operator menu or operator declaration
is required.

Make the change interact with the core workflow. An upstream discovery can
determine a downstream transformation, or conflicting evidence can require a
decision before an existing action. More dependencies, files, commands, checks,
or execution time alone are not evidence of difficulty. A separate report or
checksum appended after the original solution is not enough merely because it
reads the original outputs. Choose the mechanism the traces justify, rather
than applying an example mechanically.

What every hardening removes is an assumption the successful strategy relied
on without checking: that the only CSV in the directory is the input, that
the first match is the target, that a re-run reproduces what was recorded,
that two records of different types are never equal. Adding artifacts to the
workspace -- fixtures, decoy files, a second candidate, a stale copy, a log
that contradicts a config -- is a legitimate way to remove such an assumption,
and the way to add diversity a task lacks; it counts only when the added
material forces a decision judged by content (which of these is real, which
one applies), never when it is inert clutter the old strategy walks past.

Prefer changes to inputs or objectives that require the successful strategy to
adapt, rather than merely declaring that strategy disallowed. If correctness
depends on a method or source restriction, make it enforceable in the runnable
environment or checkable by the grader; otherwise reformulate it as an
observable task condition. When reviewing measured feedback, check successful
attempts for violations of those restrictions.

Preserve the original user goal, the original task's tool hints and discovery
aids, and requirements unrelated to the chosen change. Keep the specification
solvable and discoverable. Locate the difficulty in deciding the correct outcome
under the changed conditions, not in removing the original task's hints or
adding an implementation restriction.

Help that an earlier simplification of this task added is not part of the
original task; `traces/history` shows each easier rewrite and its diff. When the
student now succeeds with such help, withdrawing it is a legitimate
hardening. Size the withdrawal by what the attempts show, not by a preference
for small steps: every rewrite is measured only after the student has trained
on it for many epochs, so a step the traces already show to be too small costs
those epochs and buys nothing. Help the attempts do not use -- never opened,
never cited in their analysis, or reproduced from their own knowledge before
they read it -- is not what keeps them solving: withdraw all of it in this one
rewrite, which counts as one change. Withdraw only part of the help the
attempts visibly depend on: reduce a shipped file to its structure, replace it
with a short description, or drop one part of it. A failure measured when that
help was absent entirely, under an earlier policy, does not show that a
withdrawal would fail now.
When `traces/history` shows that the previous hardening of this task predicted a
mixed result and the student then solved every attempt, that step was too small
for this student, and repeating its size repeats the miss. This rewrite then
withdraws at least half of the help that still separates the current revision
from the original task (`traces/history/since-original.diff`), counted in
decisions the help makes for the student rather than in lines, including help the
attempts still use: an all-solved measurement after a withdrawal predicted to
bite is the evidence that they no longer need it. Overshooting to all-failed
costs one easier rewrite that restores half of it; undershooting again costs
another round of epochs at all-solved. Existing method requirements remain part of the original task;
repair missing checks for validity, but do not count stricter enforcement of
an unchanged requirement as the hardening mechanism.
Aim for a modest reduction in this student's solve rate toward mixed success,
not universal failure. In `run/hardening.md`, write the solved count you
expect from the next group of the same size as a number ("5 of 8"); a range
spanning everything from none to all predicts nothing. `traces/history` shows
earlier predictions beside what was measured next; where they were
systematically off -- withdrawals predicted to give mixed results that came
back all-solved -- size this change for it. Describe this as a hypothesis for student re-testing;
passing the reference solution only establishes validity. If the traces do not
support a useful change within the size limits, write
`GIVE UP: no-supported-hardening — <why>` and stop.
"""

# Which hardening a student-guided harder rewrite asks for. "real" (the
# default) grounds the change in researched real-world software behavior;
# "student" is the guidance above. On the r100 Sol arm 82 of 93 accepted
# "student" hardenings were a decoy, a record to pick, or an input edge case,
# with a median of +1 solution line, while Terminal-Bench's hard tasks are real
# builds, real formats and real upstream bugs. On the same 100 tasks "real"
# produced no decoys and about a third grounded in a pinned upstream artifact.
HARDER_GUIDANCE = os.environ.get("EVOLVE_HARDER_GUIDANCE", "real")

_REAL_HARDER_GUIDANCE = """These tasks train an agent for real terminal work, the
kind Terminal-Bench measures: building, configuring, debugging and repairing
real software, recovering data in real formats, applying a known algorithm under
a real constraint. A harder task needs more of that work or deeper knowledge of
a real system. It does not need more care in reading the workspace.

Start from the student's attempts in `traces/`: what the successful strategy
did, and which real work the task currently spares it -- a dependency that
already installs cleanly, a version whose behavior is the one everyone
remembers, a format reduced to its easy subset, a service that needs no
configuration.

Then research one real difficulty that fits this task's domain and tools: a
documented behavior change between two versions of a package the task uses, a
known issue in an upstream project's tracker together with its fix, a detail of
a file format or protocol from its specification, a failure an operator of this
software meets in practice. Your own recollection decides what to look for;
it is never the source. Your shell has network access: read the primary source
there -- the project's documentation or changelog page, the issue thread
through GitHub's REST API (`api.github.com/repos/<owner>/<repo>/issues/<n>`,
`/releases`), a package's PyPI JSON, the specification, the code at a tag.
Cite only what you fetched.

The difficulty must be real in this environment. Pin the actual upstream
artifact it comes from (`package==version`, a repository at a commit, a URL
with its sha256) in the Dockerfile, and confirm in `./sandbox exec` that the
behavior the source describes happens here: the error, the wrong output, the
crash. A library, tool, version or bug that does not exist upstream is not
allowed, and neither is a vendored imitation of one. The environment holds the
problem, never its fix: no patch, fixed release, backported file or note that
names the fix goes into the image or the task's files; the fix lives only in
the reference solution.

Write `run/hardening.md` before editing the task: the sources by URL with the
sentence of each that you rely on, the pinned artifacts, the reproduction
command with the output you observed, what the student must now do that the
successful attempts did not, and why that is one modest step from the skills
those attempts show.

These are not hardening, whatever their effect on the solve rate: decoy files,
a second candidate or stale copy to choose between, a log that contradicts a
config, an extra edge-case clause for the input, stricter output formatting, a
longer instruction. Keep the original task's wording: removing what it
already says is a withdrawal of help, not the real difficulty, and it mixes
the two in the student's measured solve rate. Help that an earlier
simplification of this task added (`traces/history/since-original.diff`) is
not the original task's. If any of it hands the student the result the task
asks for -- a value the checks expect, the exact output, the decisive step --
withdrawing it whole is this rewrite's change, however much the attempts lean
on it, and nothing new is added on top: a task that gives its answer away does
not get harder by adding to it. Say in `run/hardening.md` what you withdrew.
State any new requirement the
way a user would, in a sentence or two -- the goal and the deliverable. Do not
list the pitfalls; the agent meets them as error messages, logs and behavior
in the environment, the way the real software presents them.

The student has 2400 seconds of wall clock per attempt, and generating its own
turns takes most of that: in measured attempts its commands ran for about 100
seconds in total. So the reference solution's commands, as `./sandbox check`
times them, stay well under 300 seconds, and anything slow that is not the
point of the task -- a large download, a full build of a dependency -- happens
in the Dockerfile, at image build. What is left to the student is a debug and
rebuild loop measured in minutes. Its context is limited too: a correct
solution must not require reading an unbounded build log or data dump.

Preserve the original user goal and requirements unrelated to the change.
Aim for a modest reduction in this student's solve rate toward mixed success,
not universal failure; in `run/hardening.md`, write the solved count you
expect from the next group of the same size as a number ("5 of 8"). If no real
difficulty that fits this task can be reproduced here, write
`GIVE UP: no-real-difficulty — <what you searched and why it did not fit>` and
stop.
"""

_CALIBRATION_GUIDANCE = (
    """The previous simplification now has all attempts passing.
Read `traces/previous-simplify.json` and the current attempts. When the record
includes a parent path, compare that original task's files with the current
package to identify the exact help or restriction that changed. Keep the
reference copy and lineage record unchanged; both are checked after the session. Move toward
mixed success by undoing the previous simplification as far as the attempts
show they no longer need it. Help that hands the student the result the task
asks for -- a value the checks expect, the exact output, the decisive step --
is withdrawn whole even though the attempts rely on it: no simplification was
allowed to give it. That simplification is the first candidate, not
the limit: help that earlier simplifications added (`traces/history/since-original.diff`)
and that these attempts no longer use -- never opened, never cited, or
reproduced from their own knowledge before they read it -- is withdrawn in
this same rewrite, which counts as one change. Every rewrite is measured only
after many epochs of training on it, so a withdrawal the traces already show
to be too small costs those epochs and buys nothing. Keep
the change within the earlier task's requirements; do not introduce another
obligation or a new difficulty dimension.
When `traces/history` shows that the previous hardening of this task predicted a
mixed result and the student then solved every attempt, that step was too small
for this student, and repeating its size repeats the miss. This rewrite then
withdraws at least half of the help that still separates the current revision
from the original task (`traces/history/since-original.diff`), counted in
decisions the help makes for the student rather than in lines, including help the
attempts still use: an all-solved measurement after a withdrawal predicted to
bite is the evidence that they no longer need it. Overshooting to all-failed
costs one easier rewrite that restores half of it; undershooting again costs
another round of epochs at all-solved.

Before editing, write `run/hardening.md` with the prior intervention, the
portion being restored, the supporting attempts, and the predicted effect as
the solved count you expect from the next group of the same size ("5 of 8");
a range spanning everything from none to all predicts nothing.
If the removed condition is indivisible, restore it with limited guidance or
feedback at the recorded hint level. Keep the correctness checks for the
retained goal. A change to guidance alone may leave the verifier unchanged.
The reference solution need not grow: removing a hint or restoring one
condition can change difficulty without adding several commands. If the
evidence does not support an intermediate task, write GIVE UP with the reason.
"""
    + so.CALIBRATION_GUIDANCE
)

# EVOLVE_HARDER_FROM_PARTIAL=1 (default off): a harder signal from a simplified
# version that only some attempts solved -- the trainer's
# SWE_EVOLUTION_HARDER_RATIO below 1 -- is calibrated like an all-solved one,
# restoring toward the original, instead of taking the add-a-difficulty
# guidance. The overfit bench's ladder uses it so the next rung exists as soon
# as a version gives a gradient, while that version keeps training.
HARDER_FROM_PARTIAL = os.environ.get("EVOLVE_HARDER_FROM_PARTIAL", "0") == "1"
_ALL_PASSING = "The previous simplification now has all attempts passing."
_PARTIAL_PASSING = (
    "The previous simplification is now solved in {solved} of {attempts} attempts:\n"
    "the student has a foothold on it and has not mastered it, and that version\n"
    "keeps training beside this one. Restore toward the original task one coherent\n"
    "part of what the simplifications removed (`traces/history/since-original.diff`),\n"
    "the part these attempts already handle best, and keep the help for what they\n"
    "still fail. Aim for mixed success on the new version; a measurement short of\n"
    "all-solved does not justify withdrawing more than that one part."
)


def _calibration_guidance(solved: int, attempts: int) -> str:
    """The calibration text for a harder rewrite measured at solved/attempts,
    with the deployment's extra rules (so.extra_guidance) after it."""
    if solved >= attempts:
        return _CALIBRATION_GUIDANCE + so.extra_guidance()
    return _CALIBRATION_GUIDANCE.replace(
        _ALL_PASSING, _PARTIAL_PASSING.format(solved=solved, attempts=attempts), 1
    ) + so.extra_guidance()


_HARDER_JOB = """This task was solved {solved} of {attempts} attempts and met the
hardening threshold.

{guidance}

The size of the rewrite is checked, not trusted. {growth_bound}
The seed has {seed_lines} non-comment solution lines; the verifier may gain at most {max_asserts} assertions over the
seed's {seed_asserts}. `./sandbox check` fails outside that and the caller
rejects the rewrite. Measured on this corpus: rewrites that grew to 125 lines
came back 0/16 five times in six, while the seed at its own size was solved
every time. These limits bound the patch size; student re-testing measures difficulty.

Then do the work in this order, one file at a time. The order is not
arbitrary — each file is written against the one before it, and the synthesis
pipeline that produced these tasks runs the same sequence for that reason:

  1. `solution/solve.sh` — adapt the existing solution to the changed condition.
     Preserve the parts that still apply.
  2. the verifier — written against that answer and the requirement, not
     against incidental details of the workspace.
  3. `instruction.md` — what the agent is told. It has to make everything the
     verifier requires discoverable: findable in the workspace, or stated here.
  4. `environment/Dockerfile` — the environment the other three assume.

Add whatever new files the change needs — a fixture the Dockerfile copies, a
config, a data file. Anything you write in the package comes back with it.

Constraints on the environment, because step 4 is yours now and these are what
the pipeline that built these tasks learned the hard way:

- **Every COPY source must exist in the package.** A Dockerfile line referring to
  a file you did not write is the most common way a rewritten task is thrown
  away: it builds nowhere, and the failure arrives long after this session ended.
- **What you add has to fit in the row.** The package is shipped to training as
  one line of JSON: COPY sources together stay under 1 MiB, and files under
  `tests/` are text and together stay under 1 MiB; a binary under `tests/` is
  refused by name (the container's boot says so). A binary the environment needs is
  a COPY source under `environment/` or is produced by a RUN; a large reference
  the verifier needs is checked by hash, not shipped.
- Preserve the seed's base image and installation style; make the smallest change
  the changed condition needs. An environment rewritten wholesale is a new task,
  not a harder one.
- The sandbox has network access, and a task may use it -- fetching a pinned
  file, querying a public service -- when that is part of the work rather than
  decoration. Pin what is fetched (a URL plus a checksum or a version) so the
  reference solution stays reproducible, and make a failed fetch fail loudly:
  a non-zero exit and the error on stderr, never a silent fallback. The
  harness records what each sandbox could reach at boot, so a task that needs
  the network is diagnosable when a cluster's egress differs. No credentials
  and nothing that only works through a proxy.
- The container is the size training gives this task (`run/resources.json`),
  and `./sandbox check` measures what the reference solution costs in it. The
  task is provisioned from that measurement, never below the seed's size and
  never from a number you write. If the solution outgrows the box, `check`
  says what ran out; make the solution need less. `./sandbox check --max`
  measures at the platform ceiling (4 vCPU / 8 GiB / 10 GiB) and is for the
  rare harder task that genuinely needs more than the seed had, not a way
  past a failing check. A task that needs more than the ceiling never starts,
  so it is unrunnable rather than hard.
- Guard edits against paths you did not create (`test -f` first); prefer adding a
  local fixture over patching something the image cloned.

**Before you run `./sandbox check`, reconcile the verifier against the solution
one check at a time.** For each check, name the lines of `solve.sh` that make it
pass. A check with nothing behind it is the single largest way these tasks are
lost: the reference runs cleanly, scores zero, and the whole task is thrown away
after a full image build. Where nothing satisfies a check, fix `solve.sh` if the
check is fair, and fix the check if it asks for something the instruction never
promised and the workspace never reveals — that one would fail a real agent too.
Do this literally, check by check; an impression that it all hangs together is
what produces the failure.

Aim for a task a capable agent lands about half the time."""

_HARDER_JOB_BLIND = """This task was solved {solved} of {attempts} attempts and met the
hardening threshold.

{guidance}

The size of the rewrite is checked, not trusted. {growth_bound}
The seed has {seed_lines} non-comment solution lines; the verifier may gain at most {max_asserts} assertions over the
seed's {seed_asserts}. `./sandbox check` fails outside that and the caller
rejects the rewrite. Measured on this corpus: rewrites that grew to 125 lines
came back 0/16 five times in six, while the seed at its own size was solved
every time. These limits bound the patch size; student re-testing measures difficulty.

Then do the work in this order, one file at a time. The order is not
arbitrary — each file is written against the one before it, and the synthesis
pipeline that produced these tasks runs the same sequence for that reason:

  1. `solution/solve.sh` — adapt the existing solution to the changed condition.
     Preserve the parts that still apply.
  2. `instruction.md` — what the agent is told. Everything the new requirement
     needs checked has to be discoverable from it and from the files the image
     ships, because that is all the verifier's author will see (below).
  3. `environment/Dockerfile` — the environment the other two assume.

**`tests/` is your scratch checker, and it is thrown away.** Edit it as you
like so that `./sandbox check` exercises the new requirement against your
solution -- the check grades with whatever `tests/` holds, and must pass, with
the untouched workspace still failing. The verifier that ships with this rung
is written afterwards by a second session that is shown the instruction, the
environment and the previous revision's verifier -- not your draft, not your
solution -- and that keeps each inherited check whose requirement the
instruction still makes, changes or drops the ones the instruction changed or
dropped, and adds the checks the new requirement needs. So the seed's checks
are not a floor you must pass: a requirement you change is checked as you
state it. And a name only your solution knows will not be checked: state it in
the instruction, or make the result checkable by value. That is about names,
formats and output paths -- the deliverable. The fact your change makes the
agent infer stays in the environment for it to find, not in the instruction.

The verifier author first lists the public requirements, checks shipped material
for ways to bypass required work, and writes stable checks of the promised result.
Make the changed requirement distinguish a correct result from the observed
shortcut using only the instruction and environment; your traces and
`run/hardening.md` are withheld. Provide observable acceptance conditions rather
than grading instructions or solution hints. Keep incidental load, network
availability and scheduling out of correctness unless the public task explicitly
requires a performance or external-service condition.

Add whatever new files the change needs — a fixture the Dockerfile copies, a
config, a data file. Anything you write in the package comes back with it.

Constraints on the environment, because step 4 is yours now and these are what
the pipeline that built these tasks learned the hard way:

- **Every COPY source must exist in the package.** A Dockerfile line referring to
  a file you did not write is the most common way a rewritten task is thrown
  away: it builds nowhere, and the failure arrives long after this session ended.
- **What you add has to fit in the row.** The package is shipped to training as
  one line of JSON: COPY sources together stay under 1 MiB, and files under
  `tests/` are text and together stay under 1 MiB; a binary under `tests/` is
  refused by name (the container's boot says so). A binary the environment needs is
  a COPY source under `environment/` or is produced by a RUN; a large reference
  the verifier needs is checked by hash, not shipped.
- Preserve the seed's base image and installation style; make the smallest change
  the changed condition needs. An environment rewritten wholesale is a new task,
  not a harder one.
- The sandbox has network access, and a task may use it -- fetching a pinned
  file, querying a public service -- when that is part of the work rather than
  decoration. Pin what is fetched (a URL plus a checksum or a version) so the
  reference solution stays reproducible, and make a failed fetch fail loudly:
  a non-zero exit and the error on stderr, never a silent fallback. The
  harness records what each sandbox could reach at boot, so a task that needs
  the network is diagnosable when a cluster's egress differs. No credentials
  and nothing that only works through a proxy.
- The container is the size training gives this task (`run/resources.json`),
  and `./sandbox check` measures what the reference solution costs in it. The
  task is provisioned from that measurement, never below the seed's size and
  never from a number you write. If the solution outgrows the box, `check`
  says what ran out; make the solution need less. `./sandbox check --max`
  measures at the platform ceiling (4 vCPU / 8 GiB / 10 GiB) and is for the
  rare harder task that genuinely needs more than the seed had, not a way
  past a failing check. A task that needs more than the ceiling never starts,
  so it is unrunnable rather than hard.
- Guard edits against paths you did not create (`test -f` first); prefer adding a
  local fixture over patching something the image cloned.

Aim for a task a capable agent lands about half the time."""

# A task solved every time can be solved for the wrong reason: hardening a task
# whose verifier accepts a shortcut builds the new requirement on a check that
# does not hold. Repair is part of the rewrite, not a separate route; the loop
# records each one in evolution/repairs.jsonl.
_HARDER_REPAIR_NOTE = """

Before hardening, check that the successful attempts earned their reward. If
one passed with a result the instruction rules out -- a hard-coded answer where
the task requires computing it, skipped required work the verifier does not
check, an edited test or grading file -- or the task setup itself is broken,
repairing that is part of this rewrite: make the requirement the shortcut
skipped explicit and checkable, or fix the setup, then harden the repaired
task. Record every repair in `run/repair.json`: a list of objects with
nonempty "defect", "evidence" (attempt file and turn), "change" and "kind";
each repair is reported for review. "kind" is "reward_hack" for a shortcut an
attempt was rewarded for, "hidden_requirement" for a check the instruction
never states, "broken_setup" for an environment missing what the task needs,
and "other" for anything else. An attempt that solved the task the intended
way is not a defect, however quickly it did so."""

_EASIER_JOB = """This task was solved {solved} of {attempts} attempts.
Diagnose the attempts and reduce one obstacle while retaining a stated skill.
{guidance}"""

_PLAN_JOB = """This package is the original task, unchanged; the attempts in traces/
solved {solved} of {attempts}.

THE PLAN
A coordinator read the student's training attempts on every version of this
task and chose the version to build: the original plus the interventions in
run/intervention_plan.json, all of them together. Read it first. Each
intervention says what to change, its kind, and the bottleneck it targets.

Build exactly that version from this package. Apply every intervention as
specified and add no help the plan does not list. traces/history shows earlier
rewrites of this task: they built other versions, and none of their help is in
this one unless the plan lists it. Keep each intervention a separable part --
hint sentences as their own sentences, supplied work as its own files -- so a
later version can leave it out by building from the original without it. Help
about how to work -- a table to write, a check to run, an order of steps -- is
advice to the student, however the plan words it: it adds no requirement, and
the verifier grades the result as before, never whether the advice was
followed. An intervention of kind setup_hint, check_hint, direction_hint,
decomposition or feedback changes no requirement, so a version made only of
such interventions keeps tests/ exactly as the original has it: do not edit,
tighten or extend the verifier for it. Only scope_removal, smaller_instance and
supplied_work change what is graded, and then only for the part they change.
(Measured: v11's hint-only {{i2}} rewrote the verifier with exact-equality and
new ownership checks, and the original's reference solution failed it.) If an
intervention cannot be applied without breaking an invariant below, apply the
rest and say which one and why in change; if none can, write GIVE UP with the
reason.

Where the guidance below asks you to diagnose and choose the change, the plan
has chosen it: write the plan's bottleneck as bottleneck, and use the attempts
in traces/ to size the wording and as evidence. Every other rule below -- where
help lives, the invariants, the declaration -- holds as written.
{guidance}"""

_UNSOLVED_NOTE = """

This task has no reference solution: no model has solved it yet. Write
`solution/solve.sh` first, a script that does the work the instruction asks for
in this environment, and get `./sandbox oracle` to pass with it before you
simplify anything. The simplified task must still pass with it."""

_UNSOLVED_HARDER_NOTE = """

This task has no reference solution yet, which is why the seed counts 0
solution lines above; the solution's length is therefore not checked, only the
verifier's assertions. The attempts in `traces/` that reached reward 1 are your
oracle. Before you change anything, write `solution/solve.sh` for the task as it
stands, working from what those attempts did, and get `./sandbox oracle` to pass
with it. Then harden: step 1 adapts that solution, and the change is one rung
above it."""

_REPAIR_JOB = """Your rewrite did not survive the caller's check. It rebuilt the
package from scratch, ran `solution/solve.sh` against the verifier (exit
{exit_code}) and audited what the verifier demands against what an agent can
see. The verdict and the run's output are in `run/failure.txt`.

Read that first. If the run failed, find the check that failed and the lines
of `solution/solve.sh` responsible for it, and fix them; prefer fixing the
solution, and change the verifier only where it demands something the
instruction never promised and the workspace never reveals. If the verdict
names paths the verifier requires that nothing the agent can see reveals, name
them where the agent will read them (the instruction, or a file in the image
the instruction points at), or make the verifier stop depending on them; do
not weaken what it checks otherwise. If the verdict says the rewrite is more
than one rung above the seed, take requirements out until it is one: the
seed's deliverable plus one new thing. Do not rewrite what the run shows is
already working. The container you had is gone; a fresh one is booting, and
your first command waits for it.

Confirm with `./sandbox check` before you stop."""

_AGENT_VALIDATION_REPAIR_JOB = """Your previous session stopped without leaving
a readable `./sandbox check` record. The harness observed:

{problem}

Run `./sandbox check` once, as the rewrite stands, and stop. Change no file:
this session only records the check the rewrite needed before it ended.
Do not write `run/checks.jsonl` yourself; the sandbox command records the
result, and a check that does not pass discards the rewrite."""

_REPAIR_BLIND_NOTE = """

`tests/` here is the previous revision's checker again, a scratch copy for
your own `./sandbox check`; the verifier that failed is the blind session's,
which you are not shown, and it will be run against your repaired solution
next. Fix the solution, the instruction or the environment. Where
`run/failure.txt` shows the verifier looking for something an agent could not
have known, state it in the instruction if it is part of the deliverable (a
name, a format, a path); if it is the fact the change asks the agent to infer,
make it findable in the environment instead. You cannot edit that verifier."""

_VERIFIER_BLOCKED_JOB = """The sessions that write this task's verifier and its
independent controls, which never see your reference solution, stopped: from
the public task they could not tell whether some deliverable is right or
wrong. Their report is in `run/failure.txt`; it is a claim to check against the
instruction and the environment, not an established fact.

If the public task does leave that choice open, state the choice your
reference solution makes where an agent will read it (the instruction, or a
file in the image the instruction points at). Stating a choice the grading
already depends on does not change what the task asks for; change nothing
else. If the report is wrong, or the gap cannot be closed without changing
what the task asks for, write `GIVE UP: <reason>` to `run/verdict.txt` and stop.

`tests/` here is the previous revision's checker again, a scratch copy for
your own `./sandbox check`. New verifier sessions are written against the
corrected task next. The container you had is gone; a fresh one is booting,
and your first command waits for it.

Confirm with `./sandbox check` before you stop."""

_VERIFIER_JOB = """The task in this package was revised through a change to its
requirements or workflow. Write the verifier for the task as the
instruction states it.

You are shown the instruction, `environment/`, and the previous revision's
verifier at `{verifier_rel}` -- the one the task had before this change, not a
draft by whoever changed the task. The original seed had {seed_asserts}
assertions. You are not shown the reference solution, on purpose; AGENTS.md says
why. Keep every inherited check whose requirement the revised instruction still
makes; correct or remove one whose requirement the instruction changed or
removed; add what the new requirement needs. Use at most {max_asserts}
assertions above the original seed count, each satisfiable by an agent that has
read only the instruction and explored the container. Where the instruction
leaves a name open, check the value. Write `run/verifier-changes.md`: which
inherited checks you kept, changed or removed, and which you added, one line
each with the instruction sentence it rests on.

Then do the task yourself through `./sandbox exec`, the way the instruction
describes it, and `./sandbox grade`: it must pass. `./sandbox reset` and grade
the untouched workspace: it must fail. Save the correct script, a separate
single-error script for each independently falsifiable clause of the changed
requirement, and their cases contract under run/verifier-probes as AGENTS.md
specifies. The caller replays every script in a fresh Daytona environment before
accepting your verifier; each wrong script must execute successfully and then
fail grading."""

_VERIFIER_REPAIR_JOB = """The verifier you wrote does not agree with the task's reference solution, which
you cannot see: the caller ran that solution in a fresh container (exit
{exit_code}) and graded it with your verifier, and it failed. The run's output
is in `run/failure.txt`.

Read it first. The instruction is the contract. The usual cause is a check that
demands something the instruction does not ask for, or a name the instruction
leaves open: loosen that check to what the instruction actually promises, or
check the value instead of the name. Preserve seed checks for requirements retained
in the revised instruction, and do not weaken a check the instruction plainly requires. If the
run failed a check the instruction does require, the solution is what is wrong:
write `BLOCKED: <which check, and what the run showed>` to `run/verdict.txt` and
stop, and the caller sends the solution back.

If instead the verdict says the verifier is more than one rung above the seed --
too many assertions over the seed's count -- the solution passed and the size
is what failed: fold the checks for the new requirement into fewer assertions,
or drop the ones that restate what the seed's already cover, until the count is
within the limit. Measured on the first paired round, that was the one repair
of six blind rewrites: ten assertions against a seed's four, trimmed to pass."""


_BUDGET = """

Budget: this session is ended at {deadline} ({budget_min} minutes from now),
whatever state the files are in, and a session that ends that way is discarded
whole. `reset` and `check` build the image and take minutes each;
`./sandbox exec` takes seconds. Plan for two or three checks, not for guessing."""


_ROLE_PATCH_NOTE = """

WHERE THIS PACKAGE KEEPS ITS SOLUTION AND ITS CHECKS
`solution/solve.sh` here is a wrapper that applies `solution/fix.patch`, and
`tests/test.sh` is a runner that applies `tests/test.patch` and runs the graded
test ids listed at its end. The task's real solution is the fix patch and its
real checks are the test patch; the size limits above are measured on those,
and the two scripts change only where a commit or an id list has to change.
"""


def _recorded_solution_prompt(prompt: str) -> str:
    return (
        prompt.replace("solution/solve.sh", ev.ACTION_PATH).replace(
            "`solve.sh`", "`gpt6_actions.json`"
        )
        + """

The reference solution is a JSON list of recorded terminal actions. Edit its
keystrokes to solve the revised task. Keep step_id, offset_sec, duration and
kind on each entry; preserve interactive keystrokes and waits. End with two
completion_marker entries. ./sandbox oracle and check replay the list through
Terminus in one persistent terminal. Do not concatenate it into a shell script
or add solve.sh to override it. Solution size counts non-comment keystroke lines,
excluding JSON metadata and completion markers.
"""
    )


def _budget(timeout: int) -> str:
    deadline = time.strftime("%H:%M %Z", time.localtime(time.time() + timeout))
    return _BUDGET.format(deadline=deadline, budget_min=timeout // 60)


def _blind_layout(pkg: Path, vpkg: Path) -> None:
    """The author's package minus what the verifier's author must not see."""
    hidden = set(HIDDEN_FROM_VERIFIER)

    def ignore(d, names):
        rel = Path(d).relative_to(pkg)
        if rel == Path("run"):
            # Preserve harness inputs, excluding author analysis and oracle output.
            return set(names) - set(_LAYOUT_RUN_INPUTS)
        return {
            n
            for n in names
            if str(rel / n) in hidden or (str(rel) == "." and n in hidden)
        }

    shutil.copytree(pkg, vpkg, dirs_exist_ok=True, ignore=ignore)
    vpkg.chmod(0o700)
    (vpkg / "run").mkdir(exist_ok=True)
    shutil.copy2(VERIFIER_SPEC, vpkg / "AGENTS.md")
    shutil.copy2(SANDBOX, vpkg / "sandbox")
    os.chmod(vpkg / "sandbox", 0o755)


def _seed_tests(task: dict) -> Path | None:
    """The previous revision's ``tests/``: what the blind author starts from.
    The author's package may carry its own draft, which is scratch."""
    seed_dir = task.get("_seed_dir")
    if seed_dir and (Path(seed_dir) / "tests").is_dir():
        return Path(seed_dir) / "tests"
    return None


def _restore_seed_tests(vpkg: Path, seed_tests: Path | None) -> None:
    """Put the previous revision's verifier under the blind layout in place of
    whatever the author left in ``tests/``. The author may edit ``tests/`` as
    a scratch checker for its own ``./sandbox check``; that draft carries the
    solution's private vocabulary, which is exactly what the blind session
    must not see, and it is discarded when the blind verifier is taken."""
    if seed_tests is None:
        return
    shutil.rmtree(vpkg / "tests", ignore_errors=True)
    shutil.copytree(seed_tests, vpkg / "tests")


def _take_verifier(
    vpkg: Path,
    pkg: Path,
    seed_rel: str,
    seed_text: str,
    *,
    seed_tests: Path | None = None,
    allow_unchanged: bool = False,
) -> str:
    """Copy the verifier the blind author wrote into the author's package,
    replacing whatever ``tests/`` held (the seed's, or the author's scratch
    draft), and return its path. Calibration, a simplification that only adds
    guidance, and a hardening that only withdraws help may retain a reviewed
    verifier; other jobs must change it -- judged against the previous revision's tests,
    which is what the blind author was given."""
    rel = _verifier_on_disk(vpkg, seed_rel)
    text = (vpkg / rel).read_text()
    baseline = seed_tests if seed_tests is not None else pkg / "tests"
    if (
        rel == seed_rel
        and text == seed_text
        and not allow_unchanged
        and _probe_hashes(vpkg / "tests") == _probe_hashes(baseline)
    ):
        raise RuntimeError("verifier author changed nothing")
    # Shell graders consume sibling patches and fixtures. Transfer exactly the
    # test tree that passed the independent probes, including file deletions.
    shutil.rmtree(pkg / "tests")
    shutil.copytree(vpkg / "tests", pkg / "tests")
    # The author's last check graded its own scratch verifier; it says nothing
    # about this one, so it must not satisfy _require_checked.
    (pkg / "run" / "checks.jsonl").unlink(missing_ok=True)
    return rel


def _probe_hashes(package: Path, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in package.rglob("*")
        if p.is_file()
        and p.relative_to(package).parts[0] not in exclude
        and "__pycache__" not in p.parts
    }


_PROBE_CONTRACT_JOB = """Your replay controls under `run/verifier-probes/` have
an incomplete contract or do not match the scripts you wrote: {problem}.

If the directory or `contract.json` is absent, create it now.
Every declared case must have nonempty string fields `requirement`,
`quote`, `source`, `wrong_behavior`, and `expected_failure`; `quote` is the
public clause copied verbatim from `source` (`instruction.md` or a file under
`environment/`) and must appear there. A case whose clause no public sentence
states is removed, together with its script and the check it exercised. The caller replays `correct.sh` and
one `wrong-N.sh` per declared case, in contract order. Complete any missing
fields and scripts -- each wrong script must be a single-error variant of the
correct solution for its case -- or remove unfinished cases so they agree.
Change nothing else: the task and the other controls stay as they are, and
the verifier changes only to drop a check whose case was removed."""


_DECLARATION_JOB = """The caller cannot accept `run/simplify.json`: {problem}.

Each evidence item names an attempt file exactly as `traces/` lists it, with
its .jsonl suffix and no directory (for example `attempt-03.jsonl`), and a
line of it: a turn the file records, or turn 0 for its first line, the
attempt's summary. A result of your own sandbox test belongs in the
observation of the item for the attempt whose implementation you ran, not in
an item of its own. Correct the declaration so every field and every evidence location is valid, keeping the
diagnosis it states. Change nothing else: the task, the reference solution and
the environment stay as they are, and your last `./sandbox check` still stands."""


def _require_declaration(
    rewrite: layout.RewriteDir,
    author: layout.SessionDir,
    hint: str,
    deadline: RewriteDeadline,
) -> None:
    """Check a simplify author's declaration while its session can still fix it.

    read_decision used to run only after the blind verifier, the probes and the
    reconciling check, so a declaration naming `attempt-01` or a turn the file
    does not hold discarded a rewrite that had passed all of them: 47 of 128
    simplify rewrites on 2026-09-29 (Kimi K3). One resume of the author with the
    problem, as _repair_probe_contract does for the probe author's contract."""
    pkg = rewrite.package
    try:
        so.read_decision(pkg, hint)
        return
    except (OSError, ValueError) as error:
        problem = f"{type(error).__name__}: {error}"
    before = _probe_hashes(pkg, ("run", "traces"))
    timeout = _session_timeout(deadline, "simplify declaration repair")
    with session(rewrite, "declaration", timeout=timeout, resumes=author) as run:
        _run_codex(
            run,
            pkg,
            _DECLARATION_JOB.format(problem=problem) + _budget(timeout),
            resume=_session_id(author),
        )
    _check_verdict(pkg)
    if _probe_hashes(pkg, ("run", "traces")) != before:
        raise RuntimeError("declaration repair changed task files")
    so.read_decision(pkg, hint)


def _repair_probe_contract(
    rewrite: layout.RewriteDir,
    vsession: layout.SessionDir,
    problem: str,
    deadline: RewriteDeadline,
) -> SessionRun:
    """One resume of the verifier's session to reconcile its contract with the
    scripts it wrote. A declared case with no script used to raise
    FileNotFoundError out of the replay reader and discard the whole rewrite:
    8 of 17 failures in the first day on hip, and the cheapest possible fix
    is asking the session that wrote the other six."""
    sid = _session_id(vsession)
    timeout = _session_timeout(deadline, "probe contract repair")
    with session(
        rewrite, "probe-contract", timeout=timeout, resumes=vsession
    ) as run:
        _run_codex(
            run,
            vsession.package,
            _PROBE_CONTRACT_JOB.format(problem=problem) + _budget(timeout),
            resume=sid,
        )
    return run


def _verify_original_probes(
    vsession: layout.SessionDir,
    rewrite: layout.RewriteDir | None = None,
    deadline: RewriteDeadline | None = None,
) -> None:
    def _replay() -> None:
        original = vsession.path / "original-verifier-probes"
        if not original.exists():
            source = vsession.package / "run/verifier-probes"
            if not source.is_dir():
                raise SemanticProbeContract("Missing run/verifier-probes directory")
            shutil.copytree(source, original)
            package = vsession.package
        else:
            package = Path(
                tempfile.mkdtemp(prefix="original-replay-", dir=vsession.path)
            )
            _blind_layout(vsession.package, package)
            shutil.copytree(original, package / "run/verifier-probes")
        timeout = (
            _session_timeout(deadline, "verifier probe replay")
            if deadline is not None
            else AGENT_TIMEOUT
        )
        verify_probes(
            package,
            _harness_env(),
            timeout,
            deadline=deadline,
        )

    try:
        _replay()
    except SemanticProbeContract as error:
        if rewrite is None:
            raise
        deadline = deadline or rewrite_deadline(rewrite)
        log.info("probe contract mismatch, asking the verifier session: %s", error)
        shutil.rmtree(vsession.path / "original-verifier-probes", ignore_errors=True)
        _repair_probe_contract(rewrite, vsession, str(error), deadline)
        _replay()


def _author_independent_probes(
    rewrite: layout.RewriteDir,
    public_source: Path,
    pointer: Path,
    *,
    allow_repair: bool = True,
    deadline: RewriteDeadline | None = None,
) -> None:
    deadline = deadline or rewrite_deadline(rewrite)
    timeout = _session_timeout(deadline, "independent probe author")
    with session(rewrite, "probe", timeout=timeout) as run:
        probe = run.dir.package
        _blind_layout(public_source, probe)
        shutil.rmtree(probe / "tests")
        (probe / "tests").mkdir()
        (probe / "tests/test.sh").write_text(
            "#!/bin/sh\nmkdir -p /logs/verifier\necho 0 > /logs/verifier/reward.txt\nexit 2\n"
        )
        for name in ("seed_size.json", "seed_literals.json"):
            (probe / "run" / name).unlink(missing_ok=True)
        shutil.copy2(
            VERIFIER_SPEC.with_name("independent_verifier_probes.md"),
            probe / "AGENTS.md",
        )

        before = _probe_hashes(probe, ("run",))
        run.meta["public_inputs_sha256"] = before
        try:
            _run_codex(
                run,
                probe,
                "Create independent semantic controls from the public task.\n"
                + _budget(timeout),
            )
        finally:
            _sandbox_down(probe)
        _check_verdict(probe)
        after = _probe_hashes(probe, ("run",))
        changed = sorted(
            name
            for name in before.keys() | after.keys()
            if before.get(name) != after.get(name)
        )
        if changed:
            raise RuntimeError(
                "Independent probe author changed files outside run/: "
                + ", ".join(changed)
            )
        try:
            load_probe_contract(probe)
        except SemanticProbeContract as error:
            if not allow_repair:
                raise
            log.info(
                "independent probe contract mismatch, resuming author: %s", error
            )
            repair = _repair_probe_contract(
                rewrite, run.dir, str(error), deadline
            )
            if _probe_hashes(probe, ("run",)) != before:
                raise RuntimeError(
                    "Independent probe contract repair changed public task files"
                ) from error
            try:
                load_probe_contract(probe)
            except SemanticProbeContract as repeated:
                raise AgentSessionError(
                    agent=EVOLVE_AGENT,
                    failure_type="agent_missing_output",
                    returncode=int(repair.meta.get("exit_code") or 0),
                    stderr_path=repair.dir.stderr,
                ) from repeated
    layout.write_json_atomic(
        pointer,
        {
            "package": str(probe),
            "controls_sha256": _probe_hashes(probe / "run/verifier-probes"),
        },
    )


_LAYOUT_RUN_INPUTS = (
    "seed_size.json",
    "resources.json",
    "seed_literals.json",
    "pretest.json",
)


def _replay_inputs(vpkg: Path, probe: Path) -> tuple[dict, dict] | None:
    """Hashes of everything an independent replay reads: what _blind_layout
    copies from the verifier's package, and the probe author's controls. Each
    file carries its content, executable bits and symlink target. None when
    the package holds a directory symlink, whose contents the walk does not
    read: such inputs never reuse an early replay."""
    files = {}
    for dirpath, dirnames, filenames in os.walk(vpkg):
        rel_dir = Path(dirpath).relative_to(vpkg)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d != "run"]
            names = filenames
            names = names + [
                f"run/{n}"
                for n in _LAYOUT_RUN_INPUTS
                if (vpkg / "run" / n).is_file() or (vpkg / "run" / n).is_symlink()
            ]
            if (vpkg / "run").is_symlink():
                return None
        else:
            names = filenames
        if any((Path(dirpath) / d).is_symlink() for d in dirnames):
            return None
        for name in names:
            path = Path(dirpath) / name
            link = os.readlink(path) if path.is_symlink() else None
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                files[str(rel_dir / name)] = (
                    digest, path.stat().st_mode & 0o111, link
                )
            elif link is not None:
                files[str(rel_dir / name)] = (None, None, link)
    return files, _probe_hashes(probe / "run/verifier-probes")


def _replay_independent(
    vpkg: Path, probe: Path, deadline: RewriteDeadline
) -> None:
    """Replay the probe author's controls against the verifier's package in a
    fresh copy. Raises SemanticProbeMisses when a control is graded against
    its declared expectation."""
    replay = Path(tempfile.mkdtemp(prefix="replay-", dir=probe.parent))
    _blind_layout(vpkg, replay)
    shutil.copytree(probe / "run/verifier-probes", replay / "run/verifier-probes")
    timeout = _session_timeout(deadline, "independent probe replay")
    verify_probes(
        replay,
        _harness_env(),
        timeout,
        deadline=deadline,
    )


def _early_independent_replay(
    probe_future: Future,
    vsession: layout.SessionDir,
    deadline: RewriteDeadline,
    cancel: threading.Event | None = None,
) -> tuple[tuple, SemanticProbeMisses | None] | None:
    """The first independent replay, run beside the original-probe replay.

    Both read the verifier's finished package and neither writes outside its
    own run/, so neither can change the other's inputs. Returns the inputs it
    replayed and its outcome, or None if they moved while it ran (or it was
    cancelled, or its inputs cannot be hashed in full);
    _independent_verifier uses it only if those inputs are still current.
    """
    probe_future.result()
    if cancel is not None and cancel.is_set():
        return None
    pointer = vsession.path / "independent-probes.json"
    probe = Path(json.loads(pointer.read_text())["package"])
    before = _replay_inputs(vsession.package, probe)
    if before is None:
        return None
    try:
        _replay_independent(vsession.package, probe, deadline)
    except SemanticProbeMisses as error:
        outcome = error
    else:
        outcome = None
    if _replay_inputs(vsession.package, probe) != before:
        return None
    return before, outcome


def _early_outcome(early: Future | None, vpkg: Path, probe: Path):
    """The early replay's outcome if it replayed the current inputs, else _RERUN.

    Any other failure of the early run (a transport error, the deadline)
    is not reused either: the replay then runs again here, as it always did."""
    if early is None:
        return _RERUN
    try:
        got = early.result()
    except Exception as error:  # noqa: BLE001 -- rerun reproduces a real failure
        log.info("early independent replay not used: %s", str(error)[:200])
        return _RERUN
    if got is None:
        return _RERUN
    current = _replay_inputs(vpkg, probe)
    if current is None or got[0] != current:
        return _RERUN
    return got[1]


_RERUN = object()


def _add_time(timings: dict | None, stage: str, seconds: float) -> None:
    """Accumulate one stage's wall seconds into the rewrite's timings."""
    if timings is not None:
        timings[stage] = round(timings.get(stage, 0.0) + seconds, 1)


def _independent_verifier(
    rewrite: layout.RewriteDir,
    vsession: layout.SessionDir,
    *,
    allow_repair: bool = True,
    deadline: RewriteDeadline | None = None,
    early: Future | None = None,
) -> None:
    deadline = deadline or rewrite_deadline(rewrite)
    vpkg = vsession.package
    pointer = vsession.path / "independent-probes.json"
    if not pointer.exists():
        _author_independent_probes(
            rewrite,
            vpkg,
            pointer,
            allow_repair=allow_repair,
            deadline=deadline,
        )
    probe = Path(json.loads(pointer.read_text())["package"])

    controls_sha256 = json.loads(pointer.read_text())["controls_sha256"]
    public_sha256 = _probe_hashes(vpkg, ("run", "tests"))
    for attempt in range(2 if allow_repair else 1):
        if _probe_hashes(probe / "run/verifier-probes") != controls_sha256:
            raise RuntimeError("Independent controls changed during verifier repair")
        outcome = _early_outcome(early, vpkg, probe) if attempt == 0 else _RERUN
        try:
            if outcome is _RERUN:
                _replay_independent(vpkg, probe, deadline)
            elif outcome is not None:
                raise outcome
        except SemanticProbeMisses as error:
            if not allow_repair or attempt:
                raise
            (vpkg / "run/independent-failures.jsonl").write_text(
                error.log_path.read_text()
            )
            (vpkg / "run/verdict.txt").unlink(missing_ok=True)
            timeout = _session_timeout(deadline, "independent probe repair")
            with session(
                rewrite, "probe-repair", timeout=timeout, resumes=vsession
            ) as run:
                try:
                    _run_codex(
                        run,
                        vpkg,
                        "Independent controls received grades inconsistent with their declared expectations. "
                        "Read run/independent-failures.jsonl for their public requirements, scripts, and grading evidence. "
                        "First establish whether the control labeled correct actually satisfies every public requirement, "
                        "and whether each negative control's actual state at grading violates the public task. "
                        "For a final-artifact task, an earlier failure on changed inputs "
                        "does not invalidate a correct final artifact; "
                        "require reusable behavior only when the public task explicitly requires it. "
                        "If the correct control is invalid, a negative control is valid, or a control's classification cannot be established, "
                        "write BLOCKED: <evidence> to run/verdict.txt and stop. "
                        "Otherwise repair the verifier to accept the demonstrated valid deliverable and reject the demonstrated violations. "
                        "The caller also replays your original pre-repair controls unchanged; replacing a saved control does not remove that regression check. "
                        "Do not impose a representation or implementation restriction absent from the public task. "
                        "Preserve the public task and follow AGENTS.md, including your own replay controls.\n"
                        + _budget(timeout),
                        resume=_session_id(vsession),
                    )
                finally:
                    _sandbox_down(vpkg)
            _check_verdict(vpkg)
            if _probe_hashes(vpkg, ("run", "tests")) != public_sha256:
                raise RuntimeError(
                    "Verifier repair changed public task files"
                ) from error
            _verify_original_probes(vsession, rewrite, deadline)
        else:
            return


def _instruction_change_note(before: Path, after: Path) -> str:
    """The public instruction's diff against the previous revision, for the
    blind verifier author. Without it the author sees only the new text and the
    inherited checks, so a requirement the rewrite narrowed in one sentence while
    another sentence still reads the old way keeps its old checks, and the
    revision then rejects work its instruction permits."""
    try:
        old = (before / "instruction.md").read_text()
        new = (after / "instruction.md").read_text()
    except OSError:
        return ""
    if old == new:
        return ""
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        "previous/instruction.md", "instruction.md",
    ))
    return (
        "\nThe public instruction changed in this revision:\n\n```diff\n"
        + diff
        + "```\n\nA requirement this diff removes or narrows loses or narrows its "
        "inherited checks: an inherited check of the old scope now rejects work the "
        "instruction permits. Where an unchanged sentence still reads the old way, "
        "the changed sentence states the narrowed scope; check that scope, and name "
        "the conflict in run/verifier-changes.md. A removed sentence that only fixed "
        "how the expected values are computed (a window, a convention) removes no "
        "requirement, and its inherited bounds stay as they are.\n"
    )


def _blind_verifier(
    rewrite: layout.RewriteDir,
    task: dict,
    fmap: dict,
    *,
    deadline: RewriteDeadline | None = None,
    timings: dict | None = None,
) -> tuple[layout.SessionDir, str]:
    """Second session: write the verifier without seeing the solution.

    Lays the author's package out again under the verifier session's own
    directory with `solution/` and the harness's own files removed, runs one
    session against AGENTS.md = verifier_author.md, and copies the verifier it
    wrote back into the author's package. Returns that session and the
    verifier's path. The caller then runs the check that the two sessions
    never could: the hidden solution against the blind verifier.
    """
    deadline = deadline or rewrite_deadline(rewrite)
    started = time.monotonic()
    pkg = rewrite.package
    seed_rel = fmap["test_state_py"]
    seed_text = task["test_state_py"]
    seed_tests = _seed_tests(task)
    seed_pkg = Path(task.get("_seed_dir") or rewrite.package)
    roles = task.get("_role_files") or {}
    seed_size = ts.size_of(
        task["solve_sh"],
        seed_text,
        "python" if seed_rel.endswith(".py") else "shell",
        solution_rel=fmap["solve_sh"],
        **{
            name: (seed_pkg / roles[key]).read_text(errors="replace")
            for key, name in (
                ("solve_sh", "fix_patch"),
                ("test_state_py", "test_patch"),
            )
            if key in roles and (seed_pkg / roles[key]).is_file()
        },
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        timeout = _session_timeout(deadline, "blind verifier author")
        with session(rewrite, "verifier", timeout=timeout) as run:
            vpkg = run.dir.package
            _blind_layout(pkg, vpkg)
            _restore_seed_tests(vpkg, seed_tests)
            prompt = _VERIFIER_JOB.format(
                verifier_rel=seed_rel,
                seed_asserts=seed_size["verifier_asserts"],
                max_asserts=ts.MAX_ADDED_ASSERTS,
            )
            if task.get("_role_files"):
                prompt += _ROLE_PATCH_NOTE
            prompt += _instruction_change_note(seed_pkg, pkg)
            prompt += _reported_defects(pkg, vpkg)
            prompt += _budget(timeout)
            # A simplification may only add guidance (a hint, a starter file) and
            # keep every requirement; the previous verifier then still checks the
            # whole contract, so an unchanged verifier is the correct outcome.
            # A hardening may only withdraw help an earlier simplification added
            # (guidance, a shipped example); the requirements, and so a verifier
            # that already checks them, are then unchanged.
            withdrawal_ok = task.get("_harder_mode") == "student"
            guidance_ok = (
                bool(task.get("_calibration")) or "_simplify_hint" in task or withdrawal_ok
            )
            if task.get("_calibration"):
                prompt += (
                    "\nThis adjusts a previous simplification. If the existing verifier "
                    "still fully checks the retained goal, keep it unchanged and validate it. "
                    "A change only to guidance does not require a new assertion.\n"
                )
            elif withdrawal_ok:
                prompt += (
                    "\nThis is a hardening. If it only withdraws guidance or shipped help "
                    "and the existing verifier still fully checks every requirement, keep "
                    "it unchanged and validate it.\n"
                )
            elif guidance_ok:
                prompt += (
                    "\nThis is a simplification. If it only adds guidance and the existing "
                    "verifier still fully checks every retained requirement, keep it "
                    "unchanged and validate it.\n"
                )
            # The probe author sees the public task copy, never the verifier's
            # changing package. Its controls replay only after both authors finish.
            probe_future = pool.submit(
                _author_independent_probes,
                rewrite,
                pkg,
                run.dir.path / "independent-probes.json",
                deadline=deadline,
            )
            try:
                _run_codex(run, vpkg, prompt)
            finally:
                _sandbox_down(vpkg)
        _check_verdict(vpkg)
        sessions_done = time.monotonic()
        cancel_early = threading.Event()
        early = pool.submit(
            _early_independent_replay, probe_future, run.dir, deadline, cancel_early
        )
        try:
            _verify_original_probes(run.dir, rewrite, deadline)
        except BaseException:
            # Rejected: the early replay's verdict will never be read.
            cancel_early.set()
            early.cancel()
            raise
        probe_future.result()
    _independent_verifier(rewrite, run.dir, deadline=deadline, early=early)
    _add_time(timings, "blind_verifier", sessions_done - started)
    _add_time(timings, "independent_verifier", time.monotonic() - sessions_done)
    rel = _take_verifier(
        vpkg,
        pkg,
        seed_rel,
        seed_text,
        seed_tests=seed_tests,
        allow_unchanged=guidance_ok,
    )
    return run.dir, rel


def _blind_repair(
    rewrite: layout.RewriteDir,
    vsession: layout.SessionDir,
    fmap: dict,
    observed: str,
    exit_code: int,
    *,
    deadline: RewriteDeadline | None = None,
) -> str:
    """Resume the verifier's session with the failure the hidden solution
    produced against its verifier, and take the repaired verifier back."""
    deadline = deadline or rewrite_deadline(rewrite)
    pkg, vpkg = rewrite.package, vsession.package
    seed_rel = fmap["test_state_py"]
    sid = _session_id(vsession)
    (vpkg / "run").mkdir(exist_ok=True)
    (vpkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    (vpkg / "run" / "verdict.txt").unlink(missing_ok=True)
    timeout = _session_timeout(deadline, "blind verifier repair")
    with session(rewrite, "repair", timeout=timeout, resumes=vsession) as run:
        prompt = _VERIFIER_REPAIR_JOB.format(exit_code=exit_code) + _budget(timeout)
        try:
            _run_codex(run, vpkg, prompt, resume=sid)
        finally:
            _sandbox_down(vpkg)
    _check_verdict(vpkg)
    _verify_original_probes(vsession, rewrite, deadline)
    _independent_verifier(
        rewrite, vsession, allow_repair=False, deadline=deadline
    )
    before = (pkg / _verifier_on_disk(pkg, seed_rel)).read_text()
    return _take_verifier(vpkg, pkg, _verifier_on_disk(pkg, seed_rel), before)


def _exit_code(pkg: Path) -> int:
    chk = _last_check(pkg) or {}
    return int(chk["solve_exit"]) if chk.get("solve_exit") is not None else 1


def _reinstall_blind_verifier(vsession: layout.SessionDir, pkg: Path) -> None:
    """Put the blind verifier back into the author's package after the author
    worked with a scratch ``tests/``; the author's last check graded that
    scratch and must not count."""
    shutil.rmtree(pkg / "tests", ignore_errors=True)
    shutil.copytree(vsession.package / "tests", pkg / "tests")
    (pkg / "run" / "checks.jsonl").unlink(missing_ok=True)


def _repair_iterations(
    rewrite: layout.RewriteDir,
    task: dict,
    vsession: layout.SessionDir,
    fmap: dict,
    observed: str,
    code: int,
    *,
    check_name: str,
    rounds: int | None = None,
    deadline: RewriteDeadline,
) -> str:
    """The hidden solution and the blind verifier disagree. Repair in turns,
    up to REPAIR_ROUNDS iterations inside one rewrite deadline: first the
    author's session, resumed with the failure and with the previous
    revision's checker in ``tests/`` rather than the verifier it was kept
    away from; then, if the pair still disagrees, the verifier's session.
    Returns the verifier's path once a check passes; raises when the rounds
    or the budget run out. The seed goes back into training unchanged, which
    is the safe outcome for a pair that cannot agree."""
    pkg = rewrite.package
    rel = fmap["test_state_py"]
    text = observed
    rounds = REPAIR_ROUNDS if rounds is None else rounds
    for i in range(1, rounds + 1):
        _session_timeout(deadline, "blind author repair")
        # 1. the author, without the blind verifier
        task = _resume_author_blind(
            rewrite,
            task,
            text[-4000:],
            code,
            seq=i,
            deadline=deadline,
        )
        _reinstall_blind_verifier(vsession, pkg)
        text = _harness_check(
            pkg,
            name=f"{check_name}.author{i}",
            timeout=_session_timeout(deadline, "blind author check"),
        )
        if _agent_checked(pkg):
            return rel
        code = _exit_code(pkg)
        # 2. the verifier's author, with the run against the repaired solution
        rel = _blind_repair(
            rewrite,
            vsession,
            fmap,
            text[-4000:],
            code,
            deadline=deadline,
        )
        fmap["test_state_py"] = rel
        text = _harness_check(
            pkg,
            name=f"{check_name}.verifier{i}",
            timeout=_session_timeout(deadline, "blind verifier check"),
        )
        if _agent_checked(pkg):
            return rel
        code = _exit_code(pkg)
    raise RuntimeError(
        f"blind verifier and reference solution still disagree after "
        f"{rounds} repair iteration(s): " + text[-300:].replace("\n", " | ")
    )


def _blind_verifier_answered(
    rewrite: layout.RewriteDir,
    task: dict,
    fmap: dict,
    *,
    deadline: RewriteDeadline,
    timings: dict | None = None,
) -> tuple[layout.SessionDir, str, dict]:
    """_blind_verifier, with the author answering a verifier side that stopped.

    The verifier and probe sessions write BLOCKED when the public task leaves
    open something the grading depends on (which width convention a fitted
    `gamma` reports, which class the value 0 belongs to). Only the author can
    close that, since only the author may edit the task, so the report goes
    back to the author's session and fresh verifier sessions are written
    against the corrected task. Bounded like _repair_iterations: REPAIR_ROUNDS
    answers, all inside the shared rewrite deadline. Returns the verifier
    session, the verifier's path, and the task with the author's latest
    session recorded."""
    answers = 0
    while True:
        try:
            vsession, rel = _blind_verifier(
                rewrite, task, fmap, deadline=deadline, timings=timings
            )
            return vsession, rel, task
        except Blocked as blocked:
            if answers == REPAIR_ROUNDS:
                raise
            answers += 1
            log.info("blind verifier stopped, answering from the author: %s", blocked)
            task = _resume_author_blind(
                rewrite,
                task,
                blocked.report or str(blocked),
                0,
                seq=answers,
                deadline=deadline,
                job=_VERIFIER_BLOCKED_JOB,
            )


def _reconcile_blind(
    rewrite: layout.RewriteDir,
    vsession: layout.SessionDir,
    fmap: dict,
    task: dict,
    deadline: RewriteDeadline,
) -> bool:
    """Hidden solution against blind verifier, with bounded repair.

    The two sessions never saw each other's file, so the first time they meet
    is here. A failure means the verifier asks for something the instruction
    did not promise, or the solution does not do what the instruction says;
    _repair_iterations lets each side answer in turn. Returns whether the
    first check passed, i.e. no repair touched the package.
    """
    pkg = rewrite.package
    text = _harness_check(
        pkg,
        name="check.blind1",
        timeout=_session_timeout(deadline, "blind handoff check"),
    )
    if _agent_checked(pkg):
        return True
    fmap["test_state_py"] = _repair_iterations(
        rewrite,
        task,
        vsession,
        fmap,
        text,
        _exit_code(pkg),
        check_name="check.blind",
        deadline=deadline,
    )
    return False


PROVIDER_RESUMES = int(os.environ.get("EVOLVE_PROVIDER_RESUMES", "0"))
PROVIDER_RESUME_SEC = int(os.environ.get("EVOLVE_PROVIDER_RESUME_SEC", "120"))
PROVIDER_RESUME_MAX_SEC = 900

_PROVIDER_RESUME_JOB = """The model provider cut your session off ({failure}); nothing
you did caused it. Every file is as you left it. Continue the task from where
you stopped, without redoing finished work, and finish it as the task asked."""


def _repair_agent_validation(
    rewrite: layout.RewriteDir,
    prior: layout.SessionDir,
    pkg: Path,
    problem: AgentValidationError,
    deadline: RewriteDeadline,
) -> tuple[SessionRun, subprocess.CompletedProcess, AgentSessionError | None]:
    """Resume an author that left no check record, once, to run the check.

    Only for a missing record. A last check that did not pass is not repaired
    here: AGENTS.md discards a rewrite that never passed `./sandbox check`."""
    try:
        sid = _session_id(prior)
    except RuntimeError as error:
        raise problem from error

    agent_error = None
    run = None
    timeout = _session_timeout(deadline, "agent validation", AGENT_VALIDATION_TIMEOUT)
    try:
        with session(
            rewrite, "agent-validation", timeout=timeout, resumes=prior
        ) as run:
            try:
                result = _run_codex(
                    run,
                    pkg,
                    _AGENT_VALIDATION_REPAIR_JOB.format(problem=problem)
                    + _budget(timeout),
                    resume=sid,
                )
            finally:
                _sandbox_down(pkg)
    except AgentSessionError as error:
        assert error.result is not None
        result, agent_error = error.result, error

    with _checked_output(agent_error):
        _check_verdict(pkg)
    try:
        _require_checked(pkg)
    except AgentValidationError as error:
        error.returncode = result.returncode
        if agent_error is not None:
            raise error from agent_error
        raise
    assert run is not None
    return run, result, agent_error


def _notify_handoff(
    handoff, task: dict | None, pkg: Path, fmap: dict, author_check: dict | None
) -> None:
    """Tell the caller what the package is before the blind handoff check.

    What _collect will return if the check passes, except the measurement,
    which is the author's last passing check (the handoff check replaces it);
    the caller compares its probe inputs again before using anything. Best
    effort: a failure here never touches the rewrite."""
    if handoff is None:
        return
    try:
        if task is None:
            handoff(None)
            return
        pre = _collect(task, pkg, fmap)
        chk = author_check or {}
        if chk.get("verdict") == "pass":
            pre["_measured"] = chk.get("measured")
            pre["_box"] = chk.get("resources")
            pre["_at_max"] = bool(chk.get("at_max"))
        handoff(pre)
    except Exception as error:  # noqa: BLE001 -- an optimisation, never a verdict
        log.warning("handoff notification failed: %s", str(error)[:200])


def _reported_defects(pkg: Path, vpkg: Path) -> str:
    """Hand the verifier's author the defects the rewrite's author reported in
    the inherited task, so a repair to a check is not lost: the author's own test
    edits are discarded, and a verifier rewritten from the instruction alone
    keeps the inherited check that caused the defect. Only the defect and its
    evidence from the student's attempts travel -- nothing of the solution or of
    the author's draft checker. A malformed declaration is left to the author's
    side, where it fails the rewrite."""
    try:
        repairs = _read_repairs(pkg)
    except (OSError, ValueError):
        return ""
    if not repairs:
        return ""
    (vpkg / "run" / "reported-defects.json").write_text(json.dumps(
        [{"defect": r["defect"], "evidence": r["evidence"]} for r in repairs], indent=1) + "\n")
    return (
        "\n\nREPORTED DEFECTS. `run/reported-defects.json` lists defects the "
        "task's author found in the inherited task, each with evidence from the "
        "student's attempts. Where one says an inherited check rejects an answer "
        "the instruction permits, judge that against the instruction yourself: if "
        "the instruction permits the answer, correct the check so it accepts it "
        "while still checking the required content -- the bound was never the "
        "task's, so this is not widening; if it does not, keep the check. Either "
        "way, record your decision on each in `run/verifier-changes.md`.\n"
    )


def _read_repairs(pkg: Path) -> list[dict]:
    """The defects a harder or easier rewrite repaired on the way, from run/repair.json.

    Repair is part of the rewrite rather than a route of its own; the loop
    reports each entry in evolution/repairs.jsonl and counts it under its kind.
    A malformed declaration fails the rewrite, as a malformed simplify
    declaration does.
    """
    path = pkg / "run" / "repair.json"
    if not path.exists():
        return []
    repairs = json.loads(path.read_text())
    if isinstance(repairs, dict):
        repairs = [repairs]
    if not isinstance(repairs, list):
        raise ValueError("run/repair.json must be a list of repairs")
    for repair in repairs:
        for key in ("defect", "evidence", "change"):
            if not isinstance(repair, dict) or not str(repair.get(key) or "").strip():
                raise ValueError(f"each repair in run/repair.json needs a nonempty {key}")
        if repair.get("kind") not in REPAIR_KINDS:
            raise ValueError(
                f"each repair in run/repair.json needs a kind, one of {', '.join(REPAIR_KINDS)}"
            )
    return repairs


def evolve_agentic(
    rewrite: layout.RewriteDir,
    task: dict,
    job: str,
    *,
    observed: str = "",
    exit_code: int = 1,
    deadline: RewriteDeadline | None = None,
    handoff=None,
    timings: dict | None = None,
) -> dict:
    """Run one agent session over the rewrite's package, with its container as
    a tool.

    The agent works in the package itself and reaches the task's own
    environment through ./sandbox: a container it can run commands in, run the
    reference solution in, grade, and rebuild fresh. The caller still
    revalidates afterwards; the agent's own pass is not the gate, it is what
    stops the agent from finishing on a rewrite it never ran.

    `job` is "harder", "easier", or "repair". The attempts are the records
    the loop hardlinked under ``traces/``. Raises on failure; raises Blocked
    when the agent declined.

    ``handoff``, when given, is called with the package as collected just
    before the blind handoff check, and again with None if that check needed
    a repair: the caller may start its Daytona probes early and must drop them
    then. ``timings`` collects per-stage wall seconds.
    """
    _require_codex()
    deadline = deadline or rewrite_deadline(rewrite)
    stage_start = time.monotonic()
    # A retried session rewrites the package; nothing started before it stands.
    _notify_handoff(handoff, None, rewrite.package, {}, None)
    author_timeout = _session_timeout(deadline, f"{job} author")
    if job == "harder":
        task = {**task, "_harder_mode": "student"}
    pkg = rewrite.package
    solved = task.get("_solved", 0)
    attempts_n = task.get(
        "_attempts", len(list(rewrite.traces.glob("attempt-*.jsonl"))) or 16
    )
    previous = rewrite.traces / "previous-simplify.json"
    calibration = (
        job == "harder"
        and attempts_n > 0
        and (solved == attempts_n or (HARDER_FROM_PARTIAL and 0 < solved < attempts_n))
        and previous.exists()
        and bool(json.loads(previous.read_text()).get("simplify"))
    )
    if calibration:
        task = {**task, "_calibration": True}
    fmap = _prepare_package(pkg, task)
    if job == "easier":
        # Simplification does not use the hardening size rule.
        (pkg / "run" / "seed_size.json").unlink(missing_ok=True)
    if observed:
        (pkg / "run" / "failure.txt").write_text(observed)

    seed_size = ts.size_of(
        task["solve_sh"],
        task["test_state_py"],
        "python" if ev._verifier_rel(task).endswith(".py") else "shell",
        solution_rel=fmap["solve_sh"],
    )
    blind = job in ("harder", "easier") and VERIFIER_AUTHOR == "blind"
    prompt = (
        {
            "harder": (_HARDER_JOB_BLIND if blind else _HARDER_JOB).format(
                solved=solved,
                attempts=attempts_n,
                guidance=_calibration_guidance(solved, attempts_n)
                if calibration
                else _REAL_HARDER_GUIDANCE.replace(
                    "Your shell has network access",
                    "Search with your web tool to find candidate sources. Your shell "
                    "also has network access",
                )
                # "disabled" is a codex web_search value too: no tool, so no instruction to use it.
                if HARDER_GUIDANCE == "real" and WEB_SEARCH not in ("", "disabled")
                else _REAL_HARDER_GUIDANCE
                if HARDER_GUIDANCE == "real"
                else _STUDENT_HARDER_GUIDANCE,
                seed_lines=seed_size["solution_lines"],
                seed_asserts=seed_size["verifier_asserts"],
                growth_bound=(
                    "The reference solution may stay the same length or shrink, "
                    f"and may grow by at most {ts.MAX_ADDED} non-comment lines. "
                    "Do not add code to meet a minimum length."
                ),
                max_asserts=ts.MAX_ADDED_ASSERTS,
            )
            + _HARDER_REPAIR_NOTE
            + (_UNSOLVED_HARDER_NOTE if task.get("_unsolved") else ""),
            "easier": (_PLAN_JOB if task.get("_intervention_plan") else _EASIER_JOB).format(
                solved=solved,
                attempts=attempts_n,
                guidance=so.prompt(task.get("_simplify_hint", "vague")),
            )
            + (_UNSOLVED_NOTE if task.get("_unsolved") else ""),
            "repair": _REPAIR_JOB.format(exit_code=exit_code),
        }[job]
        + _traces_spec(rewrite.traces)
        + _budget(author_timeout)
    )

    history = rewrite.traces / "history"
    if job in ("harder", "easier") and history.is_dir() and any(history.iterdir()):
        prompt += (
            "\n\nTHIS TASK'S HISTORY\n"
            "Read traces/history/summary.md first: every earlier rewrite of this "
            "task, oldest first, with what training measured before it, how it "
            "ended and why, and which files it changed, with the diff and notes "
            "files for each (index.jsonl holds the same records as data). Read "
            "them before editing: they show which changes already moved this task "
            "and how far, and which attempts failed and why. Do not copy any of it "
            "into the task.\n"
        )

    if job in ("harder", "easier") and (rewrite.traces / "record.md").is_file():
        prompt += (
            "\n\nTHE FULL RECORD\n"
            "traces/record.md gives every revision's solve counts group by group "
            "and says where this task's whole record sits in the experiment; "
            "traces/attempts.jsonl lists every attempt the student made at it, in "
            "every run and on every revision, with the path of its full record. "
            "The rest of traces/ is a selection; when a question it leaves open "
            "matters -- whether earlier help was used, where attempts on an older "
            "revision failed -- open the records that answer it. They are "
            "read-only, and none of it goes into the task.\n"
        )

    if job in ("harder", "easier") and task.get("_student_feedback"):
        feedback = task["_student_feedback"]
        (pkg / "run" / "student_feedback.json").write_text(
            json.dumps(feedback, indent=2) + "\n"
        )
        prompt += (
            "\n\nMEASURED STUDENT FEEDBACK\n"
            "Read run/student_feedback.json before proposing the next change. "
            "It records prior measurements and the requested adjustment. "
            "Use the corresponding attempts to explain why the previous "
            "change was too easy, too hard, or execution-heavy, and revise "
            "that mechanism. Do not add the feedback, scores, or evaluator "
            "details to the task instruction or environment.\n"
        )

    if job == "easier" and task.get("_intervention_plan"):
        (pkg / "run" / "intervention_plan.json").write_text(
            json.dumps(task["_intervention_plan"], indent=2) + "\n"
        )

    if task.get("_role_files"):
        prompt += _ROLE_PATCH_NOTE
    run = None
    agent_error = None
    vsession = None
    try:
        try:
            with session(rewrite, "agent", timeout=author_timeout) as run:
                try:
                    p = _run_codex(run, pkg, prompt)
                finally:
                    _sandbox_down(pkg)
        except AgentSessionError as error:
            assert error.result is not None
            p, agent_error = error.result, error
        assert run is not None
        with _checked_output(agent_error):
            _check_verdict(pkg)
        if agent_error is not None and not _agent_checked(pkg):
            raise agent_error
        try:
            _require_checked(pkg, p)
        except AgentValidationError as error:
            error.returncode = p.returncode
            if error.failure_type != "agent_missing_validation":
                raise
            run, p, repair_error = _repair_agent_validation(
                rewrite, run.dir, pkg, error, deadline
            )
            if repair_error is not None:
                agent_error = repair_error
        if job == "easier":
            _require_declaration(
                rewrite, run.dir, task.get("_simplify_hint", "vague"), deadline
            )
        _add_time(timings, "agent", time.monotonic() - stage_start)
        if blind:
            author_check = _last_check(pkg)
            vsession, fmap["test_state_py"], answered = _blind_verifier_answered(
                rewrite,
                {**task, "_session": str(run.dir.path)},
                fmap,
                deadline=deadline,
                timings=timings,
            )
            stage_start = time.monotonic()
            _notify_handoff(handoff, task, pkg, fmap, author_check)
            if not _reconcile_blind(rewrite, vsession, fmap, answered, deadline):
                _notify_handoff(handoff, None, pkg, fmap, None)
            _add_time(timings, "reconcile", time.monotonic() - stage_start)
        out = _collect(task, pkg, fmap)
    except Blocked:
        raise
    except Exception as exc:
        # A session the classifier stopped left the package half-written,
        # so every check below it fails for that reason rather than for
        # the rewrite's. Say which it was, so the caller starts a fresh
        # session instead of recording the task as unevolvable.
        if run is not None and cyber_filtered(run.dir):
            raise Filtered(
                f"the provider's cybersecurity classifier stopped the "
                f"session ({type(exc).__name__}: {exc})"[:300]
            ) from exc
        raise
    if all(out[key] == task[key] for key in fmap) and not out["_support_changed"]:
        raise RuntimeError(
            f"agent changed nothing (exit {p.returncode}): " f"{p.stdout[-200:]}"
        )
    if not out["instruction"].strip():
        raise RuntimeError("agent emptied the instruction")
    out["_hint"] = f"agent_{job}"
    out["_agent_validated"] = _agent_checked(pkg)
    out["_session"] = str(run.dir.path)
    if agent_error is not None:
        out["_agent_failure_type"] = agent_error.failure_type
        out["_agent_exit_code"] = agent_error.returncode
    if job == "harder":
        out["_harder_mode"] = "student"
    if job == "easier":
        decision = so.read_decision(pkg, task.get("_simplify_hint", "vague"))
        decision["hint_level"] = task.get("_simplify_hint", "vague")
        out["_simplify"] = decision
        out["_family"] = "simplify"
    if job in ("harder", "easier") and (repairs := _read_repairs(pkg)):
        out["_repair"] = repairs
    if vsession is not None:
        out["_verifier_author"] = "blind"
        out["_verifier_session"] = str(vsession.path)
    return out


def _resume_author_blind(
    rewrite: layout.RewriteDir,
    task: dict,
    observed: str,
    exit_code: int,
    *,
    seq: int,
    deadline: RewriteDeadline,
    job: str | None = None,
) -> dict:
    """Resume the author's session with a failure, in blind mode: the blind
    verifier is taken out of ``tests/`` first and the previous revision's
    checker put back as the scratch it had, so the author still never sees
    the verifier -- only the failure it produced. Returns the task with the
    new session recorded."""
    prior = layout.SessionDir(Path(task.get("_session") or ""))
    if not prior.codex_home.is_dir():
        raise RuntimeError(f"no agent session to resume at {prior.path}")
    sid = _session_id(prior)
    pkg = rewrite.package
    _restore_seed_tests(pkg, _seed_tests(task))
    (pkg / "run").mkdir(exist_ok=True)
    _write_resources(pkg, task)
    _write_pretest(pkg, task)
    (pkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    # The caller's revalidation failed: no earlier pass may stand in for a rerun.
    for stale in ("verdict.txt", "checks.jsonl", "result-cache.json"):
        (pkg / "run" / stale).unlink(missing_ok=True)
    timeout = _session_timeout(deadline, "blind author repair")
    with session(rewrite, "repair-author", timeout=timeout, resumes=prior) as run:
        prompt = (
            job or (_REPAIR_JOB.format(exit_code=exit_code) + _REPAIR_BLIND_NOTE)
        ) + _budget(timeout)
        try:
            p = _run_codex(run, pkg, prompt, resume=sid)
        finally:
            _sandbox_down(pkg)
    _check_verdict(pkg)
    _require_checked(pkg, p)
    return {**task, "_session": str(run.dir.path)}


def _resume_blind(
    rewrite: layout.RewriteDir,
    task: dict,
    observed: str,
    exit_code: int,
    deadline: RewriteDeadline,
) -> dict:
    """The caller's revalidation failed on a blind rewrite: one repair
    iteration (author, then verifier), then the caller revalidates again."""
    vsession = layout.SessionDir(Path(task.get("_verifier_session") or ""))
    if not vsession.package.is_dir():
        raise RuntimeError(f"no verifier session to resume at {vsession.path}")
    pkg = rewrite.package
    fmap = ev.file_map(task)
    # One iteration here: the caller counts iterations against REPAIR_ROUNDS
    # itself, revalidating on Daytona between them.
    fmap["test_state_py"] = _repair_iterations(
        rewrite,
        task,
        vsession,
        fmap,
        observed,
        exit_code,
        check_name=f"check.resume{time.time_ns() % 100000}",
        rounds=1,
        deadline=deadline,
    )
    out = _collect(task, pkg, fmap)
    out["_repaired"] = "codex_resume_author_then_verifier"
    out["_agent_validated"] = True
    out["_session"] = str(sorted(rewrite.sessions.glob("*--repair-author"))[-1])
    return out


def resume_agentic(
    rewrite: layout.RewriteDir,
    task: dict,
    observed: str,
    exit_code: int = 1,
    *,
    deadline: RewriteDeadline | None = None,
) -> dict:
    """Continue the session that wrote this task, with the caller's failure.

    The caller rebuilt the package and ran the reference solution against the
    verifier; it failed. A fresh repair session has to rediscover what the
    first one knew -- why the files look the way they do -- from the files
    alone. The first session is on disk (`codex exec resume`), so give it the
    failure instead. Raises on failure; raises Blocked when the agent declined.
    """
    _require_codex()
    deadline = deadline or rewrite_deadline(rewrite)
    pkg = rewrite.package
    if task.get("_verifier_author") == "blind":
        # The caller's oracle failed at the row's size. The session that can
        # answer without seeing the solution is the verifier's; resuming the
        # author instead would hand it the verifier it was kept away from.
        return _resume_blind(rewrite, task, observed, exit_code, deadline)
    prior = layout.SessionDir(Path(task.get("_session") or ""))
    if not prior.codex_home.is_dir():
        raise RuntimeError(f"no agent session to resume at {prior.path}")
    sid = _session_id(prior)
    fmap = ev.file_map(task)
    (pkg / "run").mkdir(exist_ok=True)
    _write_resources(pkg, task)
    _write_pretest(pkg, task)
    (pkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    # The caller's revalidation failed: no earlier pass may stand in for a rerun.
    for stale in ("verdict.txt", "checks.jsonl", "result-cache.json"):
        (pkg / "run" / stale).unlink(missing_ok=True)
    agent_error = None
    timeout = _session_timeout(deadline, "author repair")
    try:
        with session(rewrite, "repair", timeout=timeout, resumes=prior) as run:
            prompt = _REPAIR_JOB.format(exit_code=exit_code) + _budget(timeout)
            try:
                p = _run_codex(run, pkg, prompt, resume=sid)
            finally:
                _sandbox_down(pkg)
    except AgentSessionError as error:
        assert error.result is not None
        p, agent_error = error.result, error
    with _checked_output(agent_error):
        _check_verdict(pkg)
        _require_checked(pkg, p)
        out = _collect(task, pkg, fmap)
    if all(out[key] == task[key] for key in fmap) and not out["_support_changed"]:
        raise RuntimeError(
            f"agent changed nothing on resume "
            f"(exit {p.returncode}): {p.stdout[-200:]}"
        )
    out["_repaired"] = "codex_resume"
    out["_agent_validated"] = _agent_checked(pkg)
    # The repair's codex/ shares the thread's jsonl with the agent's, so a
    # later resume from either finds the same session id.
    out["_session"] = str(run.dir.path)
    if agent_error is not None:
        out["_agent_failure_type"] = agent_error.failure_type
        out["_agent_exit_code"] = agent_error.returncode
    return out
