# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import json
from types import SimpleNamespace

import evolve_codex as ec

import pytest

from test_blind_verifier import _rewrite, _wire, SEED


@pytest.fixture
def setup(tmp_path, monkeypatch):
    rewrite = _rewrite(tmp_path, monkeypatch)
    monkeypatch.setattr(ec, "_sandbox_down", lambda pkg: None)
    monkeypatch.setattr(ec, "_session_id", lambda session: "verifier-thread")
    with ec.session(rewrite, "verifier", timeout=60) as run:
        ec._blind_layout(rewrite.package, run.dir.package)
    verifier = run.dir
    calls = []

    def author(run, package, prompt, resume=None):
        calls.append((run.meta["kind"], package, prompt, resume))
        if run.meta["kind"] == "probe":
            assert not (package / "solution").exists()
            assert not (package / "tests/test_state.py").exists()
            assert "exit 2" in (package / "tests/test.sh").read_text()
            assert not (package / "run/seed_size.json").exists()
            controls = package / "run/verifier-probes"
            controls.mkdir()
            (controls / "contract.json").write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "requirement": "Preserve input bytes",
                                "wrong_behavior": "Trim spaces",
                                "expected_failure": "Padded input must retain its spaces",
                            }
                        ]
                    }
                )
            )
            (controls / "correct.sh").write_text("correct")
            (controls / "wrong-1.sh").write_text("wrong")
        run.meta["exit_code"] = 0
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ec, "_run_codex", author)
    monkeypatch.setattr(ec, "verify_probes", lambda *args: None)
    return rewrite, verifier, calls, author


def miss(package):
    path = package / "run/verifier-probe-results.jsonl"
    path.write_text('{"case":"wrong-1","returncode":0}\n')
    raise ec.SemanticProbeMisses(["wrong-1/grade passed"], path)


def test_independent_author_cannot_see_grader_or_reference_and_is_reused(setup):
    rewrite, verifier, calls, _ = setup
    ec._independent_verifier(rewrite, verifier)
    ec._independent_verifier(rewrite, verifier, allow_repair=False)
    assert [call[0] for call in calls] == ["probe"]
    pointer = json.loads((verifier.path / "independent-probes.json").read_text())
    assert set(pointer["controls_sha256"]) == {
        "correct.sh",
        "wrong-1.sh",
        "contract.json",
    }


def test_semantic_failure_gets_one_repair_and_replays_unchanged_controls(
    setup, monkeypatch
):
    rewrite, verifier, calls, _ = setup
    replays = []

    def grade(package, env, timeout):
        if package == verifier.package:
            return
        replays.append(ec._probe_hashes(package / "run/verifier-probes"))
        if len(replays) == 1:
            miss(package)

    monkeypatch.setattr(ec, "verify_probes", grade)
    ec._independent_verifier(rewrite, verifier)
    assert [call[0] for call in calls] == ["probe", "probe-repair"]
    assert calls[-1][3] == "verifier-thread"
    assert len(replays) == 2 and replays[0] == replays[1]


def test_persistent_semantic_failure_stops_after_one_repair(setup, monkeypatch):
    rewrite, verifier, calls, _ = setup

    def grade(package, env, timeout):
        if package != verifier.package:
            miss(package)

    monkeypatch.setattr(ec, "verify_probes", grade)
    with pytest.raises(ec.SemanticProbeMisses):
        ec._independent_verifier(rewrite, verifier)
    assert [call[0] for call in calls] == ["probe", "probe-repair"]


def test_infrastructure_failure_does_not_trigger_repair(setup, monkeypatch):
    rewrite, verifier, calls, _ = setup

    def grade(*args):
        raise RuntimeError("sandbox creation failed")

    monkeypatch.setattr(ec, "verify_probes", grade)
    with pytest.raises(RuntimeError, match="sandbox creation"):
        ec._independent_verifier(rewrite, verifier)
    assert [call[0] for call in calls] == ["probe"]


def test_reference_repair_recheck_does_not_add_another_probe_repair(setup, monkeypatch):
    rewrite, verifier, calls, _ = setup
    ec._independent_verifier(rewrite, verifier)
    monkeypatch.setattr(ec, "verify_probes", lambda package, *args: miss(package))
    with pytest.raises(ec.SemanticProbeMisses):
        ec._independent_verifier(rewrite, verifier, allow_repair=False)
    assert [call[0] for call in calls] == ["probe"]


@pytest.mark.parametrize("mutation", ["public", "controls"])
def test_repair_cannot_change_public_task_or_independent_controls(
    setup, monkeypatch, mutation
):
    rewrite, verifier, calls, author = setup

    def repair(run, package, prompt, resume=None):
        result = author(run, package, prompt, resume)
        if run.meta["kind"] == "probe-repair":
            target = (
                package / "instruction.md"
                if mutation == "public"
                else calls[0][1] / "run/verifier-probes/wrong-1.sh"
            )
            target.write_text("changed")
        return result

    monkeypatch.setattr(ec, "_run_codex", repair)
    monkeypatch.setattr(
        ec,
        "verify_probes",
        lambda package, *args: miss(package) if package != verifier.package else None,
    )
    with pytest.raises(RuntimeError, match="changed"):
        ec._independent_verifier(rewrite, verifier)


def test_independent_failure_prevents_acceptance(tmp_path, monkeypatch):
    rewrite = _rewrite(tmp_path, monkeypatch)
    _wire(monkeypatch, [], [])

    def reject(*args, **kwargs):
        raise RuntimeError("independent verifier rejected")

    monkeypatch.setattr(ec, "_independent_verifier", reject)
    with pytest.raises(RuntimeError, match="independent verifier rejected"):
        ec.evolve_agentic(rewrite, dict(SEED), "harder")
    assert (rewrite.package / "tests/test_state.py").read_text() == SEED[
        "test_state_py"
    ]
