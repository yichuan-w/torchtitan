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
NEW_SOLVE = SEED["solve_sh"] + "test -s /app/result.txt\nsha256sum /app/result.txt > /app/result.sha\ntest -s /app/result.sha\n"
NEW_INSTRUCTION = SEED["instruction"] + "Also write its SHA-256 to /app/result.sha.\n"
NEW_VERIFIER = SEED["test_state_py"] + "\ndef test_sha():\n    assert os.path.isfile('/app/result.sha')\n    assert open('/app/result.sha').read().strip()\n"


def _pass(pkg: Path, **extra) -> None:
    (pkg / "run").mkdir(exist_ok=True)
    with (pkg / "run" / "checks.jsonl").open("a") as fh:
        fh.write(json.dumps({"verdict": "pass", "reward": 1.0, "solve_exit": 0,
                             "measured": {"mem_peak_mb": 50}, **extra}) + "\n")


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
    for rel, text in {"instruction.md": "do it", "solution/solve.sh": "secret",
                      "tests/test_state.py": "t", "environment/Dockerfile": "FROM x",
                      "environment/fixture.csv": "1,2", "traces/attempt-01.jsonl": "{}",
                      "run/checks.jsonl": "{}", "run/verdict.txt": "x",
                      "run/seed_size.json": "{}", "run/resources.json": "{}",
                      "AGENTS.md": "author spec", "sandbox": "#!/bin/bash"}.items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(text)
    vpkg = tmp_path / "vpkg"
    ec._blind_layout(pkg, vpkg)
    seen = {str(p.relative_to(vpkg)) for p in vpkg.rglob("*") if p.is_file()}
    assert "solution/solve.sh" not in seen and "traces/attempt-01.jsonl" not in seen
    assert "run/checks.jsonl" not in seen and "run/verdict.txt" not in seen
    assert {"instruction.md", "tests/test_state.py", "environment/Dockerfile",
            "environment/fixture.csv", "run/seed_size.json", "run/resources.json"} <= seen
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

    def fake_run_codex(run, cwd, prompt, resume=None):
        role = "verifier" if not (cwd / "solution").exists() else "author"
        sessions.append({"role": role, "cwd": cwd, "session": run.dir, "prompt": prompt,
                         "resume": resume, "saw_solution": (cwd / "solution" / "solve.sh").exists()})
        if role == "author":
            (cwd / "solution" / "solve.sh").write_text(NEW_SOLVE)
            (cwd / "instruction.md").write_text(NEW_INSTRUCTION)
            _pass(cwd)                      # the author's own check, seed verifier
        else:
            # A resumed verifier session repairs; an unchanged file would be
            # refused as "changed nothing", which is its own failure.
            text = verifier_text + ("\n# repaired\n" if resume else "")
            (cwd / "tests" / "test_state.py").write_text(text)
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


def test_blind_mode_runs_two_sessions_and_the_second_never_sees_the_solution(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)

    out = ec.evolve_agentic(rw, dict(SEED), "harder")

    assert [s["role"] for s in sessions] == ["author", "verifier"]
    assert sessions[0]["saw_solution"] is True and sessions[1]["saw_solution"] is False
    assert sessions[0]["cwd"] == rw.package
    # The verifier's author works in a copy under its own session.
    assert sessions[1]["cwd"] == sessions[1]["session"].package
    assert sessions[1]["session"].path.parent == rw.sessions
    assert "Leave `tests/` exactly as it is" in sessions[0]["prompt"]
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


def test_same_mode_runs_one_session_that_writes_everything(tmp_path, monkeypatch) -> None:
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


def test_a_disagreement_gets_one_repair_of_the_verifier_then_is_discarded(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    verdicts = iter(["fail", "fail"])

    def failing_check(pkg, name="check"):
        checks.append(name)
        (pkg / "run").mkdir(exist_ok=True)
        with (pkg / "run" / "checks.jsonl").open("a") as fh:
            fh.write(json.dumps({"verdict": next(verdicts), "reward": 0.0, "solve_exit": 1}) + "\n")
        return "VERDICT: fail   stage=oracle\nAssertionError: no /app/result.sha"

    monkeypatch.setattr(ec, "_harness_check", failing_check)
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid-v")

    with pytest.raises(RuntimeError, match="still disagree"):
        ec.evolve_agentic(rw, dict(SEED), "harder")

    # author, blind verifier, then one resume of the verifier's session -- never the author's.
    assert [s["role"] for s in sessions] == ["author", "verifier", "verifier"]
    assert sessions[2]["resume"] == "sid-v"
    assert sessions[2]["cwd"] == sessions[1]["cwd"]
    assert "does not agree with the task's reference solution" in sessions[2]["prompt"]
    assert (sessions[2]["cwd"] / "run" / "failure.txt").read_text().startswith("VERDICT: fail")
    assert checks == ["check.blind1", "check.blind2"]
    repair = sessions[2]["session"]
    assert repair.path.name.endswith("--repair")
    assert json.loads(repair.meta.read_text())["resumed"] == f"sessions/{sessions[1]['session'].path.name}"


def test_resume_of_a_blind_rewrite_goes_to_the_verifiers_session(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sessions, checks = [], []
    _wire(monkeypatch, sessions, checks)
    out = ec.evolve_agentic(rw, dict(SEED), "harder")
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid-v")
    repaired_verifier = NEW_VERIFIER.replace("read().strip()", "read()")

    def repair_session(run, cwd, prompt, resume=None):
        sessions.append({"role": "resume", "cwd": cwd, "session": run.dir, "resume": resume,
                         "prompt": prompt})
        (cwd / "tests" / "test_state.py").write_text(repaired_verifier)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def check_after_repair(pkg, name="check"):
        checks.append(name)
        _pass(pkg, stage="oracle")
        return "VERDICT: pass"

    monkeypatch.setattr(ec, "_run_codex", repair_session)
    monkeypatch.setattr(ec, "_harness_check", check_after_repair)

    fixed = ec.resume_agentic(rw, out, "AssertionError: strip", 1)

    assert sessions[-1]["role"] == "resume"
    vsession = layout.SessionDir(Path(out["_verifier_session"]))
    assert sessions[-1]["cwd"] == vsession.package
    assert sessions[-1]["resume"] == "sid-v"
    assert fixed["test_state_py"] == repaired_verifier
    assert fixed["solve_sh"] == NEW_SOLVE
    assert fixed["_repaired"] == "codex_resume_verifier"
