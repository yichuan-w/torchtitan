# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The ladder rewriter's policy and its loop, against a real scheduler.

The evolve loop is the real evolve_ondella with two parts replaced: handle (a
rewrite session and its validation) by one that writes a rewrite directory
and returns a verdict, and _prepare_fold_row (the package's row) by one that
derives a distinct row from the parent's. Everything else -- the ledger,
signals, rewrite.json, lineage, status -- is the loop's own.
"""

from __future__ import annotations

import builtins
import collections
import io
import json
import os
import threading
import time

import pytest

from torchtitan.experiments.rl.examples.tmax import layout
from torchtitan.experiments.rl.examples.tmax.overfit_bench import events
from torchtitan.experiments.rl.examples.tmax.overfit_bench import ladder_rewriter as lr
from torchtitan.experiments.rl.examples.tmax.overfit_bench.rewriter_client import (
    RewriterClient,
)
from torchtitan.experiments.rl.examples.tmax.overfit_bench.scheduler import (
    BenchScheduler,
)
from torchtitan.experiments.rl.training_lineage import content_revision

RUN = "tmax-9b--20261021-000000Z"
READOUT = ("/data/evalsets", "/validation_rollouts", "/trainer/validation_traces")


def _row(task: str = "a", rev: int = 0, test: str = "exit 0") -> dict:
    return {
        "prompt": f"solve {task}",
        "label": task,
        "metadata": {
            "instance_id": task,
            "image": "ubuntu:24.04",
            "rev": rev,
            "tmax": {"test_sh": test, "fixtures": {}, "reward_path": "/logs/verifier/reward.txt"},
        },
    }


def _until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


V0 = content_revision(_row())


# -- the policy -----------------------------------------------------------------


def test_weight_is_p_times_one_minus_p_with_exploration_and_an_original_floor():
    policy = lr.Policy()
    original, child = {"rev": 0}, {"rev": 2}
    # No group yet: the exploration weight.
    assert lr.weight(child, [], has_harder_child=False, policy=policy) == policy.explore
    # Mixed 6/12: p = 6.5/13 = 0.5, the most learnable.
    assert lr.weight(child, [(6, 12)], has_harder_child=False, policy=policy) == pytest.approx(0.25)
    # Only the last `history` groups count.
    groups = [(0, 12), (12, 12), (6, 12), (6, 12), (6, 12)]
    assert lr.weight(child, groups, has_harder_child=False, policy=policy) == pytest.approx(0.25)
    # All-failed: p = 0.5/13, small but not zero.
    p = 0.5 / 13
    assert lr.weight(child, [(0, 12)], has_harder_child=False, policy=policy) == pytest.approx(p * (1 - p))
    # The original never drops below its floor.
    assert lr.weight(original, [(0, 12)], has_harder_child=False, policy=policy) == policy.original_floor
    # A harder child halves the parent's weight.
    q = 12.5 / 13
    assert lr.weight(child, [(12, 12)], has_harder_child=True, policy=policy) == pytest.approx(
        q * (1 - q) * policy.harder_parent_factor
    )


def test_mastered_needs_the_last_two_groups_at_the_threshold():
    policy = lr.Policy()
    assert not lr.mastered([(12, 12)], policy)
    assert lr.mastered([(0, 12), (11, 12), (12, 12)], policy)
    assert not lr.mastered([(12, 12), (10, 12)], policy)
    assert not lr.mastered([(12, 12), (0, 0)], policy)


# -- the loop ---------------------------------------------------------------------


@pytest.fixture
def job(tmp_path, monkeypatch):
    root = layout.Root(tmp_path / "job")
    root.mix.publish([json.dumps(_row())])
    run = root.run(RUN)
    monkeypatch.setenv("TRL_RUN_DIR", str(run.path))
    monkeypatch.setenv("TRL_BASE", str(root.path))
    monkeypatch.delenv("TRL_ROLLOUT_ATTEMPT_ID", raising=False)
    monkeypatch.delenv(events.SOCKET_ENV, raising=False)
    monkeypatch.delenv("EVOLVE_ADMISSION_STATUS_FILE", raising=False)
    # The readout and the trainer's validation traces exist, as in a job.
    for rel in (
        "data/evalsets/original.jsonl",
        f"runs/{RUN}/validation_rollouts/a/g-1-r0.jsonl",
        f"runs/{RUN}/trainer/validation_traces/step5.jsonl",
    ):
        (root.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (root.path / rel).write_text(json.dumps({"rev": 0, "group": -1, "reward": 1}) + "\n")
    sched = BenchScheduler(root, run=run, hook_timeout_sec=0.3, import_interval_sec=0.05)
    sched.start()
    client = RewriterClient(root.path)
    client.connect()
    yield root, run, sched, client
    client.close()
    sched.stop()


class Evolve:
    """The real evolve_ondella with handle and the fold's row build replaced."""

    def __init__(self, monkeypatch, root, *, status=lambda sig: "accepted", barrier=None, servers=None):
        self.ev = ev = lr._evolve()
        # The Ladder wraps _new_rewrite_dir; restore the real one after the test.
        monkeypatch.setattr(ev, "_new_rewrite_dir", ev._new_rewrite_dir)
        self.calls: list[dict] = []
        self.root = root
        self._lock = threading.Lock()
        monkeypatch.setattr(ev, "SIMPLIFY_ENABLED", True)
        # Restored after the test; the Ladder itself narrows it.
        monkeypatch.setattr(ev, "ATTEMPT_RECORD_DIRS", ev.ATTEMPT_RECORD_DIRS)
        monkeypatch.setattr(ev, "_snapshot_lineage", lambda root, note: None)

        def handle(root, sig, *, declared, dry=False, pretests=None, protecteds=None, input_row=None):
            d = dict(sig.data)
            with self._lock:
                self.calls.append({"data": d, "input_row": input_row, "thread": threading.get_ident()})
            if barrier is not None:
                barrier.wait()  # every rewrite of the request is in flight at once
            task = root.evolution.task(d["task"])
            job = d["direction"]
            rw = ev._new_rewrite_dir(task, job)
            rw.package.mkdir(parents=True)
            (rw.package / "instruction.md").write_text(f"{job} {d.get('simplify_hint')}")
            # The author's record, as handle builds it, from the attempt index.
            ev._write_record(root, task, rw)
            outcome = status(sig)
            if servers is not None:
                # A probe case's server, as a killed `./sandbox probes` leaves it.
                case = rw.package / "run" / "probe-cases-x" / "correct"
                case.mkdir(parents=True)
                servers.boot(case)
            if outcome == "raise":
                # handle raises (a storage outage) with its rewrite left running.
                layout.write_json_atomic(rw.meta, {"task": d["task"], "status": "running"})
                raise RuntimeError("storage outage")
            meta = {
                "task": d["task"], "job": job, "signal": sig.sid, "input_rev": int(d["rev"]),
                "started": layout.stamp(), "finished": None if outcome == "accepted" else layout.stamp(),
                "status": "running" if outcome == "accepted" else outcome,
                "resources": None, "result_rev": None, "sessions": [],
            }
            layout.write_json_atomic(rw.meta, meta)
            return {"signal": sig, "rewrite": rw, "meta": meta, "status": outcome}

        def prepare(package, input_line, meta, policy_canary):
            row = json.loads(input_line)
            row["metadata"]["tmax"]["test_sh"] = f"exit 0 # {package.parent.name}"
            return row

        monkeypatch.setattr(ev, "handle", handle)
        monkeypatch.setattr(ev, "_prepare_fold_row", prepare)


def _signal(run, *, version, rev, group, direction, solved, total=12):
    attempts = [f"rollouts/a/g{group}-r{i}.jsonl" for i in range(total)]
    for rel in attempts:
        path = run.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"rev": rev, "group": group, "reward": int(direction == "harder")}) + "\n")
    layout.write_json_atomic(
        run.signal("a", group),
        {
            "task": "a", "rev": rev, "version": version, "run": run.name, "group": group,
            "direction": direction, "solved": solved, "total": total,
            "created": layout.stamp(), "attempts": attempts,
        },
    )


def _group_done(sched, *, version, rev, group, passed, scored=12):
    sched.submit(
        events.make(
            events.GROUP_DONE, version=version, task="a", rev=rev, group=group,
            passed=passed, scored=scored, policy_version=0,
        )
    )


def _drain(ladder) -> None:
    """Step until every request started has been folded and closed."""
    ladder.step(0.5)
    assert _until(lambda: (ladder.step(0.05), not ladder._futs)[1])


def _pool(sched) -> dict[str, dict]:
    return {v["id"]: v for v in sched.pool.get_state()["versions"]}


def test_an_all_failed_group_starts_k_easier_rewrites_in_parallel(job, monkeypatch):
    root, run, sched, client = job
    evolve = Evolve(monkeypatch, root, barrier=threading.Barrier(3, timeout=10))
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    ladder.start()
    _signal(run, version=V0, rev=0, group=3, direction="easier", solved=0)
    _group_done(sched, version=V0, rev=0, group=3, passed=0)
    _drain(ladder)
    # One rewrite per help level, all of them in flight together (the barrier),
    # each on its own thread and directory, each against the original's row.
    assert sorted(c["data"]["simplify_hint"] for c in evolve.calls) == ["none", "specific", "vague"]
    assert len({c["thread"] for c in evolve.calls}) == 3
    assert all(c["input_row"] == _row() for c in evolve.calls)
    rewrites = root.evolution.task("a").rewrite_dirs()
    assert len(rewrites) == 3
    # A second all-failed group of the same version starts nothing: it has
    # active easier children already.
    _signal(run, version=V0, rev=0, group=5, direction="easier", solved=0)
    _group_done(sched, version=V0, rev=0, group=5, passed=0)
    _drain(ladder)
    assert len(evolve.calls) == 3
    ledger = evolve.ev.load_ledger(root)
    assert ledger[f"{RUN}/a--g5"]["outcome"] == "superseded"
    assert "already has an active easier child" in ledger[f"{RUN}/a--g5"]["reason"]


def test_easier_children_join_the_pool_and_the_parent_stays_active(job, monkeypatch):
    root, run, sched, client = job
    # The large-help rewrite fails validation: it is simply not added.
    evolve = Evolve(
        monkeypatch, root,
        status=lambda sig: "rejected" if sig.data.get("simplify_hint") == "specific" else "accepted",
    )
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    ladder.start()
    _signal(run, version=V0, rev=0, group=3, direction="easier", solved=0)
    _group_done(sched, version=V0, rev=0, group=3, passed=0)
    _drain(ladder)
    pool = _pool(sched)
    assert pool[V0]["status"] == "active"
    children = [v for v in pool.values() if v["parent"] == V0]
    assert len(children) == 2 and all(v["status"] == "active" for v in children)
    # Fresh revisions, never input_rev + 1 twice.
    assert sorted(v["rev"] for v in children) == [1, 2]
    assert all(v["weight"] == lr.Policy.explore for v in children)
    # The all-failed original sits at its floor.
    assert pool[V0]["weight"] == lr.Policy.original_floor
    task = root.evolution.task("a")
    assert task.revs() == [1, 2]
    folds = [e for e in layout.read_jsonl(task.lineage) if e["event"] == "fold"]
    assert sorted(e["help"] for e in folds) == ["none", "vague"]
    assert {e["parent_version"] for e in folds} == {V0}
    metas = [json.loads(rw.meta.read_text()) for rw in task.rewrite_dirs()]
    assert sorted(m["status"] for m in metas) == ["accepted", "accepted", "rejected"]
    assert {m["ladder"]["version"] for m in metas if m["status"] == "accepted"} == {
        v["id"] for v in children
    }
    assert evolve.ev.load_ledger(root)[f"{RUN}/a--g3"]["outcome"] == "handled"
    # The scheduler republished: the mix holds every active version.
    assert len(root.mix.read_live().splitlines()) == 3


def _with_child(root, client, *, parent=V0, job="easier") -> str:
    """One child r1 of the original, folded by the ladder an hour ago."""
    child_row = _row(rev=1, test="exit 0 # r1")
    child = client.add(child_row, weight=0.25, parent=parent)["id"]
    task = root.evolution.task("a")
    for rev in (0, 1):
        task.rev(rev).mkdir(parents=True, exist_ok=True)
        (task.rev(rev) / "instruction.md").write_text(f"r{rev}")
    rw = task.rewrite(job, layout.stamp(time.time() - 3600))
    rw.path.mkdir(parents=True)
    layout.write_json_atomic(
        rw.meta,
        {"task": "a", "job": job, "status": "accepted", "input_rev": 0, "result_rev": 1,
         "finished": layout.stamp(time.time() - 3600),
         "ladder": {"parent": parent, "help": "vague" if job == "easier" else None, "version": child}},
    )
    return child


def test_an_all_solved_group_adds_one_harder_child_and_down_weights_the_parent(job, monkeypatch):
    root, run, sched, client = job
    v1 = _with_child(root, client)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    ladder.start()
    assert [c[:2] for c in ladder.children[V0]] == [(v1, "easier")]
    _signal(run, version=v1, rev=1, group=7, direction="harder", solved=12)
    _group_done(sched, version=v1, rev=1, group=7, passed=12)
    _drain(ladder)
    (call,) = evolve.calls
    assert "simplify_hint" not in call["data"]
    # The previous revision the hardening reads is the parent version's.
    assert call["data"]["student_feedback"]["previous_revision"] == {"rev": 0, "instruction": "r0"}
    pool = _pool(sched)
    (v2,) = [v for v in pool.values() if v["parent"] == v1]
    assert v2["rev"] == 2 and v2["status"] == "active" and v2["weight"] == lr.Policy.explore
    q = 12.5 / 13
    assert pool[v1]["status"] == "active"
    assert pool[v1]["weight"] == pytest.approx(q * (1 - q) * lr.Policy.harder_parent_factor)


def test_a_mastered_version_is_retired_two_groups_after_its_harder_child(job, monkeypatch):
    root, run, sched, client = job
    v1 = _with_child(root, client)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    ladder.start()
    # Mastered without a harder child: it stays.
    for group in (7, 8):
        _group_done(sched, version=v1, rev=1, group=group, passed=12)
    assert _until(lambda: (ladder.step(0.05), len(ladder.groups[v1]) == 2)[1])
    assert _pool(sched)[v1]["status"] == "active"
    # Its harder child arrives; the groups before it do not count.
    _signal(run, version=v1, rev=1, group=9, direction="harder", solved=12)
    _group_done(sched, version=v1, rev=1, group=9, passed=12)
    _drain(ladder)
    assert _pool(sched)[v1]["status"] == "active"
    # Two groups at >= 0.9 after it (11/12 is 0.92): retired.
    for group in (10, 11):
        _group_done(sched, version=v1, rev=1, group=group, passed=11)
    assert _until(lambda: (ladder.step(0.05), _pool(sched)[v1]["status"] == "retired")[1])
    (v2,) = [v for v in _pool(sched).values() if v["parent"] == v1]
    assert v2["status"] == "active"


def test_the_original_is_never_retired(job, monkeypatch):
    root, run, sched, client = job
    _with_child(root, client, job="harder")
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    ladder.start()
    for group in (3, 4, 5):
        _group_done(sched, version=V0, rev=0, group=group, passed=12)
    assert _until(lambda: (ladder.step(0.05), len(ladder.groups[V0]) == 3)[1])
    pool = _pool(sched)
    assert pool[V0]["status"] == "active"
    assert pool[V0]["weight"] == lr.Policy.original_floor


def test_the_ladder_never_reads_the_readout(job, monkeypatch):
    root, run, sched, client = job
    forbidden: list[str] = []

    def watch(fn):
        def wrapped(path=".", *args, **kwargs):
            text = os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
            text = text.decode() if isinstance(text, bytes) else text
            if any(part in text for part in READOUT):
                forbidden.append(f"{fn.__name__}({text})")
            return fn(path, *args, **kwargs)

        return wrapped

    evolve = Evolve(monkeypatch, root)
    for module, name in ((builtins, "open"), (io, "open"), (os, "listdir"), (os, "scandir"), (os, "stat")):
        monkeypatch.setattr(module, name, watch(getattr(module, name)))
    assert "validation_rollouts" in evolve.ev.ATTEMPT_RECORD_DIRS
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    assert evolve.ev.ATTEMPT_RECORD_DIRS == ("rollouts",)
    ladder.start()
    _signal(run, version=V0, rev=0, group=3, direction="easier", solved=0)
    _group_done(sched, version=V0, rev=0, group=3, passed=0)
    _drain(ladder)
    monkeypatch.undo()
    assert len(evolve.calls) == 3 and forbidden == []
    # The author's record was built, from training attempts only (each
    # rewrite's traces/ moved into its revision with the fold).
    index = layout.read_jsonl(root.evolution.task("a").attempts)
    assert len(index) == 12
    assert all(e["path"].startswith(f"runs/{RUN}/rollouts/") for e in index)
    for rev in (1, 2, 3):
        traces = root.evolution.task("a").rev(rev) / "traces" / "attempts.jsonl"
        assert layout.read_jsonl(traces) == index


# -- v2: cap, exploit before explore, stale retirement, sandbox teardown --------


def test_an_untested_version_weighs_strictly_below_the_lowest_learnable_one():
    policy = lr.Policy()
    assert lr.explore_weight([], policy) == policy.explore
    learnable = [0.2263, 0.1]
    w = lr.explore_weight(learnable, policy)
    assert w < min(learnable) and w == pytest.approx(0.05)
    # Never above --explore.
    assert lr.explore_weight([0.9], policy) == policy.explore
    assert lr.learnable([(0, 12), (4, 12)], policy)
    assert not lr.learnable([(0, 12), (12, 12), (0, 12)], policy)
    # Only the last `history` groups count.
    assert not lr.learnable([(4, 12), (0, 12), (0, 12), (0, 12)], policy)


def test_parallel_easier_children_only_when_nothing_is_learnable_and_the_pool_is_small():
    policy = lr.Policy()
    assert lr.easier_levels(policy, any_learnable=False, active=3) == lr.HELP_LEVELS
    assert lr.easier_levels(policy, any_learnable=False, active=4) == ("vague",)
    assert lr.easier_levels(policy, any_learnable=True, active=1) == ("vague",)


def test_stale_needs_a_child_two_all_failed_groups_and_not_the_original():
    policy = lr.Policy()
    child, original = {"rev": 3}, {"rev": 0}
    two = [(4, 12), (0, 12), (0, 12)]
    assert lr.fail_streak(two) == 2 and lr.fail_streak([(0, 12), (1, 12)]) == 0
    assert lr.stale(child, two, children=1, policy=policy)
    assert not lr.stale(child, two, children=0, policy=policy)
    assert not lr.stale(child, [(0, 12)], children=1, policy=policy)
    assert not lr.stale(original, two, children=1, policy=policy)


def _add(root, client, rev: int, *, parent=V0) -> str:
    """Version r<rev> of task a, a child of ``parent``, with its revision on disk."""
    task = root.evolution.task("a")
    for r in (0, rev):
        task.rev(r).mkdir(parents=True, exist_ok=True)
        (task.rev(r) / "instruction.md").write_text(f"r{r}")
    return client.add(_row(rev=rev, test=f"exit 0 # r{rev}"), weight=0.25, parent=parent)["id"]


def _groups(ladder, sched, plan) -> None:
    """group_done events [(version, rev, group, passed)], then wait until the
    ladder has every one."""
    want = collections.Counter(vid for vid, *_ in plan)
    want = {vid: len(ladder.groups[vid]) + n for vid, n in want.items()}
    for vid, rev, group, passed in plan:
        _group_done(sched, version=vid, rev=rev, group=group, passed=passed)
    assert _until(lambda: (ladder.step(0.05), all(len(ladder.groups[v]) >= n for v, n in want.items()))[1])


def test_a_child_at_the_cap_replaces_the_worst_version_never_the_original_or_a_recent_solver(job, monkeypatch):
    root, run, sched, client = job
    a, b, d = _add(root, client, 1), _add(root, client, 2), _add(root, client, 3)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, policy=lr.Policy(max_active=4), workers=4)
    ladder.start()
    # The original and a fail; b solved 3/12 (learnable, protected); d is
    # mastered (protected), and asks for a harder child.
    _groups(ladder, sched, [(V0, 0, 1, 0), (a, 1, 2, 0), (a, 1, 3, 0), (b, 2, 4, 3), (d, 3, 5, 12)])
    _signal(run, version=d, rev=3, group=6, direction="harder", solved=12)
    _group_done(sched, version=d, rev=3, group=6, passed=12)
    _drain(ladder)
    pool = _pool(sched)
    assert pool[a]["status"] == "retired"
    assert all(pool[v]["status"] == "active" for v in (V0, b, d))
    (child,) = [v for v in pool.values() if v["parent"] == d]
    assert child["status"] == "active"
    assert sum(v["status"] == "active" for v in pool.values()) == 4


def test_at_the_cap_with_nothing_replaceable_a_signal_starts_nothing(job, monkeypatch):
    root, run, sched, client = job
    b, d = _add(root, client, 1), _add(root, client, 2)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, policy=lr.Policy(max_active=3), workers=4)
    ladder.start()
    _groups(ladder, sched, [(V0, 0, 1, 0), (b, 1, 2, 3), (d, 2, 3, 12)])
    _signal(run, version=d, rev=2, group=4, direction="harder", solved=12)
    _group_done(sched, version=d, rev=2, group=4, passed=12)
    _drain(ladder)
    assert evolve.calls == []
    entry = evolve.ev.load_ledger(root)[f"{RUN}/a--g4"]
    assert entry["outcome"] == "superseded" and "--max-active 3" in entry["reason"]


def test_untested_versions_weigh_below_a_learnable_one(job, monkeypatch):
    root, run, sched, client = job
    c = _add(root, client, 1)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=4)
    ladder.start()
    assert _pool(sched)[c]["weight"] == lr.Policy.explore
    _groups(ladder, sched, [(V0, 0, 1, 4)])
    assert _until(lambda: (ladder.step(0.05), _pool(sched)[c]["weight"] < lr.Policy.explore)[1])
    pool = _pool(sched)
    p = 4.5 / 13
    assert pool[V0]["weight"] == pytest.approx(p * (1 - p))
    assert pool[c]["weight"] == pytest.approx(0.5 * p * (1 - p))
    assert pool[c]["weight"] < pool[V0]["weight"]


def test_no_easier_child_while_a_learnable_version_exists(job, monkeypatch):
    root, run, sched, client = job
    a = _add(root, client, 1)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=4)
    ladder.start()
    _groups(ladder, sched, [(V0, 0, 1, 4)])
    _signal(run, version=a, rev=1, group=2, direction="easier", solved=0)
    _group_done(sched, version=a, rev=1, group=2, passed=0)
    _drain(ladder)
    assert evolve.calls == []
    entry = evolve.ev.load_ledger(root)[f"{RUN}/a--g2"]
    assert entry["outcome"] == "superseded" and "no easier child while learnable" in entry["reason"]


def test_one_easier_child_when_the_pool_is_half_full(job, monkeypatch):
    root, run, sched, client = job
    for rev in (1, 2, 3):
        _add(root, client, rev)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=4)
    ladder.start()
    _signal(run, version=V0, rev=0, group=3, direction="easier", solved=0)
    _group_done(sched, version=V0, rev=0, group=3, passed=0)
    _drain(ladder)
    assert [c["data"]["simplify_hint"] for c in evolve.calls] == ["vague"]


def test_a_version_with_a_child_is_retired_after_two_all_failed_groups(job, monkeypatch):
    root, run, sched, client = job
    a = _add(root, client, 1)
    a2 = _add(root, client, 2, parent=a)
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=4)
    ladder.start()
    ladder.children[a].append((a2, "easier", 0))
    ladder.children[V0].append((a, "easier", 0))
    _groups(ladder, sched, [(a, 1, 1, 0), (V0, 0, 2, 0), (a2, 2, 3, 0)])
    pool = _pool(sched)
    # One all-failed group: stays. a2 has no child: stays.
    assert pool[a]["status"] == "active" and pool[a2]["status"] == "active"
    _groups(ladder, sched, [(a, 1, 4, 0), (V0, 0, 5, 0), (a2, 2, 6, 0)])
    assert _until(lambda: (ladder.step(0.05), _pool(sched)[a]["status"] == "retired")[1])
    pool = _pool(sched)
    assert pool[V0]["status"] == "active" and pool[a2]["status"] == "active"


class Servers:
    """Sandbox servers by pid, as sandbox_servers would read them off /proc."""

    def __init__(self):
        self.live: dict[int, tuple] = {}
        self.downed: list[int] = []
        self._lock = threading.Lock()
        self._next = 1000

    def boot(self, pkg):
        with self._lock:
            self._next += 1
            self.live[self._next] = (pkg, f"/tmp/fake-{self._next}.sock")
            return self._next

    def list(self):
        with self._lock:
            return [(pid, pkg, sock) for pid, (pkg, sock) in self.live.items()]

    def down(self, pid, sock):
        with self._lock:
            self.live.pop(pid)
            self.downed.append(pid)


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "failed", "raise"])
def test_every_ladder_rewrite_tears_down_its_sandbox_servers(job, monkeypatch, outcome):
    root, run, sched, client = job
    servers = Servers()
    evolve = Evolve(monkeypatch, root, status=lambda sig: outcome, servers=servers)
    sweeper = lr.Sweeper(root, evolve.ev, servers=servers.list, down=servers.down)
    ladder = lr.Ladder(root, client, evolve.ev, workers=4, sweeper=sweeper)
    ladder.start()
    _signal(run, version=V0, rev=0, group=3, direction="easier", solved=0)
    _group_done(sched, version=V0, rev=0, group=3, passed=0)
    _drain(ladder)
    assert len(evolve.calls) == 3
    assert servers.live == {} and len(servers.downed) == 3


def test_a_live_rewrites_servers_are_left_alone(job, monkeypatch):
    root, run, sched, client = job
    servers = Servers()
    evolve = Evolve(monkeypatch, root)
    sweeper = lr.Sweeper(root, evolve.ev, servers=servers.list, down=servers.down)
    task = root.evolution.task("a")
    live = task.path / "rewrites" / "20261022-000000Z--easier"
    (live / "package").mkdir(parents=True)
    layout.write_json_atomic(live / "rewrite.json", {"status": "running"})
    closed = task.path / "rewrites" / "20261022-000001Z--easier"
    (closed / "package").mkdir(parents=True)
    layout.write_json_atomic(closed / "rewrite.json", {"status": "rejected"})
    keep = servers.boot(live / "package")
    gone = servers.boot(task.path / "rewrites" / "20261022-000002Z--easier" / "package")
    shut = servers.boot(closed / "package")
    elsewhere = servers.boot(root.path.parent / "other-job" / "package")
    # A sibling's rewrite ended: only orphans go.
    assert sweeper.sweep(rewrite=("a", "20261022-000003Z--easier"), why="test") == 2
    assert sorted(servers.downed) == sorted([gone, shut])
    assert set(servers.live) == {keep, elsewhere}
    # The ladder's start (or stop) takes every server of the job, and only those.
    ladder = lr.Ladder(root, client, evolve.ev, workers=4, sweeper=sweeper)
    ladder.start()
    assert set(servers.live) == {elsewhere}


def test_the_original_is_never_hardened(job, monkeypatch):
    root, run, sched, client = job
    evolve = Evolve(monkeypatch, root)
    ladder = lr.Ladder(root, client, evolve.ev, workers=16)
    ladder.start()
    _signal(run, version=V0, rev=0, group=4, direction="harder", solved=3)
    _group_done(sched, version=V0, rev=0, group=4, passed=3)
    _drain(ladder)
    assert evolve.calls == []
    entry = evolve.ev.load_ledger(root)[f"{RUN}/a--g4"]
    assert entry["outcome"] == "superseded" and "is the original" in entry["reason"]
