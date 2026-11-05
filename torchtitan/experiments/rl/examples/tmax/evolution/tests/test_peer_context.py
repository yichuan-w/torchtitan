# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_PEER_CONTEXT: other in-band tasks staged as a student-ability
reference for the rewrite author, off by default."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import peer_context as pc
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

RUN = "run--20261105-000000Z"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("EVOLVE_PEER_CONTEXT", "EVOLVE_HARDER_TARGET"):
        monkeypatch.delenv(name, raising=False)
    pc._cache.clear()


def _group(root, task, group, solved, total=12, rev=0):
    run = root.run(RUN)
    for i in range(total):
        reward = 1.0 if i < solved else 0.0
        # Under rollouts/ whatever the sign: a bench set frozen from an eval
        # keeps its negative (validation) ids there.
        rollout_record.write_record(
            run.rollouts / layout.safe(task) / f"g{group}-r{i}.jsonl",
            {"task": task, "rev": rev, "run": RUN, "group": group, "rollout": i,
             "reward": reward, "agent": "terminus"},
            [{"turn": 1, "keystrokes": [f"echo {task}-{i}\n"], "output": "SECRET OUTPUT"},
             {"turn": 2, "keystrokes": [], "task_complete": True, "output": ""}],
        )


def _instruction(root, task, rev=0):
    d = root.evolution.task(task).rev(rev)
    d.mkdir(parents=True, exist_ok=True)
    (d / "instruction.md").write_text(f"Do {task}.\n")


def test_off_by_default():
    assert not pc.enabled()


def test_stage_picks_newest_in_band_groups_and_skips_the_current_task(tmp_path):
    root = layout.Root(tmp_path)
    _group(root, "easy", 50, 12)     # all solved: out of band
    _group(root, "hard", 49, 0)      # none solved: out of band
    _group(root, "self", 48, 5)      # in band, but it is the task being rewritten
    _group(root, "p1", 47, 4)
    _group(root, "p1", 10, 12)       # older group of p1 is ignored
    _group(root, "p2", 46, 8)
    _group(root, "p3", 45, 3, rev=2)
    for t in ("p1", "p2"):
        _instruction(root, t)
    _instruction(root, "p3", rev=2)
    traces = tmp_path / "traces"
    n = pc.stage(root, "self", traces, {"run": RUN})
    assert n == 3
    staged = json.loads((traces / "peers" / "index.json").read_text())
    assert staged["root"] == str(root.path)
    index = staged["peers"]
    assert [(p["task"], p["group"], p["solved"], p["total"]) for p in index] == [
        ("p1", 47, 4, 12), ("p2", 46, 8, 12), ("p3", 45, 3, 12)]
    p1 = traces / "peers" / "peer-1"
    assert (p1 / "instruction.md").read_text() == "Do p1.\n"
    assert (traces / "peers" / "peer-3" / "instruction.md").read_text() == "Do p3.\n"
    solved = (p1 / "solved.md").read_text()
    assert "echo p1-0" in solved and "(submitted)" in solved
    assert "SECRET OUTPUT" not in solved
    assert "echo p1-4" in (p1 / "failed.md").read_text()


def test_negative_validation_groups_are_read_after_training_groups(tmp_path):
    """A bench set frozen from an eval names its records g-270-r0.jsonl."""
    root = layout.Root(tmp_path)
    _group(root, "frozen_old", -12, 5, total=5)
    _group(root, "frozen_new", -270, 2, total=5)
    _group(root, "trained", 3, 6)
    _group(root, "trained", -400, 2, total=5)  # its training group wins
    peers = pc.select(root.run(RUN))
    assert [(p["task"], p["group"]) for p in peers] == [
        ("trained", 3), ("frozen_new", -270)]  # frozen_old: 5 of 5, out of band
    traces = tmp_path / "traces"
    assert pc.stage(root, "self", traces, {"run": RUN}) == 2
    assert "echo frozen_new-0" in (traces / "peers" / "peer-2" / "solved.md").read_text()


def test_band_follows_harder_target(tmp_path, monkeypatch):
    root = layout.Root(tmp_path)
    _group(root, "p1", 47, 4)
    _group(root, "p2", 46, 8)
    monkeypatch.setenv("EVOLVE_HARDER_TARGET", "6-9")
    assert [p["task"] for p in pc.select(root.run(RUN))] == ["p2"]


def test_stage_count_is_capped(tmp_path):
    root = layout.Root(tmp_path)
    for i in range(pc.PEER_COUNT + 3):
        _group(root, f"p{i}", 100 - i, 6)
    assert pc.stage(root, "self", tmp_path / "traces", {"run": RUN}) == pc.PEER_COUNT


def test_no_peers_still_stages_the_root_pointer(tmp_path):
    root = layout.Root(tmp_path)
    traces = tmp_path / "traces"
    assert pc.stage(root, "self", traces, {"run": RUN}) == 0
    assert json.loads((traces / "peers" / "index.json").read_text()) == {
        "root": str(root.path), "peers": []}
    text = pc.prompt(traces)
    assert "WHERE THE STUDENT STANDS NOW" not in text
    assert f"The whole experiment root is at {root.path}." in text


def test_stage_never_raises(tmp_path):
    root = layout.Root(tmp_path)
    assert pc.stage(root, "self", tmp_path / "traces", {}) == 0  # no run: logged, skipped
    assert not (tmp_path / "traces" / "peers").exists()


def test_long_commands_are_cut(tmp_path):
    path = tmp_path / "r.jsonl"
    rollout_record.write_record(
        path, {"agent": "terminus"},
        [{"turn": t, "keystrokes": ["x" * 1000], "output": ""} for t in range(1, 40)])
    text = pc.commands(path)
    assert "[cut]" in text and text.endswith("[cut: 39 turns in all]\n")
    assert len(text) < pc.COMMANDS_CHARS + 100


def test_prompt_lists_the_staged_peers(tmp_path):
    assert pc.prompt(tmp_path) == ""
    (tmp_path / "peers").mkdir()
    (tmp_path / "peers" / "index.json").write_text(json.dumps({"root": "/r", "peers": [
        {"dir": "traces/peers/peer-1", "solved": 5, "total": 12, "group": 47}]}))
    text = pc.prompt(tmp_path)
    assert "WHERE THE STUDENT STANDS NOW" in text
    assert "- traces/peers/peer-1/: 5 of 12 solved (group 47)" in text
    assert "- /r/runs/<run>/rollouts/<task>/g<group>-r<idx>.jsonl" in text
    assert "- /r/evolution/tasks/<task>/rewrites/<stamp>--<job>/" in text
    assert "never scan it" in text
    (tmp_path / "peers" / "index.json").write_text("[]")  # the old shape: nothing
    assert pc.prompt(tmp_path) == ""


# -- wiring: the loop stages it under the flag, the author prompt reads it ---

import evolve_codex as ec  # noqa: E402
import evolve_ondella as od  # noqa: E402
import test_blind_verifier as tb  # noqa: E402
import test_evolve_lineage as tl  # noqa: E402


@pytest.mark.parametrize("flag", [False, True])
def test_loop_stages_peers_only_under_the_flag(tmp_path, monkeypatch, flag):
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    if flag:
        monkeypatch.setenv("EVOLVE_PEER_CONTEXT", "1")
    tl._stub(monkeypatch)
    run = root.run(tl.RUN)
    for i in range(12):
        rollout_record.write_record(
            run.rollout_record("tw_peer", 5, i),
            {"task": "tw_peer", "rev": 0, "run": tl.RUN, "group": 5, "rollout": i,
             "reward": 1.0 if i < 6 else 0.0},
            [{"turn": 1, "keystrokes": ["make\n"], "output": ""}])
    seen = []
    stub = od.fb.process_one

    def wrapped(rewrite, signal, **kw):
        index = rewrite.traces / "peers" / "index.json"
        seen.append(json.loads(index.read_text()) if index.exists() else None)
        return stub(rewrite, signal, **kw)

    monkeypatch.setattr(od.fb, "process_one", wrapped)
    tl._signal(root, group=7, rev=0)
    od.handle(root, od._replay_signal(root, f"{tl.RUN}/tw_a--g7"), declared={})
    if not flag:
        assert seen == [None]
        return
    [index] = seen
    assert index["root"] == str(root.path)
    assert [(p["task"], p["solved"], p["total"]) for p in index["peers"]] == [
        ("tw_peer", 6, 12)]


ROOT_MARK = "/peer-context-root-marker"


@pytest.mark.parametrize("job", ["harder", "easier"])
def test_only_the_author_prompt_names_the_root(tmp_path, monkeypatch, job):
    """The blind verifier is kept from the root (and other rewrites' solutions)
    only by never being told where it is: the paragraph must not reach it."""
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    monkeypatch.setattr(ec.so, "read_decision", lambda *_: {"operator": "x"})
    (rw.traces / "peers").mkdir(parents=True)
    (rw.traces / "peers" / "index.json").write_text(json.dumps({"root": ROOT_MARK, "peers": [
        {"dir": "traces/peers/peer-1", "solved": 5, "total": 12, "group": 47}]}))
    try:
        ec.evolve_agentic(rw, dict(tb.SEED), job)
    except Exception:  # noqa: BLE001 -- only the prompts are under test
        pass
    authors = [s for s in sessions if s["role"] == "author"]
    verifiers = [s for s in sessions if s["role"] == "verifier"]
    assert authors and verifiers
    assert "WHERE THE STUDENT STANDS NOW" in authors[0]["prompt"]
    assert f"The whole experiment root is at {ROOT_MARK}." in authors[0]["prompt"]
    for v in verifiers:
        assert ROOT_MARK not in v["prompt"]
        assert "THE REST OF THE EXPERIMENT" not in v["prompt"]
        assert "WHERE THE STUDENT STANDS NOW" not in v["prompt"]
