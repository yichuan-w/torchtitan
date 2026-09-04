"""SWE_VERIFIER_AUTHOR=blind: the verifier is written by a session that never
sees the solution, and the two meet only in the harness's own check."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec

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


def _wire(monkeypatch, tmp_path, sessions: list, checks: list, verifier_text=NEW_VERIFIER):
    """Fake the two sessions and the harness check; record what each saw."""
    monkeypatch.setenv("SWE_EVOLUTION_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setattr(ec, "CODEX_BIN", sys.executable)
    monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "blind")
    monkeypatch.setattr(ec, "_sandbox_down", lambda _w: None)

    def fake_run_codex(work, prompt, timeout, resume=None, name="codex"):
        pkg = work / "pkg"
        role = "verifier" if not (pkg / "solution").exists() else "author"
        sessions.append({"role": role, "work": work, "prompt": prompt, "resume": resume,
                         "saw_solution": (pkg / "solution" / "solve.sh").exists()})
        if role == "author":
            (pkg / "solution" / "solve.sh").write_text(NEW_SOLVE)
            (pkg / "instruction.md").write_text(NEW_INSTRUCTION)
            _pass(pkg)                      # the author's own check, seed verifier
        else:
            (pkg / "tests" / "test_state.py").write_text(verifier_text)
        (work / "harness").mkdir(exist_ok=True)
        (work / "harness" / f"{name}.stdout.txt").write_text("")
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_harness_check(work, name="check"):
        checks.append(name)
        pkg = work / "pkg"
        assert (pkg / "solution" / "solve.sh").read_text() == NEW_SOLVE
        assert (pkg / "tests" / "test_state.py").read_text() == verifier_text
        _pass(pkg, stage="oracle")
        return "VERDICT: pass"

    monkeypatch.setattr(ec, "_run_codex", fake_run_codex)
    monkeypatch.setattr(ec, "_harness_check", fake_harness_check)


def test_blind_mode_runs_two_sessions_and_the_second_never_sees_the_solution(tmp_path, monkeypatch) -> None:
    sessions, checks = [], []
    _wire(monkeypatch, tmp_path, sessions, checks)

    out = ec.evolve_agentic(dict(SEED), "harder")

    assert [s["role"] for s in sessions] == ["author", "verifier"]
    assert sessions[0]["saw_solution"] is True and sessions[1]["saw_solution"] is False
    assert "Leave `tests/` exactly as it is" in sessions[0]["prompt"]
    assert "not shown the reference solution" in sessions[1]["prompt"]
    # The verifier came back into the author's package, and the harness ran
    # the check the two sessions never could.
    assert checks == ["check.blind1"]
    assert out["test_state_py"] == NEW_VERIFIER and out["solve_sh"] == NEW_SOLVE
    assert out["instruction"] == NEW_INSTRUCTION
    assert out["_verifier_author"] == "blind"
    assert Path(out["_verifier_trace_dir"]) == sessions[1]["work"]
    assert out["_verifier_trace_dir"] in out["_codex_trace_dirs"]
    assert out["_agent_validated"] is True


def test_same_mode_runs_one_session_that_writes_everything(tmp_path, monkeypatch) -> None:
    sessions, checks = [], []
    _wire(monkeypatch, tmp_path, sessions, checks)
    monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")

    def author_writes_all(work, prompt, timeout, resume=None, name="codex"):
        pkg = work / "pkg"
        sessions.append({"role": "author", "prompt": prompt})
        (pkg / "solution" / "solve.sh").write_text(NEW_SOLVE)
        (pkg / "instruction.md").write_text(NEW_INSTRUCTION)
        (pkg / "tests" / "test_state.py").write_text(NEW_VERIFIER)
        _pass(pkg)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ec, "_run_codex", author_writes_all)
    out = ec.evolve_agentic(dict(SEED), "harder")
    assert len(sessions) == 1 and checks == []
    assert "Leave `tests/` exactly as it is" not in sessions[0]["prompt"]
    assert "_verifier_author" not in out


def test_a_disagreement_gets_one_repair_of_the_verifier_then_is_discarded(tmp_path, monkeypatch) -> None:
    sessions, checks = [], []
    _wire(monkeypatch, tmp_path, sessions, checks)
    verdicts = iter(["fail", "fail"])

    def failing_check(work, name="check"):
        checks.append(name)
        pkg = work / "pkg"
        (pkg / "run").mkdir(exist_ok=True)
        with (pkg / "run" / "checks.jsonl").open("a") as fh:
            fh.write(json.dumps({"verdict": next(verdicts), "reward": 0.0, "solve_exit": 1}) + "\n")
        return "VERDICT: fail   stage=oracle\nAssertionError: no /app/result.sha"

    monkeypatch.setattr(ec, "_harness_check", failing_check)
    monkeypatch.setattr(ec, "_session_id", lambda _w: "sid-v")

    with pytest.raises(RuntimeError, match="still disagree"):
        ec.evolve_agentic(dict(SEED), "harder")

    # author, blind verifier, then one resume of the verifier's session -- never the author's.
    assert [s["role"] for s in sessions] == ["author", "verifier", "verifier"]
    assert sessions[2]["resume"] == "sid-v"
    assert "does not agree with the task's reference solution" in sessions[2]["prompt"]
    assert (sessions[2]["work"] / "pkg" / "run" / "failure.txt").read_text().startswith("VERDICT: fail")
    assert checks == ["check.blind1", "check.blind2"]


def test_resume_of_a_blind_rewrite_goes_to_the_verifiers_session(tmp_path, monkeypatch) -> None:
    sessions, checks = [], []
    _wire(monkeypatch, tmp_path, sessions, checks)
    out = ec.evolve_agentic(dict(SEED), "harder")
    monkeypatch.setattr(ec, "_session_id", lambda _w: "sid-v")
    repaired_verifier = NEW_VERIFIER.replace("read().strip()", "read()")

    def repair_session(work, prompt, timeout, resume=None, name="codex"):
        sessions.append({"role": "resume", "work": work, "resume": resume, "prompt": prompt})
        (work / "pkg" / "tests" / "test_state.py").write_text(repaired_verifier)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def check_after_repair(work, name="check"):
        checks.append(name)
        _pass(work / "pkg", stage="oracle")
        return "VERDICT: pass"

    monkeypatch.setattr(ec, "_run_codex", repair_session)
    monkeypatch.setattr(ec, "_harness_check", check_after_repair)

    fixed = ec.resume_agentic(out, "AssertionError: strip", 1)

    assert sessions[-1]["role"] == "resume"
    assert Path(sessions[-1]["work"]) == Path(out["_verifier_trace_dir"])
    assert sessions[-1]["resume"] == "sid-v"
    assert fixed["test_state_py"] == repaired_verifier
    assert fixed["solve_sh"] == NEW_SOLVE
    assert fixed["_repaired"] == "codex_resume_verifier"
