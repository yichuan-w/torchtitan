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

import json
import logging
import os
import posixpath
import shlex
import uuid
from functools import partial
from typing import TYPE_CHECKING

from torchtitan.experiments.rl.examples.tmax.integrity_baseline import (
    integrity_differences,
    protected_paths_of,
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


async def grade_tmax(
    sb: Sandbox,
    tmax: dict,
    *,
    workdir: str,
    timeout_sec: int | None = None,
    baseline_digests: dict[str, str] | None = None,
) -> float:
    """Grade a tmax task in the (already-run) sandbox ``sb`` and return reward.

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
    for rel, content in fixtures.items():
        dest = _grading_fixture_dest(rel)
        if dest is None:
            continue
        await sb.write_file(dest, content, user="root")
    await sb.write_file(_TEST_SH, test_sh, user="root")

    nonce = _make_nonce()
    await sb.exec(
        _pre_grade_command(reward_path, _ctrf_path_for(reward_path), nonce),
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
    # INTEGRITY BASELINE (reaudit): for a row that names protected paths, the rollouter digested them
    # right after setup and handed the digests in as ``baseline_digests``; re-digest the same paths with
    # the same command now and score 0 on any difference, before the verifier runs. Nothing is stamped in
    # the data and nothing is captured at prep. A row with protected paths and no baseline is a harness
    # bug, so it raises (void episode) rather than skipping. Such rows do not consult pre_test_sh below.
    _protected = protected_paths_of(tmax)
    if _protected:
        _differing = await integrity_differences(
            partial(sb.exec, user="root"),
            tmax,
            baseline_digests,
            workdir=workdir,
            timeout=timeout,
        )
        if _differing:
            logger.info(
                "[tmax] integrity baseline difference for %s in %d protected path(s): %s; scoring 0",
                tmax.get("task_id", "?"),
                len(_differing),
                _differing,
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
    await sb.exec(
        f"chmod +x {shlex.quote(_TEST_SH)}; bash {shlex.quote(_TEST_SH)}",
        user="root",
        check=False,
        timeout=timeout,
    )
    reward_txt = await sb.read_file(reward_path, user="root")
    if (reward_txt or "").strip() == nonce:
        logger.info(
            "[tmax] verifier left the sentinel in place (never wrote %s); " "scoring 0",
            reward_path,
        )
        return 0.0
    reward = _parse_reward(reward_txt)
    logger.info("[tmax] graded reward=%.2f (reward_path=%s)", reward, reward_path)
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


def grade_tmax_daytona(
    sb,
    tmax: dict,
    *,
    workdir: str,
    timeout_sec: int | None = None,
) -> float:
    """Same steps as ``grade_tmax`` but against a RAW ``daytona`` Sandbox.

    Uses the daytona SDK's sync API directly (``sb.process.exec`` /
    ``sb.fs.upload_file``) so the grading logic can be exercised in a standalone
    script without importing the full torchtitan/vLLM training stack. Seeds are
    placed separately by ``seed_workspace_daytona`` before the agent runs.
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

    sb.process.exec(
        _root_sh(f"chmod +x {_TEST_SH}; bash {_TEST_SH}"),
        timeout=timeout,
    )
    r = sb.process.exec(_root_sh(f"cat {reward_path}"), timeout=30)
    reward_txt = r.result if getattr(r, "exit_code", 1) == 0 else ""
    if reward_txt.strip() == nonce:
        return 0.0
    return _parse_reward(reward_txt)
