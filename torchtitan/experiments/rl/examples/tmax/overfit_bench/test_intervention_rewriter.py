# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The intervention rewriter against a real scheduler, with the ladder tests'
stand-in for evolve_ondella.handle and a scripted diagnosis."""

from __future__ import annotations

import json

import pytest

from torchtitan.experiments.rl.examples.tmax import layout
from torchtitan.experiments.rl.examples.tmax.overfit_bench import events
from torchtitan.experiments.rl.examples.tmax.overfit_bench import intervention_rewriter as ir
from torchtitan.experiments.rl.examples.tmax.overfit_bench.test_ladder_rewriter import (  # noqa: F401 -- fixture
    _group_done,
    _pool,
    _row,
    _until,
    Evolve,
    job,
    RUN,
    V0,
)


def _cand(name, kind="direction_hint", level="vague", reuses=None):
    c = {"name": name, "kind": kind, "hint_level": level, "spec": f"do {name}",
         "mechanism": f"because {name}", "prediction": "4 of 12"}
    if reuses:
        c["reuses"] = reuses
    return c


class Diagnoses:
    """Scripted diagnosis sessions: each call returns the next script entry."""

    def __init__(self, *scripts):
        self.scripts, self.calls = list(scripts), []

    def __call__(self, co, work):
        self.calls.append({"work": work, "catalog": json.loads((work / "catalog.json").read_text()),
                           "attempts": sorted(p.name for p in (work / "attempts").iterdir())})
        cands = self.scripts.pop(0)
        first = sorted((work / "attempts").iterdir())[0].name
        return {"bottleneck": {"name": "column forward", "evidence": [{"attempt": first, "turn": 3,
                                                                        "observation": "x"}]},
                "candidates": cands}


def _rollouts(run, sched, *, version, rev, group, rewards):
    for i, r in enumerate(rewards):
        rel = f"rollouts/a/g{group}-r{i}.jsonl"
        (run.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (run.path / rel).write_text(json.dumps({"rev": rev, "group": group, "reward": r, "turns": 9}) + "\n")
        sched.submit(events.make(events.ROLLOUT_DONE, version=version, task="a", rev=rev, group=group, idx=i,
                                 reward=r, finish_reason="submit", record_path=rel, policy_versions=[0, 0]))


def _drain(co, n_attempts=0, n_groups=0) -> None:
    """Step until the scheduler delivered this many attempts and groups in all
    (events arrive asynchronously) and every session started has ended."""

    def seen():
        return (sum(map(len, co.attempts.values())) >= n_attempts
                and sum(map(len, co.groups.values())) >= n_groups)

    assert _until(lambda: (co.step(0.05), seen() and not co._futs)[1])
    co.step(0.05)
    assert _until(lambda: (co.step(0.05), not co._futs)[1])


def _start(job, monkeypatch, *scripts, status=lambda sig: "accepted"):
    root, run, sched, client = job
    evolve = Evolve(monkeypatch, root, status=status)
    root.evolution.task("a").rev(0).mkdir(parents=True, exist_ok=True)
    (root.evolution.task("a").rev(0) / "instruction.md").write_text("original")
    diag = Diagnoses(*scripts)
    co = ir.Coordinator(root, client, evolve.ev, diagnose=diag)
    co.start()
    return co, evolve, diag


def _set_version(co, *members):
    return co.state["sets"][ir.key(members)]["version"]


def test_cold_start_diagnoses_after_cold_min_scored_attempts_and_builds_each_proposal(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("say install"), _cand("smaller", "smaller_instance", "none")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 5)
    _drain(co, 5, 0)
    assert diag.calls == []  # 5 < cold_min
    _rollouts(run, sched, version=V0, rev=0, group=1, rewards=[0.0])
    _drain(co, 6, 0)
    assert len(diag.calls) == 1
    # Both proposals built, each from the original row, each with its own set.
    assert sorted(c["data"]["intervention_plan"]["version"][0]["name"] for c in evolve.calls) == [
        "say install", "smaller"]
    assert all(c["input_row"] == _row() and c["data"]["rev"] == 0 for c in evolve.calls)
    assert {c["data"]["simplify_hint"] for c in evolve.calls} == {"vague", "none"}
    pool = _pool(sched)
    new = [_set_version(co, "i1"), _set_version(co, "i2")]
    assert all(pool[v]["status"] == "active" for v in new)
    # New versions are drawn first, oldest first, once each; then the weighted draw.
    cands = list(pool.values())
    first = co.which_version(cands)
    second = co.which_version(cands)
    assert {first, second} == set(new) and co.which_version(cands) is None


def test_versions_are_built_from_the_original_with_the_whole_set(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A")], [_cand("B", "feedback")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    va = _set_version(co, "i1")
    # {i1} fails a whole group: diagnose with {i1} as base, build {i1, i2}.
    _rollouts(run, sched, version=va, rev=1, group=1, rewards=[0.0] * 12)
    _group_done(sched, version=va, rev=1, group=1, passed=0)
    _drain(co, 18, 1)
    assert len(diag.calls) == 2 and diag.calls[1]["catalog"]["base_set"] == ["i1"]
    last = evolve.calls[-1]
    assert [i["id"] for i in last["data"]["intervention_plan"]["version"]] == ["i1", "i2"]
    assert last["input_row"] == _row() and last["data"]["rev"] == 0
    assert _pool(sched)[_set_version(co, "i1", "i2")]["parent"] == va


def test_no_new_diagnosis_while_a_descendant_is_untested_or_learnable(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A")], [_cand("B")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    _group_done(sched, version=V0, rev=0, group=0, passed=0)
    _drain(co, 6, 1)
    assert len(diag.calls) == 1  # {i1} is untested
    va = _set_version(co, "i1")
    _group_done(sched, version=va, rev=1, group=1, passed=5)
    _group_done(sched, version=V0, rev=0, group=2, passed=0)
    _drain(co, 6, 3)
    assert len(diag.calls) == 1  # {i1} is learnable
    _rollouts(run, sched, version=va, rev=1, group=3, rewards=[0.0] * 12)
    _group_done(sched, version=va, rev=1, group=3, passed=0)
    _drain(co, 18, 4)
    # {i1} now all-failed in its latest group: it is diagnosed as a base.
    assert len(diag.calls) == 2


def test_a_mastered_version_withdraws_setup_hints_before_scope_removals(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("forward out of scope", "scope_removal", "none")],
                              [_cand("say install", "setup_hint", "specific")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    v1 = _set_version(co, "i1")
    _rollouts(run, sched, version=v1, rev=1, group=1, rewards=[0.0] * 12)
    _group_done(sched, version=v1, rev=1, group=1, passed=0)
    _drain(co, 18, 1)
    v12 = _set_version(co, "i1", "i2")
    _rollouts(run, sched, version=v12, rev=2, group=2, rewards=[1.0] * 12)
    _group_done(sched, version=v12, rev=2, group=2, passed=12)
    _drain(co, 30, 2)
    # {i1} exists already, so withdrawing i2 (the setup hint, rank 0) is skipped
    # only because it was built; the next is i1 -> {i2}.
    assert "i2" in co.state["sets"] and co.state["sets"]["i2"]["base"] == "i1,i2"
    plan = evolve.calls[-1]["data"]["intervention_plan"]
    assert [i["id"] for i in plan["version"]] == ["i2"]
    assert plan["measured_on"]["solved"] == 12


def test_withdraw_order_puts_hints_first_and_scope_removal_last():
    members = [
        {"id": "i1", "kind": "scope_removal", "born": "1"},
        {"id": "i2", "kind": "supplied_work", "born": "2"},
        {"id": "i3", "kind": "check_hint", "born": "3"},
        {"id": "i4", "kind": "direction_hint", "born": "0"},
    ]
    assert [i["id"] for i in ir.withdraw_order(members)] == ["i3", "i4", "i2", "i1"]


def test_a_diagnosis_without_a_valid_candidate_builds_nothing(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [{"name": "x", "kind": "magic", "hint_level": "none",
                                                  "spec": "s", "mechanism": "m"}])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    assert evolve.calls == [] and co.state["diagnoses"][""]["status"] == "failed"
    with pytest.raises(ValueError):
        ir.check_diagnosis({"bottleneck": {"name": "b"}, "candidates": []}, catalog={}, max_candidates=3)


def test_the_diagnosis_reads_training_attempts_only(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    work = diag.calls[0]["work"]
    index = json.loads((work / "index.json").read_text())
    assert index and all(i["path"].startswith("rollouts/") for i in index.values())
    for p in (work / "attempts").iterdir():
        assert "/rollouts/" in str(p.resolve()) and "validation" not in str(p.resolve())
    assert (work / "original" / "instruction.md").read_text() == "original"
    versions = (work / "versions.md").read_text()
    assert "turns and" in versions and "tokens of context" in versions
    assert "how its attempts ended: submit 6" in versions


def test_a_build_cut_by_a_restart_is_built_again(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    co.state["sets"]["i1"]["status"] = "building"  # as a killed process left it
    co.state["sets"]["i9"] = {"status": "building", "base": "", "parent": V0, "version": None}
    co._save()
    co2 = ir.Coordinator(root, client, evolve.ev, diagnose=diag)
    co2.start()
    # A build with its plan on record is rebuilt; one without stays rebuildable by a later proposal.
    assert co2.state["sets"]["i1"]["status"] == "retry"
    assert co2.state["sets"]["i9"]["status"] == "interrupted" and co2._buildable("i9")
    _drain(co2)
    assert co2.state["sets"]["i1"]["status"] == "accepted" and len(evolve.calls) == 2


def test_a_provider_refusal_rebuilds_the_same_plan(job, monkeypatch):
    root, run, sched, client = job
    tries = []

    def status(sig):
        tries.append(sig.data["intervention_plan"])
        return "failed" if len(tries) == 1 else "accepted"

    co, evolve, diag = _start(job, monkeypatch, [_cand("A")], status=status)
    monkeypatch.setattr(co, "policy", ir.Policy(provider_retry_sec=0))
    real = evolve.ev.handle

    def handle(root, sig, **kw):
        h = real(root, sig, **kw)
        if h["status"] == "failed":
            h["meta"]["stage"] = "agent"
            h["meta"]["reason"] = "AgentSessionError: codex session failed (provider_capacity, exit 1)"
        return h

    monkeypatch.setattr(evolve.ev, "handle", handle)
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    assert _until(lambda: (co.step(0.05), co.state["sets"]["i1"]["status"] == "accepted")[1])
    assert len(tries) == 2 and tries[0] == tries[1] and co.state["sets"]["i1"]["retries"] == 1
    assert len(diag.calls) == 1


def test_a_plan_failure_is_not_retried(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A")], status=lambda sig: "rejected")
    monkeypatch.setattr(co, "policy", ir.Policy(provider_retry_sec=0))
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    co.step(0.05)
    assert co.state["sets"]["i1"]["status"] == "rejected" and len(evolve.calls) == 1


def test_no_new_diagnosis_while_another_version_is_unmeasured(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A"), _cand("B", "feedback")], [_cand("C")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    va, vb = _set_version(co, "i1"), _set_version(co, "i2")
    _rollouts(run, sched, version=va, rev=1, group=1, rewards=[0.0] * 12)
    _group_done(sched, version=va, rev=1, group=1, passed=0)
    _drain(co, 18, 1)
    # {i1} failed, but {i2} has no group yet: wait for it.
    assert len(diag.calls) == 1 and "untested" in co.state["diagnoses"]["i1"]["waiting"]
    _group_done(sched, version=vb, rev=2, group=2, passed=0)
    _drain(co, 18, 2)
    assert len(diag.calls) == 2


def test_a_diagnosis_waits_for_unmeasured_versions_at_most_max_wait(job, monkeypatch):
    root, run, sched, client = job
    co, evolve, diag = _start(job, monkeypatch, [_cand("A"), _cand("B", "feedback")], [_cand("C")])
    _rollouts(run, sched, version=V0, rev=0, group=0, rewards=[0.0] * 6)
    _drain(co, 6, 0)
    va = _set_version(co, "i1")
    _rollouts(run, sched, version=va, rev=1, group=1, rewards=[0.0] * 12)
    _group_done(sched, version=va, rev=1, group=1, passed=0)
    _drain(co, 18, 1)
    assert len(diag.calls) == 1  # {i2} untested
    co.state["diagnoses"]["i1"]["waiting_since"] -= co.policy.max_wait_sec + 1
    co.step(0.05)
    _drain(co)
    assert len(diag.calls) == 2
