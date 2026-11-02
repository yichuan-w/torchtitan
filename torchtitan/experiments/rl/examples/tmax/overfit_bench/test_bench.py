# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402
import publish_mix  # noqa: E402


def _row(task: str, test: str = "exit 0") -> dict:
    return {
        "prompt": f"solve {task}",
        "label": task,
        "metadata": {
            "instance_id": task,
            "image": "ubuntu:24.04",
            "tmax": {"test_sh": test, "fixtures": {}, "reward_path": "/logs/verifier/reward.txt"},
        },
    }


@pytest.fixture
def job(tmp_path):
    evalset = tmp_path / "bench.jsonl"
    evalset.write_text("".join(json.dumps(_row(t)) + "\n" for t in ("a", "b")))
    tasks = tmp_path / "tasks"
    manifest = bench.split(evalset, "tb21", tasks)
    assert sorted(manifest["tasks"]) == ["a", "b"]
    corpus = tmp_path / "terminal-bench-2-1"
    (corpus / "tasks" / "a").mkdir(parents=True)
    (corpus / "tasks" / "a" / "instruction.md").write_text("solve a")
    return bench.new_job(tasks, "a", corpus, "ref", tmp_path / "jobs", None)


def test_job_starts_from_the_original_alone_and_reads_it_out(job):
    assert job.name == "a--ref" and job.parent.name == "tb21"
    live = (job / "data/mix/live.jsonl").read_text().splitlines()
    assert [json.loads(r)["metadata"]["instance_id"] for r in live] == ["a"]
    readout = json.loads((job / "data/evalsets/original.jsonl").read_text())
    assert readout == _row("a")
    assert (job / "data/sources/terminal-bench-2-1/tasks/a/instruction.md").is_file()


def test_new_with_seed_rows_starts_the_pool_from_them(tmp_path):
    evalset = tmp_path / "bench.jsonl"
    evalset.write_text(json.dumps(_row("a")) + "\n")
    tasks = tmp_path / "tasks"
    bench.split(evalset, "tb21", tasks)
    corpus = tmp_path / "terminal-bench-2-1"
    (corpus / "tasks" / "a").mkdir(parents=True)
    (corpus / "tasks" / "a" / "instruction.md").write_text("solve a")
    rewrite = _row("a", test="exit 1")
    rewrite["metadata"]["rev"] = 1
    seed_rows = tmp_path / "seed.jsonl"
    # The original as the tasks dir holds it (no rev) plus one rewrite.
    seed_rows.write_text((tasks / "a.jsonl").read_text() + json.dumps(rewrite) + "\n")
    job = bench.new_job(tasks, "a", corpus, "ref", tmp_path / "jobs", None, seed_rows)
    pool = json.loads((job / "data/mix/pool.json").read_text())
    active = [v for v in pool["versions"] if v["status"] == "active"]
    # The original seeded again is the job's own v1 row, not a second version.
    assert [(v["rev"], v["weight"]) for v in active] == [(0, 1.0), (1, 1.0)]
    assert len(pool["versions"]) == 2


def test_publish_refuses_a_file_with_any_unparseable_row(job, tmp_path):
    rows = tmp_path / "mix.jsonl"
    bad = _row("a2")
    del bad["metadata"]["tmax"]
    rows.write_text(json.dumps(_row("a1")) + "\n" + json.dumps(bad) + "\n")
    with pytest.raises(SystemExit):
        publish_mix.check_rows(rows.read_text())
    good = publish_mix.check_rows(json.dumps(_row("a1")) + "\n" + json.dumps(_row("a")) + "\n")
    version, _ = bench.layout.Root(job).mix.publish(good)
    assert version == 2


def test_readout_takes_the_original_task_from_every_run(job):
    def index(run, step, passes, n=16):
        d = job / "runs" / run / "trainer/validation_traces" / f"step-{step}"
        d.mkdir(parents=True)
        lines = ["| Task | Trial | State | Reward |", "|---|---|---|---:|"]
        lines += [f"| a | [t{i}](x) | {'PASS' if i < passes else 'FAIL'} | 1 |" for i in range(n)]
        lines += ["| other | [o](x) | PASS | 1 |"]
        (d / "INDEX.md").write_text("\n".join(lines) + "\n")

    index("tmax-9b--20261001-000000Z", 0, 0)
    index("tmax-9b--20261001-000000Z", 5, 1)
    index("tmax-9b--20261002-000000Z", 10, 8)
    index("tmax-9b--20261002-000000Z", 15, 9)
    index("tmax-9b--20261002-000000Z", 20, 12)
    r = bench.readout(job)
    assert [p["step"] for p in r["curve"]] == [0, 5, 10, 15, 20]
    assert r["curve"][1]["passes"] == 1 and r["curve"][1]["attempts"] == 16
    assert r["first_pass_step"] == 5
    assert r["stable_solve_step"] == 10
    assert r["curve"][0]["pass_at_5"] == 0.0
    assert bench.pass_at_k(16, 16) == 1.0
    assert abs(bench.pass_at_k(16, 1) - 5 / 16) < 1e-9


def test_readout_counts_every_training_attempt_and_the_ones_to_a_stable_solve(job):
    def run(name, events):
        d = job / "runs" / name / "scheduler"
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(
            "".join(json.dumps({**e, "run": name}) + "\n" for e in events) + '{"event": "rollout_d'
        )

    def group(minute, passed):
        stamp = f"20261001-00{minute:02d}00Z"
        done = [{"event": "rollout_done", "stamp": stamp} for _ in range(12)]
        return done + [{"event": "group_done", "passed": passed, "scored": 12, "stamp": stamp}]

    def step(minute, n):
        return [{"event": "step_done", "step": n, "stamp": f"20261001-00{minute:02d}30Z"}]

    # an all-fail group trains nothing but still costs its 12 attempts
    run("tmax-9b--20261001-000000Z--attempt-1", group(1, 0) + group(2, 3) + step(2, 1))
    run("tmax-9b--20261001-000000Z--attempt-2", group(10, 12) + group(11, 5) + step(11, 2))
    for s in (0, 1, 2):
        d = job / "runs" / "tmax-9b--20261001-000000Z" / "trainer/validation_traces" / f"step-{s}"
        d.mkdir(parents=True)
        rows = [f"| a | [t{i}](x) | {'PASS' if s and i < 12 else 'FAIL'} | 1 |" for i in range(16)]
        (d / "INDEX.md").write_text("\n".join(rows) + "\n")
    r = bench.readout(job, hold=2)
    assert r["stable_solve_step"] == 1
    assert (r["train_attempts"], r["train_groups"], r["trained_groups"]) == (48, 4, 2)
    assert r["readout_attempts"] == 48
    assert r["attempts_to_stable_solve"] == 24
    assert r["train_hours"] == round((90 + 90) / 3600, 2)


def test_a_job_that_left_the_recipe_reads_out_as_its_own_arm(job):
    assert bench.readout(job)["arm"] == "recipe"
    (job / bench.OVERRIDES).write_text(
        json.dumps([{"from_step": 15, "env": {"SWE_TMAX_CONTEXT": "163840"}, "why": "x"}])
    )
    r = bench.readout(job)
    assert r["arm"] == "off-recipe from step 15"
    assert r["recipe_overrides"][0]["env"] == {"SWE_TMAX_CONTEXT": "163840"}
