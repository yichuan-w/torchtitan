"""The loop over one experiment root: discovery through the ledger, r0 from
the source corpus, one rewrite directory per handled signal, acceptance as a
renamed revision and a new mix version, and status.json rebuilt from files."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

SEED = {
    "instruction.md": "Write the report to /app/report.txt.\n",
    "environment/Dockerfile": "FROM scratch\nWORKDIR /app\n",
    "solution/solve.sh": "#!/bin/sh\n",
    "tests/test.sh": "echo 1 > /app/reward.txt\n",
}
RUN = "tmax-9b--20260904-181500Z"
VERDICTS = {"oracle": "pass", "dark_paths": [], "dark_literals": [], "step": []}


def _root(tmp_path, monkeypatch, tmax: dict | None = None) -> layout.Root:
    """A root with one seed task in the tw-extract corpus and a mix at v1.
    `tmax` is the row's grading payload, for a row that carries a pin hook."""
    base = tmp_path / "root"
    monkeypatch.setenv("TRL_BASE", str(base))
    monkeypatch.setattr(od, "SIMPLIFY_ENABLED", True)
    monkeypatch.setattr(od, "FLEET", {"cpu": None, "mem_gb": None, "disk_gb": None})
    root = layout.Root(base)
    seed = root.data / "sources" / "tw-extract" / "tasks" / "tw_a"
    for rel, text in SEED.items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(text)
    (seed / "instruction.md.bak-1").write_text("pre-canary text\n")
    row = {"prompt": SEED["instruction.md"], "label": "tw_a",
           "metadata": {"instance_id": "tw_a", "rev": 0, "daytona_cpu": 1,
                        "daytona_mem_gb": 2, "daytona_disk_gb": 2,
                        **({"tmax": tmax} if tmax else {})}}
    root.mix.publish([json.dumps(row)])
    return root


def _signal(root: layout.Root, *, run: str = RUN, task: str = "tw_a", group: int = 7,
            rev: int = 0, direction: str = "harder", created: str = "20260904-183012Z",
            n: int = 2) -> str:
    """A run with `n` rollout records and the signal that names them; returns
    the signal id."""
    r = root.run(run)
    attempts = []
    for i in range(n):
        p = r.rollout_record(task, group, i)
        rollout_record.write_record(
            p, {"task": task, "rev": rev, "run": run, "group": group, "rollout": i,
                "reward": 1.0 if direction == "harder" else 0.0, "turns": 1},
            [{"turn": 1, "keystrokes": ["ls /app\n"], "output": ""}])
        attempts.append(str(p.relative_to(r.path)))
    solved = n if direction == "harder" else 0
    layout.write_json_atomic(r.signal(task, group), {
        "task": task, "rev": rev, "run": run, "group": group, "direction": direction,
        "solved": solved, "total": n, "created": created, "attempts": attempts})
    return layout.signal_id(run, task, group)


class _Seen(list):
    """The process_one calls, in order; `.rows` is what the fold asked the row
    builder for."""

    rows: list[dict]


def _stub(monkeypatch, status: str = "accepted", **extra) -> _Seen:
    """process_one as the loop sees it: edits the package the way an agent
    would (plus the harness files), returns the record."""
    seen = _Seen()
    seen.rows = []

    def fake(rewrite, signal, *, job, seed_dir, resources=None, history=None):
        seen.append({"rewrite": rewrite, "signal": signal, "job": job, "seed_dir": seed_dir,
                     "resources": resources, "history": history})
        (rewrite.package / "instruction.md").write_text("harder\n")
        (rewrite.package / "AGENTS.md").write_text("role")
        (rewrite.package / "run").mkdir(exist_ok=True)
        (rewrite.package / "run" / "checks.jsonl").write_text('{"verdict": "pass"}\n')
        rec = {"status": status, "stage": "daytona_oracle", "operator": "container_build_alignment",
               "verdicts": VERDICTS,
               "resources": {"cpu": 2, "mem_gb": 4, "disk_gb": 2, "source": "measured:loop_probe",
                             "measured": {"mem_peak_mb": 3000}, "floor": {}}}
        rec.update(extra)
        return rec

    monkeypatch.setattr(od.fb, "process_one", fake)

    def to_row(d, *, task_id=None, inject_agent_runtime=True, pretest=None, protected=None):
        # As pack.to_row: the identity is the caller's, since the directory
        # is `package` here and `r<N>` once renamed; the hook is the caller's
        # too, and lands on the row's grading payload; so are the protected
        # lists (the real to_row lets tests/protected_paths.json override them).
        tid = task_id or Path(d).name
        text = (Path(d) / "instruction.md").read_text()
        seen.rows.append({"dir": d, "pretest": pretest, "protected": protected})
        tmax = {"test_sh": "echo 1\n"}
        if pretest and pretest[0]:
            tmax.update({"pre_test_sh": pretest[0], "pretest_env_identity": pretest[1]})
        if protected is not None:
            if protected.paths:
                tmax["protected_paths"] = list(protected.paths)
            if protected.cmds:
                tmax["protected_cmds"] = list(protected.cmds)
        return {"prompt": text, "label": tid,
                "metadata": {"instance_id": tid, "problem_statement": text, "tmax": tmax}}

    monkeypatch.setattr(od.pack, "to_row", to_row)
    return seen


def _ledger(root: layout.Root) -> list[dict]:
    return layout.read_jsonl(root.evolution.ledger)


def test_round_materializes_r0_handles_the_signal_and_folds_r1(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root)
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert (r["handled"], r["accepted"], r["mix_version"]) == (1, 1, 2), r
    task = root.evolution.task("tw_a")
    # r0 is the seed, copied once, without the backup the pool stripped.
    assert (task.rev(0) / "instruction.md").read_text() == SEED["instruction.md"]
    assert not (task.rev(0) / "instruction.md.bak-1").exists()
    # process_one got the rewrite, the input revision, the training box and
    # the (empty) operator history.
    call = seen[0]
    assert call["job"] == "harder" and call["seed_dir"] == task.rev(0)
    assert call["resources"] == {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}
    assert call["history"] == ({}, {})
    # Accepted: the package became r1 without the harness files, and the
    # rewrite directory keeps the record.
    rewrites = task.rewrite_dirs()
    assert len(rewrites) == 1 and rewrites[0].path.name.endswith("--harder")
    rw = rewrites[0]
    assert not rw.package.exists()
    assert (task.rev(1) / "instruction.md").read_text() == "harder\n"
    assert not (task.rev(1) / "AGENTS.md").exists()
    assert not (task.rev(1) / "run").exists() and not (task.rev(1) / "traces").exists()
    meta = json.loads(rw.meta.read_text())
    assert meta["status"] == "accepted" and meta["result_rev"] == 1
    assert meta["input_rev"] == 0 and meta["signal"] == sid and meta["job"] == "harder"
    assert meta["operator"] == "container_build_alignment"
    assert meta["resources"]["cpu"] == 2 and meta["verdicts"] == VERDICTS
    assert meta["finished"] >= meta["started"] and meta["sessions"] == []
    # The mix moved to v2 with the row at rev 1, sized from the measurement.
    version, path = root.mix.live_version()
    assert version == 2 and path.name.startswith("v0002--")
    manifest = json.loads(root.mix.manifest_of(path).read_text())
    assert manifest["parent_version"] == 1 and manifest["rows"] == 1
    row = json.loads(root.mix.live.read_text())
    assert row["label"] == "tw_a" and row["metadata"]["instance_id"] == "tw_a"
    assert row["metadata"]["rev"] == 1 and row["metadata"]["problem_statement"] == "harder\n"
    assert (row["metadata"]["daytona_cpu"], row["metadata"]["daytona_mem_gb"],
            row["metadata"]["daytona_disk_gb"]) == (2, 4, 2)
    # Lineage: a fold line and a rewrite index line.
    events = layout.read_jsonl(task.lineage)
    fold = next(e for e in events if e["event"] == "fold")
    assert (fold["from_rev"], fold["to_rev"], fold["mix_version"]) == (0, 1, 2)
    assert fold["rewrite"] == f"rewrites/{rw.path.name}"
    index = next(e for e in events if e["event"] == "rewrite")
    assert index["status"] == "accepted" and index["input_rev"] == 0
    # The ledger line, last, closes the signal.
    lines = _ledger(root)
    assert len(lines) == 1
    assert lines[0]["signal"] == sid and lines[0]["outcome"] == "handled"
    assert lines[0]["rewrite"] == f"tasks/tw_a/rewrites/{rw.path.name}"
    assert (lines[0]["task"], lines[0]["rev"], lines[0]["run"], lines[0]["group"],
            lines[0]["direction"]) == ("tw_a", 0, RUN, 7, "harder")
    # The signal file was never touched.
    assert root.run(RUN).signal("tw_a", 7).exists()
    # status.json from the files.
    status = od.rebuild_status(root)
    assert status["mix_version"] == 2 and status["pending"] == 0
    assert status["handled"] == 1 and status["accepted"] == 1
    assert status["rewrites_running"] == 0 and status["rejected"] == {}
    assert json.loads(root.evolution.status.read_text()) == status
    # A second round finds nothing: the ledger closed the signal.
    assert od.run_round(root, workers=1)["reason"] == "no signals"
    assert len(seen) == 1


def test_rejected_rewrite_keeps_its_package_and_its_hardlinked_traces(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    _stub(monkeypatch, status="rejected", stage="step_size", reason="one rung")

    r = od.run_round(root, workers=1)

    assert r["counts"] == {"rejected": 1} and r["mix_version"] is None
    task = root.evolution.task("tw_a")
    rw = task.rewrite_dirs()[0]
    assert rw.package.is_dir() and not task.rev(1).exists()
    # The records are the run's, by inode.
    run = root.run(RUN)
    for i in (1, 2):
        linked = rw.traces / f"attempt-{i:02d}.jsonl"
        assert linked.stat().st_ino == run.rollout_record("tw_a", 7, i - 1).stat().st_ino
    meta = json.loads(rw.meta.read_text())
    assert meta["status"] == "rejected" and meta["stage"] == "step_size"
    assert meta["reason"] == "one rung" and meta["finished"]
    assert [e["event"] for e in layout.read_jsonl(task.lineage)] == ["rewrite"]
    assert _ledger(root)[0]["outcome"] == "handled"
    assert root.mix.live_version()[0] == 1
    status = od.rebuild_status(root)
    assert status["rejected"] == {"step_size": 1} and status["accepted"] == 0


def test_an_unreadable_signal_is_junk_once_it_is_old_enough(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    run = root.run("tmax-9b--20260904-190000Z")
    stale = run.signal("tw_a", 3)
    stale.parent.mkdir(parents=True)
    stale.write_text("{not json")
    old = time.time() - 2 * od.FRESH_SEC
    os.utime(stale, (old, old))
    fresh = run.signal("tw_a", 4)
    fresh.write_text("")
    incomplete = run.signal("tw_a", 5)
    incomplete.write_text(json.dumps({"task": "tw_a", "rev": 0}))
    seen = _stub(monkeypatch)

    od.run_round(root, workers=1)

    lines = {l["signal"]: l for l in _ledger(root)}
    assert lines[layout.signal_id(run.name, "tw_a", 3)]["outcome"] == "junk"
    assert "unreadable" in lines[layout.signal_id(run.name, "tw_a", 3)]["reason"]
    assert lines[layout.signal_id(run.name, "tw_a", 3)]["group"] == 3
    assert lines[layout.signal_id(run.name, "tw_a", 5)]["outcome"] == "junk"
    assert "lacks" in lines[layout.signal_id(run.name, "tw_a", 5)]["reason"]
    assert layout.signal_id(run.name, "tw_a", 4) not in lines      # fresh: retried later
    assert seen == []
    assert stale.exists() and fresh.exists()                        # nothing moved
    assert od.rebuild_status(root)["junk"] == 2


def test_a_task_without_a_seed_is_junk(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root, task="nope")
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert r["junk"] == 1 and seen == []
    line = _ledger(root)[0]
    assert line["signal"] == sid and line["outcome"] == "junk"
    assert "data/sources" in line["reason"]
    assert not root.evolution.task("nope").rev(0).exists()


def test_easier_is_deferred_while_the_switch_is_off_and_replayed_when_on(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root, direction="easier")
    seen = _stub(monkeypatch)
    monkeypatch.setattr(od, "SIMPLIFY_ENABLED", False)

    r = od.run_round(root, workers=1)
    assert r["deferred"] == 1 and seen == []
    assert [l["outcome"] for l in _ledger(root)] == ["deferred"]
    # Off: a deferred signal is closed, not re-lined every round.
    assert od.run_round(root, workers=1)["reason"] == "no signals"
    assert len(_ledger(root)) == 1
    assert od.rebuild_status(root)["deferred"] == 1

    monkeypatch.setattr(od, "SIMPLIFY_ENABLED", True)
    assert od.rebuild_status(root)["pending"] == 1
    r = od.run_round(root, workers=1)
    assert r["handled"] == 1 and seen[0]["job"] == "easier"
    lines = _ledger(root)
    assert [l["outcome"] for l in lines] == ["deferred", "handled"]
    assert lines[1]["signal"] == sid
    # Handled now: the latest line wins in the status.
    status = od.rebuild_status(root)
    assert status["deferred"] == 0 and status["handled"] == 1


def test_one_signal_per_task_the_newest_at_the_current_rev(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    older = _signal(root, group=7, created="20260904-183012Z")
    newer = _signal(root, group=8, created="20260904-183500Z")
    stale = _signal(root, group=9, rev=1, created="20260904-184000Z")
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert r["handled"] == 1 and r["superseded"] == 2
    assert len(seen) == 1 and seen[0]["signal"]["group"] == 8
    lines = {l["signal"]: l for l in _ledger(root)}
    assert lines[newer]["outcome"] == "handled"
    assert lines[older]["outcome"] == "superseded" and newer in lines[older]["reason"]
    assert lines[stale]["outcome"] == "superseded" and "current rev 0" in lines[stale]["reason"]
    # The task is at r1 now; a late signal about rev 0 is superseded too.
    late = _signal(root, group=10, rev=0, created="20260904-190000Z")
    od.run_round(root, workers=1)
    assert {l["signal"]: l for l in _ledger(root)}[late]["outcome"] == "superseded"
    assert len(seen) == 1


def test_limit_leaves_the_rest_pending_rather_than_superseded(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    seed_b = root.data / "sources" / "tw-extract" / "tasks" / "tw_b"
    for rel, text in SEED.items():
        (seed_b / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed_b / rel).write_text(text)
    _signal(root, task="tw_a", group=1)
    _signal(root, task="tw_a", group=2, created="20260904-190000Z")
    _signal(root, task="tw_b", group=3)
    _stub(monkeypatch, status="kept", stage="agent", reason="operator-misfit")

    od.run_round(root, workers=1, limit=1)

    lines = _ledger(root)
    # tw_a sorts first: its newest was handled and its older one superseded;
    # tw_b was not reached and has no line, so it is still pending.
    assert {l["outcome"] for l in lines} == {"handled", "superseded"}
    assert all(l["task"] == "tw_a" for l in lines)
    assert od.rebuild_status(root)["pending"] == 1


def test_dry_round_writes_only_the_rewrite_directory(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    _stub(monkeypatch)

    r = od.run_round(root, workers=1, dry=True)

    assert r["counts"] == {"accepted": 1} and r["mix_version"] is None
    task = root.evolution.task("tw_a")
    rw = task.rewrite_dirs()[0]
    meta = json.loads(rw.meta.read_text())
    assert meta["dry"] is True and meta["status"] == "accepted" and meta["result_rev"] is None
    assert rw.package.is_dir() and (rw.traces / "attempt-01.jsonl").exists()
    assert not task.rev(1).exists()
    assert not root.evolution.ledger.exists() and not task.lineage.exists()
    assert root.mix.live_version()[0] == 1
    # Dry rewrites are not counted, and the signal is still pending.
    status = od.rebuild_status(root)
    assert status["accepted"] == 0 and status["pending"] == 1
    assert od.operator_history(root) == ({}, {})


def test_replay_handles_a_closed_signal_dry(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root)
    seen = _stub(monkeypatch)
    od.run_round(root, workers=1)
    assert len(_ledger(root)) == 1

    r = od.run_round(root, signal=sid)

    assert r["handled"] == 1 and len(seen) == 2
    rewrites = root.evolution.task("tw_a").rewrite_dirs()
    assert len(rewrites) == 2
    replay = json.loads(rewrites[-1].meta.read_text())
    assert replay["dry"] is True and replay["signal"] == sid and replay["input_rev"] == 0
    assert seen[1]["seed_dir"] == root.evolution.task("tw_a").rev(0)
    assert len(_ledger(root)) == 1 and root.mix.live_version()[0] == 2


def test_operator_history_counts_accepted_rewrites_only(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    task = root.evolution.task("tw_a")
    for stamp_, status, extra in (
            ("20260904-100000Z", "accepted", {}),
            ("20260904-110000Z", "rejected", {}),
            ("20260904-120000Z", "accepted", {"dry": True})):
        rw = task.rewrite("harder", stamp_)
        layout.write_json_atomic(rw.meta, {"task": "tw_a", "status": status,
                                           "operator": "container_build_alignment", **extra})
    assert od.operator_history(root) == ({"container_build_alignment": 1},
                                         {"environment_runtime_substrate": 1})


def test_lineage_snapshot_commits_records_and_never_packages_or_sessions(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    _stub(monkeypatch, status="rejected", stage="oracle", reason="boom")
    od.run_round(root, workers=1)
    od.rebuild_status(root)
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    session = rw.session("agent")
    session.codex_home.mkdir(parents=True)
    layout.write_json_atomic(session.meta, {"kind": "agent"})
    (session.codex_home / "rollout.jsonl").write_text("{}\n")

    od._snapshot_lineage(root, "test snapshot")

    git_dir = root.evolution.path / ".git"
    tracked = subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={root.path}", "ls-files"],
        cwd=root.path, check=True, capture_output=True, text=True).stdout.splitlines()
    assert set(tracked) == {
        "evolution/ledger.jsonl", "evolution/status.json",
        "evolution/tasks/tw_a/lineage.jsonl",
        f"evolution/tasks/tw_a/rewrites/{rw.path.name}/rewrite.json",
        str(root.mix.manifest_of(root.mix.live_version()[1]).relative_to(root.path)),
    }
    assert not (root.path / ".git").exists()
    assert not any("package" in p or "sessions" in p or "traces" in p for p in tracked)


def test_training_box_reads_the_row_then_the_fleet_default(monkeypatch) -> None:
    monkeypatch.setattr(od, "FLEET", {"cpu": None, "mem_gb": None, "disk_gb": None})
    assert od.training_box("t", {"t": {"cpu": 1, "mem_gb": 2, "disk_gb": 2}}) == {
        "cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}
    # A row declaring nothing, with no fleet default in the env: the harness
    # default applies, and the source says so rather than inventing a number.
    assert od.training_box("t", {}) == {"cpu": None, "mem_gb": None, "disk_gb": None,
                                        "source": "harness_default"}
    monkeypatch.setattr(od, "FLEET", {"cpu": 1, "mem_gb": 2, "disk_gb": 2})
    assert od.training_box("t", {"t": {"mem_gb": 4}}) == {
        "cpu": 1, "mem_gb": 4, "disk_gb": 2, "source": "row+fleet_default"}


def test_strip_harness_leaves_the_package(tmp_path) -> None:
    pkg = tmp_path / "package"
    for rel in ("instruction.md", "AGENTS.md", "sandbox", "run/checks.jsonl",
                "traces/attempt-01.jsonl", "environment/fixture.csv",
                "tests/__pycache__/x.pyc"):
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text("x")
    od.strip_harness(pkg)
    assert sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file()) == [
        "environment/fixture.csv", "instruction.md"]


HOOK = "set -u\nexit 0\n"
STAMP = "image:hamishi740/swerl-tmax-v3:37a79d0fd9b9"


def test_round_carries_the_rows_pin_hook_through_the_rewrite_and_the_fold(
        tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch, tmax={"test_sh": "echo 1\n", "pre_test_sh": HOOK,
                                              "pretest_env_identity": STAMP})
    _signal(root)
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert (r["handled"], r["accepted"], r["mix_version"]) == (1, 1, 2), r
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    # The loop snapshots the row's hook beside rewrite.json, outside package/,
    # where the probe reads it and the agent cannot edit it.
    assert layout.read_pretest(rw.pretest) == (HOOK, STAMP)
    # The fold hands the same hook to the row builder, which re-derives this
    # package's environment identity; the row keeps grading with the pins.
    assert [r["pretest"] for r in seen.rows] == [(HOOK, STAMP)]
    tm = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    assert tm["pre_test_sh"] == HOOK and tm["pretest_env_identity"] == STAMP


def test_a_row_without_a_hook_folds_without_one(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    seen = _stub(monkeypatch)

    assert od.run_round(root, workers=1)["accepted"] == 1
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    assert not rw.pretest.exists()
    assert [r["pretest"] for r in seen.rows] == [None]
    assert [r["protected"] for r in seen.rows] == [None]  # no lists on the row: none passed
    tm = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    assert not {"pre_test_sh", "protected_paths", "protected_cmds"} & set(tm)


PATHS = ["/app/pinned", "/app/data dir/model.bin", "tests"]
CMDS = ['sqlite3 /app/db "select count(*) from t where n=\'x\'"']


def test_fold_carries_the_rows_protected_lists_when_the_package_ships_none(
        tmp_path, monkeypatch) -> None:
    """The mix row a rewrite descends from carries protected lists; the rewrite's
    package ships no tests/protected_paths.json. The fold hands the row's lists to
    the row builder (which lets a package file override them), so the folded
    revision keeps grading by the same baseline -- the hole the loop PR closes."""
    root = _root(tmp_path, monkeypatch, tmax={"test_sh": "echo 1\n",
                                              "protected_paths": PATHS, "protected_cmds": CMDS})
    _signal(root)
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert (r["handled"], r["accepted"], r["mix_version"]) == (1, 1, 2), r
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    assert not (root.evolution.task("tw_a").rev(1) / "tests" / "protected_paths.json").exists()
    # No hook on this row, but the lists travel in the same snapshot: the hook readers see
    # None, the list reader sees the parent's lists (what the probe and the tool validate with).
    assert layout.read_pretest(rw.pretest) is None
    assert layout.read_protected_lists(rw.pretest) == {"protected_paths": PATHS, "protected_cmds": CMDS}
    assert [r["protected"] for r in seen.rows] == [od.pack.Protected(PATHS, CMDS)]  # as LISTS
    tm = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    assert tm["protected_paths"] == PATHS and tm["protected_cmds"] == CMDS
