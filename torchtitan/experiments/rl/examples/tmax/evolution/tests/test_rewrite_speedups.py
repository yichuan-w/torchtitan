# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Wall-clock work moved off a rewrite's critical path, verdicts unchanged:
codex caches deleted in the background, the independent replay beside the
original one, the Daytona probes beside the blind handoff check, and the
per-stage timings that measure it."""

from __future__ import annotations

import json
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec
import feedback_loop as fb
import rewrite_workspace as rws

from rewrite_deadline import RewriteDeadline
from test_blind_verifier import _rewrite, _wire, NEW_VERIFIER, SEED
from test_feedback_loop_gates import (
    _fake_ec,
    _pkg,
    _rewrite as _gate_rewrite,
    SEED as GATE_SEED,
    SIGNAL,
)


@pytest.fixture(autouse=True)
def stable_rewrite_deadline(monkeypatch):
    deadline = RewriteDeadline(0, 10**12, 0)
    monkeypatch.setattr(ec, "rewrite_deadline", lambda _rewrite: deadline)


# -- codex caches --------------------------------------------------------------


def test_session_exit_moves_caches_out_and_does_not_wait_for_their_deletion(
    tmp_path, monkeypatch
):
    rw = _rewrite(tmp_path, monkeypatch)
    gate = threading.Event()
    real_rmtree = shutil.rmtree

    def held_rmtree(path, ignore_errors=False):
        assert gate.wait(5)
        real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(ec.shutil, "rmtree", held_rmtree)
    with ec.session(rw, "agent", timeout=5) as run:
        home = run.dir.codex_home
        (home / "sessions").mkdir()
        (home / "sessions/rollout.jsonl").write_text("{}\n")
        (home / ".tmp/plugins").mkdir(parents=True)
        (home / ".tmp/plugins/SKILL.md").write_text("cache\n")
        (home / "config.toml").write_text("model = 'x'\n")

    # Returned with the deletion still held: the home is already clean.
    assert sorted(p.name for p in home.iterdir()) == ["sessions"]
    assert len(list(run.dir.path.glob(".codex-cache-*"))) == 1
    gate.set()
    ec.drain_cache_cleanup(rw.path)
    assert not list(run.dir.path.glob(".codex-cache-*"))
    assert (home / "sessions/rollout.jsonl").exists()


def test_publication_drops_caches_whose_deletion_never_ran(tmp_path) -> None:
    session = tmp_path / "active/sessions/20260904-000000Z--verifier"
    (session / ".codex-cache-abc/.tmp").mkdir(parents=True)
    (session / ".codex-cache-abc/.tmp/SKILL.md").write_text("cache\n")
    (session / "package").mkdir()
    (session / "package/instruction.md").write_text("do it\n")
    rws._pack_session_payloads(tmp_path / "active")
    assert sorted(p.name for p in session.iterdir()) == ["package.tar.gz"]


# -- independent replay beside the original replay ------------------------------


def _authors(probe_started: threading.Event | None = None):
    def author(run, package, prompt, resume=None):
        if run.meta["kind"] == "verifier":
            (package / "tests/test_state.py").write_text(
                SEED["test_state_py"] + "\n# revised verifier\n"
            )
        elif run.meta["kind"] == "probe":
            controls = package / "run/verifier-probes"
            controls.mkdir()
            (controls / "contract.json").write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "requirement": "Preserve input bytes",
                                "quote": "Compare /app/a.yml and /app/b.yml with gendiff",
                                "source": "instruction.md",
                                "wrong_behavior": "Trim spaces",
                                "expected_failure": "Padded input loses spaces",
                            }
                        ]
                    }
                )
            )
            (controls / "correct.sh").write_text("correct")
            (controls / "wrong-1.sh").write_text("wrong")
        return SimpleNamespace(returncode=0)

    return author


def _replays(monkeypatch):
    """Record the independent replays; the original replay runs elsewhere.
    The probe author is on for these (EVOLVE_INDEPENDENT_PROBES=1)."""
    monkeypatch.setenv("EVOLVE_INDEPENDENT_PROBES", "1")
    started = threading.Event()
    replays: list[Path] = []

    def verify(pkg, env, timeout, **kwargs):
        if Path(pkg).name.startswith("replay-"):
            replays.append(Path(pkg))
            started.set()

    monkeypatch.setattr(ec, "_run_codex", _authors())
    monkeypatch.setattr(ec, "_sandbox_down", lambda package: None)
    monkeypatch.setattr(ec, "verify_probes", verify)
    return started, replays


def test_independent_replay_runs_beside_the_original_one_and_is_reused(
    tmp_path, monkeypatch
):
    rw = _rewrite(tmp_path, monkeypatch)
    started, replays = _replays(monkeypatch)

    def original(vsession, rewrite=None, deadline=None):
        assert started.wait(5), "independent replay did not overlap the original"

    monkeypatch.setattr(ec, "_verify_original_probes", original)
    timings: dict = {}
    ec._blind_verifier(rw, dict(SEED), ec.ev.file_map(SEED), timings=timings)
    assert len(replays) == 1
    assert set(timings) == {"blind_verifier", "independent_verifier"}
    assert (rw.package / "tests/test_state.py").read_text().endswith(
        "# revised verifier\n"
    )


def test_independent_replay_reruns_when_the_original_replay_moved_the_verifier(
    tmp_path, monkeypatch
):
    rw = _rewrite(tmp_path, monkeypatch)
    started, replays = _replays(monkeypatch)

    def original_with_contract_repair(vsession, rewrite=None, deadline=None):
        assert started.wait(5)
        verifier = vsession.package / "tests/test_state.py"
        verifier.write_text(verifier.read_text() + "# contract repair\n")

    monkeypatch.setattr(ec, "_verify_original_probes", original_with_contract_repair)
    ec._blind_verifier(rw, dict(SEED), ec.ev.file_map(SEED))
    # The early replay graded the old verifier; the verdict comes from a replay
    # of the one that will ship.
    assert len(replays) == 2
    assert (replays[1] / "tests/test_state.py").read_text().endswith(
        "# contract repair\n"
    )


def test_early_replay_misses_reach_the_repair_path_unchanged(tmp_path, monkeypatch):
    rw = _rewrite(tmp_path, monkeypatch)
    monkeypatch.setattr(ec, "_run_codex", _authors())
    monkeypatch.setattr(ec, "_sandbox_down", lambda package: None)
    monkeypatch.setattr(ec, "_verify_original_probes", lambda *a, **k: None)
    replays = []

    def verify(pkg, env, timeout, **kwargs):
        if Path(pkg).name.startswith("replay-"):
            replays.append(pkg)
            log = Path(pkg) / "run/verifier-probe-results.jsonl"
            log.write_text("{}\n")
            raise ec.SemanticProbeMisses(["wrong-1/grade mismatch"], log)

    monkeypatch.setattr(ec, "verify_probes", verify)
    with pytest.raises(ec.SemanticProbeMisses):
        ec._independent_verifier(
            rw,
            _verifier_session(rw),
            allow_repair=False,
            early=_completed_early(rw),
        )
    # Reused: the early run's misses, no second replay.
    assert len(replays) == 1


def _verifier_session(rw):
    with ec.session(rw, "verifier", timeout=60) as run:
        ec._blind_layout(rw.package, run.dir.package)
    ec._author_independent_probes(
        rw, run.dir.package, run.dir.path / "independent-probes.json"
    )
    return run.dir


def _completed_early(rw):
    vsession = ec.layout.SessionDir(sorted(rw.sessions.glob("*--verifier"))[-1])
    done = ThreadPoolExecutor(max_workers=1).submit(lambda: None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            ec._early_independent_replay, done, vsession, ec.rewrite_deadline(rw)
        )


# -- Daytona probes beside the handoff check ------------------------------------


@pytest.mark.parametrize("first_check_passed", [True, False])
def test_evolve_agentic_hands_off_the_package_before_the_check(
    tmp_path, monkeypatch, first_check_passed
):
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    seen = []

    def handoff(pre):
        # What the handoff check is about to grade: the blind verifier is in.
        seen.append(None if pre is None else (pre["test_state_py"], pre["_measured"],
                                              list(checks)))

    if not first_check_passed:
        monkeypatch.setattr(ec, "_reconcile_blind", lambda *args: False)
    timings: dict = {}
    ec.evolve_agentic(rw, dict(SEED), "harder", handoff=handoff, timings=timings)
    # None first (a fresh session invalidates anything earlier), then the
    # package before any check ran, then None again if the check needed repair.
    assert seen[0] is None
    assert seen[1] == (NEW_VERIFIER, {"mem_peak_mb": 50}, [])
    assert seen[2:] == ([] if first_check_passed else [None])
    assert {"agent", "reconcile"} <= set(timings)



BOX = {"cpu": 1, "mem_gb": 2, "disk_gb": 2}


def _probes(monkeypatch):
    calls = []

    def probe(package, shortcut=None, resources=None, require_paths=None,
              pretest_file=None, deadline=None, cancel=None):
        calls.append({"shortcut": shortcut,
                      "box": tuple((resources or {}).get(k) for k in fb.RESOURCE_KEYS)})
        if shortcut:
            return {"ok": True, "stage": "daytona_shortcut", "passed": False,
                    "reward": 0.0}
        return {"ok": True, "stage": "daytona_oracle", "reward": 1.0, "solve_exit": 0,
                "measured": {"mem_peak_mb": 100, "df_used_mb": 10, "cpu_seconds": 1},
                "resources": resources}

    monkeypatch.setattr(fb, "daytona_probe", probe)
    return calls


def _harder(tmp_path):
    work, task = _pkg(tmp_path, GATE_SEED["instruction"], GATE_SEED["test_state_py"])
    task.update(solve_sh=GATE_SEED["solve_sh"], _direction="harder",
                _harder_mode="student")
    orig = {**GATE_SEED, "_harder_mode": "student"}
    return work, task, orig


def _revalidate(work, task, orig, early, box=BOX):
    return fb.revalidate(
        work, task, orig=orig, changed=["solve_sh"],
        resources=fb._probe_box(task, box), early=early,
    )


def test_early_probes_answer_for_an_unchanged_package(tmp_path, monkeypatch):
    work, task, orig = _harder(tmp_path)
    calls = _probes(monkeypatch)
    early = fb.EarlyProbes(work, orig, BOX, None, None)
    early(task)
    verdict = _revalidate(work, task, orig, early)
    assert verdict["ok"] and early.used
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["package", "repair", "box"])
def test_early_probes_are_dropped_when_an_input_moved(tmp_path, monkeypatch, change):
    work, task, orig = _harder(tmp_path)
    calls = _probes(monkeypatch)
    early = fb.EarlyProbes(work, orig, BOX, None, None)
    early(task)
    early._future.result()  # the early pair ran on the old inputs
    box = BOX
    if change == "package":
        (work / "environment/fixture.txt").write_text("moved\n")
    elif change == "repair":
        early(None)
    else:
        box = {**BOX, "mem_gb": 4}
    verdict = _revalidate(work, task, orig, early, box)
    assert verdict["ok"] and not early.used
    assert len(calls) == 4
    # The verdict's pair ran in the final box.
    assert all(c["box"] == (box["cpu"], box["mem_gb"], box["disk_gb"])
               for c in calls[2:])


def test_process_one_records_timings_and_reuses_early_probes(tmp_path, monkeypatch):
    rw, r0 = _gate_rewrite(tmp_path, monkeypatch)
    calls = _probes(monkeypatch)
    harder = "#!/bin/sh\ncd /app\ntrue\n"

    def fake_evolve_agentic(rewrite, agent_task, job, **kwargs):
        (rewrite.package / "solution/solve.sh").write_text(harder)
        new = {**agent_task, "solve_sh": harder, "_support_changed": []}
        kwargs["timings"]["agent"] = 1.0
        kwargs["handoff"](new)
        return new

    monkeypatch.setitem(sys.modules, "evolve_codex",
                        _fake_ec(evolve_agentic=fake_evolve_agentic))
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0, resources=BOX)
    assert rec["status"] == "accepted", rec
    assert len(calls) == 2
    t = rec["timings"]
    assert t["agent"] == 1.0 and t["daytona_early"] is True
    assert {"daytona_oracle", "total"} <= set(t)
