# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The teacher rewriter against a real scheduler, with a scripted teacher and scripted Daytona checks."""

from __future__ import annotations

import json
import shutil
import types

from torchtitan.experiments.rl.examples.tmax import layout
from torchtitan.experiments.rl.examples.tmax.overfit_bench import events
from torchtitan.experiments.rl.examples.tmax.overfit_bench import teacher_rewriter as tr
from torchtitan.experiments.rl.examples.tmax.overfit_bench.test_ladder_rewriter import (  # noqa: F401 -- fixture
    _pool,
    _row,
    _until,
    job,
    RUN,
    V0,
)


def _ev():
    def candidate_row(package, input_row, meta):
        row = json.loads(json.dumps(input_row))
        row["metadata"]["tmax"]["test_sh"] = "exit 0 # " + (package / "instruction.md").read_text()[:40]
        return row
    return types.SimpleNamespace(candidate_row=candidate_row, _ensure_r0=lambda root, task, tid: task.rev(0))


class Script:
    """Each round runs the next function on the teacher's working directory; records the prompts."""

    def __init__(self, *rounds):
        self.rounds, self.prompts = list(rounds), []

    def __call__(self, teacher, prompt):
        self.prompts.append(prompt)
        plan = self.rounds.pop(0)(teacher.work) if self.rounds else {}
        (teacher.work / "plan.json").write_text(json.dumps(plan))


def new_version(name, text, **plan):
    def write(work):
        d = work / "new" / name
        shutil.copytree(work / "problems" / "r0", d, ignore=shutil.ignore_patterns("status.json", "diff.patch"))
        (d / "instruction.md").write_text(text)
        return {"new": [{"dir": f"new/{name}", "weight": 1.0, "predict": "3 of 12", "why": "w"}], **plan}
    return write


def check_ok(package, *, shortcut, timeout, sandbox):
    return {"reward": 0.0 if shortcut else 1.0}


def _setup(job, monkeypatch, script, check=check_ok):
    root, run, sched, client = job
    (root.path / "job.json").write_text(json.dumps({"task": "a"}))
    r0 = root.evolution.task("a").rev(0)
    for rel, text in [("instruction.md", "solve a"), ("environment/Dockerfile", "FROM ubuntu:24.04\n"),
                      ("tests/test.sh", "exit 0\n"), ("solution/solve.sh", "true\n")]:
        (r0 / rel).parent.mkdir(parents=True, exist_ok=True)
        (r0 / rel).write_text(text)
    t = tr.Teacher(root, client, _ev(), ask=script, check=check)
    t.start()
    return t


def _attempts(run, sched, *, group, rewards, version=V0, rev=0):
    for i, r in enumerate(rewards):
        rel = f"rollouts/a/g{group}-r{i}.jsonl"
        (run.path / rel).parent.mkdir(parents=True, exist_ok=True)
        tail = "PASSED t.py::test_x\nFAILED t.py::test_y - AssertionError: no\n1 failed, 1 passed"
        head = {"rev": rev, "group": group, "rollout": i, "reward": r, "finish_reason": "submit", "turns": 3,
                "verifier": {"output_tail": tail}}
        (run.path / rel).write_text(json.dumps(head) + "\n" + json.dumps(
            {"turn": 1, "keystrokes": ["ls\n"], "output": "x" * 10000, "think": "hm"}) + "\n")
        sched.submit(events.make(events.ROLLOUT_DONE, version=version, task="a", rev=rev, group=group, idx=i,
                                 reward=r, finish_reason="submit", record_path=rel, policy_versions=[0, 0]))
    sched.submit(events.make(events.GROUP_DONE, version=version, task="a", rev=rev, group=group,
                             passed=int(sum(rewards)), scored=len(rewards), policy_version=0))


def _settle(t, cond=lambda: True):
    assert _until(lambda: (t.step(0.05), cond() and not t._futs and not t.pending)[1])


def test_the_first_round_writes_a_version_that_joins_the_pool_and_is_drawn_first(job, monkeypatch):
    root, run, sched, client = job
    script = Script(new_version("halfway", "solve a, half given", draw_first=["halfway"]))
    t = _setup(job, monkeypatch, script)
    _settle(t, lambda: len(_pool(sched)) == 2)
    added = [v for v in _pool(sched).values() if v["rev"] == 1]
    assert len(added) == 1 and added[0]["status"] == "active"
    assert "training has started" in script.prompts[0]
    assert t.which_version(list(_pool(sched).values())) == added[0]["id"]
    assert t.which_version(list(_pool(sched).values())) is None  # each once
    assert (t.work / "AGENTS.md").read_text().startswith("# You are the teacher")
    assert "12 times" in (t.work / "AGENTS.md").read_text()
    assert "GB of disk" in (t.work / "AGENTS.md").read_text()


def test_a_group_near_all_failed_calls_the_teacher_and_a_mixed_group_does_not(job, monkeypatch):
    root, run, sched, client = job
    script = Script(lambda w: {}, lambda w: {"weights": {"r0": 0.5}})
    t = _setup(job, monkeypatch, script)
    _settle(t)
    _attempts(run, sched, group=0, rewards=[1.0] * 3 + [0.0] * 9)
    _settle(t, lambda: sum(map(len, t.groups.values())) == 1)
    assert len(script.prompts) == 1  # mixed: no round
    _attempts(run, sched, group=1, rewards=[1.0] + [0.0] * 11)
    _settle(t, lambda: sum(map(len, t.groups.values())) == 2)
    assert len(script.prompts) == 2 and "g1 ended 1/12" in script.prompts[1]
    assert _pool(sched)[V0]["weight"] == 0.5


def test_the_directory_holds_every_version_and_compact_attempts(job, monkeypatch):
    root, run, sched, client = job
    script = Script(new_version("v1", "solve a gently"), lambda w: {})
    t = _setup(job, monkeypatch, script)
    _settle(t, lambda: len(_pool(sched)) == 2)
    _attempts(run, sched, group=0, rewards=[0.0] * 12)
    _settle(t, lambda: len(script.prompts) == 2)
    w = t.work
    assert (w / "problems" / "r0" / "instruction.md").read_text() == "solve a"
    assert "solve a gently" in (w / "problems" / "r1" / "diff.patch").read_text()
    status = json.loads((w / "problems" / "r1" / "status.json").read_text())
    assert status["made_in_round"] == 1 and status["your_prediction"] == "3 of 12" and status["from"] == "v1"
    first, turn = (w / "attempts" / "r0" / "g0" / "01.jsonl").read_text().splitlines()
    assert json.loads(first)["tests"] == {"test_x": "PASSED", "test_y": "FAILED: AssertionError: no"}
    assert "characters cut" in json.loads(turn)["output"] and json.loads(turn)["keystrokes"] == "ls\n"
    assert "test_y 0" in (w / "summary.md").read_text()
    hist = (w / "history.md").read_text()
    assert "`v1` -> r1: you predicted 3 of 12" in hist


def test_a_version_that_fails_its_checks_stays_out_and_the_teacher_hears_why(job, monkeypatch):
    root, run, sched, client = job

    def check(package, *, shortcut, timeout, sandbox):
        assert set(sandbox) == set(tr.SANDBOX.values())  # sized as the original's row sizes training
        return {"reward": 1.0}  # the untouched state passes too

    script = Script(new_version("free", "solve a, all given"), lambda w: {})
    t = _setup(job, monkeypatch, script, check=check)
    _settle(t, lambda: len(script.prompts) == 2)
    assert len(_pool(sched)) == 1
    assert "failed its checks" in script.prompts[1]
    assert "untouched starting state did not fail" in json.dumps(t.state["rounds"][0]["made"])


def test_a_malformed_plan_is_refused_and_reported(job, monkeypatch):
    root, run, sched, client = job
    script = Script(lambda w: {"new": [{"dir": "../escape"}]}, lambda w: {})
    t = _setup(job, monkeypatch, script)
    _settle(t, lambda: len(script.prompts) == 2)
    assert t.state["rounds"][0]["status"] == "refused"
    assert "was not carried out" in script.prompts[1]


def test_retire_and_weights_follow_the_plan(job, monkeypatch):
    root, run, sched, client = job
    script = Script(new_version("v1", "x"), lambda w: {"retire": ["r1"], "weights": {"r0": 2.0, "r9": 1.0}})
    t = _setup(job, monkeypatch, script)
    _settle(t, lambda: len(_pool(sched)) == 2)
    _attempts(run, sched, group=0, rewards=[0.0] * 12)
    _settle(t, lambda: len(script.prompts) == 2)
    pool = _pool(sched)
    assert [v["status"] for v in pool.values() if v["rev"] == 1] == ["retired"] and pool[V0]["weight"] == 2.0
    assert "weight r9: not an active version" in t.state["rounds"][1]["done"]


def test_a_resumed_run_numbers_its_groups_after_the_earlier_runs(job, monkeypatch):
    t = _setup(job, monkeypatch, Script(lambda w: {}))
    _settle(t)
    for run, group in [("a", 0), ("a", 3), ("b", 0), ("b", 1)]:
        t.on_event({"event": "group_done", "run": run, "version": V0, "group": group, "passed": 0, "scored": 12})
    assert [g["group"] for g in t.groups[V0]] == [0, 3, 4, 5]


def test_a_changed_agents_md_is_announced_in_the_next_round(job, monkeypatch):
    root, run, sched, client = job
    script = Script(lambda w: {}, lambda w: {})
    t = _setup(job, monkeypatch, script)
    _settle(t)
    monkeypatch.setattr(tr, "agents_md", lambda policy, sandbox: "# changed")
    _attempts(run, sched, group=0, rewards=[0.0] * 12)
    _settle(t, lambda: len(script.prompts) == 2)
    assert "AGENTS.md has changed" in script.prompts[1] and "AGENTS.md has changed" not in script.prompts[0]
