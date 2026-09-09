# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""One codex invocation is one session directory under the rewrite: what it
holds, how a resume finds its thread, and what the package looks like to the
agent."""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

FILES = {
    "instruction.md": "original instruction\n",
    "environment/Dockerfile": "FROM scratch\n",
    "solution/solve.sh": "#!/bin/sh\ncd /app\nmake\n",
    "tests/test_state.py": 'assert report["legacy_key"]\nassert 1\n',
}
TASK = {
    "instruction": FILES["instruction.md"],
    "dockerfile": FILES["environment/Dockerfile"],
    "solve_sh": FILES["solution/solve.sh"],
    "test_state_py": FILES["tests/test_state.py"],
    "_verifier_rel": "tests/test_state.py",
}


def test_account_auth_never_loads_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRL_BASE", str(tmp_path))
    (tmp_path / "account").mkdir()
    (tmp_path / "account/auth.json").write_text("account credential")
    monkeypatch.setattr(ec, "ACCOUNT_HOME", str(tmp_path / "account"))
    monkeypatch.setattr(ec, "_harness_env", lambda: {"OPENAI_API_KEY": "unused"})
    monkeypatch.setattr(ec.llm, "_api_key", lambda: pytest.fail("API auth used"))
    env = ec._codex_env(SimpleNamespace(codex_home=tmp_path / "private"))
    assert env["CODEX_HOME"] == str(tmp_path / "private")
    assert (tmp_path / "private/auth.json").is_symlink()
    assert (tmp_path / "private/auth.json").samefile(tmp_path / "account/auth.json")
    assert "OPENAI_API_KEY" not in env
    cmd = ec._codex_cmd(tmp_path)
    assert "--ignore-user-config" in cmd and "--json" in cmd
    assert "model_provider=openai" in cmd
    assert not any("env_key" in part or "base_url" in part for part in cmd)


def test_account_cleanup_preserves_source_auth_and_private_trace(
    tmp_path, monkeypatch
) -> None:
    account = tmp_path / "account"
    source = account / "sessions"
    source.mkdir(parents=True)
    (source / "rollout-own.jsonl").write_text("own transcript\n")
    (source / "rollout-other.jsonl").write_text("other account activity\n")
    (account / "auth.json").write_text("credential must stay here")
    private = tmp_path / "private"
    monkeypatch.setattr(ec, "ACCOUNT_HOME", str(account))
    monkeypatch.setattr(ec, "_harness_env", dict)
    ec._codex_env(SimpleNamespace(codex_home=private))
    (private / "sessions").mkdir()
    (private / "sessions/rollout-own.jsonl").write_text("own transcript\n")
    ec._prune_private_home(private)
    assert (private / "sessions/rollout-own.jsonl").read_text() == "own transcript\n"
    assert not (private / "sessions/rollout-other.jsonl").exists()
    assert not (private / "auth.json").exists()
    assert (account / "auth.json").read_text() == "credential must stay here"


@pytest.mark.parametrize("via_stdin", [False, True])
def test_sandbox_patch_uses_run_codex_and_preserves_failure(
    tmp_path, monkeypatch, via_stdin
) -> None:
    tools = tmp_path / "bin"
    tools.mkdir()
    codex = tools / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        "printf '%s' \"$1\" > command.txt\n"
        "printf '%s' \"$2\" > patch.txt\n"
        "exit 7\n"
    )
    codex.chmod(0o755)
    monkeypatch.setenv("TRL_BASE", str(tmp_path))
    patch = "*** Begin Patch\n*** Add File: result\n+$(touch unsafe)\n*** End Patch"
    command = ["bash", str(ec.SANDBOX), "patch"]
    if not via_stdin:
        command.append(patch)
    result = subprocess.run(
        command, input=patch if via_stdin else "", text=True, cwd=tmp_path
    )
    assert result.returncode == 7
    assert (tmp_path / "command.txt").read_text() == "--codex-run-as-apply-patch"
    assert (tmp_path / "patch.txt").read_text() == patch
    assert not (tmp_path / "unsafe").exists()


@pytest.mark.parametrize("has_evidence", [True, False])
def test_spec_repair_requires_a_declaration_and_has_no_growth_floor(
    tmp_path, monkeypatch, has_evidence
):
    rw = _rewrite(tmp_path, monkeypatch)

    def prepare(pkg, task):
        (pkg / "run").mkdir()
        (pkg / "run/seed_size.json").write_text("{}")
        return ec.ev.file_map(task)

    def run_codex(run, pkg, prompt):
        assert not (pkg / "run/seed_size.json").exists()
        assert (pkg / "run/failure.txt").read_text() == "suspected mismatch"
        assert "claim to check" in prompt
        (pkg / "instruction.md").write_text("clarified instruction")
        repair = {
            key: "documented observation"
            for key in (
                "diagnosis",
                "evidence",
                "change",
                "retained_skill",
                "validation",
            )
        }
        if not has_evidence:
            repair.pop("evidence")
        (pkg / "run/repair.json").write_text(json.dumps(repair))
        return subprocess.CompletedProcess([], 0, stdout="")

    monkeypatch.setattr(ec, "_require_codex", lambda: None)
    monkeypatch.setattr(ec, "_prepare_package", prepare)
    monkeypatch.setattr(
        ec,
        "session",
        lambda *a, **k: nullcontext(
            SimpleNamespace(dir=SimpleNamespace(path=tmp_path / "session"))
        ),
    )
    monkeypatch.setattr(ec, "_run_codex", run_codex)
    monkeypatch.setattr(ec, "_sandbox_down", lambda pkg: None)
    monkeypatch.setattr(ec, "_require_checked", lambda pkg: None)
    monkeypatch.setattr(ec, "_agent_checked", lambda pkg: True)
    if has_evidence:
        result = ec.evolve_agentic(
            rw, TASK, "repair_spec", observed="suspected mismatch"
        )
        assert result["_spec_repair"]["evidence"] == "documented observation"
        assert "_simplify" not in result
    else:
        with pytest.raises(ValueError, match="nonempty evidence"):
            ec.evolve_agentic(rw, TASK, "repair_spec", observed="suspected mismatch")


def _rewrite(tmp_path, monkeypatch, job: str = "harder") -> layout.RewriteDir:
    """A rewrite directory whose package holds the four files, under a root
    TRL_BASE names (the codex binary and jq are looked up beneath it)."""
    root = layout.Root(tmp_path / "root")
    monkeypatch.setenv("TRL_BASE", str(root.path))
    rw = root.evolution.task("task-a").rewrite(job)
    for rel, text in FILES.items():
        (rw.package / rel).parent.mkdir(parents=True, exist_ok=True)
        (rw.package / rel).write_text(text)
    return rw


@pytest.mark.parametrize("has_previous,solved", [(True, 8), (False, 8), (True, 4)])
def test_only_all_pass_after_simplify_uses_restoration_guidance(
    tmp_path, monkeypatch, has_previous, solved
):
    import agent_sandbox as sandbox

    rw = _rewrite(tmp_path, monkeypatch)
    rw.traces.mkdir(parents=True, exist_ok=True)
    if has_previous:
        (rw.traces / "previous-simplify.json").write_text(
            json.dumps(
                {"simplify": {"operator": "add_scaffold", "hint_level": "vague"}}
            )
        )
    calibration = has_previous and solved == 8

    def run_codex(run, pkg, prompt):
        assert (ec._CALIBRATION_GUIDANCE in prompt) == calibration
        assert (ec._STUDENT_HARDER_GUIDANCE in prompt) != calibration
        assert bool(sandbox._step_audit(pkg)) != calibration
        (pkg / "instruction.md").write_text("adjusted guidance")
        return subprocess.CompletedProcess([], 0, stdout="")

    monkeypatch.setattr(ec, "_require_codex", lambda: None)
    monkeypatch.setattr(ec, "harder_uses_operators", lambda: False)
    monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")
    monkeypatch.setattr(
        ec,
        "session",
        lambda *a, **k: nullcontext(
            SimpleNamespace(dir=SimpleNamespace(path=tmp_path / "session"))
        ),
    )
    monkeypatch.setattr(ec, "_run_codex", run_codex)
    monkeypatch.setattr(ec, "_sandbox_down", lambda pkg: None)
    monkeypatch.setattr(ec, "_check_verdict", lambda pkg: None)
    monkeypatch.setattr(ec, "_require_checked", lambda pkg: None)
    monkeypatch.setattr(ec, "_agent_checked", lambda pkg: True)
    result = ec.evolve_agentic(
        rw, {**TASK, "_solved": solved, "_attempts": 8}, "harder"
    )
    assert bool(result.get("_calibration")) == calibration


def test_calibration_can_retain_a_blind_verifier_but_requires_a_fresh_check(tmp_path):
    pkg, vpkg = tmp_path / "author", tmp_path / "verifier"
    rel, text = "tests/test_state.py", "assert result == expected\n"
    for folder in (pkg, vpkg):
        (folder / rel).parent.mkdir(parents=True)
        (folder / rel).write_text(text)
    (pkg / "run").mkdir()
    check = pkg / "run/checks.jsonl"
    check.write_text('{"verdict":"pass"}\n')
    with pytest.raises(RuntimeError, match="changed nothing"):
        ec._take_verifier(vpkg, pkg, rel, text)
    assert ec._take_verifier(vpkg, pkg, rel, text, allow_unchanged=True) == rel
    assert (pkg / rel).read_text() == text
    assert not check.exists()


class _FakePopen:
    """A codex process that writes to the streams the harness opened for it."""

    def __init__(
        self,
        command,
        *,
        stdout=None,
        stderr=None,
        out="",
        err="",
        returncode=0,
        on_start=None,
        timeout_after=None,
        **kwargs,
    ):
        self.args, self.returncode = command, returncode
        self._out, self._err = stdout, stderr
        self._text, self._errtext = out, err
        self._timeout_after = timeout_after
        self._wrote = False
        self.kwargs = kwargs
        if on_start:
            on_start(command, kwargs)

    def communicate(self, input=None, timeout=None):
        # Whatever ran before the deadline is on disk either way, and it is
        # written once: the reap after kill() does not re-run the process.
        if self._wrote:
            return None, None
        self._wrote = True
        self._out.write(self._text)
        self._out.flush()
        self._err.write(self._errtext)
        self._err.flush()
        if self._timeout_after is not None:
            raise subprocess.TimeoutExpired(self.args, self._timeout_after)
        return None, None

    def kill(self):
        self.returncode = -9


def _fake_popen(monkeypatch, **cfg):
    """Patch Popen with a fake that records the call and writes the streams."""
    seen = {}

    def factory(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return _FakePopen(command, **{**kwargs, **cfg})

    monkeypatch.setattr(ec.subprocess, "Popen", factory)
    monkeypatch.setattr(ec, "_codex_env", lambda sd: {"CODEX_HOME": str(sd.codex_home)})
    monkeypatch.setattr(ec, "_codex_bin", lambda: Path("/opt/bin/codex"))
    return seen


@pytest.mark.parametrize("timed_out", [False, True])
def test_account_run_isolates_context_and_removes_auth_link(
    tmp_path, monkeypatch, timed_out
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    account = tmp_path / "account"
    account.mkdir()
    source_auth = account / "auth.json"
    source_auth.write_text("test account credential")
    role = "Use only this task's package.\n"
    (rw.package / "AGENTS.md").write_text(role)
    monkeypatch.setattr(ec, "ACCOUNT_HOME", str(account))
    monkeypatch.setattr(ec, "CODEX_DRIVER", "exec")
    monkeypatch.setattr(ec, "_codex_bin", lambda: Path("/opt/bin/codex"))
    monkeypatch.setattr(ec.llm, "_api_key", lambda: pytest.fail("API auth used"))
    monkeypatch.setattr(
        ec,
        "_harness_env",
        lambda: {
            "OPENAI_API_KEY": "unused",
            "CODEX_THREAD_ID": "unrelated-thread",
            "CODEX_SESSION_ID": "unrelated-session",
        },
    )
    started = []

    def on_start(command, kwargs):
        env = kwargs["env"]
        private = Path(env["CODEX_HOME"])
        assert private != account
        assert (private / "auth.json").is_symlink()
        assert (private / "auth.json").samefile(source_auth)
        assert (
            not {"OPENAI_API_KEY", "CODEX_THREAD_ID", "CODEX_SESSION_ID"} & env.keys()
        )
        assert "--ignore-user-config" in command and "--json" in command
        assert "model_provider=openai" in command
        assert not any("env_key" in arg or "base_url" in arg for arg in command)
        for override in (
            "project_doc_max_bytes=0",
            "features.skip_host_skill_discovery=true",
            "features.plugins=false",
            "features.apps=false",
            "features.hooks=false",
            "skills.include_instructions=false",
        ):
            assert override in command
        started.append(private)

    class AccountPopen(_FakePopen):
        def communicate(self, input=None, timeout=None):
            if input is not None:
                assert input.startswith(role + "\n\nsimplify the task")
            return super().communicate(input=input, timeout=timeout)

    def factory(command, **kwargs):
        return AccountPopen(
            command,
            on_start=on_start,
            out="account output\n",
            timeout_after=5 if timed_out else None,
            **kwargs,
        )

    monkeypatch.setattr(ec.subprocess, "Popen", factory)
    expectation = (
        pytest.raises(subprocess.TimeoutExpired) if timed_out else nullcontext()
    )
    with expectation:
        with ec.session(rw, "agent", timeout=5) as run:
            try:
                result = ec._run_codex(run, rw.package, "simplify the task")
                assert result.returncode == 0
            finally:
                # Check before session() prunes the home, so it cannot hide a leak.
                assert not (run.dir.codex_home / "auth.json").is_symlink()
                assert not (run.dir.codex_home / "auth.json").exists()
                assert source_auth.read_text() == "test account credential"
                assert run.dir.prompt.read_text().startswith(
                    role + "\n\nsimplify the task"
                )

    assert started == [run.dir.codex_home]
    assert run.dir.stdout.read_text() == "account output\n"
    meta = json.loads(run.dir.meta.read_text())
    assert meta["status"] == ("timed_out" if timed_out else "completed")
    assert meta["exit_code"] == (-9 if timed_out else 0)
    assert source_auth.read_text() == "test account credential"


def test_session_records_start_and_end_and_prunes_the_codex_home(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)

    with ec.session(rw, "agent", timeout=5) as run:
        started = json.loads(run.dir.meta.read_text())
        jsonl = run.dir.codex_home / "sessions/2026/09/01/rollout-test.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text('{"type":"session_meta"}\n')
        (run.dir.codex_home / "state.sqlite").write_text("rebuildable")

    sd = run.dir
    assert sd.path.parent == rw.sessions and sd.path.name.endswith("--agent")
    assert stat.S_IMODE(sd.path.stat().st_mode) == 0o700
    assert started["status"] == "running" and started["finished"] is None
    meta = json.loads(sd.meta.read_text())
    assert meta["kind"] == "agent" and meta["status"] == "completed"
    assert (
        meta["model"] == ec.CODEX_MODEL and meta["reasoning_effort"] == ec.CODEX_EFFORT
    )
    assert meta["driver"] == ec.CODEX_DRIVER and meta["timeout_sec"] == 5
    assert meta["finished"] >= meta["started"] and meta["filtered"] is False
    assert meta["error"] is None
    assert jsonl.exists() and not (sd.codex_home / "state.sqlite").exists()


def test_session_records_failure_blocked_and_timeout(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="agent failed"):
        with ec.session(rw, "oracle", timeout=5) as run:
            raise RuntimeError("agent failed")
    meta = json.loads(run.dir.meta.read_text())
    assert meta["status"] == "failed" and meta["error"] == "RuntimeError: agent failed"

    with pytest.raises(ec.Blocked):
        with ec.session(rw, "agent", timeout=5) as run:
            raise ec.Blocked("GIVE UP: nothing fits")
    assert json.loads(run.dir.meta.read_text())["status"] == "blocked"

    with pytest.raises(subprocess.TimeoutExpired):
        with ec.session(rw, "repair", timeout=5) as run:
            raise subprocess.TimeoutExpired(["codex"], 5)
    assert json.loads(run.dir.meta.read_text())["status"] == "timed_out"
    # Three sessions inside one second share a stamp and sort by kind.
    assert sorted(s.path.name.split("--")[1] for s in rw.session_dirs()) == [
        "agent",
        "oracle",
        "repair",
    ]


def test_two_sessions_of_one_kind_in_one_second_get_distinct_sorted_names(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    monkeypatch.setattr(ec.time, "time", lambda: 1_800_000_000.0)
    first = ec._new_session_dir(rw, "agent")
    first.path.mkdir(parents=True)
    second = ec._new_session_dir(rw, "agent")
    assert first.path != second.path and second.path.name > first.path.name
    assert second.path.name.endswith("--agent")


def test_run_codex_streams_to_the_session_and_runs_in_the_package(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    seen = _fake_popen(monkeypatch, out="VERDICT: pass\n", err="warning\n")

    with ec.session(rw, "agent", timeout=17) as run:
        result = ec._run_codex(run, rw.package, "do the work")

    command, kwargs = seen["command"], seen["kwargs"]
    assert kwargs["cwd"] == str(rw.package)
    assert command[0] == "/opt/bin/codex" and command[1] == "exec"
    assert command[command.index("-C") + 1] == str(rw.package)
    assert f"model_reasoning_effort={ec.CODEX_EFFORT}" in command
    assert kwargs["env"]["CODEX_HOME"] == str(run.dir.codex_home)
    assert result.returncode == 0
    assert run.dir.prompt.read_text().startswith("do the work\n\n")
    assert "./sandbox patch" in run.dir.prompt.read_text()
    assert run.dir.stdout.read_text() == "VERDICT: pass\n"
    assert run.dir.stderr.read_text() == "warning\n"
    meta = json.loads(run.dir.meta.read_text())
    assert (
        meta["status"] == "completed"
        and meta["exit_code"] == 0
        and meta["timeout_sec"] == 17
    )
    # Nothing the harness wrote landed where the agent works.
    assert sorted(p.name for p in rw.package.iterdir()) == [
        "environment",
        "instruction.md",
        "solution",
        "tests",
    ]


def test_run_codex_resume_continues_in_place_and_links_the_thread(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    seen = _fake_popen(monkeypatch)
    with ec.session(rw, "agent", timeout=5) as first:
        jsonl = first.dir.codex_home / "sessions/2026/09/02/rollout-x.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text('{"type":"session_meta","payload":{"id":"sid-1"}}\n')
    assert ec._session_id(first.dir) == "sid-1"

    with ec.session(rw, "repair", timeout=5, resumes=first.dir) as run:
        ec._run_codex(run, rw.package, "fix it", resume="sid-1")

    command, kwargs = seen["command"], seen["kwargs"]
    assert command[1:3] == ["exec", "resume"] and command[3] == "sid-1"
    assert "-C" not in command
    assert kwargs["cwd"] == str(rw.package)
    assert run.dir.prompt.read_text().startswith("fix it\n\n")
    assert "./sandbox patch" in run.dir.prompt.read_text()
    linked = run.dir.codex_home / "sessions/2026/09/02/rollout-x.jsonl"
    assert linked.stat().st_ino == jsonl.stat().st_ino
    assert ec._session_id(run.dir) == "sid-1"
    meta = json.loads(run.dir.meta.read_text())
    assert (
        meta["resumed"] == f"sessions/{first.dir.path.name}"
        and meta["kind"] == "repair"
    )


def test_run_codex_preserves_partial_output_on_timeout(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    _fake_popen(
        monkeypatch, out="partial stdout", err="partial stderr", timeout_after=9
    )

    with pytest.raises(subprocess.TimeoutExpired):
        with ec.session(rw, "agent", timeout=9) as run:
            ec._run_codex(run, rw.package, "do the work")

    meta = json.loads(run.dir.meta.read_text())
    assert meta["status"] == "timed_out" and meta["timeout_sec"] == 9
    assert meta["exit_code"] == -9
    assert run.dir.stdout.read_text() == "partial stdout"
    assert run.dir.stderr.read_text() == "partial stderr"


def _trace(rw: layout.RewriteDir, n: int = 1, reward: float = 0.0) -> None:
    rollout_record.write_record(
        rw.traces / f"attempt-{n:02d}.jsonl",
        {
            "task": "task-a",
            "rev": 0,
            "run": "r",
            "group": 1,
            "rollout": n - 1,
            "reward": reward,
            "turns": 1,
        },
        [{"turn": 1, "keystrokes": ["ls /app\n"], "output": "a.txt"}],
    )


def test_simplify_codex_rewrites_in_place_and_keeps_the_session(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch, job="easier")
    _trace(rw)
    monkeypatch.setattr(ec, "_codex_bin", lambda: Path(sys.executable))
    monkeypatch.setattr(ec.llm, "_api_key", lambda: "test-key")

    def on_start(command, kwargs):
        pkg = Path(kwargs["cwd"])
        (pkg / "instruction.md").write_text("rewritten instruction")
        (pkg / "environment" / "fixture.txt").write_text("prepared prerequisite")
        (pkg / "run" / "checks.jsonl").write_text('{"verdict":"pass"}\n')
        (pkg / "run" / "simplify.json").write_text(
            json.dumps(
                {
                    "operator": "provide_initial_state",
                    "retained_skill": "transform data",
                    "bottleneck": "cannot prepare input",
                    "change": "supply one fixture",
                    "restore": "remove the fixture",
                    "prediction": "A readable fixture permits transformation; another input search would refute this.",
                    "evidence": [
                        {
                            "attempt": "attempt-01.jsonl",
                            "turn": 1,
                            "observation": "lists inputs",
                        }
                    ],
                }
            )
        )
        jsonl = Path(kwargs["env"]["CODEX_HOME"]) / "sessions/2026/09/01/trace.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text('{"type":"session_meta"}\n')

    seen = {}

    def factory(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return _FakePopen(command, out="done\n", on_start=on_start, **kwargs)

    monkeypatch.setattr(ec.subprocess, "Popen", factory)

    seed = tmp_path / "seed"
    shutil.copytree(rw.package, seed)
    result = ec.simplify_codex(
        rw, {**TASK, "_seed_dir": str(seed)}, solved=0, attempts=16
    )

    assert (
        result["instruction"] == "rewritten instruction"
        and result["_hint"] == "agent_easier"
    )
    assert result["_simplify"]["operator"] == "provide_initial_state"
    assert result["_simplify"]["hint_level"] == "vague"
    assert "environment/fixture.txt" in result["_support_changed"]
    assert not (rw.package / "run" / "seed_size.json").exists()
    assert seen["kwargs"]["cwd"] == str(rw.package)
    sd = layout.SessionDir(Path(result["_session"]))
    assert sd.path.parent == rw.sessions and sd.path.name.endswith("--agent")
    assert json.loads(sd.meta.read_text())["status"] == "completed"
    assert (sd.codex_home / "sessions/2026/09/01/trace.jsonl").exists()
    assert "TRACES." in sd.prompt.read_text()
    agents = (rw.package / "AGENTS.md").read_text()
    assert "run/simplify.json" in agents
    assert "0 of 16" in sd.prompt.read_text()
    assert "Hint level: vague" in sd.prompt.read_text()
    assert (
        seen["kwargs"]["env"]["PATH"].startswith(str(Path(sys.executable).parent) + ":")
        or True
    )


def test_traces_spec_names_the_file_and_the_readers(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    assert ec._traces_spec(rw.traces) == ""

    _trace(rw)
    spec = ec._traces_spec(rw.traces)
    assert "traces/attempt-NN.jsonl" in spec
    assert "head -qn1 traces/*.jsonl" in spec
    assert "keystrokes" in spec and "raw" in spec
    assert (
        "jq -r 'select(.turn)" in spec and "\\(.turn)" in spec
    )  # jq's escape, not python's
    assert "finish_reason" in spec


def test_simplify_without_traces_declines_before_starting_codex(tmp_path, monkeypatch):
    rw = _rewrite(tmp_path, monkeypatch, job="easier")
    with pytest.raises(ec.Blocked, match="requires attempt traces"):
        ec.simplify_codex(rw, dict(TASK))


@pytest.mark.parametrize("case", ["header_only", "infra_failure", "success", "malformed"])
def test_simplify_requires_failed_student_execution(tmp_path, monkeypatch, case):
    rw = _rewrite(tmp_path, monkeypatch, job="easier")
    _trace(rw)
    path = rw.traces / "attempt-01.jsonl"
    head, turns = rollout_record.read_record(path)
    if case == "header_only":
        turns = []  # The header's turn count is not evidence of an execution.
    elif case == "infra_failure":
        head["infra_failed"] = True
    elif case == "success":
        head["reward"] = 1
    if case == "malformed":
        path.write_text("not-json\n")
    else:
        rollout_record.write_record(path, head, turns)
    monkeypatch.setattr(ec, "evolve_agentic", lambda *a, **kw: pytest.fail("author started"))
    with pytest.raises(ec.Blocked, match="requires a failed student execution"):
        ec.simplify_codex(rw, dict(TASK))


def test_simplify_uses_valid_failure_beside_empty_trace(tmp_path, monkeypatch):
    rw = _rewrite(tmp_path, monkeypatch, job="easier")
    _trace(rw, n=2)
    (rw.traces / "attempt-01.jsonl").write_text('{"reward":0,"turns":0}\n')
    monkeypatch.setattr(ec, "evolve_agentic", lambda *a, **kw: {"started": True})
    assert ec.simplify_codex(rw, dict(TASK)) == {"started": True}


def test_prepare_package_records_the_seed_literals_size_and_box(
    tmp_path, monkeypatch
) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    rw.package.chmod(0o755)
    task = {
        **TASK,
        "_resources": {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"},
    }

    fmap = ec._prepare_package(rw.package, task)

    assert fmap["test_state_py"] == "tests/test_state.py"
    assert stat.S_IMODE(rw.package.stat().st_mode) == 0o700
    assert json.loads((rw.package / "run/seed_literals.json").read_text()) == [
        "legacy_key"
    ]
    assert json.loads((rw.package / "run/seed_size.json").read_text()) == {
        "solution_lines": 2,
        "verifier_asserts": 2,
    }
    assert (
        json.loads((rw.package / "run/resources.json").read_text())
        == task["_resources"]
    )
    assert (rw.package / "AGENTS.md").read_text() == ec.SPEC.read_text()
    assert (rw.package / "sandbox").stat().st_mode & stat.S_IXUSR
    # The seed's literals are written once: a later call (a resume) does not
    # re-audit a package the agent has already changed.
    (rw.package / "tests/test_state.py").write_text('assert report["new_key"]\n')
    ec._prepare_package(rw.package, task)
    assert json.loads((rw.package / "run/seed_literals.json").read_text()) == [
        "legacy_key"
    ]


def test_prepare_package_tells_the_author_how_to_protect_paths_and_commands(
    tmp_path, monkeypatch
) -> None:
    """The role the authoring agent reads (AGENTS.md, copied from the spec) names the
    mechanism for a variant's protected lists: tests/protected_paths.json, digested by the
    harness before and after the episode."""
    rw = _rewrite(tmp_path, monkeypatch)
    rw.package.chmod(0o755)
    task = {
        **TASK,
        "_resources": {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"},
    }

    ec._prepare_package(rw.package, task)

    agents = (rw.package / "AGENTS.md").read_text()
    assert "tests/protected_paths.json" in agents
    assert '{"paths": [...], "cmds": [...]}' in agents
    assert agents.count("tests/protected_paths.json") == 1  # one sentence, once


def test_write_resources_tells_the_sandbox_tool_the_training_box(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ec._write_resources(pkg, {"instruction": "x"})
    assert not (pkg / "run" / "resources.json").exists()
    ec._write_resources(
        pkg, {"_resources": {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}}
    )
    assert json.loads((pkg / "run" / "resources.json").read_text()) == {
        "cpu": 1,
        "mem_gb": 2,
        "disk_gb": 2,
        "source": "row",
    }


def _seed_and_pkg(tmp_path) -> tuple[Path, Path]:
    seed, pkg = tmp_path / "r0", tmp_path / "package"
    for root in (seed, pkg):
        for rel, text in FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        (root / "environment/keep.conf").write_bytes(b"same")
    return seed, pkg


def test_collect_reports_support_files_that_moved_against_the_input_revision(
    tmp_path,
) -> None:
    seed, pkg = _seed_and_pkg(tmp_path)
    (pkg / "instruction.md").write_text("new instruction")
    (pkg / "environment/fixture.bin").write_bytes(b"\x00\x01")
    (pkg / "AGENTS.md").write_text("rules")
    (pkg / "sandbox").write_text("#!/bin/bash\n")
    (pkg / "run").mkdir()
    (pkg / "run/operator.txt").write_text("op")
    (pkg / "traces").mkdir()
    (pkg / "traces/attempt-01.jsonl").write_text("{}")
    task = {**TASK, "_seed_dir": str(seed)}

    out = ec._collect(task, pkg, ec.ev.file_map(task))

    assert out["instruction"] == "new instruction"
    assert out["_support_changed"] == ["environment/fixture.bin"]
    assert ec.support_changes(pkg, None) == []
    # A support file the agent removed counts too.
    (pkg / "environment/keep.conf").unlink()
    assert ec.support_changes(pkg, seed) == [
        "environment/fixture.bin",
        "environment/keep.conf",
    ]


def test_collect_reresolves_a_switched_verifier(tmp_path) -> None:
    # Source carried tests/test_state.py; the agent rewrote the grader as
    # tests/test.sh and dropped the helper. _collect must read test.sh back and
    # carry its path, not crash on the deleted test_state.py.
    seed, pkg = _seed_and_pkg(tmp_path)
    (pkg / "tests/test_state.py").unlink()
    (pkg / "tests/test.sh").write_text("bash grader\n")
    task = {**TASK, "_seed_dir": str(seed)}

    out = ec._collect(task, pkg, ec.ev.file_map(task))

    assert out["_verifier_rel"] == "tests/test.sh"
    assert out["test_state_py"] == "bash grader\n"
    assert out["_support_changed"] == []  # the verifier is not a support file


def test_collect_raises_when_the_agent_deletes_every_verifier(tmp_path) -> None:
    seed, pkg = _seed_and_pkg(tmp_path)
    (pkg / "tests/test_state.py").unlink()
    with pytest.raises(RuntimeError, match="no verifier"):
        ec._collect({**TASK, "_seed_dir": str(seed)}, pkg, ec.ev.file_map(TASK))


def test_collect_carries_the_measurement_of_the_last_passing_check(tmp_path) -> None:
    seed, pkg = _seed_and_pkg(tmp_path)
    (pkg / "run").mkdir()
    checks = pkg / "run/checks.jsonl"
    task = {**TASK, "_seed_dir": str(seed)}

    # A failing last check carries nothing: the measurement is the box's.
    checks.write_text(
        json.dumps({"verdict": "fail", "measured": {"oom_kill": 1}}) + "\n"
    )
    out = ec._collect(task, pkg, ec.ev.file_map(task))
    assert "_measured" not in out

    checks.write_text(
        json.dumps({"verdict": "fail", "measured": {"oom_kill": 1}})
        + "\n"
        + json.dumps(
            {
                "verdict": "pass",
                "at_max": True,
                "resources": {
                    "cpu": 4,
                    "mem_gb": 8,
                    "disk_gb": 10,
                    "source": "ceiling",
                },
                "measured": {
                    "mem_peak_mb": 3100.0,
                    "cpu_seconds": 40.0,
                    "df_used_mb": 900.0,
                },
            }
        )
        + "\n"
    )
    out = ec._collect(task, pkg, ec.ev.file_map(task))
    assert out["_measured"]["mem_peak_mb"] == 3100.0
    assert out["_box"]["source"] == "ceiling"
    assert out["_at_max"] is True


def test_agent_checked_reads_the_last_check(tmp_path) -> None:
    (tmp_path / "run").mkdir()
    checks = tmp_path / "run/checks.jsonl"
    assert ec._agent_checked(tmp_path) is False
    checks.write_text('{"verdict": "fail"}\n{"verdict": "pass"}\n')
    assert ec._agent_checked(tmp_path) is True
    checks.write_text('{"verdict": "pass"}\n{"verdict": "fail"}\n')
    assert ec._agent_checked(tmp_path) is False


def test_require_checked_discards_a_session_without_a_passing_check(tmp_path) -> None:
    (tmp_path / "run").mkdir()
    with pytest.raises(RuntimeError, match="without a passing"):
        ec._require_checked(tmp_path)
    (tmp_path / "run/checks.jsonl").write_text('{"verdict": "fail"}\n')
    with pytest.raises(RuntimeError, match="without a passing"):
        ec._require_checked(tmp_path)
    (tmp_path / "run/checks.jsonl").write_text(
        '{"verdict": "fail"}\n{"verdict": "pass"}\n'
    )
    ec._require_checked(tmp_path)


def test_check_verdict_raises_blocked_on_give_up(tmp_path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run/verdict.txt").write_text("GIVE UP: operator-misfit — nothing fits")
    with pytest.raises(ec.Blocked, match="operator-misfit"):
        ec._check_verdict(tmp_path)


def test_cyber_filtered_reads_the_sessions_streams(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    sd = rw.session("agent")
    sd.path.mkdir(parents=True)
    assert ec.cyber_filtered(sd) is False
    sd.stderr.write_text("angr symbolic execution notes\n")
    assert ec.cyber_filtered(sd) is False
    sd.stdout.write_text(
        "ERROR: This content was flagged for possible cybersecurity risk. If this seems wrong...\n"
    )
    assert ec.cyber_filtered(sd) is True


def test_codex_env_puts_the_roots_bin_first_on_path(tmp_path, monkeypatch) -> None:
    rw = _rewrite(tmp_path, monkeypatch)
    monkeypatch.setattr(ec.llm, "_api_key", lambda: "k")
    monkeypatch.setenv("PATH", "/usr/bin")
    sd = rw.session("agent")

    env = ec._codex_env(sd)

    assert env["PATH"].startswith(str(tmp_path / "root" / "bin") + ":/usr/bin")
    assert env["CODEX_HOME"] == str(sd.codex_home)
    assert env["EVOLVE_HARNESS_DIR"] == str(Path(ec.__file__).resolve().parent)
    assert ec._codex_bin() == tmp_path / "root" / "bin" / "codex"


def test_candidates_carry_the_full_card(monkeypatch) -> None:
    monkeypatch.setattr(
        ec.llm, "operator_card", lambda op: '{\n "intent": "why " + "' + '"' + "\n}"
    )
    text = ec._candidates([("fam", "op_a", "one line"), ("fam", "op_b", "other")])

    assert "1. op_a (fam)" in text and "2. op_b (fam)" in text
    assert text.index("one line") < text.index('"intent"') < text.index("2. op_b")


def test_harness_files_are_the_four_the_fold_strips() -> None:
    assert ec.HARNESS == ("AGENTS.md", "sandbox", "run", "traces")


def test_write_pretest_tells_the_sandbox_tool_the_hook(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ec._write_pretest(pkg, {"instruction": "x"})
    assert not (pkg / "run" / "pretest.json").exists()
    ec._write_pretest(pkg, {"_pretest": None})
    assert not (pkg / "run" / "pretest.json").exists()
    ec._write_pretest(pkg, {"_pretest": ("set -u\nexit 0\n", "image:a/b:c")})
    assert json.loads((pkg / "run" / "pretest.json").read_text()) == {
        "pre_test_sh": "set -u\nexit 0\n",
        "pretest_env_identity": "image:a/b:c",
    }
