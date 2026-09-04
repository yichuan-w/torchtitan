from __future__ import annotations

import json
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec


def test_trace_root_is_inside_signal_queue(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SWE_EVOLUTION_TRACE_DIR", raising=False)
    monkeypatch.setenv(
        "SWE_TASK_EVOLUTION_DIR", str(tmp_path / "run" / "evolution" / "signals")
    )

    assert ec._trace_root() == (
        tmp_path / "run" / "evolution" / "signals" / "codex_traces"
    )


def test_trace_root_uses_default_signal_queue_when_env_is_unset(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("SWE_EVOLUTION_TRACE_DIR", raising=False)
    monkeypatch.delenv("SWE_TASK_EVOLUTION_DIR", raising=False)
    monkeypatch.setenv("TRL_BASE", str(tmp_path))

    assert ec._trace_root() == tmp_path / "evolution/signals/codex_traces"


def test_trace_work_persists_after_success_and_prunes_client_state(
    tmp_path, monkeypatch
) -> None:
    trace_root = tmp_path / "codex_traces"
    monkeypatch.setenv("SWE_EVOLUTION_TRACE_DIR", str(trace_root))

    with ec._trace_work("harder", {"_task_id": "task-a"}) as work:
        session = work / ".cxhome/sessions/2026/09/01/rollout-test.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text('{"type":"session_meta"}\n')
        (work / ".cxhome/state.sqlite").write_text("rebuildable")

    metadata = json.loads((work / "trace.json").read_text())
    assert work.parent == trace_root
    assert stat.S_IMODE(trace_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(work.stat().st_mode) == 0o700
    assert (work / "pkg").is_dir() and (work / "harness").is_dir()
    assert metadata["task_id"] == "task-a"
    assert metadata["status"] == "completed"
    assert metadata["reasoning_effort"] == ec.CODEX_EFFORT
    assert metadata["finished_time_unix_ns"] >= metadata["started_time_unix_ns"]
    assert session.exists()
    assert not (work / ".cxhome/state.sqlite").exists()


def test_trace_work_records_failure_and_exposes_its_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SWE_EVOLUTION_TRACE_DIR", str(tmp_path))
    error = RuntimeError("agent failed")

    with pytest.raises(RuntimeError, match="agent failed"):
        with ec._trace_work("oracle", {"_task_id": "task-b"}) as work:
            raise error

    metadata = json.loads((work / "trace.json").read_text())
    assert metadata["status"] == "failed"
    assert metadata["error_type"] == "RuntimeError"
    assert error.codex_trace_dir == str(work)


def test_lay_out_restores_private_package_mode(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o755)
    source.chmod(0o755)
    (source / "fixture.txt").write_text("fixture")
    pkg = tmp_path / "pkg"
    pkg.mkdir(mode=0o700)
    task = {
        "_src_dir": str(source),
        "instruction": "instruction",
        "dockerfile": "FROM scratch\n",
        "solve_sh": "#!/bin/sh\n",
        "test_state_py": "assert True\n",
    }

    ec._lay_out(task, pkg)

    assert stat.S_IMODE(pkg.stat().st_mode) == 0o700
    assert (pkg / "fixture.txt").read_text() == "fixture"


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "pkg").mkdir(parents=True)
    (work / "harness").mkdir()
    return work


def test_run_codex_keeps_harness_records_out_of_the_agents_directory(
    tmp_path, monkeypatch
) -> None:
    work = _work(tmp_path)
    pkg = work / "pkg"
    (pkg / "instruction.md").write_text("original instruction")
    (pkg / "AGENTS.md").write_text("agent rules")
    (pkg / "traces").mkdir()
    (pkg / "traces/attempt-01.txt").write_text("rollout transcript")
    (pkg / "run").mkdir()
    (pkg / "run/failure.txt").write_text("observed validator failure")
    monkeypatch.setattr(ec, "_codex_env", lambda _work: {"CODEX_HOME": "unused"})

    def fake_run(command, **kwargs):
        assert (work / "harness/input-package.tar.gz").exists()
        assert kwargs["input"] == "do the work"
        assert kwargs["cwd"] == str(pkg)
        assert command[command.index("-C") + 1] == str(pkg)
        assert f"model_reasoning_effort={ec.CODEX_EFFORT}" in command
        return subprocess.CompletedProcess(command, 0, "VERDICT: pass\n", "warning\n")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    result = ec._run_codex(work, "do the work", timeout=17)

    assert result.returncode == 0
    assert (work / "harness/codex_prompt.txt").read_text() == "do the work"
    assert (work / "harness/codex.stdout.txt").read_text() == "VERDICT: pass\n"
    assert (work / "harness/codex.stderr.txt").read_text() == "warning\n"
    process = json.loads((work / "harness/codex_process.json").read_text())
    assert process["status"] == "exited"
    assert process["returncode"] == 0
    assert process["timeout_seconds"] == 17
    with tarfile.open(work / "harness/input-package.tar.gz") as archive:
        names = set(archive.getnames())
    assert "instruction.md" in names
    assert "AGENTS.md" in names
    assert "traces/attempt-01.txt" in names
    assert "run/failure.txt" in names
    # Nothing the harness wrote landed where the agent works.
    assert sorted(p.name for p in pkg.iterdir()) == [
        "AGENTS.md", "instruction.md", "run", "traces"]


def test_run_codex_resume_continues_in_place_without_cd(tmp_path, monkeypatch) -> None:
    work = _work(tmp_path)
    monkeypatch.setattr(ec, "_codex_env", lambda _work: {"CODEX_HOME": "unused"})

    def fake_run(command, **kwargs):
        assert command[1:3] == ["exec", "resume"] and command[3] == "sid-1"
        assert "-C" not in command
        assert kwargs["cwd"] == str(work / "pkg")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    ec._run_codex(work, "fix it", timeout=5, resume="sid-1", name="codex.repair1")

    assert (work / "harness/codex.repair1_prompt.txt").read_text() == "fix it"
    assert not (work / "harness/input-package.tar.gz").exists()
    assert json.loads((work / "harness/codex.repair1_process.json").read_text())[
        "status"] == "exited"


def test_run_codex_preserves_partial_output_on_timeout(tmp_path, monkeypatch) -> None:
    work = _work(tmp_path)
    monkeypatch.setattr(ec, "_codex_env", lambda _work: {"CODEX_HOME": "unused"})

    def fake_run(command, **_kwargs):
        raise subprocess.TimeoutExpired(
            command, 9, output=b"partial stdout", stderr=b"partial stderr"
        )

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        ec._run_codex(work, "do the work", timeout=9)

    process = json.loads((work / "harness/codex_process.json").read_text())
    assert process["status"] == "timed_out"
    assert process["timeout_seconds"] == 9
    assert (work / "harness/codex.stdout.txt").read_text() == "partial stdout"
    assert (work / "harness/codex.stderr.txt").read_text() == "partial stderr"


def test_simplify_codex_keeps_a_replayable_trace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SWE_EVOLUTION_TRACE_DIR", str(tmp_path / "codex_traces"))
    monkeypatch.setattr(ec, "CODEX_BIN", sys.executable)
    monkeypatch.setattr(ec.llm, "_api_key", lambda: "test-key")

    def fake_run(command, **kwargs):
        pkg = Path(command[command.index("-C") + 1])
        (pkg / "instruction.md").write_text("rewritten instruction")
        session = Path(kwargs["env"]["CODEX_HOME"]) / "sessions/2026/09/01/trace.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text('{"type":"session_meta"}\n')
        return subprocess.CompletedProcess(command, 0, "done\n", "")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    task = {
        "_task_id": "task-c",
        "instruction": "original instruction",
        "dockerfile": "FROM scratch\n",
        "solve_sh": "#!/bin/sh\n",
        "test_state_py": "assert True\n",
    }

    result = ec.simplify_codex(task, trajectory="failed rollout")

    trace = Path(result["_codex_trace_dir"])
    metadata = json.loads((trace / "trace.json").read_text())
    assert result["instruction"] == "rewritten instruction"
    assert metadata["status"] == "completed"
    assert metadata["task_id"] == "task-c"
    assert (trace / "harness/input-package.tar.gz").exists()
    assert (trace / ".cxhome/sessions/2026/09/01/trace.jsonl").exists()


def test_render_attempt_keeps_every_turn_whole() -> None:
    long_out = "x" * 5000
    text = ec._render_attempt({
        "reward": 1.0, "turns": 2,
        "transcript": [{"cmd": "ls", "out": long_out}, {"cmd": "cat f", "out": "y"}],
        "test_tail": "t" * 1000,
    })

    assert text.startswith("--- attempt reward=1.0 turns=2 ---\n$ ls\n")
    assert long_out in text and "t" * 1000 in text


def test_write_traces_one_file_per_attempt_untruncated(tmp_path) -> None:
    attempts = [{"reward": 1.0, "turns": 1,
                 "transcript": [{"cmd": f"c{i}", "out": "o" * 700}]} for i in range(16)]

    ec._write_traces(tmp_path, attempts, "")

    files = sorted((tmp_path / "traces").iterdir())
    assert [f.name for f in files][:2] == ["attempt-01.txt", "attempt-02.txt"]
    assert len(files) == 16
    assert "o" * 700 in files[0].read_text()


def test_candidates_carry_the_full_card(monkeypatch) -> None:
    monkeypatch.setattr(ec.llm, "operator_card",
                        lambda op: '{\n "intent": "why " + "' + "\"" + '\n}')
    text = ec._candidates([("fam", "op_a", "one line"), ("fam", "op_b", "other")])

    assert "1. op_a (fam)" in text and "2. op_b (fam)" in text
    assert text.index("one line") < text.index('"intent"') < text.index("2. op_b")


def test_collect_carries_new_and_changed_support_files_as_bytes(tmp_path) -> None:
    src = tmp_path / "src"
    (src / "environment").mkdir(parents=True)
    (src / "environment/keep.conf").write_bytes(b"same")
    pkg = tmp_path / "pkg"
    for d in ("environment", "solution", "tests", "run", "traces"):
        (pkg / d).mkdir(parents=True)
    (pkg / "instruction.md").write_text("new instruction")
    (pkg / "environment/Dockerfile").write_text("FROM scratch\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/sh\n")
    (pkg / "tests/test_state.py").write_text("assert True\n")
    (pkg / "environment/keep.conf").write_bytes(b"same")
    (pkg / "environment/fixture.bin").write_bytes(b"\x00\x01")
    (pkg / "AGENTS.md").write_text("rules")
    (pkg / "sandbox").write_text("#!/bin/bash\n")
    (pkg / "run/operator.txt").write_text("op")
    (pkg / "traces/attempt-01.txt").write_text("t")
    task = {"_src_dir": str(src), "instruction": "old", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\n", "test_state_py": "assert True\n"}

    out = ec._collect(task, pkg, ec.ev.file_map(task))

    assert out["instruction"] == "new instruction"
    assert out["_extra_files"] == {"environment/fixture.bin": b"\x00\x01"}


def test_collect_reresolves_a_switched_verifier(tmp_path) -> None:
    # Source carried tests/test_state.py; the agent rewrote the grader as
    # tests/test.sh and dropped the helper. _collect must read test.sh back and
    # carry its path, not crash on the deleted test_state.py.
    src = tmp_path / "src"
    (src / "tests").mkdir(parents=True)
    (src / "tests/test_state.py").write_text("old helper\n")
    pkg = tmp_path / "pkg"
    for d in ("environment", "solution", "tests", "run"):
        (pkg / d).mkdir(parents=True)
    (pkg / "instruction.md").write_text("new")
    (pkg / "environment/Dockerfile").write_text("FROM scratch\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/sh\n")
    (pkg / "tests/test.sh").write_text("bash grader\n")   # only test.sh now
    task = {"_src_dir": str(src), "_verifier_rel": "tests/test_state.py",
            "instruction": "old", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\n", "test_state_py": "old helper\n"}

    out = ec._collect(task, pkg, ec.ev.file_map(task))

    assert out["_verifier_rel"] == "tests/test.sh"
    assert out["test_state_py"] == "bash grader\n"
    assert out["_extra_files"] == {}


def test_collect_raises_when_the_agent_deletes_every_verifier(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    for d in ("environment", "solution", "tests", "run"):
        (pkg / d).mkdir(parents=True)
    (pkg / "instruction.md").write_text("new")
    (pkg / "environment/Dockerfile").write_text("FROM scratch\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/sh\n")   # no tests/* at all
    task = {"_verifier_rel": "tests/test_state.py", "instruction": "old",
            "dockerfile": "FROM scratch\n", "solve_sh": "#!/bin/sh\n",
            "test_state_py": "old\n"}

    with pytest.raises(RuntimeError, match="no verifier"):
        ec._collect(task, pkg, ec.ev.file_map(task))


def test_agent_checked_reads_the_last_check(tmp_path) -> None:
    (tmp_path / "run").mkdir()
    checks = tmp_path / "run/checks.jsonl"
    assert ec._agent_checked(tmp_path) is False
    checks.write_text('{"verdict": "fail"}\n{"verdict": "pass"}\n')
    assert ec._agent_checked(tmp_path) is True
    checks.write_text('{"verdict": "pass"}\n{"verdict": "fail"}\n')
    assert ec._agent_checked(tmp_path) is False


def test_session_id_comes_from_the_recorded_session(tmp_path) -> None:
    work = tmp_path / "work"
    session = work / ".cxhome/sessions/2026/09/02/rollout-x.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        '{"type":"session_meta","payload":{"id":"01a0-sid"}}\n'
        '{"type":"turn_context","payload":{}}\n')

    assert ec._session_id(work) == "01a0-sid"


def test_check_verdict_raises_blocked_on_give_up(tmp_path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/verdict.txt").write_text("GIVE UP: operator-misfit — nothing fits")

    with pytest.raises(ec.Blocked, match="operator-misfit"):
        ec._check_verdict(tmp_path)


def test_write_resources_tells_the_sandbox_tool_the_training_box(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ec._write_resources(pkg, {"instruction": "x"})
    assert not (pkg / "run" / "resources.json").exists()

    ec._write_resources(pkg, {"_resources": {"cpu": 1, "mem_gb": 2, "disk_gb": 2,
                                             "source": "row"}})
    assert json.loads((pkg / "run" / "resources.json").read_text()) == {
        "cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}


def test_collect_carries_the_measurement_of_the_last_passing_check(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    for d in ("environment", "solution", "tests", "run"):
        (pkg / d).mkdir(parents=True)
    (pkg / "instruction.md").write_text("new")
    (pkg / "environment/Dockerfile").write_text("FROM scratch\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/sh\n")
    (pkg / "tests/test_state.py").write_text("assert True\n")
    task = {"instruction": "old", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\n", "test_state_py": "assert True\n"}
    checks = pkg / "run/checks.jsonl"

    # A failing last check carries nothing: the measurement is the box's.
    checks.write_text(json.dumps({"verdict": "fail", "measured": {"oom_kill": 1}}) + "\n")
    out = ec._collect(task, pkg, ec.ev.file_map(task))
    assert "_measured" not in out

    checks.write_text(
        json.dumps({"verdict": "fail", "measured": {"oom_kill": 1}}) + "\n"
        + json.dumps({"verdict": "pass", "at_max": True,
                      "resources": {"cpu": 4, "mem_gb": 8, "disk_gb": 10, "source": "ceiling"},
                      "measured": {"mem_peak_mb": 3100.0, "cpu_seconds": 40.0,
                                   "df_used_mb": 900.0}}) + "\n")
    out = ec._collect(task, pkg, ec.ev.file_map(task))
    assert out["_measured"]["mem_peak_mb"] == 3100.0
    assert out["_box"]["source"] == "ceiling"
    assert out["_at_max"] is True


def test_require_checked_discards_a_session_without_a_passing_check(tmp_path) -> None:
    (tmp_path / "run").mkdir()
    with pytest.raises(RuntimeError, match="without a passing"):
        ec._require_checked(tmp_path)
    (tmp_path / "run/checks.jsonl").write_text('{"verdict": "fail"}\n')
    with pytest.raises(RuntimeError, match="without a passing"):
        ec._require_checked(tmp_path)
    (tmp_path / "run/checks.jsonl").write_text('{"verdict": "fail"}\n{"verdict": "pass"}\n')
    ec._require_checked(tmp_path)


def test_lay_out_records_what_the_seed_verifier_already_depends_on_unseen(tmp_path) -> None:
    src = tmp_path / "src"
    for d in ("environment", "solution", "tests"):
        (src / d).mkdir(parents=True)
    (src / "instruction.md").write_text("Do the thing.\n")
    (src / "environment/Dockerfile").write_text("FROM scratch\n")
    (src / "solution/solve.sh").write_text("#!/bin/sh\n")
    (src / "tests/test_state.py").write_text('assert report["legacy_key"]\n')
    task = {"_src_dir": str(src), "instruction": "Do the thing.\n", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\n", "test_state_py": 'assert report["legacy_key"]\n'}
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ec._lay_out(task, pkg)
    assert json.loads((pkg / "run/seed_literals.json").read_text()) == ["legacy_key"]


def test_lay_out_records_the_seed_size(tmp_path) -> None:
    src = tmp_path / "src"
    for d in ("environment", "solution", "tests"):
        (src / d).mkdir(parents=True)
    (src / "instruction.md").write_text("Do the thing.\n")
    (src / "environment/Dockerfile").write_text("FROM scratch\n")
    (src / "solution/solve.sh").write_text("#!/bin/sh\ncd /app\nmake\n")
    (src / "tests/test_state.py").write_text("assert True\nassert 1\n")
    task = {"_src_dir": str(src), "instruction": "Do the thing.\n", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\ncd /app\nmake\n", "test_state_py": "assert True\nassert 1\n"}
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ec._lay_out(task, pkg)
    assert json.loads((pkg / "run/seed_size.json").read_text()) == {
        "solution_lines": 2, "verifier_asserts": 2}
