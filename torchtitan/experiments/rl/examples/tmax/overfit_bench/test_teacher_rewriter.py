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


def _setup(job, monkeypatch, script, check=check_ok, original_share=0.0, original_share_passing=0.5):
    root, run, sched, client = job
    (root.path / "job.json").write_text(json.dumps({"task": "a"}))
    r0 = root.evolution.task("a").rev(0)
    for rel, text in [("instruction.md", "solve a"), ("environment/Dockerfile", "FROM ubuntu:24.04\n"),
                      ("tests/test.sh", "exit 0\n"), ("solution/solve.sh", "true\n")]:
        (r0 / rel).parent.mkdir(parents=True, exist_ok=True)
        (r0 / rel).write_text(text)
    t = tr.Teacher(root, client, _ev(), ask=script, check=check,
                   policy=tr.Policy(original_share=original_share, original_share_passing=original_share_passing))
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


def test_the_original_keeps_its_share_and_cannot_be_retired(job, monkeypatch):
    root, run, sched, client = job
    script = Script(new_version("v1", "x"), lambda w: {"retire": ["r0"], "weights": {"r0": 0.0, "r1": 3.0}})
    t = _setup(job, monkeypatch, script, original_share=0.25)
    _settle(t, lambda: len(_pool(sched)) == 2)
    assert abs(_pool(sched)[V0]["weight"] - 1.0 / 3) < 1e-6  # r1 at 1.0: r0 holds 25% of draws
    _attempts(run, sched, group=0, rewards=[0.0] * 12)
    _settle(t, lambda: len(script.prompts) == 2)
    pool = _pool(sched)
    assert pool[V0]["status"] == "active" and abs(pool[V0]["weight"] - 1.0) < 1e-6  # 3.0 / 3
    assert "retire r0: the original stays in the pool" in t.state["rounds"][1]["done"]
    assert "at least 25%\n  of the draw weight" in (t.work / "AGENTS.md").read_text()


def test_the_original_gets_more_of_the_draws_once_it_passes(job, monkeypatch):
    root, run, sched, client = job
    t = _setup(job, monkeypatch, Script(new_version("v1", "x")), original_share=0.25)
    _settle(t, lambda: len(_pool(sched)) == 2)
    assert abs(_pool(sched)[V0]["weight"] - 1.0 / 3) < 1e-6  # 25% beside r1 at 1.0
    _attempts(run, sched, group=0, rewards=[1.0] + [0.0] * 11)
    _settle(t, lambda: sum(map(len, t.groups.values())) == 1)
    assert abs(_pool(sched)[V0]["weight"] - 1.0) < 1e-6  # passed once: 50%


def test_the_original_share_is_a_floor_the_teacher_can_exceed(job, monkeypatch):
    root, run, sched, client = job
    script = Script(new_version("v1", "x"), lambda w: {"weights": {"r0": 9.0, "r1": 1.0}})
    t = _setup(job, monkeypatch, script, original_share=0.25)
    _settle(t, lambda: len(_pool(sched)) == 2)
    _attempts(run, sched, group=0, rewards=[0.0] * 12)
    _settle(t, lambda: len(script.prompts) == 2)
    assert abs(_pool(sched)[V0]["weight"] - 9.0) < 1e-6  # 90% of draws, above the 25% floor: kept
    t.refresh()
    t.hold_original()
    assert abs(_pool(sched)[V0]["weight"] - 9.0) < 1e-6  # and still after the pool is re-read


def test_the_passing_share_stays_after_later_failed_groups(job, monkeypatch):
    root, run, sched, client = job
    t = _setup(job, monkeypatch, Script(new_version("v1", "x")), original_share=0.25)
    _settle(t, lambda: len(_pool(sched)) == 2)
    _attempts(run, sched, group=0, rewards=[1.0] + [0.0] * 11)
    for g in (1, 2, 3):
        _attempts(run, sched, group=g, rewards=[0.0] * 12)
    _settle(t, lambda: sum(map(len, t.groups.values())) == 4)
    t.hold_original()
    assert abs(_pool(sched)[V0]["weight"] - 1.0) < 1e-6  # still 50%: the pass is 3 groups back


def test_a_retired_original_is_added_back(job, monkeypatch):
    root, run, sched, client = job
    sched.pool.retire(V0)
    t = _setup(job, monkeypatch, Script(), original_share=0.25)
    assert _pool(sched)[V0]["status"] == "active" and _pool(sched)[V0]["weight"] == 1.0


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


def _fake_cli(path, body):
    path.write_text("#!/bin/bash\ncat > /dev/null\n" + body)
    path.chmod(0o755)
    return path


def _codex_teacher(tmp_path, primary):
    ec = types.SimpleNamespace(
        ACCOUNT_HOME=None, CODEX_MODEL="m", CODEX_EFFORT="high",
        _codex_env=lambda home: {"PATH": "/usr/bin:/bin"},
        _codex_cmd=lambda cwd, model, resume=None: [str(primary)],
        _agent_failure_type=lambda stdout, stderr: "codex_exit_nonzero",
        _session_id=lambda home: f"sid-{home.path.name}")
    (tmp_path / "w").mkdir()
    return types.SimpleNamespace(dir=tmp_path / "t", work=tmp_path / "w", state={"rounds": [{}]},
                                 _save=lambda: None, policy=types.SimpleNamespace(round_timeout=60),
                                 ev=types.SimpleNamespace(ec=ec))


def test_a_refused_round_moves_to_the_fallback_thread_and_stays_there(tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    primary = _fake_cli(tmp_path / "primary", f"echo primary >> {calls}\n"
                        "echo \"I can't help with that: it was flagged by the cyber safety system.\"\n")
    fallback = _fake_cli(tmp_path / "fallback", f"echo \"fallback $*\" >> {calls}\necho '{{}}' > plan.json\n")
    monkeypatch.setenv("TEACHER_FALLBACK_MODEL", "fb-model")
    monkeypatch.setenv("TEACHER_FALLBACK_BIN", str(fallback))
    monkeypatch.setenv("TEACHER_FALLBACK_CONFIG", "model_provider=p")
    t = _codex_teacher(tmp_path, primary)
    tr.run_codex(t, "round one")
    assert (t.work / "plan.json").is_file()
    assert t.state["backend"] == "fallback" and t.state["fallback_thread"] == "sid-thread-fallback"
    assert "thread" not in t.state
    rd = t.dir / "rounds" / "001"
    assert "cyber" in (rd / "refused.txt").read_text() and (rd / "fallback-stdout-0.txt").is_file()
    first = calls.read_text().splitlines()
    assert first[0] == "primary" and "-m fb-model" in first[1] and "model_provider=p" in first[1]
    switches = [json.loads(line) for line in (t.dir / "backend_switches.jsonl").read_text().splitlines()]
    assert [(x["round"], x["to"]) for x in switches] == [("001", "fb-model")]
    # the next round goes straight to the fallback thread, resumed
    (t.work / "plan.json").unlink()
    t.state["rounds"].append({})
    tr.run_codex(t, "round two")
    later = calls.read_text().splitlines()[len(first):]
    assert len(later) == 1 and later[0].startswith("fallback exec resume sid-thread-fallback")


def test_without_a_fallback_a_round_without_a_plan_is_left_to_fail_as_before(tmp_path, monkeypatch):
    monkeypatch.delenv("TEACHER_FALLBACK_MODEL", raising=False)
    primary = _fake_cli(tmp_path / "primary", "echo \"I can't help with that.\"\n")
    t = _codex_teacher(tmp_path, primary)
    tr.run_codex(t, "round one")
    assert not (t.work / "plan.json").exists()
    assert "backend" not in t.state and t.state["thread"] == "sid-thread"


def test_an_add_that_timed_out_but_went_through_is_found_in_the_pool():
    from torchtitan.experiments.rl.examples.tmax.overfit_bench.rewriter_client import RewriterError

    calls = []

    class Client:
        def add(self, row, *, weight, parent, timeout_sec):
            calls.append(timeout_sec)
            raise RewriterError("no reply to add from the scheduler")

        def get_state(self):
            return {"versions": [{"id": "v-r0", "rev": 0}, {"id": "v-r7", "rev": 7}]}

    t = types.SimpleNamespace(client=Client())
    assert tr.Teacher._add(t, {"x": 1}, 1.0, "v-r0", 7) == {"id": "v-r7", "rev": 7}
    assert calls == [300]
    assert tr.Teacher._add(t, {"x": 1}, 1.0, "v-r0", 8) is None
    assert calls == [300, 300, 300]


def test_a_failed_attempt_carries_the_assertion_behind_the_cut_summary(tmp_path):
    tail = ("E   RuntimeError: The size of tensor a (16) must match the size of tensor b (64)\n"
            "FAILED tests/test_outputs.py::test_column[2] - RuntimeError: The size of tensor a...\n")
    rec = tmp_path / "r.jsonl"
    for reward, name in ((0.0, "fail"), (1.0, "pass")):
        rec.write_text(json.dumps({"reward": reward, "verifier": {"output_tail": tail}}) + "\n")
        head = json.loads(tr.compact_attempt(rec, "r0", 4000).splitlines()[0])
        assert head["tests"] == {"test_column[2]": "FAILED: RuntimeError: The size of tensor a..."}
        assert ("must match the size of tensor b (64)" in head.get("verifier_output", "")) == (name == "fail")
