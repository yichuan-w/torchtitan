"""The pin hook reaches the loop's own probe: a rewrite directory that carries
the seed row's hook (pretest.json, written by the loop beside rewrite.json)
hands it to daytona_revalidate, so the reference solution is graded the way
training grades it -- pins first, verifier second."""
from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_loop as fb
from torchtitan.experiments.rl.examples.tmax import layout

HOOK = "set -u\nexit 0\n"
STAMP = "image:hamishi740/swerl-tmax-v3:37a79d0fd9b9"
FLOOR = {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}
SIGNAL = {"task": "task-a", "rev": 0, "run": "r", "group": 1, "direction": "harder",
          "solved": 16, "total": 16, "attempts": []}


def _probe_env(tmp_path, monkeypatch) -> dict:
    venv, env = tmp_path / "python", tmp_path / "env"
    venv.write_text("")
    env.write_text("")
    monkeypatch.setattr(fb, "DAYTONA_VENV_PY", str(venv))
    monkeypatch.setattr(fb, "DAYTONA_ENV_FILE", str(env))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return types.SimpleNamespace(stdout='{"ok": true, "stage": "daytona_oracle"}\n',
                                     stderr="", returncode=0)

    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    return seen


def test_daytona_probe_passes_the_hook_file_through(tmp_path, monkeypatch) -> None:
    seen = _probe_env(tmp_path, monkeypatch)
    hook = tmp_path / "pretest.json"
    hook.write_text("{}")

    fb.daytona_probe(tmp_path, pretest_file=hook)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--pretest-file") + 1] == str(hook)

    # The null probe grades the untouched workspace under the same hook.
    fb.daytona_probe(tmp_path, shortcut=":", pretest_file=hook)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--pretest-file") + 1] == str(hook)
    assert cmd[cmd.index("--shortcut") + 1] == ":"

    fb.daytona_probe(tmp_path)
    assert "--pretest-file" not in seen["cmd"]


def _rewrite(tmp_path, monkeypatch) -> tuple[layout.RewriteDir, Path]:
    root = layout.Root(tmp_path / "root")
    monkeypatch.setenv("TRL_BASE", str(root.path))
    task = {"instruction": "do the thing\n", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\n", "test_state_py": "assert True\n"}
    r0 = root.evolution.task("task-a").rev(0)
    for key, rel in fb.ev.file_map(task).items():
        (r0 / rel).parent.mkdir(parents=True, exist_ok=True)
        (r0 / rel).write_text(task[key])
    rw = root.evolution.task("task-a").rewrite("harder")
    rw.path.mkdir(parents=True)
    shutil.copytree(r0, rw.package)
    return rw, r0


def _agentic(monkeypatch) -> dict:
    seen = {}

    def fake_evolve_agentic(rewrite, agent_task, job, **kwargs):
        seen["agent_task"] = agent_task
        return {**agent_task, "instruction": "do the harder thing\n",
                "_agent_validated": True, "_support_changed": [],
                "_operator": "op", "_family": "fam"}

    def fake_revalidate(work, image, tid, new, orig=None, changed=None, resources=None,
                        baseline=None, pretest_file=None):
        seen["pretest_file"] = pretest_file
        return {"ok": True, "fast_path": "daytona_oracle", "reward": 1.0}

    fake_ec = types.SimpleNamespace(evolve_agentic=fake_evolve_agentic,
                                    Blocked=type("Blocked", (Exception,), {}),
                                    Filtered=type("Filtered", (RuntimeError,), {}),
                                    CYBER_RETRIES=2)
    monkeypatch.setitem(sys.modules, "evolve_codex", fake_ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb.llm, "operator_shortlist",
                        lambda _task, _uo, _uf: [("fam", "op", "definition")])
    monkeypatch.setattr(fb, "revalidate", fake_revalidate)
    monkeypatch.setattr(fb.shutil, "which", lambda _name: None)
    return seen


def test_process_one_hands_the_rewrites_hook_to_the_agent_and_the_probe(
        tmp_path, monkeypatch) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    layout.write_pretest(rw.pretest, HOOK, STAMP)
    seen = _agentic(monkeypatch)

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0, resources=FLOOR)

    assert rec["status"] == "accepted", rec
    assert seen["agent_task"]["_pretest"] == (HOOK, STAMP)
    assert seen["pretest_file"] == rw.pretest


def test_process_one_without_a_hook_probes_as_before(tmp_path, monkeypatch) -> None:
    rw, r0 = _rewrite(tmp_path, monkeypatch)
    seen = _agentic(monkeypatch)

    rec = fb.process_one(rw, SIGNAL, job="harder", seed_dir=r0, resources=FLOOR)

    assert rec["status"] == "accepted", rec
    assert seen["agent_task"]["_pretest"] is None
    assert seen["pretest_file"] is None
