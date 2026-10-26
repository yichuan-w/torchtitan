# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""tmax grading: run the task's verifier IN the sandbox and read back the reward.

The tmax verifier contract (AI2 terminal-agent tasks): run ``bash /tests/test.sh``
INSIDE the task container; the script writes ``/logs/verifier/reward.txt`` holding
``0`` or ``1``. Reward = that value.

Unlike R2E (which re-boots a CLEAN eval sandbox and applies a git diff), tmax has
no diff step: the agent mutates the container's filesystem directly and the
verifier inspects that same filesystem. So ``TMaxRollouter`` KEEPS the agent's
sandbox and grades in place -- this module uploads the verifier + fixtures into
the live sandbox, runs it, and parses the reward file.

Two entry points, both driving the same steps:
  - ``grade_tmax(sb, tmax, workdir, ...)`` -- for the harness ``Sandbox`` contract
    (async ``exec`` / ``write_file`` / ``read_file``); used by the rollouter.
  - ``grade_tmax_daytona(sb, tmax, workdir, ...)`` -- for a RAW ``daytona`` Sandbox
    (sync ``process.exec`` / ``fs.upload_file``); used by ``local_smoke.py`` so the
    grading logic can be exercised without the full training stack.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import posixpath
import re
import shlex
import tarfile
import uuid
from functools import partial
from typing import TYPE_CHECKING

from torchtitan.experiments.rl.examples.tmax.integrity_baseline import (
    capture_baseline_sync,
    describe_differences,
    integrity_differences,
    integrity_differences_sync,
    protected_entries_of,
)

if TYPE_CHECKING:
    # Type-only so this module imports WITHOUT the torchtitan/vLLM stack -- the
    # standalone ``local_smoke.py`` (daytona-only venv) imports it just to call
    # ``grade_tmax_daytona`` + the pure helpers, which have no torchtitan dependency.
    from torchtitan.experiments.rl.harness import Sandbox

logger = logging.getLogger(__name__)

# In-sandbox layout the verifier contract fixes.
_TESTS_DIR = "/tests"
_TEST_SH = "/tests/test.sh"
_VERIFIER_DIR = "/logs/verifier"
_DEFAULT_REWARD_PATH = "/logs/verifier/reward.txt"
# Second verifier output: the harbor contract runs pytest with `--ctrf`, so the
# per-test breakdown behind the binary reward lands here. Diagnostics only.
_DEFAULT_CTRF_PATH = "/logs/verifier/ctrf.json"


def _ctrf_path_for(reward_path: str) -> str:
    """The ctrf report sits beside the reward file, wherever that is."""
    parent = posixpath.dirname(reward_path) or _VERIFIER_DIR
    return posixpath.join(parent, "ctrf.json")


def _pre_grade_command(reward_path: str, ctrf_path: str, nonce: str) -> str:
    """Shell that resets the verifier outputs before ``test.sh`` runs.

    The agent and the verifier share one filesystem, so a rollout can pre-write
    ``reward.txt`` (optionally ``chattr +i`` it, or make its directory immutable)
    and be paid for a verifier that never ran. Clear any immutable bits, delete
    both outputs, and leave a one-shot sentinel in the reward file. Grading then
    accepts the reward only if the verifier actually replaced the sentinel; a
    sentinel that could not even be written proves the path is out of our
    control, and both cases score 0.
    """
    q_reward = shlex.quote(reward_path)
    q_ctrf = shlex.quote(ctrf_path)
    q_dir = shlex.quote(posixpath.dirname(reward_path) or _VERIFIER_DIR)
    return (
        # pip's wheel cache is never needed after install; agents that pip
        # install CUDA torch leave GBs there and the verifier's own installs
        # then fail on a full disk (run 1024: 25 of 36 infra-zero eval trials).
        "rm -rf /root/.cache/pip /home/*/.cache/pip 2>/dev/null; "
        f"chattr -R -i {q_dir} 2>/dev/null; "
        f"chattr -i {q_reward} {q_ctrf} 2>/dev/null; "
        f"rm -f {q_reward} {q_ctrf}; "
        f"mkdir -p {q_dir}; "
        f"printf %s {shlex.quote(nonce)} > {q_reward}"
    )


def _make_nonce() -> str:
    return f"tmax-sentinel-{uuid.uuid4().hex}"


def _root_sh(cmd: str) -> str:
    """Run ``cmd`` as root on either kind of Daytona sandbox: custom task
    images exec as root already, while snapshot images exec as the unprivileged
    ``daytona`` user that carries passwordless sudo."""
    q = shlex.quote(cmd)
    return f'if [ "$(id -u)" = 0 ]; then sh -c {q}; else sudo -n sh -c {q}; fi'


# Two fixture classes with OPPOSITE timing (see seed_workspace / grade_tmax):
#   environment/seeds/<rel> -- agent-facing INPUT files (the task's initial
#     workspace state). Seeded to /workspace BEFORE the agent runs (upstream
#     SWERLVanilluxSandboxEnv seeds at reset) so the policy can read them. Upstream
#     ignores the per-task workdir and always uses /workspace; we match that.
#   tests/<rel>             -- GRADING fixtures. Uploaded next to test.sh
#     (/tests/<rel>) at grade time ONLY, so the agent cannot peek at the verifier.
_SEEDS_PREFIX = "environment/seeds/"
_SEEDS_DEST = "/workspace"
_TESTS_PREFIX = "tests/"


def _eval_timeout_sec() -> int:
    val = os.environ.get("TMAX_EVAL_TIMEOUT_SEC")
    return int(val) if val and val.strip() else 900


def _grading_fixture_dest(rel: str) -> str | None:
    """Map a GRADING fixture relpath (tests/*) to its in-sandbox destination.

    ``tests/expected_output.txt`` -> ``/tests/expected_output.txt``. ``test.sh`` is
    uploaded explicitly (never as a fixture). ``environment/seeds/*`` are NOT graded
    here -- they are agent inputs seeded to /workspace before the agent runs (see
    ``seed_workspace``), so they are skipped.
    """
    rel = rel.lstrip("/")
    if rel == "tests/test.sh":
        return None  # test.sh is uploaded explicitly, never as a fixture
    if rel.startswith(_TESTS_PREFIX):
        sub = rel[len(_TESTS_PREFIX) :]
        return posixpath.join(_TESTS_DIR, sub) if sub else None
    return None


def _iter_seed_fixtures(tmax: dict, dest: str):
    """Yield ``(sandbox_path, content)`` for every ``environment/seeds/*`` input,
    placed under ``dest`` (/workspace). These are the task's agent-facing initial
    workspace files, not grading fixtures."""
    for rel, content in (tmax.get("fixtures") or {}).items():
        rel = rel.lstrip("/")
        if rel.startswith(_SEEDS_PREFIX):
            sub = rel[len(_SEEDS_PREFIX) :]
            if sub:
                yield posixpath.join(dest, sub), content


def _parse_reward(text: str) -> float:
    """Parse ``reward.txt`` contents into a float in [0, 1] (0.0 if unparsable)."""
    try:
        val = float((text or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0
    return max(0.0, min(1.0, val))


def _parse_reward_json(text: str) -> float:
    """Harbor's ``reward.json``: its ``reward``, or the one value it holds.

    Clamped to [0, 1] like ``reward.txt``; 0.0 when the file is empty or holds
    no single reward (Harbor fails such a trial instead of scoring it)."""
    try:
        rewards = json.loads(text or "")
    except ValueError:
        return 0.0
    if not isinstance(rewards, dict):
        return 0.0
    value = rewards.get("reward")
    if value is None and len(rewards) == 1:
        value = next(iter(rewards.values()))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def parse_ctrf(text: str) -> dict | None:
    """Summarize a CTRF report into ``{tests, passed, failed}`` (failed = names).

    The report is dataset-authored and only ~94% of the tmax/RTS corpus writes it,
    so an absent or malformed one yields None rather than raising. Reward does NOT
    read this -- it exists to say WHICH checks failed behind a binary reward of 0.
    """
    try:
        results = json.loads(text)["results"]
        summary = results["summary"]
        num_tests = int(summary["tests"])
        num_passed = int(summary["passed"])
    except (ValueError, TypeError, KeyError):
        return None
    failed = [
        test.get("name", "")
        for test in results.get("tests", [])
        if test.get("status") == "failed"
    ]
    return {"tests": num_tests, "passed": num_passed, "failed": failed}


def ctrf_pass_fraction(report: dict | None) -> float | None:
    """Fraction of the verifier's tests that passed, or None without a usable report.

    This is the dense counterpart to reward.txt's all-or-nothing 1: a 3-of-4 report
    yields 0.75. Clamped to [0, 1] because the counts are dataset-authored.
    """
    if not report or not report["tests"]:
        return None
    return max(0.0, min(1.0, report["passed"] / report["tests"]))


# --------------------------------------------------------------------------- #
# Harness Sandbox path (async) -- used by the rollouter.
# --------------------------------------------------------------------------- #
async def seed_workspace(sb: Sandbox, tmax: dict, *, dest: str = _SEEDS_DEST) -> None:
    """Upload the task's ``environment/seeds/*`` inputs to ``dest`` (/workspace)
    BEFORE the agent runs.

    These are agent-facing input files (the task's initial workspace state);
    upstream ``SWERLVanilluxSandboxEnv`` seeds them at reset, so a faithful rollout
    must place them before the policy runs -- otherwise seed-bearing tasks are
    structurally unsolvable (the inputs never exist during the rollout). Grading
    fixtures (tests/*) are handled separately by ``grade_tmax`` (anti-peek).
    No-op for tasks without seeds.
    """
    seeds = list(_iter_seed_fixtures(tmax, dest))
    if not seeds:
        return
    await sb.exec(f"mkdir -p {shlex.quote(dest)}", user="root", check=False, timeout=60)
    for path, content in seeds:
        parent = posixpath.dirname(path)
        if parent and parent != dest:
            await sb.exec(
                f"mkdir -p {shlex.quote(parent)}", user="root", check=False, timeout=60
            )
        await sb.write_file(path, content, user="root")


def _check_verifier_python_download(test_sh: str, output: str, reward: float) -> None:
    """Raise for uv bootstrap server failures before pytest starts."""
    if reward >= 1 or not re.search(r"\buvx\b", test_sh) or "pytest" not in test_sh:
        return
    if "test session starts" in output or "short test summary info" in output:
        return
    if (
        "error: Request failed after" in output
        and "Failed to download https://github.com/"
        "astral-sh/python-build-standalone/releases/download/" in output
        and re.search(r"HTTP status server error \(5\d\d\b", output)
    ):
        # A shell wrapper can write reward=0 even when uv never started pytest.
        raise RuntimeError("verifier Python download failed with HTTP 5xx before tests")


async def grade_tmax(
    sb: Sandbox,
    tmax: dict,
    *,
    workdir: str,
    timeout_sec: int | None = None,
    baseline_digests: dict[str, str] | None = None,
    diagnostics: dict | None = None,
    upload_tests: bool = True,
    harbor_reward: bool = False,
    env: dict[str, str] | None = None,
) -> float:
    """Grade a tmax task in the (already-run) sandbox ``sb`` and return reward.

    ``upload_tests=False`` is for a separate verifier sandbox whose image already
    holds ``/tests`` (Harbor ``environment_mode = "separate"``): nothing is
    uploaded and the image's own ``/tests/test.sh`` runs. ``harbor_reward`` reads
    the reward as Harbor's verifier does: ``reward.json``'s ``reward`` when that
    file exists, else ``reward.txt`` (four TB4 verifiers write only the JSON).
    ``env`` is the task's ``[verifier.env]``, set for ``test.sh``.

    Creates ``/logs/verifier`` + ``/tests``, uploads ``test.sh`` and the grading
    fixtures (tests/*) to their destinations, runs ``bash /tests/test.sh``, then
    reads back ``reward_path`` (default ``/logs/verifier/reward.txt``). Agent-input
    seeds (environment/seeds/*) are NOT uploaded here -- they are seeded to
    /workspace before the rollout (see ``seed_workspace``). Returns a float in
    [0, 1]; 0.0 when the reward file is missing or unparsable.
    """
    timeout = timeout_sec if timeout_sec is not None else _eval_timeout_sec()
    test_sh = tmax.get("test_sh") or ""
    fixtures = tmax.get("fixtures") or {}
    reward_path = tmax.get("reward_path") or _DEFAULT_REWARD_PATH

    await sb.exec(
        f"mkdir -p {shlex.quote(_VERIFIER_DIR)} {shlex.quote(_TESTS_DIR)} "
        f"{shlex.quote(workdir)}",
        user="root",
        check=False,
        timeout=60,
    )
    if upload_tests:
        for rel, content in fixtures.items():
            dest = _grading_fixture_dest(rel)
            if dest is None:
                continue
            await sb.write_file(dest, content, user="root")
        await sb.write_file(_TEST_SH, test_sh, user="root")

    nonce = _make_nonce()
    reward_json = posixpath.join(
        posixpath.dirname(reward_path) or _VERIFIER_DIR, "reward.json"
    )
    pre_grade = _pre_grade_command(reward_path, _ctrf_path_for(reward_path), nonce)
    if harbor_reward:
        # Harbor runs empty_dirs([verifier_dir], chmod=True) before the
        # verifier: the directory is 777, so a verifier that drops to its own
        # user can write there (live-database-cutover's starts postgres, which
        # logs to /logs/verifier/pg.log). The sentinel is opened the same way,
        # so such a verifier can also replace it. The separate verifier sandbox
        # is ours alone; the agent never touches it.
        q_dir = shlex.quote(posixpath.dirname(reward_path) or _VERIFIER_DIR)
        pre_grade = (
            f"chattr -i {shlex.quote(reward_json)} 2>/dev/null; "
            f"rm -f {shlex.quote(reward_json)}; {pre_grade}; "
            f"chmod 777 {q_dir}; chmod 666 {shlex.quote(reward_path)}"
        )
    await sb.exec(
        pre_grade,
        user="root",
        check=False,
        timeout=60,
    )
    sentinel = (await sb.read_file(reward_path, user="root") or "").strip()
    if sentinel != nonce:
        logger.warning(
            "[tmax] reward path %s is not writable pre-grade; scoring 0",
            reward_path,
        )
        return 0.0

    # PRE-VERIFY (reaudit decision-1): run the task's exported pre_test integrity check ONCE as root, here between
    # the sentinel confirmation and the verifier — the same single seam the anti-tamper reset uses. It re-hashes the
    # spec's pinned references (assert-refuse; it never restores, so it cannot clobber an honest edit, and it never
    # writes reward.txt — as a separate exec that write would be overwritten by test.sh; the short-circuit lives
    # here). A nonzero exit means a pinned reference was mutated / deleted / a pinned command diverged, so the
    # episode scores 0 WITHOUT running the verifier. Absent field ⇒ no-op (the whole corpus before this lands).
    # INTEGRITY BASELINE (reaudit): for a row that names protected paths and/or protected commands, the
    # rollouter digested them right after setup and handed the digests in as ``baseline_digests``;
    # re-digest the same entries with the same string now and score 0 on any difference, before the
    # verifier runs. Nothing is stamped in the data and nothing is captured at prep. A row with protected
    # entries and no baseline is a harness bug, so it raises (void episode) rather than skipping. Such
    # rows do not consult pre_test_sh below.
    _protected = protected_entries_of(tmax)
    if _protected:
        _differing = await integrity_differences(
            partial(sb.exec, user="root"),
            tmax,
            baseline_digests,
            workdir=workdir,
            timeout=timeout,
        )
        if _differing:
            if diagnostics is not None:
                diagnostics.update(
                    stage="integrity_baseline",
                    verifier_ran=False,
                    differing=describe_differences(_protected, _differing),
                )
            logger.info(
                "[tmax] integrity baseline difference for %s in %d protected entr(y/ies): %s; scoring 0",
                tmax.get("task_id", "?"),
                len(_differing),
                describe_differences(_protected, _differing),
            )
            return 0.0

    pre_test = tmax.get("pre_test_sh")
    if pre_test and not _protected:
        # ENVIRONMENT-DRIFT GUARD (reaudit): tw_* task ids evolve IN PLACE across breeding rounds, so a task's
        # environment can differ from the one its pins were captured against. The pins are keyed by task_id, so
        # on a changed environment the assert-refuse would falsely refuse an HONEST episode. Run the pin check
        # ONLY when this episode's environment identity equals the captured one; on any difference OR a missing
        # identity, SKIP (no-op: reward path unchanged, grading falls through to test.sh) and log one line. The
        # identities are carried in tmax by prepare_tmax_data (grade_tmax sees no image/Dockerfile of its own).
        # Round 0: both are "image:<ref>" and match, so the block runs; an evolved task that rewrote its
        # Dockerfile shows "dockerfile:<sha>" and mismatches, so it is skipped.
        _stamped = tmax.get("pretest_env_identity") or ""
        _episode = tmax.get("pretest_episode_env_identity") or ""
        if _stamped and _episode and _stamped == _episode:
            pt_rc, _pt_out, _pt_err = await sb.exec(
                pre_test,
                user="root",
                check=False,
                timeout=min(120, timeout),
            )
            if pt_rc != 0:
                logger.info(
                    "[tmax] pre_test integrity check failed (rc=%s); scoring 0 without running the verifier",
                    pt_rc,
                )
                return 0.0
        else:
            logger.info(
                "[tmax] pre_test SKIPPED for %s: environment changed since capture (stamped=%s episode=%s); "
                "grading via test.sh without the pin check",
                tmax.get("task_id", "?"),
                _stamped or "?",
                _episode or "?",
            )

    # test.sh scripts assume they are invoked as `bash /tests/test.sh` (they use
    # $(dirname "$0") to find sibling fixtures). Run as root so /logs and any
    # system path is writable; the verifier is trusted dataset code.
    rc, out, err = await sb.exec(
        f"chmod +x {shlex.quote(_TEST_SH)}; bash {shlex.quote(_TEST_SH)}",
        user="root",
        check=False,
        timeout=timeout,
        **({"env": env} if env else {}),
    )
    # The verifier's exit and output are the only record of why an episode
    # scored below 1: reward.txt holds the number and nothing else. Kept for
    # a failed episode so a reference solution that fails can be read from
    # the log instead of re-run in a fresh sandbox.
    verifier_output = (out or "") + (err or "")
    verifier_tail = verifier_output[-400:]
    if diagnostics is not None:
        # Post-episode evidence for the task editor, never an agent observation.
        diagnostics.update(exit_code=rc, output_tail=verifier_output[-16000:])
    if harbor_reward:
        json_rc, _, _ = await sb.exec(
            f"test -e {shlex.quote(reward_json)}", user="root", check=False, timeout=60
        )
        if json_rc == 0:
            text = await sb.read_file(reward_json, user="root")
            reward = _parse_reward_json(text)
            if diagnostics is not None:
                diagnostics.update(reward_file="reward.json", reward_json=text[-2000:])
            logger.info("[tmax] graded reward=%.2f (reward.json)", reward)
            return reward
        if diagnostics is not None:
            diagnostics["reward_file"] = "reward.txt"
    reward_txt = await sb.read_file(reward_path, user="root")
    reward = _parse_reward(reward_txt)
    _check_verifier_python_download(test_sh, verifier_output, reward)
    if (reward_txt or "").strip() == nonce:
        logger.info(
            "[tmax] verifier left the sentinel in place (never wrote %s; "
            "exit %s; tail %r); scoring 0",
            reward_path,
            rc,
            verifier_tail,
        )
        return 0.0
    if reward < 1.0:
        logger.info(
            "[tmax] verifier for %s exited %s with reward=%.2f; tail %r",
            tmax.get("task_id", "?"),
            rc,
            reward,
            verifier_tail,
        )
    logger.info("[tmax] graded reward=%.2f (reward_path=%s)", reward, reward_path)
    return reward


_ARTIFACTS_TGZ = "/tmp/.tmax_artifacts.tgz"


async def collect_artifacts(sb: Sandbox, paths: list[str]) -> bytes | None:
    """The declared artifacts the agent left, as one gzipped tar rooted at ``/``.

    A path that does not exist is left out, so the verifier sees exactly what
    the agent produced; None when none exists. The tar travels as base64 text
    because ``read_file`` returns text."""
    rel = " ".join(shlex.quote(p.strip("/")) for p in paths)
    await sb.exec(
        f"cd / && rm -f {_ARTIFACTS_TGZ} {_ARTIFACTS_TGZ}.b64; set --; "
        f'for p in {rel}; do [ -e "$p" ] && set -- "$@" "$p"; done; '
        f'[ "$#" -gt 0 ] && tar -czf {_ARTIFACTS_TGZ} "$@" '
        f"&& base64 -w0 {_ARTIFACTS_TGZ} > {_ARTIFACTS_TGZ}.b64; true",
        user="root",
        check=False,
        timeout=300,
    )
    text = (await sb.read_file(f"{_ARTIFACTS_TGZ}.b64", user="root") or "").strip()
    return base64.b64decode(text) if text else None


def _empty_dir_command(path: str) -> str:
    """Harbor's ``empty_dirs(..., chmod=True)`` for one directory: make it an
    empty directory opened 777, WITHOUT replacing the directory itself.

    Removing and recreating it instead (``rm -rf d && mkdir d``) leaves any
    process whose working directory is ``d`` in a deleted directory. Our exec
    runs through a long-lived Daytona session shell, so every later command,
    test.sh included, inherits it: medical-claims-processing's verifier image
    has ``WORKDIR /shared`` and a ``/shared`` directory artifact, and its
    pytest died in ``os.getcwd()`` with FileNotFoundError."""
    q = shlex.quote(path)
    return (
        f"if [ -L {q} ] || {{ [ -e {q} ] && [ ! -d {q} ]; }}; then rm -rf {q}; fi"
        f" && mkdir -p {q}"
        f" && find {q} -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +"
        f" && chmod 777 {q}"
    )


async def restore_artifacts(
    sb: Sandbox,
    data: bytes | None,
    *,
    into: str | None = None,
    empty: list[str] | None = None,
    open_parents: list[str] | None = None,
) -> None:
    """Unpack ``collect_artifacts``' tar at the same paths in ``sb``.

    ``open_parents``: the parent directories of the tar's file artifacts. Harbor
    uploads a file artifact after ``ensure_dirs([parent], chmod=True)``, so the
    parent exists and is 777 in the verifier; they get the same here. A verifier
    that runs the agent's file as an unprivileged user can need to write beside
    it. ``/`` itself is never opened.

    Harbor empties a directory artifact's target before uploading it
    (``empty_dirs([target], chmod=True)``): ``empty`` names the directory
    artifacts of a tar rooted at ``/``, and ``into`` says the tar is one
    directory's contents (an artifact with ``exclude`` patterns). Either way
    those directories are emptied in place and opened (777) first
    (``_empty_dir_command``)."""
    if not data:
        return
    await sb.write_file(_ARTIFACTS_TGZ, data, user="root")
    root = "/"
    targets = list(empty or [])
    if into is not None:
        targets.append(into)
        root = shlex.quote(into)
    prep = "".join(f"{_empty_dir_command(t)} && " for t in targets)
    for parent in sorted({p for p in (open_parents or []) if p.strip("/")}):
        q = shlex.quote(parent)
        prep += f"mkdir -p {q} && chmod 777 {q} && "
    await sb.exec(
        f"{prep}tar -C {root} -xzf {_ARTIFACTS_TGZ} && rm -f {_ARTIFACTS_TGZ}",
        user="root",
        check=False,
        timeout=300,
    )


async def _collect_excluding(sb: Sandbox, source: str, exclude: list[str]) -> bytes | None:
    """One directory artifact with its tar ``--exclude`` patterns, as Harbor
    downloads it (``tar czf - --exclude=... -C <source> .``); None if absent."""
    flags = " ".join(f"--exclude={shlex.quote(p)}" for p in exclude)
    q = shlex.quote(source)
    await sb.exec(
        f"rm -f {_ARTIFACTS_TGZ} {_ARTIFACTS_TGZ}.b64; [ -d {q} ] && "
        f"tar -czf {_ARTIFACTS_TGZ} {flags} -C {q} . "
        f"&& base64 -w0 {_ARTIFACTS_TGZ} > {_ARTIFACTS_TGZ}.b64; true",
        user="root",
        check=False,
        timeout=300,
    )
    text = (await sb.read_file(f"{_ARTIFACTS_TGZ}.b64", user="root") or "").strip()
    return base64.b64decode(text) if text else None


_MAIN_SERVICE = "main"


def _tar_members(data: bytes) -> list[tarfile.TarInfo]:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            return tar.getmembers()
    except (tarfile.TarError, OSError, EOFError):
        return []


def _dir_artifacts(data: bytes, sources: list[str]) -> list[str]:
    """The declared ``sources`` that are directories in a tar rooted at ``/``."""
    dirs = {m.name.strip("/") for m in _tar_members(data) if m.isdir()}
    return [s for s in sources if s.strip("/") and s.strip("/") in dirs]


def _file_artifact_parents(data: bytes, sources: list[str]) -> list[str]:
    """The parent directories of the declared ``sources`` that are files (not
    directories) in a tar rooted at ``/``: what Harbor's upload opens (777)."""
    files = {m.name.strip("/") for m in _tar_members(data) if not m.isdir()}
    return sorted(
        {
            posixpath.dirname("/" + s.strip("/"))
            for s in sources
            if s.strip("/") and s.strip("/") in files
        }
    )


def separate_collect_budget_sec(sv: dict) -> int:
    """Wall-clock collection and restore may take for a separate verifier,
    beyond the verifier's own budget and its boot: every collect hook's
    timeout, stopping main (60 s) when a sidecar is involved, and per artifact
    transfer the exec timeouts of collecting it (300 s; a sidecar's docker cp
    up to 630 s more) and of restoring it (300 s). The rollout guard grows by
    this for a separate-verifier row."""
    entries = _artifact_entries(sv)
    hooks = list(sv.get("collect") or [])
    sidecars = [e for e in entries if e["service"] != _MAIN_SERVICE]
    side_hooks = [h for h in hooks if (h.get("service") or _MAIN_SERVICE) != _MAIN_SERVICE]
    plain = any(e["service"] == _MAIN_SERVICE and not e["exclude"] for e in entries)
    excluded = [e for e in entries if e["service"] == _MAIN_SERVICE and e["exclude"]]
    transfers = int(plain) + len(excluded) + len(sidecars)
    return (
        sum(max(1, int(float(h.get("timeout_sec") or 60))) for h in hooks)
        + (60 if sidecars or side_hooks else 0)
        + 600 * transfers
        + 630 * len(sidecars)
    )


def _artifact_entries(sv: dict) -> list[dict]:
    """Harbor's artifact entries as ``{source, service, exclude}``.

    A bare string is a path in the agent's (main) container; a table may name a
    compose ``service`` and tar ``exclude`` patterns. Harbor's convention dir,
    collected for every task, is the row's ``convention_dir`` when it names one."""
    entries = []
    for a in list(sv.get("artifacts") or []) + (
        [sv["convention_dir"]] if sv.get("convention_dir") else []
    ):
        if isinstance(a, str):
            a = {"source": a}
        entries.append(
            {
                "source": a["source"],
                "service": a.get("service") or _MAIN_SERVICE,
                "exclude": list(a.get("exclude") or []),
            }
        )
    return entries


def _backend(sb):
    """The sandbox under the root-forcing wrappers (``_inner``), for its compose
    services. Bounded: a mock answers every attribute."""
    for _ in range(4):
        inner = getattr(sb, "_inner", None)
        if inner is None:
            break
        sb = inner
    return sb


async def _run_collect_hooks(sb, hooks: list[dict], record: list) -> None:
    """Harbor's ``_run_collect_hooks``: best-effort; a failure never aborts."""
    for hook in hooks:
        service = hook.get("service") or _MAIN_SERVICE
        timeout = max(1, int(float(hook.get("timeout_sec") or 60)))
        entry: dict = {"service": service, "command": hook["command"][:200]}
        try:
            if service == _MAIN_SERVICE:
                rc, out, err = await sb.exec(
                    hook["command"],
                    user=hook.get("user") or "root",
                    check=False,
                    timeout=timeout,
                )
            else:
                rc, out, err = await _backend(sb).service_exec(
                    hook["command"],
                    service=service,
                    user=hook.get("user"),
                    timeout=timeout,
                )
            entry["rc"] = rc
            if rc != 0:
                entry["output_tail"] = ((out or "") + (err or ""))[-500:]
        except Exception as error:  # noqa: BLE001 -- best-effort, as in Harbor
            entry["error"] = f"{type(error).__name__}: {error}"[:500]
        record.append(entry)


async def _collect_phased(sb, sv: dict, record: dict) -> list[tuple[str | None, bytes]]:
    """Harbor's ``_collect_artifacts_phased`` for a separate verifier.

    Main-service collect hooks, then main artifacts; then, when an artifact or
    hook names a sidecar, ``main`` is stopped (leftover agent processes cannot
    touch the evidence), sidecar hooks run and sidecar artifacts are copied out
    of their own containers. Returns ``(into, tar)`` pairs for
    ``restore_artifacts``: ``into`` None for a tar rooted at ``/``."""
    entries = _artifact_entries(sv)
    hooks = list(sv.get("collect") or [])
    main_hooks = [h for h in hooks if (h.get("service") or _MAIN_SERVICE) == _MAIN_SERVICE]
    side_hooks = [h for h in hooks if h not in main_hooks]
    sidecars = sorted(
        {e["service"] for e in entries if e["service"] != _MAIN_SERVICE}
        | {h["service"] for h in side_hooks}
    )
    if sidecars and not hasattr(_backend(sb), "service_exec"):
        raise ValueError(
            f"artifacts or collect hooks name compose services {sidecars}, "
            "but the agent sandbox is not a compose project"
        )
    record["hooks"] = []
    bundles: list[tuple[str | None, bytes]] = []
    await _run_collect_hooks(sb, main_hooks, record["hooks"])
    plain = [e["source"] for e in entries if e["service"] == _MAIN_SERVICE and not e["exclude"]]
    data = await collect_artifacts(sb, plain) if plain else None
    if data:
        bundles.append((None, data))
    for e in entries:
        if e["service"] == _MAIN_SERVICE and e["exclude"]:
            data = await _collect_excluding(sb, e["source"], e["exclude"])
            if data:
                bundles.append((e["source"], data))
    if sidecars:
        try:
            await _backend(sb).stop_service(_MAIN_SERVICE)
        except Exception as error:  # noqa: BLE001 -- Harbor logs and continues
            record["stop_main_error"] = f"{type(error).__name__}: {error}"[:500]
        await _run_collect_hooks(sb, side_hooks, record["hooks"])
        record["sidecar_artifacts"] = {}
        for e in entries:
            if e["service"] == _MAIN_SERVICE:
                continue
            key = f"{e['service']}:{e['source']}"
            try:
                data = await _backend(sb).copy_out(e["service"], e["source"])
            except Exception as error:  # noqa: BLE001 -- best-effort, as in Harbor
                record["sidecar_artifacts"][key] = f"{type(error).__name__}: {error}"[:300]
                continue
            record["sidecar_artifacts"][key] = len(data or b"")
            if data:
                bundles.append((None, data))
    return bundles


async def grade_separate(
    sb: Sandbox,
    tmax: dict,
    *,
    workdir: str,
    boot,
    timeout_sec: int | None = None,
    diagnostics: dict | None = None,
    labels: dict[str, str] | None = None,
    after=None,
) -> float:
    """Harbor ``environment_mode = "separate"``: copy the declared artifacts out
    of the agent's sandbox ``sb`` into a fresh sandbox of the verifier
    environment, which holds its own ``/tests``, and grade there.

    ``tmax["separate_verifier"]`` names that environment: a prebuilt ``image``,
    or a ``dockerfile`` with ``build_context`` (the task's tests/ directory),
    which is what a rewrite that changed the tests needs. ``boot`` is
    ``boot_agent_sandbox`` (injected so this module stays importable without
    the harness). ``after`` is awaited with the verifier sandbox while it is up
    (the rollouter reads the CTRF report there).

    The rest follows Harbor 0.22.0's trial: artifacts may name a compose service
    or carry ``exclude`` patterns, ``collect`` hooks run first (``_collect_phased``),
    ``env`` is the verifier's ``[verifier.env]``, and the verifier boots with
    the block's GPU/region/no-internet fields. Three things now hold for every
    separate verifier, #242's rows included:
    - the reward is read as Harbor's verifier reads it: ``reward.json`` when
      the verifier wrote one, else ``reward.txt``;
    - a directory artifact's target is emptied before it is restored;
    - Harbor's ``/logs/artifacts`` is collected when the block names it
      (``convention_dir``, which this builder writes on every TB4 row).
    A block without the other fields boots as before."""
    sv = tmax["separate_verifier"]
    record: dict = {}
    bundles = await _collect_phased(sb, sv, record)
    if diagnostics is not None:
        diagnostics["separate_verifier"] = {
            "image": sv.get("image"),
            "built": bool(sv.get("dockerfile")),
            "artifact_bytes": sum(len(data) for _, data in bundles),
            "timeout_sec": timeout_sec if timeout_sec is not None else _eval_timeout_sec(),
            **{k: v for k, v in record.items() if v},
        }
    # TB4 rows that declare them: a GPU (in its region) and no internet.
    from torchtitan.experiments.rl.examples.tmax.harbor_env import sandbox_options

    options = sandbox_options(sv)
    async with boot(
        sv.get("image") or "",
        dockerfile=sv.get("dockerfile") or None,
        build_context=sv.get("build_context") or None,
        install_claude=False,
        cpu=sv.get("daytona_cpu"),
        memory=sv.get("daytona_mem_gb"),
        disk_gb=sv.get("daytona_disk_gb"),
        labels=labels,
        **({"sandbox_options": options} if options else {}),
    ) as verifier_sb:
        sources = [e["source"] for e in _artifact_entries(sv)]
        for into, data in bundles:
            if into:
                await restore_artifacts(verifier_sb, data, into=into)
                continue
            empty = _dir_artifacts(data, sources)
            parents = _file_artifact_parents(data, sources)
            await restore_artifacts(
                verifier_sb,
                data,
                **({"empty": empty} if empty else {}),
                **({"open_parents": parents} if parents else {}),
            )
        reward = await grade_tmax(
            verifier_sb,
            tmax,
            workdir=workdir,
            timeout_sec=timeout_sec,
            diagnostics=diagnostics,
            upload_tests=False,
            harbor_reward=True,
            env=sv.get("env") or None,
        )
        if after is not None:
            await after(verifier_sb)
    return reward


async def read_ctrf_report(
    sb: Sandbox, *, path: str = _DEFAULT_CTRF_PATH
) -> dict | None:
    """Read the verifier's CTRF report from ``sb`` after ``grade_tmax`` ran.

    Diagnostics only; the reward stays whatever reward.txt said. Costs one sandbox
    file read (an exec round trip on Daytona), so callers gate this behind a knob.
    """
    return parse_ctrf(await sb.read_file(path, user="root"))


# --------------------------------------------------------------------------- #
# Raw daytona Sandbox path (sync) -- used by local_smoke.py.
# --------------------------------------------------------------------------- #
def seed_workspace_daytona(sb, tmax: dict, *, dest: str = _SEEDS_DEST) -> None:
    """``seed_workspace`` against a RAW daytona Sandbox (sync API), so the smoke
    test seeds agent inputs the SAME way the rollouter does. No-op without seeds."""
    seeds = list(_iter_seed_fixtures(tmax, dest))
    if not seeds:
        return
    # The upload agent's implicit mkdir runs unprivileged even when the sandbox
    # was created with os_user=root, so parents must exist and be writable to
    # any uid before the first upload.
    sb.process.exec(_root_sh(f"mkdir -p {dest} && chmod 777 {dest}"), timeout=60)
    for path, content in seeds:
        parent = posixpath.dirname(path)
        if parent and parent != dest:
            sb.process.exec(
                _root_sh(f"mkdir -p {parent} && chmod 777 {parent}"), timeout=60
            )
        sb.fs.upload_file(content.encode("utf-8"), path)


def _daytona_exec(sb):
    """The raw SDK's ``process.exec`` as the ``(rc, stdout, stderr)`` exec the integrity
    baseline runs its one digest string through -- as root, like every other seam."""

    def run(cmd: str, *, timeout: int) -> tuple[int, str, str]:
        r = sb.process.exec(_root_sh(cmd), timeout=timeout)
        return getattr(r, "exit_code", 1), getattr(r, "result", "") or "", ""

    return run


def capture_baseline_daytona(
    sb, tmax: dict, *, workdir: str, timeout_sec: int | None = None
) -> dict[str, str] | None:
    """The rollouter's seam for a RAW ``daytona`` Sandbox: digest the row's protected entries
    after the seeds are in and before anything acts (``local_smoke``), so
    ``grade_tmax_daytona`` can re-measure. None for a row without entries."""
    timeout = timeout_sec if timeout_sec is not None else _eval_timeout_sec()
    return capture_baseline_sync(
        _daytona_exec(sb), tmax, workdir=workdir, timeout=timeout
    )


def grade_tmax_daytona(
    sb,
    tmax: dict,
    *,
    workdir: str,
    timeout_sec: int | None = None,
    baseline_digests: dict[str, str] | None = None,
) -> float:
    """Same steps as ``grade_tmax`` but against a RAW ``daytona`` Sandbox.

    Uses the daytona SDK's sync API directly (``sb.process.exec`` /
    ``sb.fs.upload_file``) so the grading logic can be exercised in a standalone
    script without importing the full torchtitan/vLLM training stack. Seeds are
    placed separately by ``seed_workspace_daytona`` before the agent runs, and
    a protected row's baseline by ``capture_baseline_daytona`` -- the same rule
    as ``grade_tmax``: no baseline for a protected row raises, a difference
    scores 0 before the verifier runs, identical proceeds.
    """
    timeout = timeout_sec if timeout_sec is not None else _eval_timeout_sec()
    test_sh = tmax.get("test_sh") or ""
    fixtures = tmax.get("fixtures") or {}
    reward_path = tmax.get("reward_path") or _DEFAULT_REWARD_PATH

    dirs = f"{_VERIFIER_DIR} {_TESTS_DIR} {workdir}"
    sb.process.exec(_root_sh(f"mkdir -p {dirs} && chmod 777 {dirs}"), timeout=60)
    for rel, content in fixtures.items():
        dest = _grading_fixture_dest(rel)
        if dest is None:
            continue
        sb.fs.upload_file(content.encode("utf-8"), dest)
    sb.fs.upload_file(test_sh.encode("utf-8"), _TEST_SH)

    nonce = _make_nonce()
    sb.process.exec(
        _root_sh(_pre_grade_command(reward_path, _ctrf_path_for(reward_path), nonce)),
        timeout=60,
    )
    r = sb.process.exec(_root_sh(f"cat {reward_path}"), timeout=30)
    sentinel = (r.result if getattr(r, "exit_code", 1) == 0 else "").strip()
    if sentinel != nonce:
        return 0.0

    # INTEGRITY BASELINE: the same seam as grade_tmax (after the sentinel, before the
    # verifier), the same string through the SDK's exec, the same judgement.
    _protected = protected_entries_of(tmax)
    if _protected:
        _differing = integrity_differences_sync(
            _daytona_exec(sb),
            tmax,
            baseline_digests,
            workdir=workdir,
            timeout=timeout,
        )
        if _differing:
            logger.info(
                "[tmax] integrity baseline difference for %s in %d protected entr(y/ies): %s; scoring 0",
                tmax.get("task_id", "?"),
                len(_differing),
                describe_differences(_protected, _differing),
            )
            return 0.0

    verification = sb.process.exec(
        _root_sh(f"chmod +x {_TEST_SH}; bash {_TEST_SH}"),
        timeout=timeout,
    )
    r = sb.process.exec(_root_sh(f"cat {reward_path}"), timeout=30)
    reward_txt = r.result if getattr(r, "exit_code", 1) == 0 else ""
    reward = _parse_reward(reward_txt)
    _check_verifier_python_download(test_sh, verification.result or "", reward)
    if reward_txt.strip() == nonce:
        return 0.0
    return reward
