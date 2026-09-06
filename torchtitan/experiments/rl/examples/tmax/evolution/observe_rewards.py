#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Observe task-version rewards without changing the trainer or its W&B run."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout  # noqa: E402

LOG = logging.getLogger("observe_rewards")


def source_wandb(run: layout.Run) -> str:
    with run.stdout_log.open() as stream:
        # The training logger prints its URL during startup. Bound the read so a
        # missing URL does not rescan a multi-GB training log every restart.
        prefix = stream.read(8 * 1024 * 1024)
    match = re.search(
        r"https://wandb\.ai/([^/\s]+)/([^/\s]+)/runs/([A-Za-z0-9_-]+)", prefix
    )
    if match is None:
        raise FileNotFoundError(
            "trainer W&B URL is not available yet; retry after startup"
        )
    return "/".join(match.groups())


def read_events(path: Path) -> list[dict]:
    """Leave an unfinished last append for the next poll; reject corrupt records."""
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        LOG.warning("skip incomplete append path=%s", path)
        data = data[: data.rfind(b"\n") + 1]
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def aggregate(rows: list[dict]) -> dict:
    scored = sum(row["scored"] for row in rows)
    return {
        "tasks": len({row["task"] for row in rows}),
        "groups": len(rows),
        "scored": scored,
        "infra": sum(row["infra"] for row in rows),
        "solved": sum(row["solved"] for row in rows),
        "accuracy": sum(row["solved"] for row in rows) / scored if scored else None,
        "reward": sum(row["reward_sum"] for row in rows) / scored if scored else None,
    }


def summarize(rows: list[dict], folded: set[str]) -> dict:
    # The seed-only series keeps r0 observations even after that task evolves.
    # The paired series instead fixes both task identity and sample hash at this snapshot.
    seed = [row for row in rows if row["rev"] == 0]
    stable = [row for row in seed if row["task"] not in folded]
    by_task = collections.defaultdict(list)
    for row in stable:
        if row["scored"] and row["sample_revision"]:
            by_task[(row["task"], row["sample_revision"])].append(row)
    pairs = []
    for task_rows in by_task.values():
        task_rows.sort(key=lambda row: row["group"])
        if len(task_rows) > 1 and task_rows[0]["epoch"] != task_rows[-1]["epoch"]:
            pairs.append({"first": task_rows[0], "latest": task_rows[-1]})
    policies = sorted({row["policy_at_claim"] for row in seed})
    return {
        "all": aggregate(rows),
        "seed_only": aggregate(seed),
        "never_rewritten": aggregate(stable),
        "paired_first": aggregate([pair["first"] for pair in pairs]),
        "paired_latest": aggregate([pair["latest"] for pair in pairs]),
        "pairs": pairs,
        "seed_by_policy": [
            {
                "policy_at_claim": policy,
                **aggregate([r for r in seed if r["policy_at_claim"] == policy]),
            }
            for policy in policies
        ],
    }


def collect(
    root: layout.Root, run: layout.Run, output: Path
) -> tuple[list[dict], list[dict]]:
    events = read_events(run.trainer / "training_lineage/events.jsonl")
    claimed = {}
    finalized = {}
    for event in events:
        if event.get("event") not in {"claimed", "finalized"}:
            continue
        target = claimed if event["event"] == "claimed" else finalized
        key = event["group_id"]
        if key in target and target[key].get("occurrence_id") != event.get(
            "occurrence_id"
        ):
            raise ValueError(
                f"reused group id {key}; observe each trainer lifetime separately"
            )
        target[key] = event
    cache = output / "groups"
    cache.mkdir(exist_ok=True)
    rows = []
    for group, event in sorted(finalized.items()):
        started = time.monotonic()
        task = event["task_id"]
        key = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        path = cache / f"{group}-{key}.json"
        if path.exists():
            rows.append(json.loads(path.read_text())["row"])
            continue
        LOG.info(
            "start task=%s group=%s finalized=%s", task, group, event.get("timestamp")
        )
        claim = claimed.get(group)
        if claim is None:
            raise ValueError(f"no claim for finalized group {group}")
        headers = []
        for record in sorted(
            (run.rollouts / layout.safe(task)).glob(f"g{group}-r*.jsonl")
        ):
            with record.open() as stream:
                header = json.loads(stream.readline())
            if header["task"] != task or header["group"] != group:
                raise ValueError(f"rollout identity mismatch: {record}")
            headers.append(header)
        if not headers or len(headers) != event["num_rollouts"]:
            LOG.warning(
                "skip task=%s group=%s headers=%s expected=%s elapsed=%.3f",
                task,
                group,
                len(headers),
                event["num_rollouts"],
                time.monotonic() - started,
            )
            continue
        revisions = {header["rev"] for header in headers}
        if len(revisions) != 1:
            raise ValueError(f"mixed task revisions in group {group}")
        valid = [
            h
            for h in headers
            if not h.get("infra_failed")
            and isinstance(h.get("reward"), (int, float))
            and math.isfinite(h["reward"])
        ]
        row = {
            "task": task,
            "rev": revisions.pop(),
            "group": group,
            "epoch": claim["dataset_epoch"],
            "sample_revision": event["sample_revision"],
            "policy_at_claim": claim["generator_policy_version"],
            "scored": len(valid),
            "solved": sum(h["reward"] > 0 for h in valid),
            "reward_sum": sum(h["reward"] for h in valid),
            "infra": sum(bool(h.get("infra_failed")) for h in headers),
            "n": len(headers),
        }
        layout.write_json_atomic(
            path,
            {
                "source_run": str(run.path),
                "claim": claim,
                "finalized": event,
                "headers": headers,
                "row": row,
            },
        )
        rows.append(row)
        LOG.info(
            "done task=%s group=%s scored=%s solved=%s elapsed=%.3f",
            task,
            group,
            row["scored"],
            row["solved"],
            time.monotonic() - started,
        )
    folds = []
    for task in root.evolution.task_dirs():
        if task.lineage.exists():
            folds.extend(
                {"task": task.task_id, **event}
                for event in read_events(task.lineage)
                if event.get("event") == "fold"
            )
    return rows, folds


def publish(wb, rows: list[dict], result: dict, snapshot: Path) -> None:
    import wandb

    columns = [
        "task",
        "rev",
        "group",
        "epoch",
        "policy_at_claim",
        "sample_revision",
        "scored",
        "solved",
        "infra",
        "accuracy",
        "reward",
    ]
    table_rows = [
        dict(
            row,
            accuracy=row["solved"] / row["scored"] if row["scored"] else None,
            reward=row["reward_sum"] / row["scored"] if row["scored"] else None,
        )
        for row in rows
    ]
    table = wandb.Table(
        columns=columns, data=[[row[key] for key in columns] for row in table_rows]
    )
    chart = wandb.plot.line_series(
        xs=[row["policy_at_claim"] for row in result["seed_by_policy"]],
        ys=[
            [row[key] for row in result["seed_by_policy"]]
            for key in ("accuracy", "reward")
        ],
        keys=["accuracy", "reward"],
        title="Original task versions: accuracy and reward",
        xname="Policy at group claim",
    )
    template = Path(__file__).with_name("reward_lineage.html").read_text()
    page = template.replace("__ROWS__", json.dumps(table_rows).replace("<", "\\u003c"))
    (snapshot / "lineage.html").write_text(page)
    payload = {
        "observer/snapshot_unix": time.time(),
        "observer/groups": len(rows),
        "seed_only/by_policy": chart,
        "lineage/groups": table,
        "lineage/explorer": wandb.Html(page, inject=False),
    }
    if result["paired_first"]["scored"] and result["paired_latest"]["scored"]:
        comparison = wandb.Table(
            columns=["observation", "accuracy"],
            data=[
                [name, result[key]["accuracy"]]
                for name, key in (
                    ("First", "paired_first"),
                    ("Latest", "paired_latest"),
                )
            ],
        )
        payload["paired/comparison"] = wandb.plot.bar(
            comparison,
            "observation",
            "accuracy",
            title="Same unchanged tasks: first vs latest solve rate",
        )
    for category in ("seed_only", "never_rewritten", "paired_first", "paired_latest"):
        payload.update(
            {
                f"{category}/{key}": value
                for key, value in result[category].items()
                if value is not None
            }
        )
    wb.log(payload)
    LOG.info("published snapshot=%s groups=%s url=%s", snapshot.name, len(rows), wb.url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--run", required=True, help="fixed run name; never follows runs/latest"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="observer-owned output directory"
    )
    parser.add_argument(
        "--source-wandb",
        help="entity/project/trainer-run-id; default: URL in trainer stdout",
    )
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    if args.interval < 10:
        parser.error("--interval must be at least 10 seconds")
    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(args.out / "observe.log"),
            logging.StreamHandler(),
        ],
    )
    lock = (args.out / "observer.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    root = layout.Root(args.root.resolve())
    run = root.run(args.run)
    args.source_wandb = args.source_wandb or source_wandb(run)
    config = {
        "source_wandb": args.source_wandb,
        "source_run": str(run.path),
        "interval": args.interval,
    }
    config_path = args.out / "config.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError("output directory belongs to another source or configuration")
    layout.write_json_atomic(config_path, config)
    wb = None
    if args.upload:
        import wandb

        entity, project, source_id = args.source_wandb.split("/")
        wb = wandb.init(
            entity=entity,
            project=project,
            id=f"observe-{source_id}",
            name=f"observe-{source_id}",
            job_type="reward-observer",
            group=source_id,
            dir=str(args.out),
            resume="allow",
            config={
                "source_wandb": args.source_wandb,
                "interval_seconds": args.interval,
                "axis": "generator policy at group claim; not exact per-token policy",
                "cohort": "same task and sample hash, never folded by snapshot time",
            },
            settings=wandb.Settings(
                disable_code=True,
                disable_git=True,
                disable_job_creation=True,
                save_code=False,
                x_disable_meta=True,
                x_disable_stats=True,
                x_save_requirements=False,
            ),
        )
        wb.define_metric("observer/snapshot_unix")
        for category in (
            "seed_only",
            "never_rewritten",
            "paired_first",
            "paired_latest",
            "observer",
        ):
            wb.define_metric(f"{category}/*", step_metric="observer/snapshot_unix")
        layout.write_json_atomic(args.out / "wandb.json", {"url": wb.url, "id": wb.id})
    try:
        while True:
            start = time.monotonic()
            LOG.info("poll start source=%s pid=%s", run.path, os.getpid())
            rows, folds = collect(root, run, args.out)
            result = summarize(rows, {fold["task"] for fold in folds})
            snapshot = (
                args.out
                / "snapshots"
                / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
            )
            snapshot.mkdir(parents=True)
            layout.write_json_atomic(
                snapshot / "result.json",
                {"inputs": config, "rows": rows, "folds": folds, "summary": result},
            )
            if wb is not None:
                publish(wb, rows, result, snapshot)
            LOG.info(
                "poll done snapshot=%s groups=%s elapsed=%.3f",
                snapshot,
                len(rows),
                time.monotonic() - start,
            )
            if not args.watch:
                break
            time.sleep(args.interval)
    finally:
        if wb is not None:
            wb.finish()


if __name__ == "__main__":
    main()
