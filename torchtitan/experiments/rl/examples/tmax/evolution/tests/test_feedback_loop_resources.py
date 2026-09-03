"""The box goes in with the task and the measured size comes out with the
rewrite: feedback_loop hands the agent the training-size container, runs its
own probe in the box the agent's check measured, and provisions the row from
the probe's own counters (never below the seed)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedback_loop as fb


FLOOR = {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}
BOX = {"cpu": 1, "mem_gb": 2, "disk_gb": 2}


def test_provision_is_max_of_seed_and_measurement() -> None:
    m = {"mem_peak_mb": 2500, "df_used_mb": 500, "cpu_seconds": 100}
    p = fb.provision(m, FLOOR, box=BOX, by="agent_check")
    # memory measured above the seed rises (2500 MB * 1.3 -> 4 GiB); cpu and
    # disk measured below it stay at the seed's size.
    assert (p["cpu"], p["mem_gb"], p["disk_gb"]) == (1, 4, 2)
    assert p["source"] == "measured:agent_check"
    assert p["sized"] == {"cpu": 1, "mem_gb": 4, "disk_gb": 2}
    assert p["floor"] == {"cpu": 1, "mem_gb": 2, "disk_gb": 2}
    assert p["box"] == BOX and p["at_max"] is False


def test_provision_without_a_measurement_keeps_the_seed_size() -> None:
    p = fb.provision(None, FLOOR)
    assert (p["cpu"], p["mem_gb"], p["disk_gb"]) == (1, 2, 2)
    assert p["source"] == "inherited" and p["sized"] is None


def test_provision_with_neither_is_nothing_to_write() -> None:
    assert fb.provision(None, None) is None
    assert fb.provision({}, {"cpu": None, "mem_gb": None, "disk_gb": None}) is None


def test_provision_sizes_from_measurement_alone_when_the_row_declared_nothing() -> None:
    p = fb.provision({"mem_peak_mb": 100, "df_used_mb": 100, "cpu_seconds": 2000}, None,
                     by="loop_probe")
    assert (p["cpu"], p["mem_gb"], p["disk_gb"]) == (3, 2, 2)
    assert p["source"] == "measured:loop_probe"


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


def test_process_one_probes_in_the_agents_box_and_sizes_from_its_own_reading(
        tmp_path, monkeypatch) -> None:
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
        # The agent's own check said 3000 MB: enough to raise the box to 4 GiB.
        return {**agent_task, "instruction": "do the harder thing\n",
                "_measured": {"mem_peak_mb": 3000, "df_used_mb": 800, "cpu_seconds": 50},
                "_box": BOX, "_at_max": False,
                "_agent_validated": True, "_extra_files": {}}

    def fake_revalidate(work, image, tid, new, orig=None, changed=None, resources=None):
        seen["probe_box"] = resources
        # The loop's own probe, in that 4 GiB box, read 1200 MB.
        return {"ok": True, "fast_path": "daytona_oracle", "reward": 1.0,
                "measured": {"mem_peak_mb": 1200, "df_used_mb": 800, "cpu_seconds": 50},
                "resources": {k: resources[k] for k in ("cpu", "mem_gb", "disk_gb")}}

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
    # The agent's reading picked the probe's box: 3000 MB * 1.3 -> 4 GiB.
    box = seen["probe_box"]
    assert (box["cpu"], box["mem_gb"], box["disk_gb"]) == (1, 4, 2)
    assert box["source"] == "measured:agent_check"
    # The row is sized from the probe's reading, not the agent's: 1200 MB * 1.3
    # -> 2 GiB, which is the seed's size.
    size = rec["resources"]
    assert (size["cpu"], size["mem_gb"], size["disk_gb"]) == (1, 2, 2)
    assert size["source"] == "measured:loop_probe"
    assert size["box"] == {"cpu": 1, "mem_gb": 4, "disk_gb": 2}
    on_disk = json.loads((out_root / "task-a" / ".resources.json").read_text())
    assert (on_disk["cpu"], on_disk["mem_gb"], on_disk["disk_gb"]) == (1, 2, 2)
    assert on_disk["measured"]["mem_peak_mb"] == 1200
