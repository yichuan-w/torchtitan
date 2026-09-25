# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The structural-rewrite gate on unseen verifier paths: what counts as
visible, what the probe is asked, and how a failure reaches the agent; and
process_one's verdicts over one rewrite directory."""
from __future__ import annotations

import json
import shutil
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_loop as fb
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record


@pytest.mark.parametrize(
    "infrastructure,second_ok", [(True, True), (True, False), (False, False)]
)
def test_python_download_failure_uses_the_existing_bounded_probe_retry(
    tmp_path, monkeypatch, infrastructure, second_ok
):
    from torchtitan.experiments.rl.examples.tmax.grading import (
        _check_verifier_python_download,
    )

    if infrastructure:
        output = (
            "error: Request failed after 3 retries\n"
            "Failed to download https://github.com/"
            "astral-sh/python-build-standalone/releases/download/example/python.tar.gz\n"
            "HTTP status server error (504 Gateway Timeout)\n"
        )
        with pytest.raises(RuntimeError) as failure:
            _check_verifier_python_download("uvx --with pytest pytest", output, 0.0)
        reason = str(failure.value)
    else:
        reason = "reward=0.0 solve_exit=0"
    calls, sleeps = [], []
    env = tmp_path / "daytona.env"
    env.write_text("")
    monkeypatch.setattr(fb, "DAYTONA_VENV_PY", sys.executable)
    monkeypatch.setattr(fb, "DAYTONA_ENV_FILE", str(env))
    monkeypatch.setattr(fb.time, "sleep", sleeps.append)

    def run(command, **kwargs):
        calls.append(command)
        ok = len(calls) > 1 and second_ok
        return types.SimpleNamespace(
            stdout=json.dumps({"ok": ok, "why": reason}),
            stderr="",
            returncode=0 if ok else 1,
        )

    monkeypatch.setattr(fb.subprocess, "run", run)
    result = fb.daytona_probe(tmp_path)
    assert result["ok"] == (infrastructure and second_ok)
    assert len(calls) == (2 if infrastructure else 1)
    assert sleeps == ([20] if infrastructure else [])


def _pkg(tmp_path, instruction, verifier, readme=None):
    work = tmp_path / "work"
    (work / "environment").mkdir(parents=True)
    (work / "environment/Dockerfile").write_text("FROM scratch\nWORKDIR /app\n")
    if readme is not None:
        (work / "environment/README.md").write_text(readme)
    # A rewrite one rung above SEED: the seed's solution plus four lines.
    task = {
        "instruction": instruction,
        "dockerfile": "FROM scratch\nWORKDIR /app\n",
        "solve_sh": "#!/bin/sh\ncd /app\nmake\nmake test\ncp out report.txt\n",
        "test_state_py": verifier,
    }
    return work, task


SEED = {
    "instruction": "Write the report to /app/report.txt.",
    "dockerfile": "FROM scratch\nWORKDIR /app\n",
    "solve_sh": "#!/bin/sh\n",
    "test_state_py": 'assert open("/app/report.txt").read()\n',
}
SIGNAL = {
    "task": "t",
    "rev": 0,
    "run": "r",
    "group": 1,
    "direction": "harder",
    "solved": 16,
    "total": 16,
    "attempts": [],
}


def _rewrite(
    tmp_path, monkeypatch, seed: dict = SEED
) -> tuple[layout.RewriteDir, Path]:
    """r0 holding `seed`, and a rewrite whose package is a copy of it."""
    root = layout.Root(tmp_path / "root")
    monkeypatch.setenv("TRL_BASE", str(root.path))
    task = root.evolution.task("t")
    r0 = task.rev(0)
    for key, rel in fb.ev.file_map(seed).items():
        (r0 / rel).parent.mkdir(parents=True, exist_ok=True)
        (r0 / rel).write_text(seed[key])
    rw = task.rewrite("harder")
    rw.path.mkdir(parents=True)
    shutil.copytree(r0, rw.package)
    return rw, r0


def _fake_ec(**overrides):
    return types.SimpleNamespace(
        Blocked=type("Blocked", (Exception,), {}),
        Filtered=type("Filtered", (RuntimeError,), {}),
        CYBER_RETRIES=2,
        **overrides,
    )


@pytest.mark.parametrize("probe_state", ["pass", "unavailable", "error"])
def test_revalidate_uses_only_daytona_even_with_docker_installed(
    tmp_path, monkeypatch, probe_state
):
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")

    def local_execution(*args, **kwargs):
        pytest.fail("revalidation attempted local execution")

    monkeypatch.setattr(fb.subprocess, "run", local_execution)
    monkeypatch.setattr(fb.sl, "build_image", local_execution)
    monkeypatch.setattr(fb.sl, "oracle_check", local_execution)
    monkeypatch.setattr(fb.sl, "shortcut_check", local_execution)
    calls = []

    def probe(*args, **kwargs):
        calls.append(kwargs)
        if probe_state == "unavailable":
            return None
        if probe_state == "error":
            return {"ok": False, "stage": "daytona_error", "why": "unreachable"}
        return {"ok": True, "reward": 1.0, "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", probe)
    verdict = fb.revalidate(work, task)

    if probe_state == "pass":
        assert verdict["ok"] and verdict["fast_path"] == "daytona_oracle"
        assert len(calls) == 2 and any(c.get("shortcut") == ":" for c in calls)
    else:
        assert not verdict["ok"] and len(calls) == 2
        expected = "daytona_unavailable" if probe_state == "unavailable" else "daytona_error"
        assert verdict["stage"] == expected
        assert fb.verdicts_of(verdict)["oracle"] == "error"


@pytest.mark.parametrize("oracle_ok", [True, False])
def test_final_probes_overlap_and_join_before_return(tmp_path, monkeypatch, oracle_ok):
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    both_started = threading.Barrier(2)
    finished = set()
    box = {"cpu": 1, "mem_gb": 2, "disk_gb": 2}
    hook = tmp_path / "pretest.json"

    def probe(package, shortcut=None, resources=None, pretest_file=None, **kwargs):
        assert package == work and resources == box and pretest_file == hook
        both_started.wait(timeout=2)
        finished.add("null" if shortcut else "oracle")
        return {"ok": True, "passed": False} if shortcut else {
            "ok": oracle_ok, "stage": "daytona_oracle", "reward": int(oracle_ok),
        }

    monkeypatch.setattr(fb, "daytona_probe", probe)
    result = fb.revalidate(work, task, resources=box, pretest_file=hook)
    assert result["ok"] is oracle_ok
    assert finished == {"oracle", "null"}


def test_new_dark_paths_names_only_what_nothing_visible_reveals(tmp_path) -> None:
    work, task = _pkg(
        tmp_path,
        "Complete the workflow described in /app/ops/README.md.",
        'assert open("/app/report.txt").read()\n'
        'assert open("/app/ops/audit.json").read()\n'
        'assert open("/app/ops/summary.csv").read()\n'
        'assert open("/usr/bin/curl")\n'
        'PATH = "/usr/local/bin:/usr/bin"\n'
        'import glob; glob.glob("/app/*.log")\n',
        readme="Leave the audit in /app/ops/audit.json.",
    )
    dark = fb.new_dark_paths(work, task, SEED)
    # audit.json: documented in the README the image ships, so visible. The
    # PATH string and the glob are not paths. report.txt is flagged even
    # though the seed required it: the seed's instruction named it and the
    # rewrite's no longer does, which is the instruction dropping something.
    # /usr/bin/curl is left for the container check downstream to clear.
    assert dark == ["/app/ops/summary.csv", "/app/report.txt", "/usr/bin/curl"]


def test_new_dark_paths_ignores_what_the_seed_already_required_unseen(tmp_path) -> None:
    seed = {**SEED, "test_state_py": 'assert open("/app/hidden.txt").read()\n'}
    work, task = _pkg(
        tmp_path, "Do the thing.", 'assert open("/app/hidden.txt").read()\n'
    )
    assert fb.new_dark_paths(work, task, seed) == []


def test_revalidate_keeps_test_failure_when_solution_output_is_empty(
    tmp_path, monkeypatch
) -> None:
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    verifier = {"exit_code": 0, "output_tail": "FAILED test_report: missing report"}
    monkeypatch.setattr(
        fb,
        "daytona_probe",
        lambda *args, **kwargs: {
            "ok": False,
            "stage": "daytona_oracle",
            "reward": 0.0,
            "solve_exit": 0,
            "tail": "",
            "verifier": verifier,
        },
    )
    verdict = fb.revalidate(work, task, orig=SEED)
    assert not verdict["ok"]
    assert verdict["verifier"] == verifier
    assert verdict["tail"] == verifier["output_tail"]


def test_revalidate_records_paths_the_untouched_container_lacks(
    tmp_path, monkeypatch
) -> None:
    work, task = _pkg(
        tmp_path,
        "Do the thing.",
        'assert open("/app/out.json").read()\nassert open("/usr/bin/curl")\n',
    )
    calls = []

    def fake_probe(
        w, shortcut=None, resources=None, require_paths=None, pretest_file=None
    ):
        calls.append((shortcut, list(require_paths or [])))
        if shortcut is None:
            return {
                "ok": True,
                "stage": "daytona_oracle",
                "reward": 1.0,
                "solve_exit": 0,
                "paths_checked": require_paths,
                "paths_missing": [p for p in require_paths if p == "/app/out.json"],
                "measured": {"mem_peak_mb": 100},
                "resources": {"cpu": 1},
            }
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)

    v = fb.revalidate(
        work,
        task,
        orig=SEED,
        changed=["test_state_py"],
        resources={"cpu": 1},
    )

    assert (None, ["/app/out.json", "/usr/bin/curl"]) in calls
    # Advice, not a verdict: the rewrite passes and the missing path rides
    # along in the record for whoever reads it.
    assert v["ok"] is True and v["fast_path"] == "daytona_oracle"
    assert v["advice"]["dark_paths"] == ["/app/out.json"]
    assert len(calls) == 2  # the null probe still runs
    assert fb.verdicts_of(v) == {
        "oracle": "pass",
        "dark_paths": ["/app/out.json"],
        "dark_literals": [],
        "step": [],
    }


def test_revalidate_passes_when_every_unseen_path_is_a_precondition(
    tmp_path, monkeypatch
) -> None:
    work, task = _pkg(tmp_path, "Do the thing.", 'assert open("/usr/bin/curl")\n')

    def fake_probe(
        w, shortcut=None, resources=None, require_paths=None, pretest_file=None
    ):
        if shortcut is None:
            return {
                "ok": True,
                "stage": "daytona_oracle",
                "reward": 1.0,
                "solve_exit": 0,
                "paths_checked": require_paths,
                "paths_missing": [],
                "measured": {"mem_peak_mb": 100},
                "resources": {"cpu": 1},
            }
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)

    v = fb.revalidate(work, task, orig=SEED, changed=["test_state_py"])
    assert v["ok"] is True and v["fast_path"] == "daytona_oracle"


@pytest.mark.parametrize("stage", ["step_size", "daytona_oracle"])
def test_process_one_returns_a_repairable_verdict_to_the_agents_session(
    tmp_path, monkeypatch, stage
) -> None:
    monkeypatch.setattr(
        fb.sl, "sh", lambda *a, **k: pytest.fail("rewrite attempted local execution")
    )
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    seen = {}
    reason = (
        "The rewrite is more than one rung above the seed"
        if stage == "step_size"
        else "reward=0"
    )
    verdicts = iter(
        [
            {
                "ok": False,
                "stage": stage,
                "why": reason,
                "tail": "failure output",
                "solve_exit": 0,
            },
            {"ok": True, "fast_path": "daytona_oracle", "reward": 1.0},
        ]
    )

    def fake_evolve_agentic(rewrite, agent_task, job, **kwargs):
        seen["rewrite"], seen["job"] = rewrite, job
        (rewrite.package / "instruction.md").write_text("Write /app/out.json.\n")
        return {
            **agent_task,
            "instruction": "Write /app/out.json.\n",
            "test_state_py": 'assert open("/app/out.json").read()\n',
            "_session": str(rewrite.session("agent", "20260904-000000Z").path),
            "_support_changed": [],
        }

    def fake_resume_agentic(rewrite, new, observed, exit_code=1):
        seen["observed"], seen["exit_code"] = observed, exit_code
        return {**new, "instruction": "Write the audit to /app/out.json.\n"}

    monkeypatch.setitem(
        sys.modules,
        "evolve_codex",
        _fake_ec(
            evolve_agentic=fake_evolve_agentic, resume_agentic=fake_resume_agentic
        ),
    )
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb, "revalidate", lambda *a, **k: next(verdicts))

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)

    assert rec["status"] == "accepted", rec
    assert rec["oracle_repair"]["ok"] is True
    assert seen["rewrite"] is rw and seen["job"] == "harder"
    assert seen["observed"] == reason + "\n\nfailure output"
    assert seen["exit_code"] == 0
    assert rec["arm"] == "codex" and rec["job"] == "harder"
    assert rec["verdicts"]["oracle"] == "pass" and rec["changed"] == [
        "instruction",
        "test_state_py",
    ]
    # The repair's files are on disk in the package.
    assert (
        rw.package / "instruction.md"
    ).read_text() == "Write the audit to /app/out.json.\n"
    assert "usage" in rec and rec["t_end"] >= rec["t_start"]


def test_process_one_rejects_on_the_verdict_and_says_which_stage(
    tmp_path, monkeypatch
) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)

    def fake_evolve_agentic(rewrite, agent_task, job, **kwargs):
        return {
            **agent_task,
            "instruction": "harder\n",
            "_support_changed": [],
        }

    monkeypatch.setitem(
        sys.modules, "evolve_codex", _fake_ec(evolve_agentic=fake_evolve_agentic)
    )
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(
        fb,
        "revalidate",
        lambda *a, **k: {
            "ok": False,
            "stage": "null_pass",
            "why": "verifier passes on the untouched workspace",
        },
    )

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)

    assert rec["status"] == "rejected" and rec["stage"] == "null_pass"
    assert rec["reason"].startswith("verifier passes")
    assert rec["verdicts"]["oracle"] == "fail"


@pytest.mark.parametrize("arm", ["codex", "claude"])
def test_process_one_keeps_when_the_agent_declines(
    tmp_path, monkeypatch, arm
) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    ec = _fake_ec()

    def declines(rewrite, agent_task, job, **kwargs):
        raise ec.Blocked("GIVE UP: no student-relevant change fits")

    ec.evolve_agentic = declines
    monkeypatch.setitem(sys.modules, "evolve_codex", ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", arm)

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)
    assert rec["status"] == "kept" and "student-relevant" in rec["reason"]


@pytest.mark.parametrize("arm", ["codex", "claude"])
def test_student_hardening_never_requests_an_operator(tmp_path, monkeypatch, arm):
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_RETUNE_AGENT", arm)

    def no_shortlist(*args):
        raise AssertionError("student mode must not request a shortlist")

    def evolve(rewrite, task, job):
        return {
            **task,
            "instruction": "A changed core workflow.",
            "_agent_validated": True,
        }

    monkeypatch.setattr(fb.llm, "operator_shortlist", no_shortlist)
    monkeypatch.setitem(sys.modules, "evolve_codex", _fake_ec(evolve_agentic=evolve))
    monkeypatch.setattr(fb, "revalidate", lambda *a, **k: {"ok": True, "reward": 1.0})
    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)
    assert rec["status"] == "accepted"
    assert rec["harder_mode"] == "student"
    assert "operator" not in rec and "family" not in rec


def test_process_one_rejects_the_retired_chat_arm(
    tmp_path, monkeypatch
) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "chat")
    with pytest.raises(ValueError, match="SWE_RETUNE_AGENT must be"):
        fb.process_one(
            rw, {**SIGNAL, "direction": "easier", "solved": 0}, job="easier", seed_dir=r0
        )
    assert (rw.package / "instruction.md").read_text() == SEED["instruction"]


@pytest.mark.parametrize("declaration", ["_simplify", "_spec_repair"])
def test_easier_uses_full_validation_without_harder_growth(
    tmp_path, monkeypatch, declaration
):
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    task.update(
        solve_sh=SEED["solve_sh"],
        _direction="easier",
        **{declaration: {"operator": "add_scaffold"}},
    )
    calls = []

    def probe(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "passed": False, "reward": 1, "solve_exit": 0}

    monkeypatch.setattr(fb, "daytona_probe", probe)
    verdict = fb.revalidate(
        work, task, orig=SEED, changed=["instruction"]
    )
    assert verdict["ok"] and verdict["fast_path"] == "daytona_oracle"
    assert len(calls) == 2 and any(c.get("shortcut") == ":" for c in calls)
    task["_direction"] = "harder"
    verdict = fb.revalidate(
        work, task, orig=SEED, changed=["instruction"]
    )
    assert verdict["ok"] and verdict["fast_path"] == "daytona_oracle"


def test_calibration_retains_full_validation_and_the_growth_ceiling(
    tmp_path, monkeypatch
):
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    task.update(solve_sh=SEED["solve_sh"], _direction="harder", _calibration=True)
    calls = []

    def probe(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "passed": False, "reward": 1, "solve_exit": 0}

    monkeypatch.setattr(fb, "daytona_probe", probe)
    verdict = fb.revalidate(
        work, task, orig=SEED, changed=["instruction"]
    )
    assert verdict["ok"] and verdict["fast_path"] == "daytona_oracle"
    assert len(calls) == 2 and any(c.get("shortcut") == ":" for c in calls)
    task["solve_sh"] += "\necho extra\n" * (fb.ts.MAX_ADDED + 1)
    verdict = fb.revalidate(work, task, orig=SEED, changed=["solve_sh"])
    assert not verdict["ok"] and verdict["stage"] == "step_size"


@pytest.mark.parametrize(
    "null",
    [None, {"ok": False, "why": "sandbox unavailable"}, {"ok": True}],
)
def test_incomplete_null_check_cannot_accept_a_rewrite(tmp_path, monkeypatch, null):
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    task.update(
        solve_sh=SEED["solve_sh"],
        _direction="easier",
        _simplify={"operator": "add_scaffold"},
    )
    monkeypatch.setattr(
        fb,
        "daytona_probe",
        lambda *args, shortcut=None, **kwargs: null
        if shortcut
        else {"ok": True, "reward": 1},
    )
    verdict = fb.revalidate(
        work, task, orig=SEED, changed=["instruction"]
    )
    assert not verdict["ok"] and verdict["stage"] == "null_check"


@pytest.mark.parametrize("infra_failed", [False, True])
def test_feedback_keeps_task_when_simplify_has_no_failed_execution(
    tmp_path, monkeypatch, infra_failed
):
    import evolve_codex as ec

    rw, r0 = _rewrite(tmp_path, monkeypatch)
    before = {
        str(p.relative_to(rw.package)): p.read_bytes()
        for p in rw.package.rglob("*")
        if p.is_file()
    }
    rollout_record.write_record(
        rw.traces / "attempt-01.jsonl",
        {"reward": 0, "turns": 1, "infra_failed": infra_failed},
        [{"turn": 1, "keystrokes": ["ls\n"]}] if infra_failed else [],
    )
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(
        ec, "evolve_agentic", lambda *a, **kw: pytest.fail("author started")
    )
    monkeypatch.setattr(
        fb, "revalidate", lambda *a, **kw: pytest.fail("sandbox started")
    )
    rec = fb.process_one(rw, {**SIGNAL, "solved": 0}, job="easier", seed_dir=r0)
    assert rec["status"] == "kept" and rec["stage"] == "agent"
    assert "requires a failed student execution" in rec["reason"]
    assert all(
        (rw.package / rel).read_bytes() == content for rel, content in before.items()
    )
    assert all((r0 / rel).read_bytes() == content for rel, content in before.items())


def test_easier_records_decision_and_can_decline(tmp_path, monkeypatch):
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    ec = _fake_ec()
    choice = {"retained_skill": "convert", "change": "one file"}

    def simplify(rewrite, task, **kwargs):
        assert task["_direction"] == "easier"
        return {
            **task,
            "instruction": "Convert one file.",
            "_simplify": choice,
            "_family": "simplify",
            "_agent_validated": True,
        }

    ec.simplify_codex = simplify
    monkeypatch.setitem(sys.modules, "evolve_codex", ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb, "revalidate", lambda *a, **k: {"ok": True})
    rec = fb.process_one(rw, {**SIGNAL, "solved": 0}, job="easier", seed_dir=r0)
    assert rec["status"] == "accepted" and rec["simplify"] == choice
    # The declaration names no intervention kind: the change comes from the attempts.
    assert "operator" not in rec and rec["family"] == "simplify" and rec["agent_validated"]

    def decline(*args, **kwargs):
        raise ec.Blocked("repair_required: invalid task")

    ec.simplify_codex = decline
    rec = fb.process_one(rw, {**SIGNAL, "solved": 0}, job="easier", seed_dir=r0)
    assert rec["status"] == "kept" and "repair_required" in rec["reason"]


def test_spec_defect_keeps_the_input_revision_without_starting_repair(
    tmp_path, monkeypatch
):
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    ec = _fake_ec()
    report = (
        "BLOCKED: repair_required: " + "visible contract disagrees with the check " * 8
    )
    original = {
        str(path.relative_to(r0)): path.read_bytes()
        for path in r0.rglob("*")
        if path.is_file()
    }

    def simplify(rewrite, task, **kwargs):
        (rewrite.package / "run").mkdir()
        (rewrite.package / "run/verdict.txt").write_text(report)
        rewrite.traces.mkdir(exist_ok=True)
        (rewrite.traces / "attempt-01.jsonl").write_text('{"reward": 0}\n')
        (rewrite.package / "environment/partial-edit.txt").write_text("unfinished")
        raise ec.Blocked(report[:200])

    def unexpected(*args, **kwargs):
        pytest.fail("a declined simplify started repair or validation")

    ec.simplify_codex, ec.evolve_agentic = simplify, unexpected
    monkeypatch.setitem(sys.modules, "evolve_codex", ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb, "revalidate", unexpected)
    rec = fb.process_one(rw, {**SIGNAL, "solved": 0}, job="easier", seed_dir=r0)
    assert rec["action"] == "simplify" and "simplify" not in rec
    assert rec["spec_repair"]["reported"] == report
    assert rec["status"] == "kept" and rec["stage"] == "repair_required"
    assert rec["reason"] == report[:300]
    assert {
        str(path.relative_to(r0)): path.read_bytes()
        for path in r0.rglob("*")
        if path.is_file()
    } == original
    assert (rw.package / "environment/partial-edit.txt").read_text() == "unfinished"
    assert (rw.traces / "attempt-01.jsonl").read_text() == '{"reward": 0}\n'


def test_final_size_gate_uses_student_mode(tmp_path, monkeypatch):
    work, task = _pkg(tmp_path, SEED["instruction"], SEED["test_state_py"])
    task.update(solve_sh=SEED["solve_sh"], _direction="harder", _harder_mode="student")
    original = {**SEED, "_harder_mode": "student"}
    monkeypatch.setattr(
        fb,
        "daytona_probe",
        lambda *a, **k: {
            "ok": True,
            "passed": False,
            "reward": 1,
            "solve_exit": 0,
        },
    )
    result = fb.revalidate(
        work, task, orig=original, changed=["solve_sh"]
    )
    assert result["ok"] is True


def test_verdicts_of_maps_the_revalidation_stages() -> None:
    assert fb.verdicts_of(None)["oracle"] is None
    assert (
        fb.verdicts_of({"ok": True, "fast_path": "instruction_only"})["oracle"]
        == "skipped"
    )
    assert fb.verdicts_of({"ok": False, "stage": "step_size", "step": ["s"]}) == {
        "oracle": "pass",
        "dark_paths": [],
        "dark_literals": [],
        "step": ["s"],
    }
    assert fb.verdicts_of(
        {"ok": False, "stage": "daytona_oracle", "literals": ["k"]}
    ) == {"oracle": "fail", "dark_paths": [], "dark_literals": ["k"], "step": []}
    assert fb.verdicts_of({"ok": False, "stage": "daytona_error"})["oracle"] == "error"


def test_revalidate_records_names_the_task_never_states(tmp_path, monkeypatch) -> None:
    work, task = _pkg(
        tmp_path,
        "Write /app/report.json.",
        'report = json.load(open("/app/report.json"))\n'
        'assert report["source_sha256"]\nassert report["input_records"] == 3\n',
    )

    def fake_probe(
        w, shortcut=None, resources=None, require_paths=None, pretest_file=None
    ):
        if shortcut is None:
            return {
                "ok": True,
                "stage": "daytona_oracle",
                "reward": 1.0,
                "solve_exit": 0,
                "paths_checked": require_paths,
                "paths_missing": [],
                "measured": {"mem_peak_mb": 100},
                "resources": {"cpu": 1},
            }
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)

    # The seed's verifier already read input_records unseen; only the new key counts.
    v = fb.revalidate(
        work,
        task,
        orig=SEED,
        changed=["test_state_py"],
        baseline=["input_records"],
    )
    assert v["ok"] is True
    assert v["advice"]["dark_literals"] == ["source_sha256"]

    # An oracle failure carries the names along, so one repair round sees both.
    monkeypatch.setattr(
        fb,
        "daytona_probe",
        lambda *a, **k: {
            "ok": False,
            "stage": "daytona_oracle",
            "reward": 0.0,
            "solve_exit": 1,
            "tail": "boom",
        },
    )
    v = fb.revalidate(
        work,
        task,
        orig=SEED,
        changed=["test_state_py"],
        baseline=["input_records"],
    )
    assert v["stage"] == "daytona_oracle" and v["literals"] == ["source_sha256"]
    assert "Also:" in v["why"] and "source_sha256" in v["why"]


def test_seed_literals_come_from_the_input_revision(tmp_path) -> None:
    src = tmp_path / "r0"
    (src / "environment").mkdir(parents=True)
    (src / "environment" / "README.md").write_text("The report has input_records.\n")
    task = {
        **SEED,
        "test_state_py": 'assert report["input_records"]\nassert report["hidden_key"]\n',
        "_verifier_rel": "tests/test_state.py",
    }
    assert fb.seed_literals(task, src) == ["hidden_key"]


def test_revalidate_sends_back_a_rewrite_that_jumped_too_far(
    tmp_path, monkeypatch
) -> None:
    work, task = _pkg(
        tmp_path,
        "Write the report to /app/report.txt.",
        'assert open("/app/report.txt").read()\n',
    )
    task["solve_sh"] = "\n".join(f"step {i}" for i in range(30)) + "\n"  # seed: 1 line

    def fake_probe(
        w, shortcut=None, resources=None, require_paths=None, pretest_file=None
    ):
        if shortcut is None:
            return {
                "ok": True,
                "stage": "daytona_oracle",
                "reward": 1.0,
                "solve_exit": 0,
                "paths_checked": require_paths,
                "paths_missing": [],
                "measured": {"mem_peak_mb": 100},
                "resources": {"cpu": 1},
            }
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)

    v = fb.revalidate(work, task, orig=SEED, changed=["solve_sh"])
    assert v["ok"] is False and v["stage"] == "step_size"
    assert any("at most 8 more" in s for s in v["step"]) and "size bounds" in v["why"]

    # One rung above the seed passes.
    task["solve_sh"] = (
        SEED["solve_sh"] + "\n".join(f"step {i}" for i in range(5)) + "\n"
    )
    v = fb.revalidate(work, task, orig=SEED, changed=["solve_sh"])
    assert v["ok"] is True


def test_a_filtered_session_is_retried_fresh_then_gives_up(
    tmp_path, monkeypatch
) -> None:
    rw, _r0 = _rewrite(tmp_path, monkeypatch)
    calls = []
    rec: dict = {}

    class FakeEC:
        CYBER_RETRIES = 2
        Filtered = type("Filtered", (RuntimeError,), {})

        def evolve_agentic(self, rewrite, agent_task, job, **kwargs):
            calls.append((rewrite, job))
            if len(calls) < 3:
                raise self.Filtered("classifier stopped the session")
            return {"instruction": "harder", "_support_changed": []}

    ec = FakeEC()
    out = fb._evolve_retrying_the_filter(
        ec, rec, "tw_x", rw, {"instruction": "seed"}
    )
    assert out["instruction"] == "harder"
    assert len(calls) == 3 and all(c == (rw, "harder") for c in calls)
    assert rec["cyber_filtered"] == 2

    # Filtered every time: the task is left alone rather than recorded as bad.
    class AlwaysFiltered(FakeEC):
        def evolve_agentic(self, rewrite, agent_task, job, **kwargs):
            calls.append((rewrite, job))
            raise self.Filtered("stopped again")

    calls.clear()
    with pytest.raises(AlwaysFiltered.Filtered):
        fb._evolve_retrying_the_filter(AlwaysFiltered(), rec, "tw_x", rw, {})
    assert len(calls) == 3
