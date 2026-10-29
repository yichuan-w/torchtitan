#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The evolve loop: signals under runs/*/signals -> one rewrite each -> the mix.

The trainer writes one signal per zero-variance group into its run directory:
a task the policy solved 0/k (too hard, make it easier) or k/k (too easy, make
it harder). This loop reads those signals, rewrites each task the feedback way
in a directory of its own, and folds what survives revalidation into the live
mix as a new version that TMaxDataset hot-reloads. It runs on the same machine
as the training that feeds it. LAYOUT.md is the contract; every path here
comes from layout.py.

A round:
  1. discover: every signal file under runs/*/signals whose id has no ledger
     line (or whose latest line is `deferred` while that direction is on).
     Nothing under a run directory is ever moved or deleted; the ledger is
     the loop's memory.
  2. choose: one signal per task, the newest whose `rev` is the task's
     current revision. Two rewrites of one revision in one round would be
     siblings racing for the same r<N+1>, and a signal about a revision that
     is no longer the task's measured a policy input that is gone; both get a
     `superseded` line and the reason. A task with a rewrite in progress is
     skipped until it ends; then its signals are judged against its new
     revision. Picks start newest signal first. A signal whose task has
     since finished another whole group at the same revision without a
     signal gets a `resolved` line instead of a rewrite: that group came out
     mixed, so the task no longer needs one.
  3. handle, concurrently across tasks: copy r<rev> to the rewrite's
     package/, hardlink the rollout records under package/traces/, snapshot
     the row's pin hook as pretest.json beside rewrite.json, run
     feedback_loop.process_one there, record the verdict in rewrite.json.
  4. fold, as soon as a rewrite is accepted: strip the harness files, rename
     package/ to r<N+1>/, rebuild the row (the replaced row's hook and size
     carried across) and publish a new mix version -- one per batch of
     rewrites that finished together, on a closer thread of its own so the
     coordinator keeps collecting rewrites and refilling slots meanwhile.
     Then the lineage line, then the ledger line, last. A round of many
     concurrent rewrites runs for hours; folding on acceptance means a loop
     stopped mid-round loses only the rewrites still in flight.
  5. refresh status.json during the round and rebuild it when the round ends;
     commit the records (never packages, sessions or traces) to the audit repo.

Observable (one log line per signal, one per fold), resumable (a signal with
no ledger line is handled again; a rewrite the loop died inside is marked by
finalize_interrupted_traces), reproducible (a rewrite directory holds the
input revision it started from, the records it read, and every session).

Usage:
  evolve_ondella.py --once                         one round, then exit
  evolve_ondella.py --interval 120 --workers 16    continuous
  evolve_ondella.py --once --dry --only <task>     handle, publish nothing
  evolve_ondella.py --signal <run>/<task>--g<N>    replay one handled signal (dry)
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import json
import logging
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evolve_codex as ec  # noqa: E402
import feedback_loop as fb  # noqa: E402
import grader_replay  # noqa: E402
from finalize_interrupted_traces import finalize_interrupted  # noqa: E402
import pack_to_dataset as pack  # noqa: E402
from recorded_solution import solution_path  # noqa: E402
import rewrite_workspace as rw_workspace  # noqa: E402
from rewrite_admission import admission_is_open  # noqa: E402
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.evolution_metrics import record_outcome  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.policy_canary import (  # noqa: E402
    content_hash,
    PolicyCanaryConfig,
    PolicyCanaryQueue,
)

log = logging.getLogger("evolve")

# The 0/k -> easier branch is switchable, because the ratchet it drives only
# turns one way. A simplify is accepted almost every time -- the revalidation
# asks whether solve.sh still passes, and rewriting the instruction cannot break
# solve.sh -- while an evolve has to survive a rebuilt verifier. Measured on
# this corpus: 693 accepted simplifies against 335 accepted evolves in a week,
# and 814 against 26 in an earlier window, with the on-mix solve rate climbing
# while the fixed eval stayed flat. Off, the too-hard tail freezes instead of
# being loosened, and the only signals that move a task are the ones asking
# for more difficulty. A signal deferred while it was off stays deferred:
# turning the switch on picks up what training produces from then on, never the
# backlog, whose revisions the pool has already moved past.
SIMPLIFY_ENABLED = os.environ.get("SWE_EVOLVE_SIMPLIFY", "0").lower() not in (
    "0",
    "false",
    "no",
)
# An accepted easier rewrite must still pass what the parent's grader passed
# (grader_replay.py): its passing training finals and the parent's and the
# original's reference solutions, replayed under the rewrite's grader. Off by
# default here; the overfit bench's rewriters turn it on.
EASIER_GRADER_REPLAY = grader_replay.enabled()
EASIER_MIN_SCORED_ATTEMPTS = int(
    os.environ.get("EVOLVE_EASIER_MIN_SCORED_ATTEMPTS", "2")
)
if EASIER_MIN_SCORED_ATTEMPTS < 2:
    raise ValueError("EVOLVE_EASIER_MIN_SCORED_ATTEMPTS must be at least 2")
# The trainer's floor for a group to signal (SWE_EVOLUTION_MIN_SCORED), and the
# endings it does not count as scored; _resolved_by must judge a group the same
# way, or a group the trainer stayed silent on cancels a live request.
TRAINER_MIN_SCORED = max(2, int(os.environ.get("SWE_EVOLUTION_MIN_SCORED", "2")))
RULE_TERMINATED_REASONS = frozenset({"format_error", "truncated"})
# What a seed copy leaves behind: backups of the pre-canary-strip instruction
# are not part of the task and would show the agent text the pool deliberately
# removed; the other two are records of an older loop's, not the package's.
SEED_IGNORE = shutil.ignore_patterns(
    "*.bak-*", ".provenance.json", ".resources.json", "__pycache__", ".git"
)
SIGNAL_KEYS = (
    "task",
    "rev",
    "run",
    "group",
    "direction",
    "solved",
    "total",
    "attempts",
)
# An unreadable signal younger than this may still be being written; LAYOUT
# has the trainer rename it into place, so this is belt and braces.
FRESH_SEC = 60
FREE_SLOT_POLL_SEC = 5.0
STATUS_REFRESH_SEC = float(os.environ.get("EVOLVE_STATUS_REFRESH_SEC", "60"))
if STATUS_REFRESH_SEC <= 0:
    raise ValueError("EVOLVE_STATUS_REFRESH_SEC must be positive")


def _env_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else None


# The fleet default: what a row declaring no daytona_* of its own gets in
# training. Read from this process's env the way the harness reads it, so it
# is the trainer's only when the launcher carries the trainer's TT_DAYTONA_*
# across; main() logs what it resolved to. On the live mix every row declares
# all three (663/663 on wd-20260903b), so this is the fallback for a rebuilt
# mix, not the common path.
FLEET = {
    "cpu": _env_int("TT_DAYTONA_CPU"),
    "mem_gb": _env_int("TT_DAYTONA_MEM_GB"),
    "disk_gb": _env_int("TT_DAYTONA_DISK_GB"),
}
ROW_KEYS = {
    "cpu": "daytona_cpu",
    "mem_gb": "daytona_mem_gb",
    "disk_gb": "daytona_disk_gb",
}


def read_declared(
    mix: Path,
    *,
    rows_out: dict[str, dict] | None = None,
) -> tuple[dict[str, dict], dict[str, tuple[str, str]], dict[str, "pack.Protected"]]:
    """One pass over the mix: per row, the daytona_* it declares and the pin
    hook it carries (metadata.tmax.pre_test_sh with its environment stamp),
    both keyed by instance id. The hook is read here because a package
    carries none: the row is its only home, and the fold rebuilds the row from
    the package."""
    resources: dict[str, dict] = {}
    pretests: dict[str, tuple[str, str]] = {}
    protecteds: dict[str, "pack.Protected"] = {}
    try:
        text = layout.MixDir(mix.parent).read_live() if mix.name == "live.jsonl" else mix.read_text()
    except FileNotFoundError:
        return resources, pretests, protecteds
    for ln in text.splitlines():
        if not ln.strip():
            continue
        row = json.loads(ln)
        md = row.get("metadata") or {}
        iid = md.get("instance_id")
        if not iid:
            continue
        if rows_out is not None:
            rows_out[iid] = row
        resources[iid] = {k: md[rk] for k, rk in ROW_KEYS.items() if rk in md}
        hook = row_pretest(md)
        if hook:
            pretests[iid] = hook
        lists = pack.Protected.from_tmax(md.get("tmax") or {})
        if lists is not None:
            protecteds[iid] = lists
    return resources, pretests, protecteds


def row_pretest(md: dict) -> tuple[str, str] | None:
    """A row's pin hook as ``(pre_test_sh, pretest_env_identity)``, None when
    it carries no check."""
    tm = md.get("tmax") or {}
    if not tm.get("pre_test_sh"):
        return None
    return str(tm["pre_test_sh"]), str(tm.get("pretest_env_identity") or "")


def _calibration_gave_up(task, rev: int) -> bool:
    """Whether a hardening of revision ``rev`` already ended in GIVE UP."""
    for previous in task.rewrite_dirs():
        try:
            meta = json.loads(previous.meta.read_text())
        except (OSError, ValueError):
            continue
        if (
            meta.get("job") == "harder"
            and meta.get("input_rev") == rev
            and meta.get("status") == "kept"
            and str(meta.get("reason") or "").startswith("GIVE UP")
        ):
            return True
    return False


def _attach_student_feedback(
    signal: dict, task: layout.TaskDir, *, job: str, rev: int
) -> None:
    """Give a harder signal the student measurement the agent reads.

    Hardening always uses the student's attempts. Attach the measurement
    before the rewrite starts, unless the signal already carries it.
    """
    if (
        job != "harder"
        or signal.get("student_feedback") is not None
    ):
        return
    signal["student_feedback"] = {
        "measurement": {
            key: signal[key] for key in ("run", "group", "rev", "solved", "total")
        },
        "measurement_scope": "One rollout group from the named training run.",
    }
    parent_instruction = task.rev(rev - 1) / "instruction.md"
    if rev > 0 and parent_instruction.exists():
        signal["student_feedback"]["previous_revision"] = {
            "rev": rev - 1,
            "instruction": parent_instruction.read_text(),
        }


def _public_files(pkg: Path) -> dict[str, Path]:
    """A package's own files, without the harness's (ec.HARNESS)."""
    return {
        str(p.relative_to(pkg)): p
        for p in sorted(pkg.rglob("*"))
        if p.is_file()
        and p.relative_to(pkg).parts[0] not in ec.HARNESS
        and "__pycache__" not in p.parts
    }


def _package_diff(before: Path, after: Path) -> str:
    """Unified diff of two packages' own files; a binary change is named."""
    old, new = _public_files(before), _public_files(after)
    out = []
    for rel in sorted(old.keys() | new.keys()):
        a = old[rel].read_bytes() if rel in old else b""
        b = new[rel].read_bytes() if rel in new else b""
        if a == b:
            continue
        try:
            a_text, b_text = a.decode(), b.decode()
        except UnicodeDecodeError:
            out.append(f"Binary file {rel} {'added' if not a else 'removed' if not b else 'changed'}\n")
            continue
        out.extend(difflib.unified_diff(
            a_text.splitlines(keepends=True), b_text.splitlines(keepends=True),
            f"a/{rel}" if rel in old else "/dev/null",
            f"b/{rel}" if rel in new else "/dev/null",
        ))
    return "".join(out)


def _write_history(
    root: layout.Root,
    task: layout.TaskDir,
    rewrite: layout.RewriteDir,
    *,
    strict_storage: bool = False,
    current: dict | None = None,
) -> int:
    """Every earlier rewrite of this task, for the agent rewriting it now.

    traces/history/summary.md is the page to read first: each earlier rewrite,
    oldest first, with its direction, what training measured before it, how
    it ended and why, and which files it changed. index.jsonl holds the same
    records as data. Beside them, <rewrite>.diff is what that rewrite changed
    -- against its result when it was accepted, against the package it left
    otherwise, so an attempt that failed shows what was tried -- and
    <rewrite>.notes.md the author's own notes and failure record. Under
    traces/, so the blind verifier and the probe never see it and nothing of
    it travels with the task. Returns the number of rewrites recorded.

    ``current`` is the signal being handled now. Each earlier measurement is
    dated against it (which run, how long before), since it measured the
    student as it was then: training since, or a later run, may have changed it.
    """
    out = rewrite.traces / "history"
    lines = []
    texts: dict[str, str] = {}
    for earlier in task.rewrite_dirs():
        if earlier.path == rewrite.path:
            continue
        try:
            meta = json.loads(earlier.meta.read_text())
        except OSError:
            if strict_storage:
                raise
            log.warning("%s unreadable earlier rewrite: %s", task.task_id, earlier.meta)
            continue
        except ValueError:
            log.warning("%s unreadable earlier rewrite: %s", task.task_id, earlier.meta)
            continue
        name = earlier.path.name
        entry = {
            key: meta.get(key)
            for key in ("job", "started", "finished", "input_rev", "result_rev",
                        "status", "stage", "reason", "changed", "harder_mode")
        }
        entry["rewrite"] = name
        measured = (meta.get("student_feedback") or {}).get("measurement")
        if measured is None and meta.get("signal"):
            run_name, stem = meta["signal"].split("/", 1)
            try:
                s = json.loads((root.run(run_name).signals / f"{stem}.json").read_text())
                measured = {
                    k: s.get(k)
                    for k in ("run", "group", "rev", "solved", "total", "created")
                }
            except (OSError, ValueError):
                measured = None
        if measured is not None and current:
            measured = {**measured, **_measurement_age(measured, current)}
        entry["measured_on_input"] = measured
        base = task.rev(meta["input_rev"]) if meta.get("input_rev") is not None else None
        result = (
            task.rev(meta["result_rev"])
            if meta.get("status") == "accepted" and meta.get("result_rev") is not None
            else earlier.package
        )
        out.mkdir(parents=True, exist_ok=True)
        diff_text = ""
        if base is not None and base.is_dir() and result.is_dir():
            diff_text = _package_diff(base, result)
            (out / f"{name}.diff").write_text(diff_text)
            entry["diff"] = f"{name}.diff"
        # An accepted rewrite's package is stripped and renamed by the fold;
        # its notes were kept beside it (_keep_author_notes).
        notes = []
        for rel in AUTHOR_NOTES:
            for path in (earlier.package / rel, earlier.notes / Path(rel).name):
                if path.is_file():
                    notes.append(f"## {rel}\n\n{path.read_text(errors='replace')}\n")
                    break
        if notes:
            (out / f"{name}.notes.md").write_text("\n".join(notes))
            entry["notes"] = f"{name}.notes.md"
        lines.append(entry)
        texts[name] = diff_text
    # What training measured on each accepted rewrite's result: the next
    # rewrite's input measurement, or for the newest revision the signal being
    # handled now (its attempts are this rewrite's traces/). This is what an
    # author's prediction is checked against.
    for entry in lines:
        if entry.get("status") != "accepted" or entry.get("result_rev") is None:
            continue
        later = [e for e in lines if e.get("input_rev") == entry["result_rev"]]
        if later and later[0].get("measured_on_input"):
            entry["measured_next"] = later[0]["measured_on_input"]
        elif current and current.get("rev") == entry["result_rev"]:
            entry["measured_next"] = {
                k: current.get(k) for k in ("run", "group", "rev", "solved", "total")
            } | {"current_signal": True}
    if lines:
        (out / "index.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
    if lines:
        summary = _history_summary(task.task_id, lines, texts)
        since = _since_original(task, current, out)
        if since:
            summary += since
        (out / "summary.md").write_text(summary)
    return len(lines)


REVISION_MIXED_GROUPS = 3


def _stage_revision(run_dir: Path, signal: dict, out: Path, workspace) -> int:
    """The revision's other groups in this run, beside the signal's attempts.

    A signal group is all solved or all failed, so on its own it cannot show
    which decision separates a pass from a fail. trajectory.jsonl is every
    scored group the student finished on this revision in the run, oldest
    first; mixed/ holds the attempts of the latest REVISION_MIXED_GROUPS mixed
    groups. Returns the number of groups in the trajectory.
    """
    task, rev = str(signal["task"]), signal.get("rev")
    groups: dict[int, list[tuple[int, dict, Path]]] = {}
    for path in sorted((run_dir / "rollouts" / layout.safe(task)).glob("g*-r*.jsonl")):
        try:
            with path.open() as stream:
                head = json.loads(stream.readline())
        except (OSError, ValueError):
            continue
        if head.get("rev") != rev or head.get("infra_failed"):
            continue
        groups.setdefault(int(head["group"]), []).append((int(head["rollout"]), head, path))
    if not groups:
        return 0
    lines, mixed = [], []
    for group in sorted(groups):
        records = sorted(groups[group], key=lambda r: r[0])
        solved = sum(1 for _, h, _ in records if (h.get("reward") or 0) >= 1.0)
        reasons: dict[str, int] = {}
        for _, h, _ in records:
            reasons[str(h.get("finish_reason"))] = reasons.get(str(h.get("finish_reason")), 0) + 1
        lines.append({"group": group, "solved": solved, "total": len(records),
                      "finish_reasons": reasons})
        if 0 < solved < len(records) and group != signal.get("group"):
            mixed.append((group, records))
    out.mkdir(parents=True, exist_ok=True)
    (out / "trajectory.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    for group, records in mixed[-REVISION_MIXED_GROUPS:]:
        for idx, _, path in records:
            workspace.copy_input(path, out / "mixed" / f"g{group}-r{idx}.jsonl")
    return len(lines)


ATTEMPT_FIELDS = ("rev", "group", "rollout", "reward", "finish_reason", "infra_failed")
# The record directories of a run that _index_attempts reads. The overfit
# bench's ladder rewriter sets this to ("rollouts",): validation there is the
# bench's readout, which a rewriter may not read. The evolve loop reads both.
ATTEMPT_RECORD_DIRS = ("rollouts", "validation_rollouts")
# One writer of a task's attempts.jsonl at a time: the overfit bench's ladder
# rewriter runs several rewrites of one task at once, and each rewrites the
# index through the same .incoming file. Per task, so the evolve loop, which
# runs one rewrite per task, never waits on it.
_ATTEMPT_INDEX_LOCKS: dict[Path, threading.Lock] = {}
_ATTEMPT_INDEX_LOCKS_LOCK = threading.Lock()


def _index_attempts(root: layout.Root, task: layout.TaskDir) -> list[dict]:
    """task.attempts brought up to date and returned: one line per attempt
    record at this task in any run of the root, training and validation
    (ATTEMPT_RECORD_DIRS). Only
    records not indexed yet are opened, and only their header line: on a FUSE
    root every file read is a round trip, and a task gathers thousands."""
    with _ATTEMPT_INDEX_LOCKS_LOCK:
        lock = _ATTEMPT_INDEX_LOCKS.setdefault(task.path, threading.Lock())
    with lock:
        return _update_attempt_index(root, task)


def _update_attempt_index(root: layout.Root, task: layout.TaskDir) -> list[dict]:
    index = layout.read_jsonl(task.attempts)
    seen = {e["path"] for e in index}
    fresh = []
    for run in root.run_dirs():
        for kind in (getattr(run, name) for name in ATTEMPT_RECORD_DIRS):
            for path in sorted((kind / layout.safe(task.task_id)).glob("g*-r*.jsonl")):
                rel = str(path.relative_to(root.path))
                if rel in seen:
                    continue
                try:
                    with path.open() as stream:
                        head = json.loads(stream.readline())
                except (OSError, ValueError):
                    continue  # still being written; indexed on a later rewrite
                fresh.append({"run": run.path.name, "path": rel,
                              **{k: head.get(k) for k in ATTEMPT_FIELDS}})
    if fresh:
        index += fresh
        task.path.mkdir(parents=True, exist_ok=True)
        tmp = task.attempts.with_name(task.attempts.name + ".incoming")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in index))
        os.replace(tmp, task.attempts)
    return index


def _runs_of(counts: list[str]) -> str:
    """"0/16 x3 1/16 0/16": consecutive equal group results written once."""
    out: list[list] = []
    for c in counts:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return " ".join(c if n == 1 else f"{c} x{n}" for c, n in out)


def _write_record(root: layout.Root, task: layout.TaskDir, rewrite: layout.RewriteDir) -> int:
    """traces/record.md and traces/attempts.jsonl: this task's full record, for
    the author to read when the selection in traces/ leaves a question open.
    record.md gives each revision's solve counts group by group, oldest first,
    and where the attempts, packages and lineage sit in the root; attempts.jsonl
    lists every attempt with its path, so the author opens only the ones it
    needs. Under traces/, so the blind verifier is never told where the hidden
    solutions sit. Returns the number of attempts indexed."""
    index = _index_attempts(root, task)
    groups: dict[tuple, list[dict]] = {}
    for e in index:
        if e.get("infra_failed") or e.get("group") is None:
            continue
        groups.setdefault((e.get("rev"), e["run"], e["group"]), []).append(e)
    curves: dict = {}
    for (rev, run, group), attempts in sorted(groups.items(), key=lambda g: g[0][1:]):
        solved = sum(1 for a in attempts if (a.get("reward") or 0) >= 1.0)
        kind = "validation" if group < 0 else "training"
        curves.setdefault((rev, kind), []).append(f"{solved}/{len(attempts)}")
    lines = [
        f"# Full record of {task.task_id}",
        "",
        "Read-only. Read whatever answers your question; write nothing in the "
        "root, and copy none of it into the task.",
        "",
        "## Solve counts per revision, group by group, oldest first",
    ]
    lines += [f"- r{rev} {kind}: {_runs_of(c)}"
              for (rev, kind), c in sorted(curves.items(), key=lambda x: (x[0][0] is None, x[0]))]
    lines += ["- none recorded"] if not curves else []
    lines += [
        "",
        "## Every attempt",
        "traces/attempts.jsonl: one line per attempt -- run, group, rollout, rev, "
        f"reward, finish_reason, infra_failed, and path, relative to {root.path}. "
        "The record at that path is the full attempt: line 1 its header, then "
        "one line per turn, as in traces/attempt-NN.jsonl. In the file name "
        "g<group>-r<n>, n is the attempt's index in its group, not the revision.",
        "",
        "## Every revision of the task",
        f"- Packages: {task.path}/r<N>/",
        f"- Rewrites with their notes, diffs and outcome: {task.path}/rewrites/",
        f"- Lineage, one line per revision: {task.lineage}",
        "",
    ]
    rewrite.traces.mkdir(parents=True, exist_ok=True)
    (rewrite.traces / "record.md").write_text("\n".join(lines))
    (rewrite.traces / "attempts.jsonl").write_text("".join(json.dumps(e) + "\n" for e in index))
    return len(index)


def _since_original(task: layout.TaskDir, current: dict | None, out: Path) -> str:
    """How the revision being rewritten now differs from the original task, as
    one diff: the net effect of every earlier rewrite, so help a simplification
    added many revisions ago is as visible as the latest change."""
    try:
        rev = int((current or {})["rev"])
    except (KeyError, TypeError, ValueError):
        return ""
    if rev == 0 or not task.rev(0).is_dir() or not task.rev(rev).is_dir():
        return ""
    diff = _package_diff(task.rev(0), task.rev(rev))
    (out / "since-original.diff").write_text(diff)
    return (
        f"\n## Since the original task: r0 -> r{rev}\n"
        f"- Changed: {_diff_stat(diff) if diff else 'nothing'}\n"
        "- Diff: since-original.diff\n"
        "- Everything in it was added or changed by the rewrites above; what an "
        "easier rewrite added is help, not part of the original task.\n"
    )


def _diff_stat(diff: str) -> str:
    """"path (+added -removed), ..." for a _package_diff."""
    stat: dict[str, list[int]] = {}
    path = prev = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            if path == "/dev/null":
                path = prev
        elif line.startswith("--- "):
            prev = line[4:].removeprefix("a/")
        elif line.startswith("Binary file "):
            stat[line.split()[2]] = [0, 0]
        elif path and line.startswith("+"):
            stat.setdefault(path, [0, 0])[0] += 1
        elif path and line.startswith("-"):
            stat.setdefault(path, [0, 0])[1] += 1
    return ", ".join(f"{p} (+{a} -{r})" for p, (a, r) in stat.items()) or "nothing"


def _measurement_age(measured: dict, current: dict) -> dict:
    """Where an earlier measurement stands against the signal handled now."""
    age: dict = {"same_run_as_current": measured.get("run") == current.get("run")}
    try:
        hours = (
            layout.parse_stamp(current["created"]) - layout.parse_stamp(measured["created"])
        ) / 3600
        age["hours_before_current"] = round(hours, 1)
    except (KeyError, TypeError, ValueError):
        pass
    return age


def _history_summary(task_id: str, attempts: list[dict], texts: dict[str, str]) -> str:
    """The history as one page to read first: every earlier rewrite, oldest
    first, with what training measured before it, how it ended and what it
    changed."""

    def measured(entry: dict) -> str:
        m = entry.get("measured_on_input") or {}
        if m.get("solved") is None:
            return "not recorded"
        when = []
        if "same_run_as_current" in m:
            when.append("this training run" if m["same_run_as_current"] else "an earlier training run")
        if m.get("hours_before_current") is not None:
            when.append(f"{m['hours_before_current']} h before the current signal")
        return (
            f"{m['solved']}/{m['total']} attempts solved (run {m.get('run')}, group {m.get('group')}"
            + (f"; {', '.join(when)}" if when else "")
            + ")"
        )

    out = [f"# Earlier rewrites of {task_id}\n\nOldest first. Diffs and notes named "
           "below are files beside this one.\n\nEach measurement is the student as it was "
           "when measured. The student keeps training, and a later run may start from a "
           "different policy, so a result from many hours earlier or from an earlier run "
           "is weaker evidence of what the current student can do than a recent one from "
           "this run.\n"]
    for e in attempts:
        produced = f" -> r{e['result_rev']}" if e.get("result_rev") is not None else ""
        out.append(
            f"\n## {e['rewrite']}: {e.get('job')} r{e.get('input_rev')}{produced}, "
            f"{e.get('status')}{' at ' + e['stage'] if e.get('stage') else ''}\n"
            f"- Training on r{e.get('input_rev')} before it: {measured(e)}\n"
            + (
                f"- Training on r{e['result_rev']} after it: "
                f"{e['measured_next'].get('solved')}/{e['measured_next'].get('total')} solved"
                + (" (the signal being handled now; its attempts are traces/)"
                   if e["measured_next"].get("current_signal") else "")
                + "; compare with the prediction in its notes\n"
                if e.get("measured_next") else ""
            )
            + (f"- Reason: {e['reason']}\n" if e.get("reason") else "")
            + (f"- Changed: {_diff_stat(texts.get(e['rewrite'], ''))}\n- Diff: {e['diff']}\n"
               if e.get("diff") else "- Changed: no package left to compare\n")
            + (f"- Author notes: {e['notes']}\n" if e.get("notes") else "")
        )
    return "".join(out)


def training_box(tid: str, declared: dict[str, dict] | None) -> dict:
    """The size training gives this task: the row's own values, the fleet
    default where the row declares nothing, None where neither says (the
    harness default then applies, and the source names that)."""
    own = (declared or {}).get(tid) or {}
    box = {k: own[k] if own.get(k) is not None else FLEET[k] for k in ROW_KEYS}
    if all(own.get(k) is not None for k in ROW_KEYS):
        src = "row"
    elif any(box[k] is None for k in ROW_KEYS):
        src = "row+fleet_default+harness_default" if own else "harness_default"
    else:
        src = "row+fleet_default" if own else "fleet_default"
    return {**box, "source": src}


# --------------------------------------------------------------------------
# One loop per root
# --------------------------------------------------------------------------

LOCK_HEARTBEAT_SEC = 30
# A contender on another node treats a heartbeat older than this as a dead
# holder. Three beats: one missed beat is a stalled GPFS write, not a death.
LOCK_STALE_SEC = 3 * LOCK_HEARTBEAT_SEC


def _heartbeat(fd: int) -> None:
    while True:
        time.sleep(LOCK_HEARTBEAT_SEC)
        try:
            os.utime(fd)
        except OSError:
            pass


def acquire_singleton(lock_path: Path) -> int:
    """Hold the one-loop-per-root lock for the life of this process.

    The loop is a singleton by contract -- two instances over one root handle
    the same signals, send the same k/k task to Codex twice and fold over
    each other's mix -- but nothing enforced it. There are several ways to
    launch it (restart_evolve.sh, the training launcher, a hand-typed
    command); only one stops the previous instance first. Measured
    2026-09-02: nine launches in one day, two instances alive at once for 27
    minutes. Guarding every launcher is whack-a-mole; the process guards
    itself.

    Two layers, because no single primitive reaches across nodes here:

    * flock, for the node the loop runs on. The kernel drops it when the
      holder dies, however it dies, so a SIGKILLed loop never leaves a stale
      lock. flock rather than fcntl: a POSIX record lock is released the
      moment its process closes any fd on the file, which a stray read of the
      lock file would do silently.
    * a heartbeat, for other nodes. Neither flock nor fcntl held across nodes
      on this GPFS mount (measured 2026-09-02: a holder on della-tridao did
      not stop a contender on della-gpu), so the holder touches the file's
      mtime every LOCK_HEARTBEAT_SEC and a contender that finds another host
      named in the file with a heartbeat under LOCK_STALE_SEC refuses. A dead
      holder on another node clears itself after LOCK_STALE_SEC; nothing has
      to be removed by hand.

    Test modes (--once, --only, --dry) take it too: a hand round over a live
    loop's root collides the same way. The returned fd must stay open.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 4096, 0).decode(errors="replace").strip()
        os.close(fd)
        raise SystemExit(
            f"evolve_ondella: another instance already runs over "
            f"{lock_path.parent} ({holder or 'holder unknown'}). Stop it "
            f"first -- restart_evolve.sh does -- rather than start a second."
        ) from None
    host = socket.gethostname()
    holder = os.pread(fd, 4096, 0).decode(errors="replace").strip()
    age = time.time() - os.fstat(fd).st_mtime
    if holder and f"host={host} " not in holder and age < LOCK_STALE_SEC:
        os.close(fd)
        raise SystemExit(
            f"evolve_ondella: another instance runs over {lock_path.parent} "
            f"on a different node, heartbeat {age:.0f}s old ({holder}). Stop "
            f"it there; if it is already dead, its heartbeat goes stale after "
            f"{LOCK_STALE_SEC}s and this start will go through."
        )
    os.ftruncate(fd, 0)
    os.write(
        fd,
        (
            f"host={host} pid={os.getpid()} "
            f"started={time.strftime('%Y-%m-%dT%H:%M:%S%z')} "
            f"argv={' '.join(sys.argv)}\n"
        ).encode(),
    )
    threading.Thread(
        target=_heartbeat, args=(fd,), daemon=True, name="evolve-lock-heartbeat"
    ).start()
    return fd


# --------------------------------------------------------------------------
# Discovery: the ledger is the loop's memory
# --------------------------------------------------------------------------


@dataclass
class Signal:
    """One signal file as the loop sees it. `data` is None when it did not
    parse; `junk` then says why."""

    run: layout.Run
    path: Path
    sid: str
    data: dict | None = None
    junk: str | None = None

    @property
    def task(self) -> str:
        if self.data:
            return str(self.data["task"])
        return self.path.stem.rpartition("--g")[0] or self.path.stem

    @property
    def group(self) -> int | None:
        if self.data:
            return self.data.get("group")
        tail = self.path.stem.rpartition("--g")[2]
        return int(tail) if tail.isdigit() else None


class NoSeed(Exception):  # noqa: N818 - Preserve the existing exception name.
    """The task has no r0 and no source corpus carries it."""


def load_ledger(root: layout.Root) -> dict[str, dict]:
    """The latest ledger line per signal id."""
    latest: dict[str, dict] = {}
    # The closer thread appends while the coordinator and status thread read.
    with _LEDGER_LOCK:
        lines = layout.read_jsonl(root.evolution.ledger)
    for line in lines:
        sid = line.get("signal")
        if sid:
            latest[sid] = line
    return latest


def _pending(
    root: layout.Root, ledger: dict[str, dict]
) -> list[tuple[layout.Run, Path, str]]:
    """Every signal file with no ledger line at all. The id is the run and the
    file's stem, which is what layout.signal_id spells.

    A `deferred` line closes a signal for good. Turning the easier direction on
    does not reopen the backlog: those signals measured revisions the pool has
    since moved past, so replaying them would spend the arm's first rounds on
    stale measurements instead of on what training is producing now."""
    out = []
    for run in root.run_dirs():
        for p in run.signal_files():
            sid = f"{run.name}/{p.stem}"
            last = ledger.get(sid)
            if last is None:
                out.append((run, p, sid))
    return out


def read_signal(path: Path) -> tuple[dict | None, str | None]:
    """(data, None) for a signal; (None, why) for junk; (None, None) for a
    file too young to condemn."""
    try:
        data = json.loads(path.read_text())
        missing = [k for k in SIGNAL_KEYS if k not in data]
        if missing:
            return None, f"signal lacks {missing}"
        if data["direction"] not in ("harder", "easier"):
            return None, f"unknown direction {data['direction']!r}"
        # Empty when the trainer runs with SWE_ROLLOUT_RECORDS=0: the group's
        # verdict still stands, the agent just has no traces to read.
        if not isinstance(data["attempts"], list):
            return None, "attempts is not a list"
        int(data["rev"])
        return data, None
    except Exception as e:  # noqa: BLE001 -- anything unreadable is junk
        try:
            fresh = time.time() - path.stat().st_mtime < FRESH_SEC
        except OSError:
            return None, None
        if fresh:
            return None, None
        return None, f"unreadable: {type(e).__name__}: {e}"[:200]


# Parsed signals by path. The trainer renames a signal into place and never
# rewrites it (LAYOUT), so a pending signal is read once per loop, not once per
# refill and status refresh: on the FUSE root each read is a round trip, and a
# backlog of ~120 pending signals cost a minute per refill.
_SIGNALS: dict[str, dict] = {}


def discover(root: layout.Root, ledger: dict[str, dict]) -> list[Signal]:
    found = []
    for run, p, sid in _pending(root, ledger):
        cached = _SIGNALS.get(str(p))
        if cached is not None:
            data, junk = dict(cached), None
        else:
            data, junk = read_signal(p)
            if data is not None:
                _SIGNALS[str(p)] = dict(data)
        if data is None and junk is None:
            log.warning("signal %s does not parse yet (fresh, retrying)", sid)
            continue
        found.append(Signal(run, p, sid, data, junk))
    return found


def choose(
    root: layout.Root, signals: list[Signal]
) -> tuple[list[Signal], list[tuple[Signal, str, Signal | None]]]:
    """One signal per task: the newest whose rev is the task's current one.

    Returns the picks and the rest as (signal, why, the pick it yields to);
    a signal about a revision that is no longer current yields to nothing.
    """
    by_task: dict[str, list[Signal]] = {}
    for s in signals:
        by_task.setdefault(s.task, []).append(s)
    picks, rest = [], []
    for tid, items in sorted(by_task.items()):
        current = root.evolution.task(tid).latest_rev() or 0
        items.sort(key=lambda s: (str(s.data.get("created", "")), s.path.name))
        matching = [s for s in items if int(s.data["rev"]) == current]
        pick = matching[-1] if matching else None
        for s in items:
            if s is pick:
                continue
            if int(s.data["rev"]) != current:
                rest.append(
                    (
                        s,
                        f"rev {s.data['rev']} is not the task's current rev {current}",
                        None,
                    )
                )
            else:
                rest.append((s, f"newer signal {pick.sid} covers rev {current}", pick))
        if pick is not None:
            picks.append(pick)
    return picks, rest


# --------------------------------------------------------------------------
# Handling one signal
# --------------------------------------------------------------------------


def unsolved(root: layout.Root, tid: str, rev: int) -> bool:
    """Whether the revision a signal measured has no reference solution. Its
    easier rewrite is what writes one, so that signal runs with the easier
    direction off too."""
    src = root.evolution.task(tid).rev(rev)
    if not src.exists() and rev == 0:
        sources = root.data / "sources"
        seeds = (
            [corpus / "tasks" / tid for corpus in sources.iterdir()]
            if sources.is_dir()
            else []
        )
        seeds = [p for p in seeds if (p / "instruction.md").is_file()]
        if len(seeds) != 1:
            return False  # materialize_r0 reports it
        src = seeds[0]
    try:
        solution_path(src)
    except FileNotFoundError:
        return True
    return False


def _read_live_mix(root: layout.Root) -> list[dict]:
    """The live mix rows, read again if a concurrent publish is swapping it in."""
    return [json.loads(ln) for ln in root.mix.read_live().splitlines() if ln.strip()]


# One materialization of a task's r0 at a time: several rewrites of one task
# may start at once (the overfit bench's ladder), and each would otherwise copy
# into the same r0.incoming.
_R0_LOCKS: dict[Path, threading.Lock] = {}
_R0_LOCKS_LOCK = threading.Lock()


def _ensure_r0(root: layout.Root, task: layout.TaskDir, tid: str) -> Path:
    """The task's r0, materialized by the first caller and reused by the rest."""
    with _R0_LOCKS_LOCK:
        lock = _R0_LOCKS.setdefault(task.path, threading.Lock())
    with lock:
        dest = task.rev(0)
        return dest if dest.exists() else materialize_r0(root, task, tid)


def materialize_r0(root: layout.Root, task: layout.TaskDir, tid: str) -> Path:
    """r0 is the seed package, copied once from whichever corpus carries it.
    Copied whole into r0.incoming and renamed, so a crash mid-copy cannot
    leave a half package that a later round would evolve."""
    sources = root.data / "sources"
    candidates = (
        sorted(
            corpus / "tasks" / tid
            for corpus in sources.iterdir()
            if (corpus / "tasks" / tid / "instruction.md").is_file()
        )
        if sources.is_dir()
        else []
    )
    if not candidates:
        raise NoSeed(f"no seed package for {tid} under data/sources/*/tasks")
    if len(candidates) != 1:
        raise NoSeed(f"ambiguous seed package for {tid}: {candidates}")
    src = candidates[0]
    dest = task.rev(0)
    incoming = dest.with_name("r0.incoming")
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.copytree(src, incoming, ignore=SEED_IGNORE)
    for row in _read_live_mix(root):
        md = row.get("metadata") or {}
        if md.get("instance_id") != tid or not md.get("prebuilt_provenance"):
            continue
        image = md.get("image", "")
        if "@sha256:" not in image or md.get("dockerfile"):
            raise ValueError(f"{tid}: malformed prebuilt seed row")
        dockerfile = incoming / "environment" / "Dockerfile"
        if not dockerfile.exists():
            dockerfile = incoming / "Dockerfile"
        if not dockerfile.exists():
            raise ValueError(f"{tid}: prebuilt source has no Dockerfile")
        # Keep the audited source recipe out of the build context used by r0.
        layout.write_json_atomic(
            incoming / ".prebuilt-source.json",
            {
                "image": image,
                "source_dockerfile": dockerfile.read_text(),
                "provenance": md["prebuilt_provenance"],
            },
        )
        dockerfile.write_text(f"FROM {image}\n")
        break
    os.rename(incoming, dest)
    log.info("%s: r0 materialized from %s", tid, src.relative_to(root.path))
    return dest


# Rewrite directory names handed out by this process. The evolve loop runs one
# rewrite per task at a time, so a name free on disk is free; the overfit
# bench's ladder rewriter starts several rewrites of one task in the same
# second, and each must get its own directory before any of them creates it.
_REWRITE_NAMES: set[Path] = set()
_REWRITE_NAMES_LOCK = threading.Lock()


def _new_rewrite_dir(task: layout.TaskDir, job: str) -> layout.RewriteDir:
    t = time.time()
    with _REWRITE_NAMES_LOCK:
        rw = task.rewrite(job, layout.stamp(t))
        while rw.path.exists() or rw.path in _REWRITE_NAMES:
            t += 1
            rw = task.rewrite(job, layout.stamp(t))
        _REWRITE_NAMES.add(rw.path)
    return rw


def _compact_resources(p: dict | None) -> dict | None:
    """The provisioned size as rewrite.json and the row carry it."""
    if not p:
        return None
    return {
        **{k: p.get(k) for k in ROW_KEYS},
        "source": p.get("source"),
        "measured": p.get("measured"),
    }


def handle(
    root: layout.Root,
    sig: Signal,
    *,
    declared: dict[str, dict],
    dry: bool = False,
    pretests: dict[str, tuple[str, str]] | None = None,
    protecteds: dict | None = None,
    input_row: dict | None = None,
) -> dict:
    """One signal, one rewrite directory, one verdict in rewrite.json.

    An accepted rewrite stays `running` on disk until the fold renames its
    package: the rewrite is not done until the revision exists, and a loop
    that dies in between leaves a record finalize_interrupted_traces marks
    rather than an `accepted` with no revision behind it.

    `pretests` is the pin hook per row of the mix (read_declared). The task's,
    when it has one, is snapshotted as the rewrite's pretest.json before
    process_one runs: the agent's tool and the loop's probe both grade with
    it, the way training does.
    """
    arm = fb.retune_arm()
    d = dict(sig.data)
    tid, rev = str(d["task"]), int(d["rev"])
    job = "harder" if d["direction"] == "harder" else "easier"
    task = root.evolution.task(tid)
    src = task.rev(rev)
    if not src.exists():
        if rev != 0:
            raise NoSeed(
                f"r{rev} does not exist under {task.path.relative_to(root.path)}"
            )
        src = _ensure_r0(root, task, tid)
    durable_rewrite = _new_rewrite_dir(task, job)
    workspace = rw_workspace.prepare_rewrite(root, durable_rewrite, src)
    rewrite = workspace.active
    if workspace.is_local:
        log.info("%s active rewrite workspace: %s", tid, rewrite.path)
    meta = {
        "task": tid,
        "job": job,
        "signal": sig.sid,
        "input_rev": rev,
        "started": layout.stamp(),
        "finished": None,
        "status": "running",
        "arm": arm,
        "verdicts": None,
        "resources": None,
        "result_rev": None,
        "sessions": [],
        "execution_storage": "node_local" if workspace.is_local else "durable",
    }
    if dry:
        meta["dry"] = True
    layout.write_json_atomic(rewrite.meta, meta)
    workspace.claim(meta)
    hook = (pretests or {}).get(tid)
    lists = (protecteds or {}).get(tid)
    if hook or lists:
        # The snapshot beside rewrite.json: the hook AND the row's protected
        # lists, so the probe, the sandbox tool and the fold all see the same
        # inherited lists (a package file, if the agent writes one, overrides).
        layout.write_pretest(
            rewrite.pretest,
            *(hook or ("", "")),
            protected_paths=lists.paths if lists else None,
            protected_cmds=lists.cmds if lists else None,
        )
    processing_started = False
    try:
        workspace.stage_source(src, ignore=SEED_IGNORE)
        run_dir = root.run(str(d["run"])).path
        for i, rel in enumerate(d["attempts"], 1):
            workspace.copy_input(
                run_dir / rel, rewrite.traces / f"attempt-{i:02d}.jsonl"
            )
        _stage_revision(run_dir, d, rewrite.traces / "revision", workspace)
        n_history = _write_history(
            root, task, rewrite, strict_storage=workspace.is_local, current=d
        )
        _write_record(root, task, rewrite)
        if n_history:
            log.info("%s history: %d earlier rewrites in traces/history", tid, n_history)
        parent_snapshot = None
        parent_hashes = {}
        previous_context = None
        context_hash = None
        # Calibration may only undo the latest simplification. Once a hardening of
        # this revision gave up (that simplification is already fully undone, but
        # earlier help is still in the task), harden it the ordinary way instead,
        # or the task never gets harder again.
        calibration_exhausted = job == "harder" and _calibration_gave_up(task, rev)
        for previous in [] if calibration_exhausted else task.rewrite_dirs():
            try:
                previous_meta = json.loads(previous.meta.read_text())
            except OSError:
                if workspace.is_local:
                    raise
                log.warning("%s unreadable prior rewrite: %s", tid, previous.meta)
                continue
            except ValueError:
                # An interrupted historical rewrite must not stop this signal.
                log.warning("%s unreadable prior rewrite: %s", tid, previous.meta)
                continue
            context = (
                {
                    "input_rev": previous_meta["input_rev"],
                    "simplify": previous_meta["simplify"],
                }
                if previous_meta.get("simplify")
                else previous_meta.get("simplify_context")
                if previous_meta.get("calibration")
                else None
            )
            if (
                previous_meta.get("status") == "accepted"
                and previous_meta.get("result_rev") == rev
                and context
            ):
                parent_snapshot = rewrite.traces / "previous-simplify-parent"
                # A hardlink would let edits to the reference change the original revision.
                workspace.copy_input_tree(
                    task.rev(context["input_rev"]), parent_snapshot
                )
                parent_hashes = {
                    str(path.relative_to(parent_snapshot)): layout.sha256_file(path)
                    for path in sorted(parent_snapshot.rglob("*"))
                    if path.is_file()
                }
                previous_context = {
                    **context,
                    "result_rev": rev,
                    "parent": {
                        "path": str(parent_snapshot.relative_to(rewrite.package)),
                        "sha256": parent_hashes,
                    },
                    "observed": {
                        key: d[key] for key in ("direction", "solved", "total")
                    },
                }
                layout.write_json_atomic(
                    rewrite.traces / "previous-simplify.json", previous_context
                )
                context_hash = layout.sha256_file(
                    rewrite.traces / "previous-simplify.json"
                )
                break
        _attach_student_feedback(d, task, job=job, rev=rev)
        processing_started = True
        rec = fb.process_one(
            rewrite,
            d,
            job=job,
            seed_dir=workspace.seed_dir,
            resources=training_box(tid, declared),
        )
        if parent_snapshot is not None:
            current_hashes = {
                str(path.relative_to(parent_snapshot)): layout.sha256_file(path)
                for path in sorted(parent_snapshot.rglob("*"))
                if path.is_file()
            }
            context_file = rewrite.traces / "previous-simplify.json"
            if (
                current_hashes != parent_hashes
                or not context_file.is_file()
                or layout.sha256_file(context_file) != context_hash
            ):
                rec.update(
                    status="rejected",
                    stage="simplify_context",
                    reason="The original task reference or its lineage record was changed.",
                )
        if rec.get("calibration"):
            assert previous_context is not None
            context = previous_context
            rationale = rewrite.package / "run" / "hardening.md"
            context.setdefault("calibrations", []).append(
                {
                    "input_rev": rev,
                    "observed": context["observed"],
                    "rationale": rationale.read_text() if rationale.exists() else None,
                }
            )
            rec["simplify_context"] = context
        if (
            job == "easier"
            and EASIER_GRADER_REPLAY
            and rec.get("status") == "accepted"
            and not dry
        ):
            rec["grader_replay"] = _grader_replay(
                root, task, tid, rev, src, rewrite, declared, stage=rec.get("stage")
            )
            if not rec["grader_replay"]["ok"]:
                rec.update(
                    status="rejected",
                    stage="grader_stricter",
                    reason=rec["grader_replay"]["why"],
                )
        canary_config = PolicyCanaryConfig.from_env()
        if rec.get("status") == "accepted" and canary_config.group_size:
            if dry:
                rec["policy_canary"] = {"ok": False, "stage": "dry_not_measured"}
            else:
                if input_row is None or int(input_row["metadata"].get("rev", 0)) != rev:
                    raise ValueError("policy canary requires the exact input revision's mix row")
                candidate = candidate_row(rewrite.package, input_row, {**meta, **rec})
                queue = PolicyCanaryQueue(root)
                identity, request = queue.submit(
                    rewrite=str(durable_rewrite.path.relative_to(root.path)),
                    task=tid, input_rev=rev, route=rec["route"],
                    original=input_row, candidate=candidate, config=canary_config,
                    expires_at=rec["rewrite_deadline_at"],
                )
                log.info("%s policy canary queued: %s", tid, identity)
                pending_meta = {
                    **meta, **rec, "status": "running", "stage": "policy_canary_pending",
                    "policy_canary": {"request_id": identity, "ok": False},
                }
                layout.write_json_atomic(rewrite.meta, pending_meta)
                workspace.claim(pending_meta)
                verdict = queue.wait(identity, request, config=canary_config)
                rec["policy_canary"] = verdict
                if not verdict["ok"]:
                    rec.update(status="kept", stage=verdict["stage"], reason=verdict["why"])
                    if verdict["stage"] in ("invalid_signal", "policy_canary_error", "policy_canary_timeout"):
                        rec["route"] = "invalid_signal"
    except rw_workspace.WorkspaceTransferError:
        # A storage outage is retryable infrastructure failure. Do not close
        # the signal; rw_workspace.recover publishes the local record before
        # the next round and finalize_interrupted marks it.
        raise
    except OSError as e:
        if workspace.is_local and not processing_started:
            raise rw_workspace.WorkspaceTransferError(
                f"could not stage durable inputs for {tid}; local rewrite left "
                f"at {rewrite.path}: {e}"
            ) from e
        rec = {
            "status": "failed",
            "stage": "setup",
            "reason": f"{type(e).__name__}: {e}"[:300],
        }
    except Exception as e:  # noqa: BLE001 -- the rewrite records its own failure
        rec = {
            "status": "failed",
            "stage": "setup",
            "reason": f"{type(e).__name__}: {e}"[:300],
        }
    for key in (
        "action",
        "route",
        "repair",
        "policy_canary",
        "operator",
        "harder_mode",
        "require_solution_growth",
        "student_feedback",
        "family",
        "hint",
        "simplify",
        "calibration",
        "simplify_context",
        "grader_replay",
        "stage",
        "reason",
        "verdicts",
        "changed",
        "oracle_repair",
        "agent_validated",
        "agent_failure_type",
        "agent_exit_code",
        "cyber_filtered",
        "rewrite_budget_sec",
        "rewrite_deadline_at",
        "final_validation_reserve_sec",
        "usage",
        "timings",
    ):
        if key in rec:
            meta[key] = rec[key]
    meta["resources"] = _compact_resources(rec.get("resources"))
    meta["sessions"] = [f"sessions/{s.path.name}" for s in rewrite.session_dirs()]
    status = rec.get("status") or "failed"
    if status == "accepted" and not dry:
        meta["status"] = "running"
    else:
        meta["status"] = status
        meta["finished"] = layout.stamp()
    layout.write_json_atomic(rewrite.meta, meta)
    published = time.monotonic()
    try:
        rewrite = workspace.publish()
    except rw_workspace.UnpublishableRewrite:
        # publish() committed a failed record; close the signal on it once
        # rather than leaving it open for the next round to redo.
        rewrite = workspace.durable
        meta = json.loads(rewrite.meta.read_text())
        return {"signal": sig, "rewrite": rewrite, "meta": meta, "status": "failed"}
    workspace.cleanup()
    if isinstance(meta.get("timings"), dict):
        meta["timings"]["publish"] = round(time.monotonic() - published, 1)
        # An accepted rewrite's record is written again by the fold. The
        # record is already committed, so a failed rewrite of it is cosmetic.
        if meta["status"] != "running":
            try:
                layout.write_json_atomic(rewrite.meta, meta)
            except OSError as exc:
                log.warning("could not record publish time in %s: %s", rewrite.meta, exc)
    return {"signal": sig, "rewrite": rewrite, "meta": meta, "status": status}


def _grader_replay(
    root: layout.Root,
    task: layout.TaskDir,
    tid: str,
    rev: int,
    src: Path,
    rewrite: layout.RewriteDir,
    declared: dict[str, dict],
    *,
    stage: str | None,
) -> dict:
    """grader_replay.check for an accepted easier rewrite of ``src`` (r<rev>)."""
    if stage == "instruction_only":
        # Only the instruction moved: grader, environment and solution are
        # the parent's, so nothing it passed can fail now.
        return {"ok": True, "skipped": "instruction_only"}
    original = task.rev(0)
    verdict = grader_replay.check(
        rewrite.package,
        parent=src,
        original=original if original.exists() else None,
        finals=grader_replay.passing_finals(root, tid, rev),
        probe=fb.daytona_probe,
        scratch=rewrite.path / "grader-replay",
        resources=training_box(tid, declared),
        pretest_file=rewrite.pretest if rewrite.pretest.exists() else None,
    )
    log.info(
        "%s grader replay of r%d: ok=%s %s", tid, rev, verdict["ok"], verdict.get("counts")
    )
    return verdict


# What a later rewrite of the task reads from an earlier one's run/: its
# intent, its predicted solve count and the defects it repaired.
AUTHOR_NOTES = ("run/hardening.md", "run/simplify.json", "run/repair.json", "run/failure.txt")


def _keep_author_notes(package: Path) -> None:
    """Copy the author's notes beside the package before a fold strips run/.

    Without this an accepted rewrite's intent and prediction vanish, and the
    next rewrite cannot compare that prediction with what was measured next.
    """
    notes = layout.RewriteDir(package.parent).notes
    for rel in AUTHOR_NOTES:
        if (package / rel).is_file():
            notes.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package / rel, notes / Path(rel).name)


def strip_harness(pkg: Path) -> None:
    """Take out what the harness put in, before a package becomes a revision."""
    for name in ec.HARNESS:
        p = pkg / name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
    for d in list(pkg.rglob("__pycache__")):
        shutil.rmtree(d, ignore_errors=True)


def _rewrite_ref(root: layout.Root, rewrite: layout.RewriteDir) -> str:
    return str(rewrite.path.relative_to(root.evolution.path))


# The coordinator and the closer thread both append to the ledger; one writer
# at a time keeps every line whole.
_LEDGER_LOCK = threading.Lock()


def _ledger_entry(sig: Signal, outcome: str, **extra) -> dict:
    d = sig.data or {}
    return {
        "stamp": layout.stamp(),
        "signal": sig.sid,
        "task": sig.task,
        "rev": d.get("rev"),
        "run": sig.run.name,
        "group": sig.group,
        "direction": d.get("direction"),
        "outcome": outcome,
        **extra,
    }


def _append_ledger(root: layout.Root, entries: list[dict]) -> None:
    """Ledger lines in one write: a refill closing 40 deferred signals used to
    pay one FUSE append each."""
    if not entries:
        return
    path = root.evolution.ledger
    with _LEDGER_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
            f.flush()


def _ledger_line(root: layout.Root, sig: Signal, outcome: str, **extra) -> None:
    _append_ledger(root, [_ledger_entry(sig, outcome, **extra)])


def _close(root: layout.Root, h: dict, *, dry: bool) -> None:
    """The lineage index line, then the ledger line, last: a signal is closed
    only once everything about its rewrite is on disk."""
    if dry:
        return
    rewrite, meta = h["rewrite"], h["meta"]
    task = root.evolution.task(meta["task"])
    layout.append_jsonl(
        task.lineage,
        {
            "stamp": layout.stamp(),
            "event": "rewrite",
            "rewrite": f"rewrites/{rewrite.path.name}",
            "job": meta["job"],
            "action": meta.get("action"),
            "input_rev": meta["input_rev"],
            "status": meta["status"],
        },
    )
    try:
        record_outcome(root, rewrite, meta)
    except OSError as e:
        # Flow metrics only; a storage error here must not stop the closer.
        log.warning("outcome record for %s skipped: %s", rewrite.path.name, e)
    _ledger_line(root, h["signal"], "handled", rewrite=_rewrite_ref(root, rewrite))


def _finish(h: dict, status: str, *, stage: str, reason: str) -> None:
    meta = h["meta"]
    meta.update(
        {
            "status": status,
            "stage": stage,
            "reason": reason[:300],
            "finished": layout.stamp(),
        }
    )
    layout.write_json_atomic(h["rewrite"].meta, meta)
    h["status"] = status


def candidate_row(package: Path, input_row: dict, meta: dict) -> dict:
    """Build the identical provisioned row for admission probes and publication."""
    old_md = input_row["metadata"]
    row = pack.to_row(
        str(package), task_id=meta["task"], pretest=row_pretest(old_md),
        protected=pack.Protected.from_tmax(old_md.get("tmax") or {}),
    )
    sized = meta.get("resources") or {}
    for key, rk in ROW_KEYS.items():
        if sized.get(key) is not None:
            row["metadata"][rk] = sized[key]
        elif rk not in row["metadata"] and rk in old_md:
            row["metadata"][rk] = old_md[rk]
    # Membership annotations are not in the package; keep them across revisions.
    for key in ("corpus", "terminal_domain"):
        if key in old_md:
            row["metadata"].setdefault(key, old_md[key])
    row["metadata"]["rev"] = int(meta["input_rev"]) + 1
    return row


def fold_workers() -> int:
    """EVOLVE_FOLD_WORKERS: threads preparing one fold's packages (1 = serial)."""
    n = int(os.environ.get("EVOLVE_FOLD_WORKERS", "8"))
    if n < 1:
        raise ValueError("EVOLVE_FOLD_WORKERS must be at least 1")
    return n


def _prepare_fold_row(
    package: Path, input_line: str, meta: dict, policy_canary: dict
) -> dict:
    """Strip one package and build its row. Touches only that package."""
    _keep_author_notes(package)
    strip_harness(package)
    # The directory is `package` now and `r<N>` once renamed, so the row's
    # identity is passed rather than read off the name; so is the hook, which
    # the package never held, and so are the row's protected lists -- unless
    # the package ships its own tests/protected_paths.json, which pack.to_row
    # lets override them.
    row = candidate_row(package, json.loads(input_line), meta)
    if policy_canary.get("ok") and content_hash(row) != policy_canary["candidate_sha256"]:
        raise ValueError("candidate row changed after its policy canary")
    return row


def fold(root: layout.Root, accepted: list[dict]) -> int | None:
    """The accepted rewrites given into one new mix version.

    The row is rebuilt from the package (pack.to_row, so a folded row is
    indistinguishable from a freshly prepared one) and provisioned at
    max(what the seed had, what the reference solution measured): the
    daytona_* keys do not live in a package, so a folded row would otherwise
    arrive without them and fall back to the fleet default -- on this corpus
    1 CPU against a measured 2, a task starved into reading as too hard.
    Without a measurement the replaced row's values carry across.

    The replaced row's pin hook carries across the same way, and for the
    same reason: it lives on the row, not in the package. The row builder
    re-derives the new package's environment identity beside the seed's
    stamp, so a rewrite that kept the environment keeps the check and one
    that rebuilt it is skipped by grading, as the guard intends.

    A task no longer in the mix is not re-added: it was taken out
    deliberately, and a new label lands at the END of the file, which
    rotates the held-out slice. Replace only.

    With policy admission disabled, task validation is the publication gate.
    Later training groups measure difficulty; old policy-probe records do not
    impose admission or expiry requirements on this mode.
    """
    if not accepted:
        return None
    policy_admission_enabled = PolicyCanaryConfig.from_env().group_size != 0
    mix = root.mix
    rows: dict[str, str] = {}
    order: list[str] = []
    for ln in mix.read_live().splitlines():
        if ln.strip():
            iid = json.loads(ln)["metadata"]["instance_id"]
            rows[iid] = ln
            order.append(iid)
    gated: list[tuple[dict, str, int, dict]] = []
    for h in accepted:
        meta = h["meta"]
        tid, n = meta["task"], int(meta["input_rev"])
        task = root.evolution.task(tid)
        target = task.rev(n + 1)
        if tid not in rows:
            _finish(h, "rejected", stage="not_in_mix", reason="no longer in the mix")
            continue
        if layout.exists(target):
            # Cannot happen while one loop runs: choose() hands out one
            # signal per task per round. A revision is never overwritten.
            _finish(h, "failed", stage="fold", reason=f"{target.name} already exists")
            continue
        policy_canary = (
            (meta.get("policy_canary") or {}) if policy_admission_enabled else {}
        )
        if policy_admission_enabled:
            if not policy_canary.get("ok") or "expires_at" not in policy_canary:
                _finish(h, "kept", stage="policy_canary_missing", reason="no passing current-policy admission check")
                continue
            # A storage outage can delay publication after admission has completed.
            if time.time() >= policy_canary["expires_at"]:
                meta["route"] = "invalid_signal"
                _finish(h, "kept", stage="policy_canary_expired", reason="current-policy admission expired before publication")
                continue
        gated.append((h, tid, n, policy_canary))

    # Each package's strip and row build touch only that package, so they run
    # in parallel; everything after is serial and in batch order. A second
    # rewrite of a task in one batch builds on the first's row, so it is
    # prepared serially below, exactly as before.
    t_prep = time.monotonic()
    prepared: dict[int, Future] = {}
    first: dict[str, int] = {}
    for i, (_h, tid, _n, _pc) in enumerate(gated):
        first.setdefault(tid, i)
    workers = min(fold_workers(), len(first))
    if workers > 1:
        with ThreadPoolExecutor(workers, thread_name_prefix="evolve-fold") as pool:
            for i in first.values():
                h, tid, _n, pc = gated[i]
                prepared[i] = pool.submit(
                    _prepare_fold_row, h["rewrite"].package, rows[tid], h["meta"], pc
                )
    prep_sec = time.monotonic() - t_prep

    t_commit = time.monotonic()
    folded = []
    for i, (h, tid, n, policy_canary) in enumerate(gated):
        try:
            if i in prepared:
                row = prepared[i].result()
            else:
                row = _prepare_fold_row(
                    h["rewrite"].package, rows[tid], h["meta"], policy_canary
                )
        except Exception as e:  # noqa: BLE001 -- one malformed package, not the round
            _finish(h, "failed", stage="fold", reason=f"{type(e).__name__}: {e}")
            continue
        rows[tid] = json.dumps(row, ensure_ascii=False)
        folded.append(
            (h, n + 1, {rk: row["metadata"].get(rk) for rk in ROW_KEYS.values()})
        )
    if not folded:
        return None
    # Renamed only once every row is built, so a loop killed while a batch is
    # being packed leaves no r<N+1> on disk without a mix version naming it.
    for h, to_rev, _res in folded:
        os.rename(
            h["rewrite"].package, root.evolution.task(h["meta"]["task"]).rev(to_rev)
        )
    version, path = mix.publish([rows[iid] for iid in order])
    for h, to_rev, res in folded:
        meta, rewrite = h["meta"], h["rewrite"]
        meta.update(
            {"status": "accepted", "result_rev": to_rev, "finished": layout.stamp()}
        )
        layout.write_json_atomic(rewrite.meta, meta)
        h["status"] = "accepted"
        layout.append_jsonl(
            root.evolution.task(meta["task"]).lineage,
            {
                "stamp": layout.stamp(),
                "event": "fold",
                "from_rev": meta["input_rev"],
                "to_rev": to_rev,
                "mix_version": version,
                "rewrite": f"rewrites/{rewrite.path.name}",
            },
        )
        log.info(
            "fold %s: r%d -> r%d, %s (%s)",
            meta["task"],
            meta["input_rev"],
            to_rev,
            " ".join(f"{k}={v}" for k, v in res.items()),
            (meta.get("resources") or {}).get("source") or "inherited",
        )
    log.info(
        "published mix v%04d (%d rows, %d folded) -> %s",
        version,
        len(order),
        len(folded),
        path.name,
    )
    log.info(
        "fold timing: prep %.1fs for %d rewrites on %d workers (parallel), "
        "commit %.1fs (serial)",
        prep_sec,
        len(prepared),
        max(workers, 1),
        time.monotonic() - t_commit,
    )
    return version


# --------------------------------------------------------------------------
# What the loop rebuilds from its files
# --------------------------------------------------------------------------


def _rewrite_metas(root: layout.Root):
    for task in root.evolution.task_dirs():
        for rw in task.rewrite_dirs():
            try:
                meta = json.loads(rw.meta.read_text())
            except (OSError, ValueError):
                continue
            if meta.get("dry"):
                continue
            yield task, rw, meta


def _queue(root: layout.Root, ledger: dict[str, dict], running: set[str]) -> dict:
    """The tasks a free worker could start now, counted once per task.

    `pending` counts raw signal files: several per task, the running rewrites'
    own signals and ones a round will supersede. This applies choose() and the
    resolution and deferral rules the way select_work does, so `waiting_tasks` is
    the backlog a free worker would start.
    """
    listing: dict = {}
    found = [
        s
        for s in discover(root, ledger)
        if s.data is not None and not s.junk and _resolved_by(root, s, listing) is None
    ]
    picks, _ = choose(root, found)
    waiting = {s.task for s in picks if not _deferred(root, s)} - running
    return {"waiting_tasks": len(waiting), "running": len(running)}


def rebuild_status(
    root: layout.Root,
    *,
    active_tasks: set[str] | None = None,
) -> dict:
    """status.json from the ledger and every task's rewrite files. No counter
    is carried over; losing the file loses nothing.

    During a round, ``active_tasks`` are the tasks the coordinator has a live
    rewrite of. The live set overrides the on-disk count because node-local workspaces are not visible
    under the durable root until they finish publishing.
    """
    ledger = load_ledger(root)
    by_outcome: dict[str, int] = {}
    for line in ledger.values():
        by_outcome[line.get("outcome", "?")] = (
            by_outcome.get(line.get("outcome", "?"), 0) + 1
        )
    rewrites = {"running": 0, "accepted": 0, "failed": 0, "kept": 0}
    rejected: dict[str, int] = {}
    routes: dict[str, dict[str, int]] = {}
    running_on_disk: set[str] = set()
    for _task, _rw, meta in _rewrite_metas(root):
        st = meta.get("status")
        route = meta.get("route")
        if route is not None:
            outcomes = routes.setdefault(route, {})
            outcomes[st] = outcomes.get(st, 0) + 1
        if st == "running" and meta.get("task"):
            running_on_disk.add(meta["task"])
        if st == "rejected":
            stage = meta.get("stage") or "unknown"
            rejected[stage] = rejected.get(stage, 0) + 1
        elif st == "interrupted":
            rewrites["failed"] += 1
        elif st in rewrites:
            rewrites[st] += 1
    live = root.mix.live_version()
    status = {
        "updated": layout.stamp(),
        "mix_version": live[0] if live else None,
        "pending": len(_pending(root, ledger)),
        "handled": by_outcome.get("handled", 0),
        "deferred": by_outcome.get("deferred", 0),
        "junk": by_outcome.get("junk", 0),
        "superseded": by_outcome.get("superseded", 0),
        "resolved": by_outcome.get("resolved", 0),
        "rewrites_running": (
            rewrites["running"] if active_tasks is None else len(active_tasks)
        ),
        "accepted": rewrites["accepted"],
        "rejected": rejected,
        "failed": rewrites["failed"],
        "kept": rewrites["kept"],
        "routes": routes,
        "queue": _queue(
            root,
            ledger,
            running_on_disk if active_tasks is None else active_tasks,
        ),
    }
    layout.write_json_atomic(root.evolution.status, status)
    return status


def _refresh_status_during_round(
    root: layout.Root, *, active_tasks: set[str]
) -> None:
    """Refresh observability without making it a dependency of rewrite work."""
    try:
        rebuild_status(root, active_tasks=active_tasks)
    except Exception as error:  # noqa: BLE001 -- status is a derived snapshot
        log.warning(
            "in-round status refresh failed; rewrite work continues: %s: %s",
            type(error).__name__,
            error,
        )


def _snapshot_lineage(root: layout.Root, note: str) -> None:
    """Commit the loop's records so the history of every task survives.

    The loop is the research object, and analysing it needs the lineage
    (r0 -> r1 -> r2 ...) with the verdicts that produced it. A git repository
    whose metadata sits in evolution/.git and whose work tree is the root
    keeps every round as a commit of the ledger, status.json, every task's
    lineage.jsonl and rewrite.json, and the mix manifests -- and nothing
    else: packages, sessions and traces hold verifiers, solutions and
    transcripts, and the paths are named one by one rather than `add -A`ed.
    Failures never break the round -- versioning is an audit trail, not a
    dependency.
    """
    git_dir = root.evolution.path / ".git"
    base = ["git", f"--git-dir={git_dir}", f"--work-tree={root.path}"]
    # cwd is the work tree, so the pathspecs below are relative to its top.
    run = dict(cwd=str(root.path), check=True, capture_output=True, text=True)
    try:
        if not git_dir.exists():
            subprocess.run(base + ["init", "-q"], **run)
            subprocess.run(base + ["config", "core.worktree", str(root.path)], **run)
        ev = root.evolution.path
        paths = [ev / "ledger.jsonl", ev / "status.json"]
        paths += ev.glob("outcomes/*.jsonl")
        paths += ev.glob("tasks/*/lineage.jsonl")
        paths += ev.glob("tasks/*/rewrites/*/rewrite.json")
        paths += root.mix.history.glob("*.manifest.json")
        spec = (
            "\n".join(str(p.relative_to(root.path)) for p in paths if p.exists()) + "\n"
        )
        subprocess.run(base + ["add", "--pathspec-from-file=-"], input=spec, **run)
        r = subprocess.run(
            base
            + [
                "-c",
                "user.email=evolve@terminal-rl",
                "-c",
                "user.name=evolve-loop",
                "commit",
                "-q",
                "-m",
                note,
            ],
            cwd=str(root.path),
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log.info("lineage snapshot committed: %s", note)
    except Exception as e:  # noqa: BLE001 -- never fail a round on archiving
        log.warning("lineage snapshot failed: %s", e)


# --------------------------------------------------------------------------
# A round
# --------------------------------------------------------------------------


def _replay_signal(root: layout.Root, sid: str) -> Signal:
    """The one signal `--signal` names, ledger or no ledger."""
    run_name, _, stem = sid.partition("/")
    run = root.run(run_name)
    path = run.signals / f"{stem}.json"
    if not path.exists():
        raise SystemExit(f"no signal file at {path}")
    data, junk = read_signal(path)
    if data is None:
        raise SystemExit(f"{sid}: {junk or 'does not parse'}")
    return Signal(run, path, sid, data, None)


def _names(path: Path, listing: dict) -> frozenset[str]:
    """A directory's entry names, listed once per call; empty when absent."""
    key = ("names", path)
    if key not in listing:
        try:
            listing[key] = frozenset(os.listdir(path))
        except (FileNotFoundError, NotADirectoryError):
            listing[key] = frozenset()
        except OSError as exc:
            # A transient storage error: treat as empty now, list again next call.
            log.warning("could not list %s: %s", path, exc)
            return frozenset()
    return listing[key]


def _groups(run: layout.Run, task: str, listing: dict) -> dict[int, list[Path]]:
    """A run's rollout records of one task by group, listed once per call.

    One listing of the run's rollouts/ answers "has this run any record of the
    task" for every task, instead of one is_dir() per task per run."""
    key = (run.path, task)
    if key not in listing:
        groups: dict[int, list[Path]] = {}
        name = layout.safe(task)
        if name in _names(run.rollouts, listing):
            folder = run.rollouts / name
            for entry in _names(folder, listing):
                if not (entry.startswith("g") and entry.endswith(".jsonl")):
                    continue
                group, sep, _ = entry[1:-len(".jsonl")].partition("-r")
                if sep and group.isdigit():
                    groups.setdefault(int(group), []).append(folder / entry)
        listing[key] = groups
    return listing[key]


# A rollout record's mtime and the header fields _resolved_by reads, kept once
# the record is older than FRESH_SEC. Records are renamed into place and never
# rewritten (rollout_record.write_record), so a settled one is stat'ed and read
# once per loop rather than on every refill and status refresh.
_RECORD_MTIMES: dict[Path, float] = {}
_RECORD_HEADERS: dict[Path, dict] = {}
_HEADER_KEYS = ("rev", "infra_failed", "reward", "finish_reason")


def _record_mtime(path: Path, now: float) -> float:
    mtime = _RECORD_MTIMES.get(path)
    if mtime is None:
        mtime = path.stat().st_mtime
        if now - mtime >= FRESH_SEC:
            _RECORD_MTIMES[path] = mtime
    return mtime


def _record_header(path: Path) -> dict:
    head = _RECORD_HEADERS.get(path)
    if head is None:
        full = rollout_record.read_header(path)
        head = {k: full[k] for k in _HEADER_KEYS if k in full}
        if path in _RECORD_MTIMES:
            _RECORD_HEADERS[path] = head
    return head


def _resolved_by(root: layout.Root, s: Signal, listing: dict) -> str | None:
    """Why a signal no longer asks for a rewrite, or None while it still does.

    It does once the task has finished another whole group at the same
    revision, after the signal, without a signal of its own: the trainer
    judged that group inside both thresholds, so the task now trains. A
    later group that did signal is a newer request, which choose() prefers.
    Without rollout records there is no such evidence and the signal waits.
    """
    task, rev, own = s.task, int(s.data["rev"]), s.group
    size = len(_groups(s.run, task, listing).get(own, []))
    created = s.data.get("created")
    if size < 2 or not created:
        return None
    since, now = layout.parse_stamp(created), time.time()
    if "runs" not in listing:
        listing["runs"] = root.run_dirs()
    for run in listing["runs"]:
        for group, paths in sorted(_groups(run, task, listing).items()):
            if run.path == s.run.path and group == own:
                continue
            # A whole group, finished after this signal and long enough ago
            # that a signal of its own would already be on disk.
            if (
                len(paths) != size
                or run.signal(task, group).name in _names(run.signals, listing)
            ):
                continue
            last = max(_record_mtime(path, now) for path in paths)
            if last <= since or now - last < FRESH_SEC:
                continue
            headers = [_record_header(path) for path in paths]
            if any(int(h.get("rev", -1)) != rev for h in headers):
                continue
            scored = [
                h["reward"]
                for h in headers
                if not h.get("infra_failed")
                and h.get("finish_reason") not in RULE_TERMINATED_REASONS
                and math.isfinite(float(h.get("reward", math.nan)))
            ]
            if len(scored) >= TRAINER_MIN_SCORED:
                solved = sum(reward > 0 for reward in scored)
                return (
                    f"{run.name} g{group} at rev {rev} solved {solved}/{len(scored)} "
                    "without a signal"
                )
    return None


def _created(s: Signal) -> str:
    return str(s.data.get("created", ""))


def _defer_reason(root: layout.Root, s: Signal) -> str | None:
    """Explain why an easier signal must not start a rewrite."""
    if s.data["direction"] != "easier":
        return None
    if not SIMPLIFY_ENABLED and not unsolved(root, s.task, int(s.data["rev"])):
        return "the easier direction is off"
    total = int(s.data["total"])
    if total < EASIER_MIN_SCORED_ATTEMPTS:
        return f"only {total} scored attempts; need {EASIER_MIN_SCORED_ATTEMPTS}"
    num_attempts = len(s.data["attempts"])
    if num_attempts < EASIER_MIN_SCORED_ATTEMPTS:
        return (
            f"only {num_attempts} scored trace records for {total} attempts; "
            f"need {EASIER_MIN_SCORED_ATTEMPTS}"
        )
    return None


def _deferred(root: layout.Root, s: Signal) -> bool:
    return _defer_reason(root, s) is not None


def select_work(
    root: layout.Root,
    ledger: dict[str, dict],
    result: dict,
    *,
    only: str | None,
    limit: int | None,
    dry: bool,
    signal: str | None,
    running: frozenset[str] = frozenset(),
    stuck: frozenset[str] = frozenset(),
) -> list[Signal] | None:
    """The signals to start now, newest first, and the bookkeeping for the
    ones not started.

    ``running`` names the tasks with a rewrite in progress: their signals wait
    for it, since a second rewrite of the same package would race it for
    r<N+1>. When it ends, the next refill sees them against the task's new
    revision: one about the revision just replaced is superseded, one about
    the current revision starts, whatever the previous verdict was.
    ``stuck`` names signals whose handling raised before a rewrite existed;
    they close no ledger line, so without it a refill would hand them back
    forever. A later round tries them again.

    The backlog runs newest signal first. A signal whose task has since
    finished a mixed group at the same revision is closed as `resolved`
    (_resolved_by) instead of started.

    Returns None when there was nothing to look at at all (the caller reports
    "no signals"), and a possibly empty list otherwise -- empty meaning every
    signal was junk, deferred or superseded rather than started.

    Re-entrant by construction: discover() and choose() only read, and the
    ledger the caller passes decides what is still pending. Every path that
    closes a signal writes its ledger line before this returns, all in one
    append.
    """
    lines: list[dict] = []
    if signal:
        picks, rest = [_replay_signal(root, signal)], []
        found: list[Signal] = []
    else:
        found = discover(root, ledger)
        if only:
            found = [s for s in found if s.task == only]
        found = [s for s in found if s.task not in running and s.sid not in stuck]
        listing: dict = {}
        live = []
        for s in found:
            if s.junk:
                log.warning("junk signal %s: %s", s.sid, s.junk)
                result["junk"] += 1
                if not dry:
                    lines.append(_ledger_entry(s, "junk", reason=s.junk))
            elif s.data is not None:
                why = _resolved_by(root, s, listing)
                if why is None:
                    live.append(s)
                    continue
                log.info("%s %s resolved: %s", s.task, s.sid, why)
                result["resolved"] += 1
                if not dry:
                    lines.append(_ledger_entry(s, "resolved", reason=why))
        picks, rest = choose(root, live)
    if not picks and not rest:
        _append_ledger(root, lines)
        return None

    todo, deferred = [], []
    for s in sorted(picks, key=_created, reverse=True):
        reason = None if signal else _defer_reason(root, s)
        if reason is not None:
            deferred.append((s, reason))
        else:
            todo.append(s)
    if limit:
        todo = todo[:limit]
    closing = {s.sid for s in todo} | {s.sid for s, _reason in deferred}
    for s, reason in deferred:
        log.info("%s %s deferred: %s", s.task, s.sid, reason)
        result["deferred"] += 1
        if not dry:
            lines.append(_ledger_entry(s, "deferred", reason=reason))
    for s, why, pick in rest:
        # A signal yielding to a pick this round did not reach (--limit)
        # stays pending; one about a gone revision is closed now.
        if pick is not None and pick.sid not in closing:
            continue
        log.info("%s %s superseded: %s", s.task, s.sid, why)
        result["superseded"] += 1
        if not dry:
            lines.append(_ledger_entry(s, "superseded", reason=why))
    _append_ledger(root, lines)
    return todo


class _Closer:
    """Folds and closes finished rewrites on one thread of its own, in order.

    Folding a batch on the FUSE root takes minutes (strip, row build, the
    renames, a 19 MB mix version, then the lineage and ledger lines); done on
    the coordinator it held every finished rewrite and every free slot until
    it was over. Batches are taken in the order they were handed over; batches
    queued while a fold runs are folded together, still one mix version per
    fold. A task stays in ``busy`` from hand-over until its ledger line is
    written, so the coordinator never starts a second rewrite of it while its
    signal is still pending. On an error the thread stops with its tasks still
    busy and the coordinator re-raises; what it did not close stays open for
    the next round, as before.
    """

    def __init__(self, root: layout.Root, *, dry: bool, result: dict) -> None:
        self.root, self.dry, self.result = root, dry, result
        self.error: BaseException | None = None
        self._cv = threading.Condition()
        self._queue: list[dict] = []
        self._busy: set[str] = set()
        self._working = False
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="evolve-closer", daemon=True)
        self._thread.start()

    def submit(self, batch: list[dict]) -> None:
        if not batch:
            return
        with self._cv:
            self._queue.extend(batch)
            self._busy.update(h["meta"]["task"] for h in batch)
            self._cv.notify_all()

    def busy(self) -> frozenset[str]:
        """Tasks handed over and not closed yet."""
        with self._cv:
            return frozenset(self._busy)

    def idle(self) -> bool:
        with self._cv:
            return not self._queue and not self._working

    def wait_idle(self, timeout: float | None) -> bool:
        with self._cv:
            return self._cv.wait_for(
                lambda: (not self._queue and not self._working) or self.error is not None,
                timeout,
            )

    def raise_if_failed(self) -> None:
        if self.error is not None:
            raise self.error

    def stop(self) -> None:
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
        self._thread.join()

    def _run(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._queue or self._stopping)
                if not self._queue:
                    return
                batch, self._queue = self._queue, []
                self._working = True
            try:
                self._close_batch(batch)
            except BaseException as error:  # noqa: BLE001 -- re-raised by the coordinator
                log.exception("fold/close of %d rewrites failed: %s", len(batch), error)
                with self._cv:
                    self.error = error
                    self._working = False
                    self._cv.notify_all()
                return
            with self._cv:
                self._busy.difference_update(h["meta"]["task"] for h in batch)
                self._working = False
                self._cv.notify_all()

    def _close_batch(self, batch: list[dict]) -> None:
        if not self.dry:
            # fold() settles each rewrite one way or the other (accepted with
            # a revision, or rejected/failed at the fold); closing them
            # afterwards keeps the ledger line the last thing written.
            accepted = [h for h in batch if h["status"] == "accepted"]
            t0 = time.monotonic()
            version = fold(self.root, accepted)
            if version is not None:
                self.result["mix_version"] = version
            if accepted:
                log.info(
                    "fold of %d accepted rewrites took %.1fs (mix v%s)",
                    len(accepted),
                    time.monotonic() - t0,
                    f"{version:04d}" if version is not None else " unchanged",
                )
        t0 = time.monotonic()
        closed = 0
        for h in batch:
            if h["status"] != "accepted" or not self.dry:
                _close(self.root, h, dry=self.dry)
                closed += 1
        if closed and not self.dry:
            log.info("closed %d rewrites in %.1fs", closed, time.monotonic() - t0)


class _StatusRefresher:
    """The in-round status.json refresh on a thread of its own: a rebuild
    scans every rewrite.json under the root, about two minutes on FUSE, and
    the coordinator must not wait for a derived snapshot."""

    def __init__(self, root: layout.Root) -> None:
        self.root = root
        self._thread: threading.Thread | None = None

    def start(self, active_tasks: set[str]) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._refresh, args=(set(active_tasks),), name="evolve-status", daemon=True
        )
        self._thread.start()

    def _refresh(self, active_tasks: set[str]) -> None:
        t0 = time.monotonic()
        _refresh_status_during_round(self.root, active_tasks=active_tasks)
        log.info("in-round status refresh took %.1fs", time.monotonic() - t0)

    def join(self) -> None:
        # Before the end-of-round rebuild, which a late in-round one would
        # otherwise overwrite with an older snapshot.
        if self._thread is not None:
            self._thread.join()


def _mix_key(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_mtime_ns, st.st_size


def run_round(
    root: layout.Root,
    *,
    only: str | None = None,
    limit: int | None = None,
    workers: int = 8,
    dry: bool = False,
    signal: str | None = None,
) -> dict:
    # A resumed trainer can reuse a signal or record path; trust the caches
    # for one round, not for the life of the process.
    _SIGNALS.clear()
    _RECORD_MTIMES.clear()
    _RECORD_HEADERS.clear()
    ledger = load_ledger(root)
    # A replay is inspection: it must not fold a rewrite of a revision the
    # task has moved past, or close a signal the ledger already closed.
    dry = dry or bool(signal)
    result: dict = {
        "handled": 0,
        "accepted": 0,
        "deferred": 0,
        "junk": 0,
        "superseded": 0,
        "resolved": 0,
        "counts": {},
        "mix_version": None,
    }
    if not admission_is_open(dry=dry):
        return {**result, "reason": "training admission paused"}
    todo = select_work(
        root,
        ledger,
        result,
        only=only,
        limit=limit,
        dry=dry,
        signal=signal,
    )
    if todo is None:
        return {**result, "reason": "no signals"}
    if not todo:
        return result

    if not dry and rw_workspace.recover(root):
        # Nothing of this root is running between rounds, so a recovered
        # record still `running` was cut short by a failed publication.
        finalize_interrupted(root)

    # Freeze the epoch-derived repair budget for this round before its workers
    # enter node-local workspaces. The repair loops can then consult the cache
    # without reading the remote mix or trainer logs mid-session.
    if rw_workspace.configured_root(root) is not None:
        ec.rewrite_budget_sec(root, refresh=True)

    # What box training gives each task and which pin hook it grades under,
    # from the mix the trainer reads. The mix is 19 MB on the FUSE root, so it
    # is read when work is about to be submitted rather than per completion,
    # and re-read only when a fold has published a new version since.
    handled: list[dict] = []
    refill = not signal and not limit and not dry
    last_status_refresh = time.monotonic()
    closer = _Closer(root, dry=dry, result=result)
    status = _StatusRefresher(root)
    declared_cache: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs: dict = {}
            # Signals whose handle() raised before a rewrite existed: they close
            # no ledger line, so a refill would otherwise pick them again forever.
            stuck: set[str] = set()

            def declared_now():
                canary = bool(PolicyCanaryConfig.from_env().group_size)
                key = (_mix_key(root.mix.live), canary)
                if declared_cache.get("key") != key:
                    input_rows = {} if canary else None
                    declared_cache["value"] = (
                        *read_declared(root.mix.live, rows_out=input_rows),
                        input_rows,
                    )
                    declared_cache["key"] = key
                return declared_cache["value"]

            def fill(batch: list[Signal]) -> None:
                """Submit up to the free slots, newest mix in hand."""
                if not admission_is_open(dry=dry):
                    return
                batch = batch[: workers - len(futs)]
                if not batch:
                    return
                declared, pretests, protecteds, input_rows = declared_now()
                for s in batch:
                    if not admission_is_open(dry=dry):
                        break
                    futs[
                        ex.submit(
                            handle,
                            root,
                            s,
                            declared=declared,
                            dry=dry,
                            pretests=pretests,
                            protecteds=protecteds,
                            input_row=input_rows.get(s.task) if input_rows is not None else None,
                        )
                    ] = s

            def fill_free_slots() -> None:
                """Take pending signals into the free slots.

                The busy set is taken before the ledger is read: the closer
                writes a task's ledger line before it lets go of the task, so
                a task missing from the set has its line in the ledger read
                below, and its finished signal is never handed out again."""
                t0 = time.monotonic()
                running = frozenset(s.task for s in futs.values()) | closer.busy()
                before = len(futs)
                try:
                    more = select_work(
                        root,
                        load_ledger(root),
                        result,
                        only=only,
                        limit=limit,
                        dry=dry,
                        signal=None,
                        running=running,
                        stuck=frozenset(stuck),
                    )
                    fill(more or [])
                except OSError as e:
                    # A transient storage error (EPERM/EIO from the FUSE root)
                    # skips this refill; raising here ended the round and left
                    # every free slot empty until the running rewrites drained.
                    log.warning("refill skipped: %s: %s", type(e).__name__, e)
                    return
                took = time.monotonic() - t0
                if len(futs) > before or took >= 5:
                    log.info(
                        "refill took %.1fs: %d started, %d running, %d closing",
                        took,
                        len(futs) - before,
                        len(futs),
                        len(closer.busy()),
                    )

            fill(todo)
            while True:
                closer.raise_if_failed()
                if not futs:
                    # Nothing running: what is still being folded frees its
                    # tasks when it is closed, and their signals start then.
                    if not closer.idle():
                        closer.wait_idle(FREE_SLOT_POLL_SEC if refill else None)
                        closer.raise_if_failed()
                    if refill and admission_is_open():
                        fill_free_slots()
                    if not futs and closer.idle():
                        break
                    continue
                # A new signal can arrive while the first rewrite is still
                # running. Poll only when there is an idle worker; otherwise
                # wait for one to finish. The idle loop already watches signals
                # every 5 s.
                wait_timeout = (
                    FREE_SLOT_POLL_SEC if refill and len(futs) < workers else None
                )
                if not dry:
                    until_status_refresh = max(
                        0.0,
                        STATUS_REFRESH_SEC - (time.monotonic() - last_status_refresh),
                    )
                    wait_timeout = (
                        until_status_refresh
                        if wait_timeout is None
                        else min(wait_timeout, until_status_refresh)
                    )
                done, _ = wait(
                    list(futs),
                    timeout=wait_timeout,
                    return_when=FIRST_COMPLETED,
                )
                # Rewrites handled in this wait, folded and closed together.
                finished: list[dict] = []
                for fut in done:
                    s = futs.pop(fut)
                    try:
                        h = fut.result()
                    except NoSeed as e:
                        log.warning("junk signal %s: %s", s.sid, e)
                        result["junk"] += 1
                        if not dry:
                            _ledger_line(root, s, "junk", reason=str(e)[:200])
                        continue
                    except Exception as e:  # noqa: BLE001 -- one signal, not the round
                        log.exception(
                            "%s: handling failed before a rewrite existed: %s", s.sid, e
                        )
                        stuck.add(s.sid)
                        continue
                    meta = h["meta"]
                    log.info(
                        "%s %s r%s %s %s/%s -> %s (%s%s) %s",
                        meta["task"],
                        s.sid,
                        meta["input_rev"],
                        meta["job"],
                        s.data.get("solved"),
                        s.data.get("total"),
                        h["status"],
                        meta.get("stage") or "-",
                        f": {meta['reason'][:200]}" if meta.get("reason") else "",
                        _rewrite_ref(root, h["rewrite"]),
                    )
                    handled.append(h)
                    finished.append(h)
                # Folded as soon as they are accepted, every rewrite that
                # finished in this wait into one mix version, rather than once
                # for the whole round. A round of 64 concurrent rewrites runs
                # for hours, and a loop stopped inside it (a restart for a
                # config change, a killed unit) used to leave every accepted
                # package sitting as `running` with no revision behind it; on
                # 2026-09-14 that was 7 accepted rewrites recovered by hand and
                # 87 more at risk. One version per batch rather than per
                # rewrite: each version rewrites the whole mix, so N rewrites
                # finishing together cost one write, not N in a queue.
                #
                # The closer does it on its own thread: a fold on the FUSE
                # root takes minutes, and done here it held every other
                # finished rewrite and every free slot until it was over
                # (2026-10-14: finished rewrites collected in 5-minute bursts).
                # The tasks handed over count as running until they are closed
                # -- their signals are still pending in the ledger, and a second
                # rewrite would race the fold for r<N+1>.
                closer.submit(finished)
                # A free slot takes the next pending signal now, including one
                # written while all existing rewrites are still running.
                #
                # Not for a dry round, which closes no ledger line: the junk,
                # deferred and superseded signals it reports are still pending on
                # the next call, and refilling would count each of them once per
                # refill. A dry round is a snapshot of one moment by design.
                if refill and len(futs) < workers and admission_is_open():
                    fill_free_slots()
                if (
                    not dry
                    and time.monotonic() - last_status_refresh >= STATUS_REFRESH_SEC
                ):
                    status.start({s.task for s in futs.values()})
                    last_status_refresh = time.monotonic()
    finally:
        closer.stop()
        status.join()
    closer.raise_if_failed()

    for h in handled:
        result["counts"][h["status"]] = result["counts"].get(h["status"], 0) + 1
    result["handled"] = len(handled)
    result["accepted"] = result["counts"].get("accepted", 0)
    return result


def _wait_for_signals(root: layout.Root, max_wait: int, tick: float = 5.0) -> None:
    """Sleep until a pending signal shows up, or `max_wait` seconds pass.

    Training writes signals in bursts and then goes quiet for a whole step,
    so a flat sleep made a signal written a second after a round wait the
    better part of two minutes for nothing. Watch instead and return as soon
    as something lands; the timeout is only there so a round still happens
    if a signal is ever written without us noticing.
    """
    ledger = load_ledger(root)
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if admission_is_open() and _pending(root, ledger):
            return
        time.sleep(tick)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one round then exit")
    ap.add_argument(
        "--interval", type=int, default=120, help="seconds between rounds when looping"
    )
    ap.add_argument("--only", help="handle only this task's signals (dev)")
    ap.add_argument("--limit", type=int, help="cap tasks handled per round (dev)")
    ap.add_argument(
        "--workers", type=int, default=8, help="concurrent rewrites per round"
    )
    ap.add_argument(
        "--dry",
        action="store_true",
        help="handle, but publish no mix version and write no ledger or "
        "lineage line; everything lands under the rewrite directory. "
        "Implies --once",
    )
    ap.add_argument(
        "--signal",
        metavar="RUN/TASK--gN",
        help="replay this signal whether or not the ledger closed it; " "implies --dry",
    )
    args = ap.parse_args()
    root = layout.Root.from_env()
    # Before the lock and the log: a root whose epoch is shorter than the
    # final-validation reserve would fail every rewrite at its first session.
    budget = ec.rewrite_budget_sec(root, refresh=True)
    if budget <= ec.FINAL_VALIDATION_RESERVE_SEC:
        raise SystemExit(
            f"rewrite budget {budget:.0f}s ({ec.rewrite_budget_why()}) "
            f"does not exceed the {ec.FINAL_VALIDATION_RESERVE_SEC}s final-validation "
            "reserve, so no session could start; set EVOLVE_REWRITE_BUDGET_SEC or "
            "lower EVOLVE_FINAL_VALIDATION_RESERVE_SEC"
        )
    tool_bin = rw_workspace.prepare_tool_bin(root)
    if tool_bin != root.bin:
        os.environ[rw_workspace.TOOL_BIN_ENV] = str(tool_bin)
    # Before the log file is even opened: a refused second instance must not
    # write "loop up" into the log the first one owns.
    _lock_fd = acquire_singleton(
        root.evolution.loop_lock
    )  # noqa: F841 -- held for the process lifetime

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        # FileHandler only. The unit launches the loop with `>> loop.log 2>&1`
        # for stray prints and tracebacks; a StreamHandler here would write
        # every record to the same file a second time.
        handlers=[logging.FileHandler(root.evolution.loop_log)],
    )
    if work_root := rw_workspace.configured_root(root):
        log.info(
            "active rewrites use node-local storage at %s; completed records "
            "publish to %s",
            work_root,
            root.evolution.path,
        )
        log.info("agent tools staged at %s", tool_bin)
    log.info(
        "rewrite budget at start: %.0fs (%s); %ds reserved for final validation, "
        "%.0fs left for sessions; recomputed every 10 min",
        budget,
        ec.rewrite_budget_why(),
        ec.FINAL_VALIDATION_RESERVE_SEC,
        budget - ec.FINAL_VALIDATION_RESERVE_SEC,
    )
    if not (args.dry or args.signal):
        # The singleton lock excludes another live loop before orphaned rewrites
        # are marked. The earlier loop may have exited before restart_evolve.sh
        # could stop it and finalize its records. Local workspaces are published
        # first, so what they hold is marked with the rest.
        t0 = time.monotonic()
        published = rw_workspace.recover(root)
        log.info(
            "startup: published %d local rewrites in %.1fs",
            len(published),
            time.monotonic() - t0,
        )
        t0 = time.monotonic()
        # The ledger leaves open what may still read `running`; what recover
        # just published is examined whatever the ledger says.
        recovered = finalize_interrupted(root, include=published)
        log.info(
            "startup: finalized orphaned records in %.1fs: %s",
            time.monotonic() - t0,
            recovered,
        )
        if recovered["failed"]:
            raise RuntimeError(f"could not finalize orphaned rewrites: {recovered}")
        if recovered["marked"]:
            log.warning(
                "finalized records left running by an earlier loop: %s", recovered
            )
    # A row declaring no daytona_* is boxed at this size, here and (if the
    # trainer's env agrees) in training. None means the harness default 2/4/6,
    # which is not what the trainer runs at unless its env says so too.
    log.info(
        "fleet default for rows declaring no daytona_*: cpu=%s mem_gb=%s "
        "disk_gb=%s (from TT_DAYTONA_CPU/MEM_GB/DISK_GB; None = harness "
        "default 2/4/6)",
        FLEET["cpu"],
        FLEET["mem_gb"],
        FLEET["disk_gb"],
    )

    dry = args.dry or bool(args.signal)
    if args.once or args.only or dry:
        r = run_round(
            root,
            only=args.only,
            limit=args.limit,
            workers=args.workers,
            dry=dry,
            signal=args.signal,
        )
        log.info("round done%s: %s", " (dry)" if dry else "", r)
        if not dry:
            rebuild_status(root)
            if r.get("handled"):
                _snapshot_lineage(root, f"once: {r}")
        return
    log.info(
        "evolve loop up: pid=%d root=%s interval=%ds workers=%d simplify=%s",
        os.getpid(),
        root.path,
        args.interval,
        args.workers,
        SIMPLIFY_ENABLED,
    )
    while True:
        try:
            r = run_round(root, workers=args.workers)
            if (
                r.get("handled")
                or r.get("junk")
                or r.get("deferred")
                or r.get("superseded")
                or r.get("resolved")
            ):
                log.info("round: %s", r)
            rebuild_status(root)
            if r.get("handled"):
                _snapshot_lineage(root, f"round: {r}")
        except Exception as e:  # noqa: BLE001
            log.exception("round failed: %s", e)
        _wait_for_signals(root, args.interval)


if __name__ == "__main__":
    main()
