#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Overfit bench: one task, one job; what is measured is the rewriter.

    bench.py split   --evalset tb2_1.jsonl --bench tb21 --out <tasks dir>
    bench.py new     --tasks <tasks dir> --task <id> --sources <corpus dir>
                     --rewriter <name> --jobs-root <dir> [--bin <dir>]
                     [--seed-rows <jsonl>]
    bench.py seed    <job dir> --rows <jsonl> [--weight W]
    bench.py readout <job dir> [<job dir> ...] [--json]

``split`` turns a benchmark's eval JSONL into one single-row file per task.
``new`` makes a job: an experiment root (LAYOUT.md) whose seed mix is that one
row and whose validation set is the same row, the original task with its own
grader. ``seed`` (or ``new --seed-rows``) replaces the pool a job starts from
before training starts. ``readout`` reports, per job, the original task's
pass@5 at every validation and the first step from which it stays solved.
README.md has the bench definition and the rewriter interface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import new_root  # noqa: E402

layout = new_root.layout

EVALSET = "original.jsonl"
JOB_JSON = "job.json"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def split(evalset: Path, bench: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    tasks = {}
    for line in evalset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        task = row["metadata"]["instance_id"]
        if task in tasks:
            raise SystemExit(f"{evalset}: task {task} appears twice")
        (out / f"{task}.jsonl").write_text(line + "\n", encoding="utf-8")
        tasks[task] = _sha256(line)
    manifest = {
        "bench": bench,
        "evalset": str(evalset.resolve()),
        "evalset_sha256": layout.sha256_file(evalset),
        "tasks": tasks,
    }
    layout.write_json_atomic(out / "manifest.json", manifest)
    return manifest


def new_job(
    tasks: Path,
    task: str,
    sources: Path,
    rewriter: str,
    jobs_root: Path,
    bin_dir: Path | None,
    seed_rows: Path | None = None,
) -> Path:
    manifest = json.loads((tasks / "manifest.json").read_text())
    row_file = tasks / f"{task}.jsonl"
    if task not in manifest["tasks"]:
        raise SystemExit(f"{task} is not in {tasks}/manifest.json")
    if not (sources / "tasks" / task / "instruction.md").is_file():
        raise SystemExit(f"{sources}/tasks/{task} is not a task package")
    base = jobs_root / manifest["bench"] / f"{task}--{rewriter}"
    root = new_root.create(
        base,
        mix=row_file,
        sources=[sources],
        bin_dir=bin_dir,
        name=base.name,
        purpose=f"overfit bench {manifest['bench']}/{task}, rewriter {rewriter}",
        profile=None,
        fork_from=None,
    )
    # The readout set is the seed row itself, never served from the mix, so no
    # publication of the rewriter's can reach the grader it is scored by.
    evalsets = root.data / "evalsets"
    evalsets.mkdir(parents=True, exist_ok=True)
    (evalsets / EVALSET).write_text(row_file.read_text(encoding="utf-8"))
    layout.write_json_atomic(
        root.path / JOB_JSON,
        {
            "bench": manifest["bench"],
            "task": task,
            "rewriter": rewriter,
            "row_sha256": manifest["tasks"][task],
            "evalset": manifest["evalset"],
            "evalset_sha256": manifest["evalset_sha256"],
            "created": layout.stamp(),
        },
    )
    if seed_rows is not None:
        seed(root.path, seed_rows)
    return root.path


def seed(job: Path, rows_file: Path, weight: float = 1.0) -> dict:
    """Make the job's pool start from exactly these rows, each its own active
    version at ``weight`` (the original stays only if it is one of them).

    It writes ``data/mix/pool.json``, which the scheduler loads before its first
    draw, and publishes the rows as the next mix version, so the scheduler's
    boot import of ``live.jsonl`` finds nothing to change. A plain mix publish
    would come up with every weight at 1. Refused while a scheduler serves the
    job (its pool is the socket's then) and, whole, if any row does not parse."""
    from torchtitan.experiments.rl.examples.tmax.overfit_bench.pool import (
        PoolError,
        VersionPool,
    )
    from torchtitan.experiments.rl.examples.tmax.overfit_bench.rewriter_client import (
        scheduler_socket,
    )

    root = layout.Root(job)
    if scheduler_socket(root)[0] is not None:
        raise SystemExit(
            f"a scheduler is serving {job}; seed before training starts, "
            "or add versions through the socket"
        )
    rows = []
    for line in rows_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            # Rows are stamped as new_root stamps the job's own (rev 0 unless
            # given), so the original row from the tasks dir is the same version.
            row.setdefault("metadata", {}).setdefault("rev", 0)
            rows.append(row)
    if not rows:
        raise SystemExit(f"{rows_file}: no rows")
    pool = VersionPool()
    try:
        if root.mix.pool_state.is_file():
            pool.load_state_dict(json.loads(root.mix.pool_state.read_text()))
        else:
            # What the scheduler's boot import would make of the current mix.
            live = [json.loads(ln) for ln in root.mix.read_live().splitlines() if ln.strip()]
            pool.reconcile(live, source="boot")
        changed = pool.reconcile(rows, source="seed")
        for v in pool.active():
            if v.weight != weight:
                pool.reweight(v.id, weight, source="seed")
    except PoolError as e:
        raise SystemExit(f"{rows_file}: {e}") from e
    layout.write_json_atomic(root.mix.pool_state, pool.state_dict())
    if changed["added"] or changed["retired"]:
        root.mix.publish(pool.rows())
    return {"active": len(pool.active()), **changed}


_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|[^|]*\|\s*(PASS|FAIL)\s*\|")


def pass_at_k(n: int, c: int, k: int = 5) -> float:
    """Unbiased pass@k from n attempts with c passes."""
    if n < k:
        return float(c > 0)
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def validations(job: Path) -> dict[int, tuple[int, int]]:
    """{step: (attempts, passes)} for the original task, from every run's
    validation traces; a step validated again by a later run keeps the later."""
    task = json.loads((job / JOB_JSON).read_text())["task"]
    found: dict[int, tuple[int, int]] = {}
    for run in layout.Root(job).run_dirs():
        traces = run.path / "trainer" / "validation_traces"
        if not traces.is_dir():
            continue
        for step_dir in traces.glob("step-*"):
            index = step_dir / "INDEX.md"
            if not index.is_file():
                continue
            n = c = 0
            for line in index.read_text(encoding="utf-8").splitlines():
                m = _ROW.match(line)
                if m and m.group(1) == task:
                    n += 1
                    c += m.group(2) == "PASS"
            if n:
                found[int(step_dir.name.removeprefix("step-"))] = (n, c)
    return found


def _stamp(event: dict) -> float:
    from datetime import datetime, timezone

    return (
        datetime.strptime(event["stamp"], "%Y%m%d-%H%M%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def spend(job: Path, upto_step: int | None = None) -> dict:
    """What training cost, from every run's scheduler events: attempts
    (dropped all-pass / all-fail groups included, since each still takes the
    rollout engine for its 12 attempts), groups and how many of them trained,
    and wall clock (each run from its first event to its last). With
    ``upto_step``, attempts and wall clock stop at the first time that step
    was reached."""
    events = []
    for run in layout.Root(job).run_dirs():
        path = run.path / "scheduler" / "events.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a run killed mid-write
    events = [e for e in events if "stamp" in e]
    events.sort(key=_stamp)
    if upto_step is not None:
        end = next(
            (
                i
                for i, e in enumerate(events)
                if e["event"] == "step_done" and e["step"] >= upto_step
            ),
            None,
        )
        if end is None:
            return {}
        events = events[: end + 1]
    by_run: dict[str, list[float]] = {}
    for e in events:
        by_run.setdefault(e.get("run", ""), []).append(_stamp(e))
    groups = [e for e in events if e["event"] == "group_done"]
    return {
        "attempts": sum(e["event"] == "rollout_done" for e in events),
        "groups": len(groups),
        "trained_groups": sum(0 < g["passed"] < g["scored"] for g in groups),
        "steps": max((e["step"] for e in events if e["event"] == "step_done"), default=0),
        "hours": round(sum(max(t) - min(t) for t in by_run.values()) / 3600, 2),
    }


def readout(job: Path, threshold: float = 0.5, hold: int = 3) -> dict:
    """The job's curve and its first stable solve: the first validated step
    whose pass@5 and the next ``hold - 1`` validations' are all >= threshold."""
    meta = json.loads((job / JOB_JSON).read_text())
    vals = validations(job)
    steps = sorted(vals)
    curve = [(s, *vals[s], pass_at_k(*vals[s])) for s in steps]
    stable = next(
        (
            curve[i][0]
            for i in range(len(curve) - hold + 1)
            if all(p >= threshold for *_, p in curve[i : i + hold])
        ),
        None,
    )
    versions = layout.Root(job).mix.versions()
    total = spend(job)
    return {
        "bench": meta["bench"],
        "task": meta["task"],
        "rewriter": meta["rewriter"],
        "last_step": steps[-1] if steps else None,
        "first_pass_step": next((s for s, _, c, _ in curve if c), None),
        "stable_solve_step": stable,
        "best_pass_at_5": max((p for *_, p in curve), default=None),
        "last_pass_at_5": curve[-1][3] if curve else None,
        "mix_versions": len(versions),
        "train_attempts": total.get("attempts", 0),
        "train_groups": total.get("groups", 0),
        "trained_groups": total.get("trained_groups", 0),
        "train_hours": total.get("hours", 0.0),
        # Readout attempts measure and do not train, so they are counted apart.
        "readout_attempts": sum(n for _, n, _, _ in curve),
        "attempts_to_stable_solve": (
            spend(job, stable).get("attempts") if stable is not None else None
        ),
        "hours_to_stable_solve": (
            spend(job, stable).get("hours") if stable is not None else None
        ),
        "curve": [
            {"step": s, "attempts": n, "passes": c, "pass_at_5": round(p, 4)}
            for s, n, c, p in curve
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("--evalset", type=Path, required=True)
    s.add_argument("--bench", required=True)
    s.add_argument("--out", type=Path, required=True)
    n = sub.add_parser("new")
    n.add_argument("--tasks", type=Path, required=True, help="a split output dir")
    n.add_argument("--task", required=True)
    n.add_argument(
        "--sources",
        type=Path,
        required=True,
        help="the benchmark's task tree, holding tasks/<id>/ packages",
    )
    n.add_argument("--rewriter", required=True)
    n.add_argument("--jobs-root", type=Path, required=True)
    n.add_argument("--bin", type=Path, help="directory holding codex and jq")
    n.add_argument(
        "--seed-rows",
        type=Path,
        help="rows the pool starts from instead of the original alone (see seed)",
    )
    d = sub.add_parser("seed")
    d.add_argument("job", type=Path)
    d.add_argument("--rows", type=Path, required=True)
    d.add_argument("--weight", type=float, default=1.0)
    r = sub.add_parser("readout")
    r.add_argument("jobs", type=Path, nargs="+")
    r.add_argument("--threshold", type=float, default=0.5)
    r.add_argument("--hold", type=int, default=3)
    r.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "split":
        m = split(a.evalset, a.bench, a.out)
        print(f"{len(m['tasks'])} tasks -> {a.out}")
    elif a.cmd == "new":
        print(
            new_job(a.tasks, a.task, a.sources, a.rewriter, a.jobs_root, a.bin, a.seed_rows)
        )
    elif a.cmd == "seed":
        r = seed(a.job, a.rows, a.weight)
        print(f"{r['active']} active version(s): {r['added']} added, {r['retired']} retired")
    else:
        results = [readout(j, a.threshold, a.hold) for j in a.jobs]
        if a.json:
            print(json.dumps(results, indent=2))
            return
        cols = (
            "bench", "task", "rewriter", "last_step", "first_pass_step",
            "stable_solve_step", "best_pass_at_5", "last_pass_at_5",
            "mix_versions", "train_attempts", "trained_groups", "train_groups",
            "train_hours", "attempts_to_stable_solve", "readout_attempts",
        )
        print("\t".join(cols))
        for x in results:
            print(
                "\t".join(
                    str(x[k])
                    for k in cols
                )
            )


if __name__ == "__main__":
    main()
