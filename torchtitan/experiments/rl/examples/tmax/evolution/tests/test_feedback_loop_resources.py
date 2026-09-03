"""The box goes in with the task and the measured size comes out with the
rewrite: feedback_loop hands the agent the training-size container, sizes the
row from the reference solution's measured cost (never below the seed), and
revalidates at that size."""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_loop as fb


FLOOR = {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}


def test_provision_is_max_of_seed_and_measurement() -> None:
    new = {"_measured": {"mem_peak_mb": 2500, "df_used_mb": 500, "cpu_seconds": 100},
           "_box": {"cpu": 1, "mem_gb": 2, "disk_gb": 2}, "_at_max": False}
    p = fb.provision(new, FLOOR)
    # memory measured above the seed rises (2500 MB * 1.3 -> 4 GiB); cpu and
    # disk measured below it stay at the seed's size.
    assert (p["cpu"], p["mem_gb"], p["disk_gb"]) == (1, 4, 2)
    assert p["source"] == "measured"
    assert p["sized"] == {"cpu": 1, "mem_gb": 4, "disk_gb": 2}
    assert p["floor"] == {"cpu": 1, "mem_gb": 2, "disk_gb": 2}
    assert p["box"] == new["_box"] and p["at_max"] is False


def test_provision_without_a_measurement_keeps_the_seed_size() -> None:
    p = fb.provision({}, FLOOR)
    assert (p["cpu"], p["mem_gb"], p["disk_gb"]) == (1, 2, 2)
    assert p["source"] == "inherited" and p["sized"] is None


def test_provision_with_neither_is_nothing_to_write() -> None:
    assert fb.provision({}, None) is None
    assert fb.provision({}, {"cpu": None, "mem_gb": None, "disk_gb": None}) is None


def test_provision_sizes_from_measurement_alone_when_the_row_declared_nothing() -> None:
    p = fb.provision({"_measured": {"mem_peak_mb": 100, "df_used_mb": 100,
                                    "cpu_seconds": 2000}}, None)
    assert (p["cpu"], p["mem_gb"], p["disk_gb"]) == (3, 2, 2)


def test_daytona_probe_runs_in_the_box_it_is_given(tmp_path, monkeypatch) -> None:
    venv = tmp_path / "python"
    env = tmp_path / "env"
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

    v = fb.daytona_probe(tmp_path, resources={"cpu": 1, "mem_gb": 2, "disk_gb": 2,
                                              "source": "row"})
    assert v["ok"]
    cmd = seen["cmd"]
    assert cmd[cmd.index("--cpu") + 1] == "1"
    assert cmd[cmd.index("--mem-gb") + 1] == "2"
    assert cmd[cmd.index("--disk-gb") + 1] == "2"
    assert "--shortcut" not in cmd

    # A key left None is not passed: the harness default applies, as in training.
    fb.daytona_probe(tmp_path, shortcut=":", resources={"cpu": None, "mem_gb": 4,
                                                        "disk_gb": None})
    cmd = seen["cmd"]
    assert "--cpu" not in cmd and "--disk-gb" not in cmd
    assert cmd[cmd.index("--mem-gb") + 1] == "4"
    assert cmd[cmd.index("--shortcut") + 1] == ":"


def test_process_one_hands_the_box_in_and_the_size_out(tmp_path, monkeypatch) -> None:
    src = tmp_path / "src"
    task = {"instruction": "do the thing\n", "dockerfile": "FROM scratch\n",
            "solve_sh": "#!/bin/sh\n", "test_state_py": "assert True\n"}
    for key, rel in fb.ev.file_map(task).items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(task[key])
    out_root = tmp_path / "retuned"
    seen = {}

    def fake_evolve_agentic(agent_task, job, **kwargs):
        seen["agent_task"] = agent_task
        return {**agent_task, "instruction": "do the harder thing\n",
                "_measured": {"mem_peak_mb": 3000, "df_used_mb": 800, "cpu_seconds": 50},
                "_box": {"cpu": 1, "mem_gb": 2, "disk_gb": 2}, "_at_max": False,
                "_agent_validated": True, "_extra_files": {}}

    def fake_revalidate(work, image, tid, new, orig=None, changed=None, resources=None):
        seen["revalidate_resources"] = resources
        return {"ok": True, "fast_path": "daytona_oracle", "reward": 1.0}

    fake_ec = types.SimpleNamespace(evolve_agentic=fake_evolve_agentic,
                                    Blocked=type("Blocked", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "evolve_codex", fake_ec)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    monkeypatch.setattr(fb.ev, "load", lambda _work: dict(task))
    monkeypatch.setattr(fb.ev, "history_from_pool", lambda _dirs: ({}, {}))
    monkeypatch.setattr(fb.llm, "operator_shortlist",
                        lambda _task, _uo, _uf: [("fam", "op", "definition")])
    monkeypatch.setattr(fb, "revalidate", fake_revalidate)
    monkeypatch.setattr(fb.shutil, "which", lambda _name: None)

    rec = fb.process_one({"task_id": "task-a", "solved": 16, "graded": 16,
                          "attempts": []}, src, out_root, resources=FLOOR)

    assert rec["status"] == "ok", rec
    assert seen["agent_task"]["_resources"] == FLOOR
    size = seen["revalidate_resources"]
    # 3000 MB * 1.3 -> 4 GiB above the seed's 2; cpu and disk stay at the seed.
    assert (size["cpu"], size["mem_gb"], size["disk_gb"]) == (1, 4, 2)
    assert size["source"] == "measured"
    assert rec["resources"] == size
    on_disk = json.loads((out_root / "task-a" / ".resources.json").read_text())
    assert (on_disk["cpu"], on_disk["mem_gb"], on_disk["disk_gb"]) == (1, 4, 2)
    assert on_disk["measured"]["mem_peak_mb"] == 3000
