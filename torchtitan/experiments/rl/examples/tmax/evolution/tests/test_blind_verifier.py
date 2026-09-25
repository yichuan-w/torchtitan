# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""SWE_VERIFIER_AUTHOR=blind: the verifier is written by a session that never
sees the solution, in a package copy under its own session directory, and
the two meet only in the harness's own check."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec
from torchtitan.experiments.rl.examples.tmax import layout

SEED = {
    "task_id": "tw_1",
    "instruction": "Compare /app/a.yml and /app/b.yml with gendiff into /app/result.txt.\n",
    "dockerfile": "FROM python:3.11-slim\nWORKDIR /app\n",
    "solve_sh": "#!/bin/bash\nset -e\ngendiff /app/a.yml /app/b.yml > /app/result.txt\n",
    "test_state_py": "import os\n\ndef test_result():\n    assert os.path.isfile('/app/result.txt')\n",
    "_verifier_rel": "tests/test_state.py",
}
NEW_SOLVE = (
    SEED["solve_sh"]
    + "test -s /app/result.txt\nsha256sum /app/result.txt > /app/result.sha\ntest -s /app/result.sha\n"
)
NEW_INSTRUCTION = SEED["instruction"] + "Also write its SHA-256 to /app/result.sha.\n"
NEW_VERIFIER = (
    SEED["test_state_py"]
    + "\ndef test_sha():\n    assert os.path.isfile('/app/result.sha')\n    assert open('/app/result.sha').read().strip()\n"
)


def _pass(pkg: Path, **extra) -> None:
    (pkg / "run").mkdir(exist_ok=True)
    with (pkg / "run" / "checks.jsonl").open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "verdict": "pass",
                    "reward": 1.0,
                    "solve_exit": 0,
                    "measured": {"mem_peak_mb": 50},
                    **extra,
                }
            )
            + "\n"
        )


def _rewrite(tmp_path, monkeypatch) -> layout.RewriteDir:
    root = layout.Root(tmp_path / "root")
    monkeypatch.setenv("TRL_BASE", str(root.path))
    rw = root.evolution.task("tw_1").rewrite("harder")
    for key, rel in ec.ev.file_map(SEED).items():
        (rw.package / rel).parent.mkdir(parents=True, exist_ok=True)
        (rw.package / rel).write_text(SEED[key])
    return rw


def test_blind_layout_hides_the_solution_and_the_harness_files(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    for rel, text in {
        "instruction.md": "do it",
        "solution/solve.sh": "secret",
        "tests/test_state.py": "t",
        "environment/Dockerfile": "FROM x",
        "environment/fixture.csv": "1,2",
        "traces/attempt-01.jsonl": "{}",
        "run/checks.jsonl": "{}",
        "run/verdict.txt": "x",
        "run/hardening.md": "reference strategy",
        "run/student_feedback.json": "student attempts",
        "run/notes/oracle.txt": "reference output",
        "run/check.blind1.txt": "oracle diagnostics",
        "run/seed_size.json": "{}",
        "run/resources.json": "{}",
        "run/seed_literals.json": "{}",
        "run/pretest.json": "{}",
        "AGENTS.md": "author spec",
        "sandbox": "#!/bin/bash",
    }.items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(text)
    vpkg = tmp_path / "vpkg"
    ec._blind_layout(pkg, vpkg)
    seen = {str(p.relative_to(vpkg)) for p in vpkg.rglob("*") if p.is_file()}
    assert "solution/solve.sh" not in seen and "traces/attempt-01.jsonl" not in seen
    assert "run/checks.jsonl" not in seen and "run/verdict.txt" not in seen
    assert {path for path in seen if path.startswith("run/")} == {
        "run/seed_size.json",
        "run/resources.json",
        "run/seed_literals.json",
        "run/pretest.json",
    }
    assert {
        "instruction.md",
        "tests/test_state.py",
        "environment/Dockerfile",
        "environment/fixture.csv",
        "run/seed_size.json",
        "run/resources.json",
    } <= seen
    # The spec is the verifier author's, not the task author's.
    assert (vpkg / "AGENTS.md").read_text() != "author spec"
    assert "not shown the reference solution" in (vpkg / "AGENTS.md").read_text()


def test_take_verifier_replaces_the_seeds_and_voids_the_authors_check(tmp_path) -> None:
    pkg, vpkg = tmp_path / "pkg", tmp_path / "vpkg"
    for d in (pkg, vpkg):
        (d / "tests").mkdir(parents=True)
    (pkg / "tests" / "test_state.py").write_text(SEED["test_state_py"])
    (vpkg / "tests" / "test_state.py").write_text(NEW_VERIFIER)
    _pass(pkg)
    rel = ec._take_verifier(vpkg, pkg, "tests/test_state.py", SEED["test_state_py"])
    assert rel == "tests/test_state.py"
    assert (pkg / rel).read_text() == NEW_VERIFIER
    assert not (pkg / "run" / "checks.jsonl").exists()


def test_take_verifier_refuses_an_unchanged_verifier(tmp_path) -> None:
    pkg, vpkg = tmp_path / "pkg", tmp_path / "vpkg"
    for d in (pkg, vpkg):
        (d / "tests").mkdir(parents=True)
        (d / "tests" / "test_state.py").write_text(SEED["test_state_py"])
    with pytest.raises(RuntimeError, match="changed nothing"):
        ec._take_verifier(vpkg, pkg, "tests/test_state.py", SEED["test_state_py"])


def _wire(monkeypatch, sessions: list, checks: list, verifier_text=NEW_VERIFIER):
    """Fake the sessions and the harness check; record what each saw."""
    monkeypatch.setattr(ec, "_codex_bin", lambda: Path(sys.executable))
    monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "blind")
    monkeypatch.setattr(ec, "_sandbox_down", lambda _pkg: None)
    # These tests isolate author/reference reconciliation; independent replay has separate tests.
    monkeypatch.setattr(ec, "_author_independent_probes", lambda *args: None)
    monkeypatch.setattr(ec, "_independent_verifier", lambda *args, **kwargs: None)

    def fake_run_codex(run, cwd, prompt, resume=None):
        role = "verifier" if not (cwd / "solution").exists() else "author"
        sessions.append(
            {
                "role": role,
                "cwd": cwd,
                "session": run.dir,
                "prompt": prompt,
                "resume": resume,
                "saw_solution": (cwd / "solution" / "solve.sh").exists(),
            }
        )
        if role == "author":
            (cwd / "solution" / "solve.sh").write_text(NEW_SOLVE)
            (cwd / "instruction.md").write_text(NEW_INSTRUCTION)
            _pass(cwd)  # the author's own check, seed verifier
        else:
            # A resumed verifier session repairs; an unchanged file would be
            # refused as "changed nothing", which is its own failure.
            text = verifier_text + ("\n# repaired\n" if resume else "")
            (cwd / "tests" / "test_state.py").write_text(text)
            controls = cwd / "run/verifier-probes"
            controls.mkdir(exist_ok=True)
            (controls / "correct.sh").write_text("original correct")
            (controls / "wrong-1.sh").write_text("original wrong")
            (controls / "contract.json").write_text('{}')
        run.meta["exit_code"] = 0
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_harness_check(pkg, name="check"):
        checks.append(name)
        assert (pkg / "solution" / "solve.sh").read_text() == NEW_SOLVE
        assert (pkg / "tests" / "test_state.py").read_text().startswith(verifier_text)
        _pass(pkg, stage="oracle")
        return "VERDICT: pass"

    monkeypatch.setattr(ec, "_run_codex", fake_run_codex)
    monkeypatch.setattr(ec, "_harness_check", fake_harness_check)
    replays = []
    monkeypatch.setattr(
        ec, "verify_probes", lambda pkg, env, timeout: replays.append(pkg)
    )
    return replays


@pytest.mark.parametrize("job", ["harder", "easier"])
def test_blind_mode_runs_two_sessions_and_the_second_never_sees_the_solution(
    tmp_path, monkeypatch, job
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    replays = _wire(monkeypatch, sessions, checks)

    if job == "easier":
        monkeypatch.setattr(
            ec.so,
            "read_decision",
            lambda *args: {
                "operator": "test_simplification",
                "retained_skill": "compare files",
            },
        )
    out = ec.evolve_agentic(rw, dict(SEED), job)

    assert [s["role"] for s in sessions] == ["author", "verifier"]
    assert sessions[0]["saw_solution"] is True and sessions[1]["saw_solution"] is False
    assert sessions[0]["cwd"] == rw.package
    # The verifier's author works in a copy under its own session.
    assert sessions[1]["cwd"] == sessions[1]["session"].package
    assert replays == [sessions[1]["cwd"]]
    assert sessions[1]["session"].path.parent == rw.sessions
    if job == "harder":
        assert "`tests/` is your scratch checker, and it is thrown away" in sessions[0]["prompt"]
    assert "not shown the reference solution" in sessions[1]["prompt"]

    # The verifier came back into the author's package, and the harness ran
    # the check the two sessions never could.
    assert checks == ["check.blind1"]
    assert out["test_state_py"] == NEW_VERIFIER and out["solve_sh"] == NEW_SOLVE
    assert out["instruction"] == NEW_INSTRUCTION
    assert out["_verifier_author"] == "blind"
    assert Path(out["_verifier_session"]) == sessions[1]["session"].path
    assert Path(out["_session"]) == sessions[0]["session"].path
    assert out["_agent_validated"] is True
    kinds = [s.path.name.split("--")[1] for s in rw.session_dirs()]
    assert kinds == ["agent", "verifier"]
    for s in rw.session_dirs():
        assert json.loads(s.meta.read_text())["status"] == "completed"


def test_harder_uses_student_feedback_without_operator_menu(tmp_path, monkeypatch):
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    out = ec.evolve_agentic(rw, dict(SEED), "harder")
    prompt = sessions[0]["prompt"]
    size = json.loads((rw.package / "run/seed_size.json").read_text())
    assert size["require_growth"] is False
    assert "may stay the same length or shrink" in prompt
    assert "run/hardening.md" in prompt
    assert "new inference or decision" in prompt
    assert "listed axes" not in prompt
    assert "operator-misfit" not in prompt
    assert "no-supported-hardening" in prompt
    assert "_operator" not in out
    assert out["_harder_mode"] == "student"


@pytest.mark.parametrize("job", ["harder", "easier"])
def test_measured_feedback_reaches_proposer_only(tmp_path, monkeypatch, job):
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    if job == "easier":
        monkeypatch.setattr(
            ec.so, "read_decision", lambda *_: {"operator": "add_scaffold"}
        )
    feedback = {"solved": 16 if job == "harder" else 0, "scored": 16, "action": job}
    ec.evolve_agentic(rw, {**SEED, "_student_feedback": feedback}, job)
    assert "Read run/student_feedback.json" in sessions[0]["prompt"]
    assert (
        json.loads((rw.package / "run/student_feedback.json").read_text()) == feedback
    )
    assert "student_feedback" not in (rw.package / "instruction.md").read_text()


@pytest.mark.parametrize("job", ["harder", "easier"])
def test_task_history_reaches_the_author_only(tmp_path, monkeypatch, job):
    """traces/history (written by the loop) is named in the author's prompt;
    the blind verifier and the probe neither read about it nor get it."""
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    if job == "easier":
        monkeypatch.setattr(
            ec.so, "read_decision", lambda *_: {"operator": "add_scaffold"}
        )
    (rw.traces / "history").mkdir(parents=True)
    (rw.traces / "history" / "index.jsonl").write_text('{"rewrite": "r"}\n')
    ec.evolve_agentic(rw, dict(SEED), job)
    assert "traces/history/revisions.jsonl" in sessions[0]["prompt"]
    for later in sessions[1:]:
        assert "traces/history" not in later["prompt"]
        assert not (Path(later["cwd"]) / "traces").exists()


def test_no_history_no_history_paragraph(tmp_path, monkeypatch):
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    ec.evolve_agentic(rw, dict(SEED), "harder")
    assert "THIS TASK'S HISTORY" not in sessions[0]["prompt"]


def test_failed_semantic_replay_prevents_accepting_verifier(tmp_path, monkeypatch):
    rw = _rewrite(tmp_path, monkeypatch)
    _wire(monkeypatch, [], [])

    def reject(pkg, env, timeout):
        raise RuntimeError("Semantic probe wrong-2/grade passed unexpectedly")

    monkeypatch.setattr(ec, "verify_probes", reject)
    with pytest.raises(RuntimeError, match="wrong-2/grade"):
        ec.evolve_agentic(rw, dict(SEED), "harder")
    assert (rw.package / "tests/test_state.py").read_text() == SEED["test_state_py"]


@pytest.mark.parametrize("repair", [False, True])
def test_failed_verifier_process_is_recorded_before_replay(
    tmp_path, monkeypatch, repair
):
    rw = _rewrite(tmp_path, monkeypatch)
    replays = _wire(monkeypatch, [], [])
    cleanups = []
    monkeypatch.setattr(ec, "_sandbox_down", lambda pkg: cleanups.append(pkg))
    monkeypatch.setattr(ec, "_session_id", lambda sd: "verifier-session")

    def failed_process(run, cwd, prompt, resume=None):
        run.meta["exit_code"] = 1
        run.dir.stdout.write_text("usage limit exceeded\n")
        return type("P", (), {"returncode": 1})()

    monkeypatch.setattr(ec, "_run_codex", failed_process)
    fmap = ec.ev.file_map(SEED)
    with pytest.raises(RuntimeError, match="Verifier .* exited 1; see"):
        if repair:
            with ec.session(rw, "verifier", timeout=ec.AGENT_TIMEOUT) as previous:
                ec._blind_layout(rw.package, previous.dir.package)
            ec._blind_repair(rw, previous.dir, fmap, "reference failed", 1)
        else:
            ec._blind_verifier(rw, dict(SEED), fmap)

    failed = [
        sd
        for sd in rw.session_dirs()
        if json.loads(sd.meta.read_text())["status"] == "failed"
    ]
    assert len(failed) == 1
    meta = json.loads(failed[0].meta.read_text())
    assert meta["exit_code"] == 1
    assert "exited 1" in meta["error"]
    assert failed[0].stdout.read_text() == "usage limit exceeded\n"
    assert len(cleanups) == 1 and replays == []
    assert (rw.package / "tests/test_state.py").read_text() == SEED["test_state_py"]


def test_same_mode_runs_one_session_that_writes_everything(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")

    def author_writes_all(run, cwd, prompt, resume=None):
        sessions.append({"role": "author", "prompt": prompt})
        (cwd / "solution" / "solve.sh").write_text(NEW_SOLVE)
        (cwd / "instruction.md").write_text(NEW_INSTRUCTION)
        (cwd / "tests" / "test_state.py").write_text(NEW_VERIFIER)
        _pass(cwd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ec, "_run_codex", author_writes_all)
    out = ec.evolve_agentic(rw, dict(SEED), "harder")
    assert len(sessions) == 1 and checks == []
    assert "Leave `tests/` exactly as it is" not in sessions[0]["prompt"]
    assert "_verifier_author" not in out
    assert len(rw.session_dirs()) == 1


def _failing_check(checks, verdicts):
    def failing_check(pkg, name="check"):
        checks.append(name)
        (pkg / "run").mkdir(exist_ok=True)
        with (pkg / "run" / "checks.jsonl").open("a") as fh:
            fh.write(
                json.dumps({"verdict": next(verdicts), "reward": 0.0, "solve_exit": 1})
                + "\n"
            )
        return "VERDICT: fail   stage=oracle\nAssertionError: no /app/result.sha"

    return failing_check


def test_a_disagreement_repairs_author_then_verifier_and_is_discarded_after_the_rounds(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    replays = _wire(monkeypatch, sessions, checks)
    monkeypatch.setattr(ec, "REPAIR_ROUNDS", 1)
    monkeypatch.setattr(
        ec, "_harness_check", _failing_check(checks, iter(["fail", "fail", "fail"]))
    )
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid")

    with pytest.raises(RuntimeError, match="still disagree after 1 repair iteration"):
        ec.evolve_agentic(rw, dict(SEED), "harder")

    # author, blind verifier; then one iteration: the author resumed without
    # the verifier, then the verifier's session resumed with the new failure.
    assert [s["role"] for s in sessions] == ["author", "verifier", "author", "verifier"]
    assert sessions[2]["resume"] == "sid" and sessions[3]["resume"] == "sid"
    assert sessions[2]["cwd"] == rw.package
    assert sessions[3]["cwd"] == sessions[1]["cwd"]
    assert "you are not shown" in sessions[2]["prompt"]
    assert "does not agree with the task's reference solution" in sessions[3]["prompt"]
    assert checks == ["check.blind1", "check.blind.author1", "check.blind.verifier1"]
    assert sessions[2]["session"].path.name.endswith("--repair-author")
    assert sessions[3]["session"].path.name.endswith("--repair")
    assert len(replays) == 2 and replays[1].name.startswith("original-replay-")


def test_a_disagreement_resolved_by_the_authors_repair_needs_no_verifier_repair(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    monkeypatch.setattr(ec, "REPAIR_ROUNDS", 3)
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid")
    passing = ec._harness_check
    verdicts = iter(["fail"])

    def check(pkg, name="check"):
        if name == "check.blind1":
            return _failing_check(checks, verdicts)(pkg, name)
        return passing(pkg, name)

    monkeypatch.setattr(ec, "_harness_check", check)
    out = ec.evolve_agentic(rw, dict(SEED), "harder")
    assert [s["role"] for s in sessions] == ["author", "verifier", "author"]
    assert checks == ["check.blind1", "check.blind.author1"]
    assert out["solve_sh"] == NEW_SOLVE and out["test_state_py"] == NEW_VERIFIER


def test_author_repair_never_sees_the_blind_verifier(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    seed = tmp_path / "r0"
    (seed / "tests").mkdir(parents=True)
    (seed / "tests/test_state.py").write_text(SEED["test_state_py"])
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    monkeypatch.setattr(ec, "REPAIR_ROUNDS", 1)
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid")
    seen = {}
    real = ec._run_codex

    def spy(run, cwd, prompt, resume=None):
        if resume and (cwd / "solution").exists():
            seen["tests"] = (cwd / "tests/test_state.py").read_text()
        return real(run, cwd, prompt, resume)

    monkeypatch.setattr(ec, "_run_codex", spy)
    passing = ec._harness_check
    verdicts = iter(["fail"])
    monkeypatch.setattr(
        ec,
        "_harness_check",
        lambda pkg, name="check": _failing_check(checks, verdicts)(pkg, name)
        if name == "check.blind1"
        else passing(pkg, name),
    )
    ec.evolve_agentic(rw, {**SEED, "_seed_dir": str(seed)}, "harder")
    assert seen["tests"] == SEED["test_state_py"]


def test_resume_of_a_blind_rewrite_goes_to_the_author_first(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    out = ec.evolve_agentic(rw, dict(SEED), "harder")
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid-v")
    repaired_verifier = NEW_VERIFIER.replace("read().strip()", "read()")

    def repair_session(run, cwd, prompt, resume=None):
        sessions.append(
            {
                "role": "resume",
                "cwd": cwd,
                "session": run.dir,
                "resume": resume,
                "prompt": prompt,
            }
        )
        (cwd / "tests" / "test_state.py").write_text(repaired_verifier)
        _pass(cwd)  # the author's own check against its scratch tests/
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def check_after_repair(pkg, name="check"):
        checks.append(name)
        _pass(pkg, stage="oracle")
        return "VERDICT: pass"

    monkeypatch.setattr(ec, "_run_codex", repair_session)
    monkeypatch.setattr(ec, "_harness_check", check_after_repair)

    fixed = ec.resume_agentic(rw, out, "AssertionError: strip", 1)

    # The author's session is resumed first, in its own package, and the
    # repaired solution passes against the blind verifier, so the verifier's
    # session is never resumed; the tests/ it wrote there was scratch.
    assert sessions[-1]["role"] == "resume"
    assert sessions[-1]["cwd"] == rw.package
    assert sessions[-1]["resume"] == "sid-v"
    assert fixed["test_state_py"] == NEW_VERIFIER
    assert fixed["solve_sh"] == NEW_SOLVE
    assert fixed["_repaired"] == "codex_resume_author_then_verifier"
    assert Path(fixed["_session"]).name.endswith("--repair-author")
