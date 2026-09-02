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
    assert metadata["task_id"] == "task-a"
    assert metadata["status"] == "completed"
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


def test_lay_out_restores_private_trace_directory_mode(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o755)
    source.chmod(0o755)
    (source / "fixture.txt").write_text("fixture")
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    task = {
        "_src_dir": str(source),
        "instruction": "instruction",
        "dockerfile": "FROM scratch\n",
        "solve_sh": "#!/bin/sh\n",
        "test_state_py": "assert True\n",
    }

    ec._lay_out(task, work)

    assert stat.S_IMODE(work.stat().st_mode) == 0o700


def test_run_codex_archives_pre_agent_workspace_prompt_and_process_output(
    tmp_path, monkeypatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "instruction.md").write_text("original instruction")
    (work / "AGENTS.md").write_text("agent rules")
    (work / "traces").mkdir()
    (work / "traces/attempt-01.txt").write_text("rollout transcript")
    (work / "run").mkdir()
    (work / "run/failure.txt").write_text("observed validator failure")
    monkeypatch.setattr(ec, "_codex_env", lambda _work: {"CODEX_HOME": "unused"})

    def fake_run(command, **kwargs):
        assert (work / "run/input-package.tar.gz").exists()
        assert kwargs["input"] == "do the work"
        return subprocess.CompletedProcess(command, 0, "VERDICT: pass\n", "warning\n")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    result = ec._run_codex(work, "do the work", timeout=17)

    assert result.returncode == 0
    assert (work / "run/codex_prompt.txt").read_text() == "do the work"
    assert (work / "run/codex.stdout.txt").read_text() == "VERDICT: pass\n"
    assert (work / "run/codex.stderr.txt").read_text() == "warning\n"
    process = json.loads((work / "run/codex_process.json").read_text())
    assert process["status"] == "exited"
    assert process["returncode"] == 0
    assert process["timeout_seconds"] == 17
    with tarfile.open(work / "run/input-package.tar.gz") as archive:
        names = set(archive.getnames())
    assert "instruction.md" in names
    assert "AGENTS.md" in names
    assert "traces/attempt-01.txt" in names
    assert "run/failure.txt" in names


def test_run_codex_preserves_partial_output_on_timeout(tmp_path, monkeypatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(ec, "_codex_env", lambda _work: {"CODEX_HOME": "unused"})

    def fake_run(command, **_kwargs):
        raise subprocess.TimeoutExpired(
            command, 9, output=b"partial stdout", stderr=b"partial stderr"
        )

    monkeypatch.setattr(ec.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        ec._run_codex(work, "do the work", timeout=9)

    process = json.loads((work / "run/codex_process.json").read_text())
    assert process["status"] == "timed_out"
    assert process["timeout_seconds"] == 9
    assert (work / "run/codex.stdout.txt").read_text() == "partial stdout"
    assert (work / "run/codex.stderr.txt").read_text() == "partial stderr"


def test_simplify_codex_keeps_a_replayable_trace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SWE_EVOLUTION_TRACE_DIR", str(tmp_path / "codex_traces"))
    monkeypatch.setattr(ec, "CODEX_BIN", sys.executable)
    monkeypatch.setattr(ec.llm, "_api_key", lambda: "test-key")

    def fake_run(command, **kwargs):
        work = Path(command[command.index("-C") + 1])
        (work / "instruction.md").write_text("rewritten instruction")
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
    assert (trace / "run/input-package.tar.gz").exists()
    assert (trace / ".cxhome/sessions/2026/09/01/trace.jsonl").exists()
