#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""offline_drive.sh's rounds with one systemd user unit per rewrite (DISPATCH=unit).

The default drive runs each round as one `evolve_ondella.py --once --workers N`
process whose workers are threads, and whose codex sessions and sandboxes are
its children inside the drive's own unit: `systemctl stop` of the drive, or a
restart to change N, kills every rewrite in flight. Here each pending task is
handed to a unit of its own,

    bench-rw-<root name>-<task>    bash offline_rewrite.sh  ->  evolve_ondella.py --task <task>

which the drive's unit does not contain, so the drive can stop, restart or
exit while rewrites carry on.

    offline_dispatch.py adopt --root <root> --evo <arm evolution dir>
    offline_dispatch.py round --root <root> --label <k> --workers <default> \
        --env-file <root>/tmp/rw-unit.env0 --rewrite-sh <bench>/offline_rewrite.sh

`adopt` runs once when the drive starts: it lists this root's live units and
marks `interrupted` what the others left `running` (finalize_interrupted_traces
--except-task <each live task>), so offline_stage.py --only-failed sees them as
the infrastructure losses they are. The live units are left alone.

`round` is the default drive's `--once` round: every task with a pending
signal (a signal file with no ledger line) gets one rewrite. A tick lists the
live units, the pending tasks and `<root>/workers`; it starts units for pending
tasks without one, newest signal first, while fewer than `workers` units are
live. `workers` is re-read every tick, so writing a new integer into it takes
effect without a restart; 0 pauses dispatching (live units go on). A task is
started at most once per round, as `--once` hands out one signal per task: one
that leaves its signal pending (handling raised before a rewrite existed, the
unit refused to start) waits for the next round, as before. The round ends when
no unit of the root is live and nothing is left to start. Units already live
when the round starts (adopted) count against `workers`.

Staging is unchanged: offline_drive.sh runs offline_stage.py --skip-accepted
--only-failed --max-attempts --attempts-since between rounds, after a round has
drained, so MAX_ATTEMPTS, MAX_ROUNDS and ATTEMPTS_SINCE mean what they mean for
the default drive.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

_EVO = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVO.parent))
import layout  # noqa: E402

UNIT_PREFIX = "bench-rw-"
# What a unit name may hold without escaping (systemd also allows ':' and '\').
NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
LIVE_STATES = frozenset({"active", "activating", "deactivating", "reloading", "refreshing"})
POLL_SEC = 30.0


def unit_name(root_name: str, task: str) -> str:
    return f"{UNIT_PREFIX}{root_name}-{task}"


def _description(root: Path, task: str) -> str:
    return f"evolve rewrite {task} in {root}"


def parse_units(text: str, root: Path) -> dict[str, str]:
    """{task: active state} from `systemctl --user list-units --plain
    --no-legend` lines, for this root's rewrite units. The description names
    the root, so a unit of another root whose name starts with this one's
    (`<root>-b-<task>`) is not taken for one of ours."""
    prefix = UNIT_PREFIX + root.name + "-"
    suffix = " in " + str(root)
    out = {}
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        unit, _load, active, _sub, desc = parts
        if not unit.startswith(prefix) or not unit.endswith(".service"):
            continue
        if not desc.endswith(suffix):
            continue
        task = unit[len(prefix) : -len(".service")]
        if desc != _description(root, task):
            continue
        out[task] = active
    return out


def systemctl_units(root: Path) -> str:
    return subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", "--no-pager",
         f"{UNIT_PREFIX}{root.name}-*"],
        check=True, capture_output=True, text=True,
    ).stdout


def live_tasks(root: Path, list_units: Callable[[Path], str] = systemctl_units) -> set[str]:
    return {t for t, st in parse_units(list_units(root), root).items() if st in LIVE_STATES}


def pending_tasks(root: Path) -> dict[str, str]:
    """{task: newest `created` among its pending signals}. Pending is what the
    loop's _pending calls it: a signal file whose id has no ledger line at all."""
    r = layout.Root(root)
    closed = set()
    for line in layout.read_jsonl(r.evolution.ledger):
        if line.get("signal"):
            closed.add(line["signal"])
    out: dict[str, str] = {}
    for run in r.run_dirs():
        for p in run.signal_files():
            if f"{run.name}/{p.stem}" in closed:
                continue
            try:
                data = json.loads(p.read_text())
                task, created = str(data["task"]), str(data.get("created", ""))
            except (OSError, ValueError, KeyError, TypeError):
                # Junk: the loop closes it with a `junk` line once it reads it.
                task, created = p.stem.rpartition("--g")[0] or p.stem, ""
            if created >= out.get(task, ""):
                out[task] = created
    return out


def read_workers(root: Path, default: int) -> int:
    """<root>/workers as an integer >= 0; `default` when absent or unreadable."""
    try:
        n = int((root / "workers").read_text().strip())
    except (OSError, ValueError):
        return default
    return n if n >= 0 else default


class Dispatcher:
    """One round of units over a root. ``list_units``, ``start`` and ``sleep``
    are the seams the tests replace."""

    def __init__(
        self,
        root: Path,
        *,
        workers: int,
        env_file: Path,
        rewrite_sh: Path,
        log: Callable[[str], None],
        list_units: Callable[[Path], str] = systemctl_units,
        start: Callable[[list[str]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_sec: float = POLL_SEC,
    ) -> None:
        if not NAME_RE.fullmatch(root.name):
            raise SystemExit(f"root name {root.name!r} cannot be part of a unit name")
        self.root, self.default_workers = root, workers
        self.env_file, self.rewrite_sh = env_file, rewrite_sh
        self.log, self.list_units, self.sleep, self.poll_sec = log, list_units, sleep, poll_sec
        self.start = start or (lambda cmd: subprocess.run(cmd, check=True, capture_output=True, text=True))

    def live(self) -> set[str]:
        return live_tasks(self.root, self.list_units)

    def start_cmd(self, task: str) -> list[str]:
        return [
            "systemd-run", "--user", f"--unit={unit_name(self.root.name, task)}", "--collect",
            f"--working-directory={self.root}", f"--description={_description(self.root, task)}",
            f"--setenv=RW_TASK={task}", f"--setenv=RW_ENV_FILE={self.env_file}",
            "bash", str(self.rewrite_sh),
        ]

    def round(self, label: str) -> dict:
        dispatched: set[str] = set()
        refused: list[str] = []
        last = None
        while True:
            live = self.live()
            pending = pending_tasks(self.root)
            workers = read_workers(self.root, self.default_workers)
            todo = [
                t
                for t in sorted(pending, key=lambda t: (pending[t], t), reverse=True)
                if t not in live and t not in dispatched
            ]
            if not live and not todo:
                break
            start_now = todo[: max(0, workers - len(live))]
            for task in start_now:
                dispatched.add(task)
                if not NAME_RE.fullmatch(task):
                    refused.append(task)
                    self.log(f"round {label}: task {task!r} cannot be a unit name; left pending")
                    continue
                try:
                    self.start(self.start_cmd(task))
                except subprocess.CalledProcessError as e:
                    refused.append(task)
                    self.log(f"round {label}: {unit_name(self.root.name, task)} did not start: "
                             f"{(e.stderr or '').strip()[:300]}")
                    continue
                live.add(task)
                self.log(f"round {label}: started {unit_name(self.root.name, task)}")
            state = (len(live), workers, len(todo) - len(start_now))
            if state != last:
                self.log(f"round {label}: live={state[0]} workers={workers} waiting={state[2]} "
                         f"dispatched={len(dispatched)}")
                last = state
            self.sleep(self.poll_sec)
        left = sorted(set(pending_tasks(self.root)) & dispatched)
        summary = {"dispatched": len(dispatched), "refused": refused, "still_pending": left}
        self.log(f"round {label} done: {json.dumps(summary)}")
        return summary


def adopt(root: Path, evo: Path, log: Callable[[str], None],
          list_units: Callable[[Path], str] = systemctl_units,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> set[str]:
    """The live units of this root, left running; everything else's leftover
    `running` records marked interrupted by the arm's own finalizer."""
    live = live_tasks(root, list_units)
    log(f"adopt: {len(live)} live rewrite units: {sorted(live)}")
    cmd = [sys.executable, str(evo / "finalize_interrupted_traces.py")]
    for task in sorted(live):
        cmd += ["--except-task", layout.safe(task)]
    p = run(cmd, capture_output=True, text=True, env={**os.environ, "TRL_BASE": str(root)})
    log(f"adopt: finalize exit={p.returncode} {(p.stdout or '').strip().splitlines()[-1:] or ''} "
        f"{(p.stderr or '').strip()[-300:]}")
    if p.returncode != 0:
        raise SystemExit(f"finalize_interrupted_traces failed ({p.returncode}); not dispatching")
    return live


def check_arm(evo: Path) -> None:
    """The arm's loop has to know --task and its finalizer --except-task (the
    loop commit cherry-picked onto the arm); a loop without them would run a
    whole-root round per unit."""
    if '"--task"' not in (evo / "evolve_ondella.py").read_text():
        raise SystemExit(f"{evo}/evolve_ondella.py has no --task; DISPATCH=unit needs the loop commit")
    if '"--except-task"' not in (evo / "finalize_interrupted_traces.py").read_text():
        raise SystemExit(f"{evo}/finalize_interrupted_traces.py has no --except-task")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adopt")
    a.add_argument("--root", required=True, type=Path)
    a.add_argument("--evo", required=True, type=Path)
    r = sub.add_parser("round")
    r.add_argument("--root", required=True, type=Path)
    r.add_argument("--label", required=True)
    r.add_argument("--workers", required=True, type=int, help="when <root>/workers is absent")
    r.add_argument("--env-file", required=True, type=Path)
    r.add_argument("--rewrite-sh", required=True, type=Path)
    r.add_argument("--poll-sec", type=float, default=POLL_SEC)
    for p in (a, r):
        p.add_argument("--log", required=True, type=Path, help="the drive's log, appended to")
    args = ap.parse_args()
    root = args.root.resolve()

    def log(msg: str) -> None:
        with open(args.log, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")

    if args.cmd == "adopt":
        check_arm(args.evo)
        live = adopt(root, args.evo, log)
        print(f"adopted {len(live)}")
        return
    summary = Dispatcher(root, workers=args.workers, env_file=args.env_file,
                         rewrite_sh=args.rewrite_sh, log=log, poll_sec=args.poll_sec).round(args.label)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
