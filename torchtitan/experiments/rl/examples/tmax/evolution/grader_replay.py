# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""An easier rewrite must not make the grader stricter.

The oracle gate asks whether the rewrite's own reference solution passes the
rewrite's own grader. It cannot see a rewrite that, while adding help, also
tightens the tests: measured on TB2.1 torch-tensor-parallelism, "easier"
rewrites moved to 3-D inputs, a weighted loss, a nonzero bias, input-gradient
checks and an AST check on the mechanism, and 16 student finals rebuilt from
the training records passed the original grader but failed the rewritten one.

This check replays solutions an earlier grader passed under the rewrite's
grader, each on a fresh Daytona sandbox through the same probe as the oracle
(daytona_revalidate.py, the training harness and grading contract):

- the training finals of the parent version that scored 1: each rollout's
  recorded keystrokes (a vanillux record's bash commands), replayed turn by
  turn through Terminus as a recorded
  solution (recorded_solution.py's action format), so the workspace is rebuilt
  by the commands that built it when it was graded;
- the parent revision's reference solution and the original task's (r0), when
  they differ from the rewrite's own (which the oracle already ran).

A solution that fails under the rewrite is run once more in the package it
passed in (the parent, or r0 for the original reference). Only a solution that
passes there and fails here counts against the rewrite; one that fails in both
is a replay the harness could not reproduce (recorded as ``unreplayable``),
and a probe that did not complete is an ``error``. Neither rejects.

Not covered: what a rollout did outside its keystrokes (the records keep no
per-command duration, so every command waits Terminus's default 1 s and the
next turn starts once the terminal is idle, at most ``TURN_GAP_SEC`` later);
finals of versions other than the parent; anything when the parent has no
passing final and both reference solutions equal the rewrite's own. Readout
records (``validation_rollouts/``, negative groups) are never read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from recorded_solution import ACTION_PATH
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

log = logging.getLogger("evolve")

# Off by default in the evolve loop; the overfit bench's rewriters turn it on.
ENV = "EVOLVE_EASIER_GRADER_REPLAY"
# How many of the parent's passing finals to replay, newest first. Each costs
# one sandbox, and one more if it fails under the rewrite.
MAX_FINALS = int(os.environ.get("EVOLVE_GRADER_REPLAY_MAX_FINALS", "8"))
# The longest a replayed turn waits for the previous one's commands. Fast
# replay moves on as soon as the terminal is idle; this only bounds a command
# still running (or a recorded-timing rerun, which waits it in full).
TURN_GAP_SEC = 20.0
# Probe stages that mean "did not complete", not "the solution failed".
_INFRA_STAGES = frozenset(
    {"daytona_error", "daytona_unavailable", "cancelled", "package_error", "no_solution"}
)


# vanillux_loop.SUBMIT_MARKER, spelled here so this module does not import the
# agent loop and its harness dependencies.
_SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def enabled(default: str = "0") -> bool:
    return os.environ.get(ENV, default) == "1"


def passing_finals(root: layout.Root, task_id: str, rev: int) -> list[Path]:
    """Training records of ``task_id`` at ``rev`` that scored 1, oldest first.

    Only ``runs/*/rollouts/`` is read, and a record with a negative group (a
    readout attempt) is skipped even there."""
    found = []
    for run in root.run_dirs():
        for path in (run.rollouts / layout.safe(task_id)).glob("g*-r*.jsonl"):
            try:
                head = rollout_record.read_header(path)
            except (OSError, ValueError):
                continue  # still being written
            group = head.get("group")
            if (
                str(head.get("task")) != str(task_id)
                or head.get("rev") != rev
                or not isinstance(group, int)
                or group < 0
                or head.get("infra_failed")
                or (head.get("reward") or 0.0) < 1.0
            ):
                continue
            found.append(((run.path.name, group, head.get("rollout") or 0), path))
    return [path for _, path in sorted(found)]


def actions_from_record(path: Path) -> list[dict]:
    """A training rollout as a recorded solution: every turn's keystrokes in
    order, then the two completion markers Terminus's submit takes.

    A vanillux record has no keystrokes: each turn ran one bash command (the
    first tool call in ``raw``) through ``eval`` in a shell that keeps cwd and
    exported variables. Each such command is typed as one ``eval`` of its
    base64, which runs it the same way in the replay's persistent shell without
    interactive bash reinterpreting it (history expansion, prompts on
    continuation lines). The submit marker ran nothing in the task and is
    skipped. A command that itself calls ``exit`` ends the replay's shell,
    which vanillux's per-command process did not."""
    head, turns = rollout_record.read_record(path)
    vanillux = head.get("agent") == "vanillux"
    actions: list[dict] = []
    offset, step = 0.0, 0
    for turn in turns:
        if vanillux:
            cmd = rollout_record.vanillux_command(turn.get("raw", ""))
            keys = (
                [f'eval "$(printf %s {base64.b64encode(cmd.encode()).decode()} | base64 -d)"\n']
                if cmd and cmd != "echo " + _SUBMIT_MARKER
                else []
            )
        else:
            keys = [k for k in turn.get("keystrokes") or () if k]
        if not keys:
            continue
        step = int(turn.get("turn") or step + 1)
        actions += [
            {
                "kind": "terminal_action",
                "step_id": step,
                "offset_sec": offset,
                "keystrokes": k,
                "duration": 1.0,
            }
            for k in keys
        ]
        offset += TURN_GAP_SEC
    for i in (1, 2):
        actions.append(
            {
                "kind": "completion_marker",
                "step_id": step + i,
                "offset_sec": offset + i - 1,
                "keystrokes": "",
                "duration": 0,
            }
        )
    return actions


def _solution_digest(solution: Path) -> str | None:
    if not solution.is_dir():
        return None
    h = hashlib.sha256()
    for path in sorted(p for p in solution.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(solution)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def _link_or_copy(src: str, dst: str) -> None:
    # The probe only reads the package, so a hardlink is as good as a copy
    # where both sides share a filesystem.
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def stage_package(
    base: Path, dest: Path, *, solution_dir: Path | None = None, actions: list[dict] | None = None
) -> Path:
    """``base`` with its solution replaced: a reference solution's directory,
    or a recorded action list. The harness's run/ and traces/ stay out."""
    skip = {"run", "traces", "solution"}
    shutil.copytree(
        base,
        dest,
        symlinks=True,
        copy_function=_link_or_copy,
        ignore=lambda d, names: [n for n in names if Path(d) == base and n in skip],
    )
    if solution_dir is not None:
        shutil.copytree(solution_dir, dest / "solution", symlinks=True)
    else:
        (dest / ACTION_PATH).parent.mkdir(parents=True, exist_ok=True)
        (dest / ACTION_PATH).write_text(json.dumps(actions))
    return dest


@dataclass(frozen=True)
class _Item:
    name: str
    kind: str  # final | parent_reference | original_reference
    home: Path  # the package whose grader passed it
    solution_dir: Path | None = None
    actions: list[dict] | None = None


def _brief(verdict: dict | None) -> dict:
    v = verdict or {"ok": False, "stage": "daytona_unavailable"}
    out = {k: v.get(k) for k in ("ok", "stage", "reward", "solve_exit")}
    if not v.get("ok") and v.get("why"):
        out["why"] = str(v["why"])[:200]
    return out


def _infra(verdict: dict | None) -> bool:
    return verdict is None or verdict.get("stage") in _INFRA_STAGES


def check(
    package: Path,
    *,
    parent: Path,
    original: Path | None,
    finals: list[Path],
    probe: Callable[..., dict | None],
    scratch: Path,
    resources: dict | None = None,
    pretest_file: Path | None = None,
    max_finals: int = MAX_FINALS,
) -> dict:
    """Replay what earlier graders passed under ``package``'s grader.

    ``probe(work, resources=..., pretest_file=...)`` is feedback_loop's
    daytona_probe. Returns ``ok`` False with ``why`` when some solution passes
    where it was graded before and fails here."""
    items: list[_Item] = []
    seen = {_solution_digest(package / "solution")}
    for kind, home in (("parent_reference", parent), ("original_reference", original)):
        digest = _solution_digest(home / "solution") if home is not None else None
        if digest is None or digest in seen:
            continue  # the oracle, or the other reference, already ran it
        seen.add(digest)
        items.append(_Item(kind, kind, home, solution_dir=home / "solution"))
    chosen = finals[-max_finals:] if max_finals > 0 else []
    for path in chosen:
        try:
            actions = actions_from_record(path)
        except (OSError, ValueError, KeyError) as e:
            log.warning("grader replay: unreadable record %s: %s", path, e)
            continue
        items.append(_Item(f"{path.parent.parent.parent.name}/{path.name}", "final", parent, actions=actions))
    summary = {
        "finals_available": len(finals),
        "finals_replayed": sum(1 for i in items if i.kind == "final"),
        "references_replayed": sorted(i.kind for i in items if i.kind != "final"),
    }
    if not items:
        return {"ok": True, **summary, "results": [], "note": "nothing to replay"}

    scratch.mkdir(parents=True, exist_ok=True)

    def run(i: int, item: _Item, where: Path, label: str) -> dict | None:
        work = scratch / f"{i:02d}-{label}"
        try:
            stage_package(
                where, work, solution_dir=item.solution_dir, actions=item.actions
            )
            return probe(work, resources=resources, pretest_file=pretest_file)
        except OSError as e:  # staging the copy: this host, not the rewrite
            return {"ok": False, "stage": "package_error", "why": f"{type(e).__name__}: {e}"}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    try:
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            here = list(
                pool.map(lambda a: run(a[0], a[1], package, "rewrite"), enumerate(items))
            )
            redo = [
                i for i, v in enumerate(here) if not (v or {}).get("ok") and not _infra(v)
            ]
            home = dict(
                zip(redo, pool.map(lambda i: run(i, items[i], items[i].home, "home"), redo))
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    results = []
    for i, (item, v) in enumerate(zip(items, here)):
        if (v or {}).get("ok"):
            verdict = "pass"
        elif _infra(v):
            verdict = "error"
        elif (home.get(i) or {}).get("ok"):
            verdict = "stricter"
        elif _infra(home.get(i)):
            verdict = "error"
        else:
            verdict = "unreplayable"
        row = {"name": item.name, "kind": item.kind, "verdict": verdict, "rewrite": _brief(v)}
        if i in home:
            row["home"] = _brief(home[i])
        results.append(row)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    stricter = [r["name"] for r in results if r["verdict"] == "stricter"]
    out = {"ok": not stricter, **summary, "counts": counts, "results": results}
    if stricter:
        out["why"] = (
            f"{len(stricter)} solution(s) the earlier grader passed fail the "
            f"rewrite's grader: {', '.join(stricter)}"
        )[:300]
    return out
