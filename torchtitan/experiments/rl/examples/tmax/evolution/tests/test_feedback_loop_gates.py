"""The structural-rewrite gate on unseen verifier paths: what counts as
visible, what the probe is asked, and how a failure reaches the agent."""
from __future__ import annotations

import sys
import types

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_loop as fb


def _pkg(tmp_path, instruction, verifier, readme=None):
    work = tmp_path / "work"
    (work / "environment").mkdir(parents=True)
    (work / "environment/Dockerfile").write_text("FROM scratch\nWORKDIR /app\n")
    if readme is not None:
        (work / "environment/README.md").write_text(readme)
    # A rewrite one rung above SEED: the seed's solution plus four lines.
    task = {"instruction": instruction, "dockerfile": "FROM scratch\nWORKDIR /app\n",
            "solve_sh": "#!/bin/sh\ncd /app\nmake\nmake test\ncp out report.txt\n",
            "test_state_py": verifier}
    return work, task


SEED = {"instruction": "Write the report to /app/report.txt.",
        "dockerfile": "FROM scratch\nWORKDIR /app\n", "solve_sh": "#!/bin/sh\n",
        "test_state_py": 'assert open("/app/report.txt").read()\n'}


def test_new_dark_paths_names_only_what_nothing_visible_reveals(tmp_path) -> None:
    work, task = _pkg(
        tmp_path, "Complete the workflow described in /app/ops/README.md.",
        'assert open("/app/report.txt").read()\n'
        'assert open("/app/ops/audit.json").read()\n'
        'assert open("/app/ops/summary.csv").read()\n'
        'assert open("/usr/bin/curl")\n'
        'PATH = "/usr/local/bin:/usr/bin"\n'
        'import glob; glob.glob("/app/*.log")\n',
        readme="Leave the audit in /app/ops/audit.json.")
    dark = fb.new_dark_paths(work, task, SEED)
    # audit.json: documented in the README the image ships, so visible. The
    # PATH string and the glob are not paths. report.txt is flagged even
    # though the seed required it: the seed's instruction named it and the
    # rewrite's no longer does, which is the instruction dropping something.
    # /usr/bin/curl is left for the container check downstream to clear.
    assert dark == ["/app/ops/summary.csv", "/app/report.txt", "/usr/bin/curl"]


def test_new_dark_paths_ignores_what_the_seed_already_required_unseen(tmp_path) -> None:
    seed = {**SEED, "test_state_py": 'assert open("/app/hidden.txt").read()\n'}
    work, task = _pkg(tmp_path, "Do the thing.",
                      'assert open("/app/hidden.txt").read()\n')
    assert fb.new_dark_paths(work, task, seed) == []


def test_revalidate_sends_back_paths_the_untouched_container_lacks(tmp_path, monkeypatch) -> None:
    work, task = _pkg(tmp_path, "Do the thing.",
                      'assert open("/app/out.json").read()\nassert open("/usr/bin/curl")\n')
    calls = []

    def fake_probe(w, shortcut=None, resources=None, require_paths=None):
        calls.append((shortcut, list(require_paths or [])))
        if shortcut is None:
            return {"ok": True, "stage": "daytona_oracle", "reward": 1.0, "solve_exit": 0,
                    "paths_checked": require_paths,
                    "paths_missing": [p for p in require_paths if p == "/app/out.json"],
                    "measured": {"mem_peak_mb": 100}, "resources": {"cpu": 1}}
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["test_state_py"],
                      resources={"cpu": 1})

    assert calls[0] == (None, ["/app/out.json", "/usr/bin/curl"])
    assert v["ok"] is False and v["stage"] == "dark_paths"
    assert v["paths"] == ["/app/out.json"]
    assert "/app/out.json" in v["why"] and "/usr/bin/curl" not in v["why"]
    assert v["solve_exit"] == 0
    assert len(calls) == 1                      # no null probe after a rejection


def test_revalidate_passes_when_every_unseen_path_is_a_precondition(tmp_path, monkeypatch) -> None:
    work, task = _pkg(tmp_path, "Do the thing.", 'assert open("/usr/bin/curl")\n')

    def fake_probe(w, shortcut=None, resources=None, require_paths=None):
        if shortcut is None:
            return {"ok": True, "stage": "daytona_oracle", "reward": 1.0, "solve_exit": 0,
                    "paths_checked": require_paths, "paths_missing": [],
                    "measured": {"mem_peak_mb": 100}, "resources": {"cpu": 1}}
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["test_state_py"])
    assert v["ok"] is True and v["fast_path"] == "daytona_oracle"


def test_process_one_returns_a_dark_paths_verdict_to_the_agents_session(tmp_path, monkeypatch) -> None:
    src = tmp_path / "src"
    task = dict(SEED)
    for key, rel in fb.ev.file_map(task).items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(task[key])
    seen = {}
    verdicts = iter([
        {"ok": False, "stage": "dark_paths", "paths": ["/app/out.json"],
         "why": "The verifier requires paths ... /app/out.json ...", "solve_exit": 0},
        {"ok": True, "fast_path": "daytona_oracle", "reward": 1.0},
    ])

    def fake_evolve_agentic(agent_task, job, **kwargs):
        return {**agent_task, "instruction": "Write /app/out.json.\n",
                "test_state_py": 'assert open("/app/out.json").read()\n',
                "_codex_trace_dir": str(tmp_path / "trace"), "_extra_files": {}}

    def fake_resume_agentic(new, observed, exit_code=1):
        seen["observed"], seen["exit_code"] = observed, exit_code
        return {**new, "instruction": "Write the audit to /app/out.json.\n",
                "_extra_files": {}}

    fake_ec = types.SimpleNamespace(evolve_agentic=fake_evolve_agentic,
                                    resume_agentic=fake_resume_agentic,
                                    Blocked=type("Blocked", (Exception,), {}),
                                    Filtered=type("Filtered", (RuntimeError,), {}),
                                    CYBER_RETRIES=2)
    monkeypatch.setitem(sys.modules, "evolve_codex", fake_ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb.ev, "load", lambda _w: dict(task))
    monkeypatch.setattr(fb.ev, "history_from_pool", lambda _d: ({}, {}))
    monkeypatch.setattr(fb.llm, "operator_shortlist", lambda *_a: [("fam", "op", "def")])
    monkeypatch.setattr(fb, "revalidate", lambda *a, **k: next(verdicts))
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    rec = fb.process_one({"task_id": "t", "solved": 16, "graded": 16, "attempts": []},
                         src, tmp_path / "retuned")

    assert rec["status"] == "ok", rec
    assert rec["oracle_repair"]["ok"] is True
    assert seen["observed"].startswith("The verifier requires paths")
    assert seen["exit_code"] == 0


def test_revalidate_sends_back_names_the_task_never_states(tmp_path, monkeypatch) -> None:
    work, task = _pkg(tmp_path, "Write /app/report.json.",
                      'report = json.load(open("/app/report.json"))\n'
                      'assert report["source_sha256"]\nassert report["input_records"] == 3\n')

    def fake_probe(w, shortcut=None, resources=None, require_paths=None):
        if shortcut is None:
            return {"ok": True, "stage": "daytona_oracle", "reward": 1.0, "solve_exit": 0,
                    "paths_checked": require_paths, "paths_missing": [],
                    "measured": {"mem_peak_mb": 100}, "resources": {"cpu": 1}}
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    # The seed's verifier already read input_records unseen; only the new key counts.
    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["test_state_py"],
                      baseline=["input_records"])
    assert v["ok"] is False and v["stage"] == "dark_literals"
    assert v["literals"] == ["source_sha256"]
    assert "source_sha256" in v["why"]

    # An oracle failure carries the names along, so one repair round sees both.
    monkeypatch.setattr(fb, "daytona_probe", lambda *a, **k: {
        "ok": False, "stage": "daytona_oracle", "reward": 0.0, "solve_exit": 1, "tail": "boom"})
    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["test_state_py"],
                      baseline=["input_records"])
    assert v["stage"] == "daytona_oracle" and v["literals"] == ["source_sha256"]
    assert "Also:" in v["why"] and "source_sha256" in v["why"]


def test_seed_literals_come_from_the_pool_copy(tmp_path) -> None:
    src = tmp_path / "src"
    (src / "environment").mkdir(parents=True)
    (src / "environment" / "README.md").write_text("The report has input_records.\n")
    task = {**SEED, "test_state_py": 'assert report["input_records"]\nassert report["hidden_key"]\n',
            "_verifier_rel": "tests/test_state.py"}
    assert fb.seed_literals(task, src) == ["hidden_key"]


def test_revalidate_sends_back_a_rewrite_that_jumped_too_far(tmp_path, monkeypatch) -> None:
    work, task = _pkg(tmp_path, "Write the report to /app/report.txt.",
                      'assert open("/app/report.txt").read()\n')
    task["solve_sh"] = "\n".join(f"step {i}" for i in range(30)) + "\n"     # seed: 1 line

    def fake_probe(w, shortcut=None, resources=None, require_paths=None):
        if shortcut is None:
            return {"ok": True, "stage": "daytona_oracle", "reward": 1.0, "solve_exit": 0,
                    "paths_checked": require_paths, "paths_missing": [],
                    "measured": {"mem_peak_mb": 100}, "resources": {"cpu": 1}}
        return {"ok": True, "stage": "daytona_shortcut", "passed": False}

    monkeypatch.setattr(fb, "daytona_probe", fake_probe)
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["solve_sh"])
    assert v["ok"] is False and v["stage"] == "step_size"
    assert any("at most 8 more" in s for s in v["step"]) and "one rung" in v["why"]

    # One rung above the seed passes.
    task["solve_sh"] = SEED["solve_sh"] + "\n".join(f"step {i}" for i in range(5)) + "\n"
    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["solve_sh"])
    assert v["ok"] is True


def test_a_filtered_session_is_retried_fresh_then_gives_up(monkeypatch) -> None:
    calls = []
    rec: dict = {}

    class FakeEC:
        CYBER_RETRIES = 2
        Filtered = type("Filtered", (RuntimeError,), {})

        def evolve_agentic(self, agent_task, job, **kwargs):
            calls.append(job)
            if len(calls) < 3:
                raise self.Filtered("classifier stopped the session")
            return {"instruction": "harder", "_extra_files": {}}

    ec = FakeEC()
    out = fb._evolve_retrying_the_filter(ec, rec, "tw_x", {"instruction": "seed"}, [], [])
    assert out["instruction"] == "harder"
    assert len(calls) == 3                      # two retries, each a fresh session
    assert rec["cyber_filtered"] == 2

    # Filtered every time: the task is left alone rather than recorded as bad.
    class AlwaysFiltered(FakeEC):
        def evolve_agentic(self, agent_task, job, **kwargs):
            calls.append(job)
            raise self.Filtered("stopped again")

    calls.clear()
    with pytest.raises(AlwaysFiltered.Filtered):
        fb._evolve_retrying_the_filter(AlwaysFiltered(), rec, "tw_x", {}, [], [])
    assert len(calls) == 3
