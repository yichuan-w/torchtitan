# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""offline_dispatch.py: a DISPATCH=unit round starts one unit per pending task,
counts adopted units against <root>/workers, re-reads that file every tick,
starts a task once per round, and composes with offline_stage.py into the same
attempt counts as the default drive."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import offline_dispatch as od
import pytest

STAGE = Path(__file__).resolve().parents[1] / "offline_stage.py"


class FakeSystemd:
    """list-units / systemd-run for one root; ``on_tick`` runs at each sleep."""

    def __init__(self, root: Path, live: tuple[str, ...] = ()) -> None:
        self.root = root
        self.units: dict[str, str] = {t: "active" for t in live}
        self.started: list[str] = []
        self.on_tick = lambda n: None
        self.ticks = 0

    def list_units(self, root: Path) -> str:
        assert root == self.root
        return "".join(
            f"{od.unit_name(root.name, t)}.service loaded {st} running evolve rewrite {t} in {root}\n"
            for t, st in sorted(self.units.items())
        )

    def start(self, cmd: list[str]) -> None:
        unit = next(a for a in cmd if a.startswith("--unit=")).split("=", 1)[1]
        task = next(a for a in cmd if a.startswith("--setenv=RW_TASK=")).split("=", 2)[2]
        assert unit == od.unit_name(self.root.name, task)
        assert "--collect" in cmd and cmd[-2:] == ["bash", "/bench/offline_rewrite.sh"]
        self.units[task] = "active"
        self.started.append(task)

    def sleep(self, _sec: float) -> None:
        self.ticks += 1
        assert self.ticks < 500, "round did not end"
        self.on_tick(self.ticks)

    def finish(self, task: str) -> None:
        self.units.pop(task, None)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "pool--A0-r2"
    (root / "evolution").mkdir(parents=True)
    return root


def _signal(root: Path, task: str, created: str, run: str = "src.offline1") -> str:
    sig = root / "runs" / run / "signals" / f"{task}--g1.json"
    sig.parent.mkdir(parents=True, exist_ok=True)
    sig.write_text(json.dumps({"task": task, "created": created, "group": 1}))
    return f"{run}/{task}--g1"


def _close(root: Path, sid: str) -> None:
    with open(root / "evolution" / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"signal": sid, "outcome": "handled"}) + "\n")


def _dispatcher(root: Path, sd: FakeSystemd, workers: int = 2, log=None) -> od.Dispatcher:
    return od.Dispatcher(
        root, workers=workers, env_file=root / "tmp/rw-unit.env0",
        rewrite_sh=Path("/bench/offline_rewrite.sh"), log=log or (lambda m: None),
        list_units=sd.list_units, start=sd.start, sleep=sd.sleep, poll_sec=0,
    )


def test_units_of_a_root_whose_name_extends_ours_are_not_ours(tmp_path):
    root = tmp_path / "X--a-r1"
    other = tmp_path / "X--a-r1-b"
    text = (
        f"bench-rw-X--a-r1-t1.service loaded active running evolve rewrite t1 in {root}\n"
        f"bench-rw-X--a-r1-b-t2.service loaded active running evolve rewrite t2 in {other}\n"
        f"bench-rw-X--a-r1-t3.service loaded failed failed evolve rewrite t3 in {root}\n"
    )
    assert od.parse_units(text, root) == {"t1": "active", "t3": "failed"}
    assert od.live_tasks(root, lambda r: text) == {"t1"}


def test_adopted_units_count_against_workers_and_are_not_restarted(tmp_path):
    root = _root(tmp_path)
    sids = {t: _signal(root, t, c) for t, c in (("a", "1"), ("b", "2"), ("c", "3"))}
    sd = FakeSystemd(root, live=("a",))

    def tick(n):
        if n == 1:
            # Only one free slot next to the adopted unit: the newest signal, c.
            assert sd.started == ["c"]
            for t in ("a", "c"):
                sd.finish(t)
                _close(root, sids[t])
        elif n == 2:
            assert sd.started == ["c", "b"]
            sd.finish("b")
            _close(root, sids["b"])

    sd.on_tick = tick
    summary = _dispatcher(root, sd, workers=2).round("1")
    assert sd.started == ["c", "b"], "the adopted task was started again"
    assert summary == {"dispatched": 2, "refused": [], "still_pending": []}


def test_the_workers_file_is_read_every_tick(tmp_path):
    root = _root(tmp_path)
    sids = {t: _signal(root, t, str(i)) for i, t in enumerate("abcdef")}
    (root / "workers").write_text("1\n")
    sd = FakeSystemd(root)
    seen = []

    def tick(n):
        seen.append(len(sd.units))
        if n == 1:
            (root / "workers").write_text("3\n")
        elif n == 2:
            (root / "workers").write_text("0\n")  # pause: live units go on
            for t in list(sd.units):
                sd.finish(t)
                _close(root, sids[t])
        elif n == 4:
            (root / "workers").write_text("not a number\n")  # falls back to --workers
        elif n >= 5:
            for t in list(sd.units):
                sd.finish(t)
                _close(root, sids[t])

    sd.on_tick = tick
    _dispatcher(root, sd, workers=2).round("1")
    # 1 live, then 3, then paused at 0 for two ticks, then 2 (the default).
    assert seen[:5] == [1, 3, 0, 0, 2], seen
    assert sorted(sd.started) == list("abcdef") and len(sd.started) == 6


def test_a_task_is_started_once_per_round(tmp_path):
    """A unit that ends leaving its signal open (handling raised before a
    rewrite existed) is not started again in the round, as --once does not
    hand the signal out again; the round ends and reports it."""
    root = _root(tmp_path)
    _signal(root, "stuck", "1")
    sd = FakeSystemd(root)
    sd.on_tick = lambda n: sd.finish("stuck")
    summary = _dispatcher(root, sd).round("1")
    assert sd.started == ["stuck"]
    assert summary["still_pending"] == ["stuck"]


def test_a_unit_that_does_not_start_is_reported_and_not_retried(tmp_path):
    root = _root(tmp_path)
    _signal(root, "a", "1")
    sd = FakeSystemd(root)
    calls = []

    def refuse(cmd):
        calls.append(cmd)
        raise subprocess.CalledProcessError(1, cmd, stderr="Unit already exists")

    d = _dispatcher(root, sd)
    d.start = refuse
    assert d.round("1")["refused"] == ["a"] and len(calls) == 1


def test_adopt_finalizes_everything_but_the_live_tasks(tmp_path):
    root = _root(tmp_path)
    sd = FakeSystemd(root, live=("t2", "t1"))
    sd.units["t3"] = "failed"
    ran = []

    def run(cmd, **kw):
        ran.append((cmd, kw["env"]["TRL_BASE"]))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"outcome": "finalize_summary"}\n', stderr="")

    live = od.adopt(root, Path("/arm/evolution"), lambda m: None, list_units=sd.list_units, run=run)
    assert live == {"t1", "t2"}
    [(cmd, base)] = ran
    assert cmd[1] == "/arm/evolution/finalize_interrupted_traces.py"
    assert cmd[2:] == ["--except-task", "t1", "--except-task", "t2"]
    assert base == str(root)


def test_check_arm_refuses_a_loop_without_task_mode(tmp_path):
    (tmp_path / "evolve_ondella.py").write_text('ap.add_argument("--once")\n')
    (tmp_path / "finalize_interrupted_traces.py").write_text('"--except-task"\n')
    with pytest.raises(SystemExit, match="no --task"):
        od.check_arm(tmp_path)


# ------------------------------------------------- attempts, composed with staging


def _source(tmp_path: Path, tasks: list[str]) -> tuple[Path, Path]:
    src = tmp_path / "set" / "runs" / "src"
    (src / "signals").mkdir(parents=True)
    (src / "launch.json").write_text("{}")
    for t in tasks:
        (src / "signals" / f"{t}--g1.json").write_text(json.dumps(
            {"task": t, "direction": "harder", "run": "src", "group": 1, "rev": 0,
             "solved": 12, "total": 12, "attempts": [], "created": "20261106-000000Z"}))
    sel = tmp_path / "set" / "selected.json"
    sel.write_text(json.dumps({t: {"group": 1} for t in tasks}))
    return src, sel


def _stage(root: Path, src: Path, sel: Path, k: int, since: str, max_attempts: int) -> int:
    out = subprocess.run(
        [sys.executable, str(STAGE), "--run", str(src), "--selected", str(sel), "--root", str(root),
         "--name", f"src.offline{k}", "--skip-accepted", "--only-failed",
         "--max-attempts", str(max_attempts), "--attempts-since", since],
        check=True, capture_output=True, text=True,
    ).stdout
    return int(out.split()[1])


def test_attempt_counts_match_the_default_drive(tmp_path):
    """offline_drive.sh's rounds with units: stage, drain, restage. A task that
    keeps failing gets MAX_ATTEMPTS rewrites, an accepted or kept one gets one,
    one lost to an interruption is retried within the budget, and the drive
    stops when staging finds nothing -- what the default drive's --once
    rounds give."""
    verdicts = {
        "t_fail": ["failed", "failed", "failed"],
        "t_acc": ["accepted"],
        "t_kept": ["kept"],
        "t_int": ["interrupted", "accepted"],
    }
    root = _root(tmp_path)
    src, sel = _source(tmp_path, sorted(verdicts))
    since, n = "20991231-0000", [0]
    sd = FakeSystemd(root)

    def handle(task: str) -> None:
        # What a unit's `evolve_ondella.py --task` leaves: a rewrite record and
        # the ledger line closing the staged signal.
        status = verdicts[task].pop(0)
        n[0] += 1
        rw = root / "evolution/tasks" / task / "rewrites" / f"20991231-0000{n[0]:02d}Z-harder"
        rw.mkdir(parents=True)
        (rw / "rewrite.json").write_text(json.dumps({"status": status}))
        if status == "accepted":
            (root / "evolution/tasks" / task / "r1").mkdir()
        ledger = root / "evolution/ledger.jsonl"
        closed = ledger.read_text() if ledger.exists() else ""
        for p in (root / "runs").glob(f"*/signals/{task}--g1.json"):
            sid = f"{p.parent.parent.name}/{task}--g1"
            if f'"{sid}"' not in closed:
                _close(root, sid)

    def tick(_n):
        for t in list(sd.units):
            sd.finish(t)
            handle(t)

    sd.on_tick = tick
    rounds = 0
    for k in range(1, 7):  # MAX_ROUNDS=6
        if _stage(root, src, sel, k, since, max_attempts=2) == 0:
            break
        rounds += 1
        _dispatcher(root, sd, workers=8).round(str(k))
    attempts = {t: len(list((root / "evolution/tasks" / t / "rewrites").iterdir())) for t in verdicts}
    assert attempts == {"t_fail": 2, "t_acc": 1, "t_kept": 1, "t_int": 2}
    assert rounds == 2
    assert sorted(sd.started) == sorted(["t_fail", "t_acc", "t_kept", "t_int", "t_fail", "t_int"])


def test_run_writes_the_workers_file_only_when_asked_and_sets_dispatch(tmp_path):
    import evolve_bench as eb

    root = tmp_path / "root"
    root.mkdir()
    log = eb.Log(tmp_path / "log")
    eb._write_workers(root, 8, overwrite=False, log=log)
    assert (root / "workers").read_text() == "8\n"
    (root / "workers").write_text("24\n")  # set by hand while the arm runs
    eb._write_workers(root, 8, overwrite=False, log=log)
    assert (root / "workers").read_text() == "24\n"
    eb._write_workers(root, 12, overwrite=True, log=log)  # run --workers 12
    assert (root / "workers").read_text() == "12\n"
    arm = {"env": {}, "profile": "p", "workers": 8}
    env = eb._drive_env(root, arm, eb.DRIVE_PINS, tmp_path / "run", tmp_path / "sel", "unit")
    assert env["DISPATCH"] == "unit"
    assert eb._drive_env(root, arm, eb.DRIVE_PINS, tmp_path / "run", tmp_path / "sel")["DISPATCH"] == "loop"
    assert "DISPATCH" in eb.DRIVE_OWNED
