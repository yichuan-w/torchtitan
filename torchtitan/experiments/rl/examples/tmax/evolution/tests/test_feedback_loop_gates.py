"""The structural-rewrite gate on unseen verifier paths: what counts as
visible, what the probe is asked, and how a failure reaches the agent; and
process_one's verdicts over one rewrite directory."""
from __future__ import annotations

import shutil
import sys
import types

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_loop as fb
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record


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
SIGNAL = {"task": "t", "rev": 0, "run": "r", "group": 1, "direction": "harder",
          "solved": 16, "total": 16, "attempts": []}


def _rewrite(tmp_path, monkeypatch, seed: dict = SEED) -> tuple[layout.RewriteDir, Path]:
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
        CYBER_RETRIES=2, **overrides)


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


def test_revalidate_records_paths_the_untouched_container_lacks(tmp_path, monkeypatch) -> None:
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
    # Advice, not a verdict: the rewrite passes and the missing path rides
    # along in the record for whoever reads it.
    assert v["ok"] is True and v["fast_path"] == "daytona_oracle"
    assert v["advice"]["dark_paths"] == ["/app/out.json"]
    assert len(calls) == 2                      # the null probe still runs
    assert fb.verdicts_of(v) == {"oracle": "pass", "dark_paths": ["/app/out.json"],
                                 "dark_literals": [], "step": []}


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


def test_process_one_returns_a_step_size_verdict_to_the_agents_session(tmp_path, monkeypatch) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    seen = {}
    verdicts = iter([
        {"ok": False, "stage": "step_size", "step": ["the reference solution has 40 lines"],
         "why": "The rewrite is more than one rung above the seed: ...", "solve_exit": 0},
        {"ok": True, "fast_path": "daytona_oracle", "reward": 1.0},
    ])

    def fake_evolve_agentic(rewrite, agent_task, job, **kwargs):
        seen["rewrite"], seen["job"] = rewrite, job
        seen["shortlist"] = kwargs.get("operator")
        (rewrite.package / "instruction.md").write_text("Write /app/out.json.\n")
        return {**agent_task, "instruction": "Write /app/out.json.\n",
                "test_state_py": 'assert open("/app/out.json").read()\n',
                "_session": str(rewrite.session("agent", "20260904-000000Z").path),
                "_support_changed": [], "_operator": "op", "_family": "fam"}

    def fake_resume_agentic(rewrite, new, observed, exit_code=1):
        seen["observed"], seen["exit_code"] = observed, exit_code
        return {**new, "instruction": "Write the audit to /app/out.json.\n"}

    monkeypatch.setitem(sys.modules, "evolve_codex", _fake_ec(
        evolve_agentic=fake_evolve_agentic, resume_agentic=fake_resume_agentic))
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb.llm, "operator_shortlist", lambda *_a: [("fam", "op", "def")])
    monkeypatch.setattr(fb, "revalidate", lambda *a, **k: next(verdicts))
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0,
                         history=({"op": 3}, {"fam": 3}))

    assert rec["status"] == "accepted", rec
    assert rec["oracle_repair"]["ok"] is True
    assert seen["rewrite"] is rw and seen["job"] == "harder"
    assert seen["observed"].startswith("The rewrite is more than one rung")
    assert seen["exit_code"] == 0
    assert rec["operator"] == "op" and rec["arm"] == "codex" and rec["job"] == "harder"
    assert rec["verdicts"]["oracle"] == "pass" and rec["changed"] == ["instruction", "test_state_py"]
    # The repair's files are on disk in the package.
    assert (rw.package / "instruction.md").read_text() == "Write the audit to /app/out.json.\n"
    assert "usage" in rec and rec["t_end"] >= rec["t_start"]


def test_process_one_rejects_on_the_verdict_and_says_which_stage(tmp_path, monkeypatch) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)

    def fake_evolve_agentic(rewrite, agent_task, job, **kwargs):
        return {**agent_task, "instruction": "harder\n", "_support_changed": [],
                "_operator": "op", "_family": "fam"}

    monkeypatch.setitem(sys.modules, "evolve_codex", _fake_ec(evolve_agentic=fake_evolve_agentic))
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb.llm, "operator_shortlist", lambda *_a: [("fam", "op", "def")])
    monkeypatch.setattr(fb, "revalidate", lambda *a, **k: {
        "ok": False, "stage": "null_pass", "why": "verifier passes on the untouched workspace"})
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)

    assert rec["status"] == "rejected" and rec["stage"] == "null_pass"
    assert rec["reason"].startswith("verifier passes")
    assert rec["verdicts"]["oracle"] == "fail"


def test_process_one_keeps_when_the_agent_declines_and_blocks_when_no_axis_fits(tmp_path, monkeypatch) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    ec = _fake_ec()

    def declines(rewrite, agent_task, job, **kwargs):
        raise ec.Blocked("GIVE UP: operator-misfit — the seed has one step")

    ec.evolve_agentic = declines
    monkeypatch.setitem(sys.modules, "evolve_codex", ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb.llm, "operator_shortlist", lambda *_a: [("fam", "op", "def")])
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)
    assert rec["status"] == "kept" and "operator-misfit" in rec["reason"]

    def no_axis(*_a):
        raise fb.llm.Blocked("no operator fits")

    monkeypatch.setattr(fb.llm, "operator_shortlist", no_axis)
    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0)
    assert rec["status"] == "blocked" and rec["stage"] == "operator"


def test_process_one_easier_reads_the_records_for_the_chat_arm(tmp_path, monkeypatch) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    rollout_record.write_record(
        rw.traces / "attempt-01.jsonl",
        {"task": "t", "rev": 0, "run": "r", "group": 1, "rollout": 0, "reward": 0.0, "turns": 2},
        [{"turn": 1, "keystrokes": ["cat /app/missing\n"], "output": "No such file"},
         {"turn": 2, "keystrokes": [], "task_complete": True, "output": ""}])
    seen = {}

    def fake_simplify(task, solved, attempts, trajectory, hint):
        seen.update(solved=solved, attempts=attempts, trajectory=trajectory, hint=hint)
        return {**task, "instruction": task["instruction"] + "Look in /app.\n", "_hint": hint}

    monkeypatch.setenv("SWE_RETUNE_AGENT", "chat")
    monkeypatch.setattr(fb.ev, "simplify", fake_simplify)
    monkeypatch.setattr(fb.shutil, "which", lambda _n: None)

    rec = fb.process_one(rw, {**SIGNAL, "direction": "easier", "solved": 0},
                         job="easier", seed_dir=r0)

    assert rec["status"] == "accepted" and rec["stage"] == "instruction_only", rec
    assert rec["changed"] == ["instruction"] and rec["hint"] == "vague"
    assert (seen["solved"], seen["attempts"]) == (0, 16)
    assert "$ cat /app/missing" in seen["trajectory"] and "No such file" in seen["trajectory"]
    assert (rw.package / "instruction.md").read_text().endswith("Look in /app.\n")
    assert rec["verdicts"]["oracle"] == "skipped"


def test_format_trace_prefers_failures() -> None:
    records = [
        ({"reward": 1.0, "turns": 1}, [{"turn": 1, "keystrokes": ["ok\n"], "output": "fine"}]),
        ({"reward": 0.0, "turns": 1}, [{"turn": 1, "raw": "no response", "output": "x" * 700}]),
    ]
    text = fb.format_trace(records)
    assert text.startswith("--- attempt reward=0.0")
    assert "$ no response" in text and "ok" not in text.split("\n")[1]
    assert len(text) < 700 + 200                # the chat prompt trims the output


def test_verdicts_of_maps_the_revalidation_stages() -> None:
    assert fb.verdicts_of(None)["oracle"] is None
    assert fb.verdicts_of({"ok": True, "fast_path": "instruction_only"})["oracle"] == "skipped"
    assert fb.verdicts_of({"ok": False, "stage": "step_size", "step": ["s"]}) == {
        "oracle": "pass", "dark_paths": [], "dark_literals": [], "step": ["s"]}
    assert fb.verdicts_of({"ok": False, "stage": "daytona_oracle", "literals": ["k"]}) == {
        "oracle": "fail", "dark_paths": [], "dark_literals": ["k"], "step": []}
    assert fb.verdicts_of({"ok": False, "stage": "daytona_error"})["oracle"] == "error"


def test_revalidate_records_names_the_task_never_states(tmp_path, monkeypatch) -> None:
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
    assert v["ok"] is True
    assert v["advice"]["dark_literals"] == ["source_sha256"]

    # An oracle failure carries the names along, so one repair round sees both.
    monkeypatch.setattr(fb, "daytona_probe", lambda *a, **k: {
        "ok": False, "stage": "daytona_oracle", "reward": 0.0, "solve_exit": 1, "tail": "boom"})
    v = fb.revalidate(work, "img", "tid", task, orig=SEED, changed=["test_state_py"],
                      baseline=["input_records"])
    assert v["stage"] == "daytona_oracle" and v["literals"] == ["source_sha256"]
    assert "Also:" in v["why"] and "source_sha256" in v["why"]


def test_seed_literals_come_from_the_input_revision(tmp_path) -> None:
    src = tmp_path / "r0"
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


def test_a_filtered_session_is_retried_fresh_then_gives_up(tmp_path, monkeypatch) -> None:
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
    out = fb._evolve_retrying_the_filter(ec, rec, "tw_x", rw, {"instruction": "seed"}, [])
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
        fb._evolve_retrying_the_filter(AlwaysFiltered(), rec, "tw_x", rw, {}, [])
    assert len(calls) == 3
