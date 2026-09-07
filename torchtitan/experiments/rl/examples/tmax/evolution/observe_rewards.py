#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Observe task-version rewards without changing the trainer or its W&B run."""

from __future__ import annotations

import argparse
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402
import observe_lineage  # noqa: E402

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


def collect(
    root: layout.Root, run: layout.Run, output: Path, task_filter: str | None = None
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
        if task_filter and event["task_id"] != task_filter:
            continue
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
        if task_filter and task.task_id != task_filter:
            continue
        if task.lineage.exists():
            folds.extend(
                {"task": task.task_id, **event}
                for event in read_events(task.lineage)
                if event.get("event") == "fold"
            )
    return rows, folds


def publish(wb, result: dict, snapshot: Path, charts: dict) -> None:
    import wandb

    # The timeline spans the run; the SDK's 10k history default drops later events.
    wandb.Table.MAX_ROWS = wandb.Table.MAX_ARTIFACT_ROWS
    datasets = {
        "Unchanged task accuracy": result["unchanged"]["points"],
        "Rewrite accuracy": result["comparisons"],
        "Task timeline": result["timeline"],
    }
    payload = {}
    focus = next((r["task"] for r in result["comparisons"]), "")
    for title, rows in datasets.items():
        columns = charts[title]["columns"]
        table = wandb.Table(
            columns=columns, data=[[r.get(c) for c in columns] for r in rows]
        )
        payload[title] = wandb.plot_table(
            charts[title]["id"],
            table,
            fields={column: column for column in columns},
            string_fields={
                "task": focus,
                "cohort": f"Same {len(result['unchanged']['cohort'])} tasks; latest epoch may be incomplete",
            },
        )
    wb.log(payload)
    LOG.info(
        "published snapshot=%s plots=%s url=%s", snapshot.name, len(payload), wb.url
    )


def register_charts(entity: str, output: Path) -> dict:
    import wandb

    definitions = json.loads(Path(__file__).with_name("reward_charts.json").read_text())
    key = hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest()[
        :12
    ]
    path = output / f"charts-{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    api = wandb.Api()
    for index, (title, chart) in enumerate(definitions.items()):
        chart["id"] = api.create_custom_chart(
            entity=entity,
            name=f"task-observer-{key}-{index}",
            display_name=title,
            spec_type="vega2",
            access="private",
            spec=chart["spec"],
        )
    layout.write_json_atomic(path, definitions)
    return definitions


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
    parser.add_argument("--task", help="one-task local smoke test; cannot upload")
    args = parser.parse_args()
    if args.task and (args.upload or args.watch):
        parser.error("--task is a local smoke test; omit --upload and --watch")
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
    if args.task:
        config["task"] = args.task
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError("output directory belongs to another source or configuration")
    layout.write_json_atomic(config_path, config)
    wb = None
    charts = None
    if args.upload:
        import wandb

        entity, project, source_id = args.source_wandb.split("/")
        charts = register_charts(entity, args.out)
        wb = wandb.init(
            entity=entity,
            project=project,
            id=f"accuracy-{source_id}",
            name=f"accuracy-{source_id}",
            job_type="reward-observer",
            group=source_id,
            dir=str(args.out),
            resume="allow",
            allow_val_change=True,
            config={
                "source_wandb": args.source_wandb,
                "interval_seconds": args.interval,
                "axis": "generator policy at group claim; not exact per-token policy",
                "cohort": "intersection across observed epochs; same original hash; never folded by snapshot time",
            },
            settings=wandb.Settings(
                console="off",
                disable_code=True,
                disable_git=True,
                disable_job_creation=True,
                save_code=False,
                x_disable_meta=True,
                x_disable_stats=True,
                x_save_requirements=False,
            ),
        )
        layout.write_json_atomic(args.out / "wandb.json", {"url": wb.url, "id": wb.id})
    try:
        while True:
            start = time.monotonic()
            LOG.info("poll start source=%s pid=%s", run.path, os.getpid())
            rows, folds = collect(root, run, args.out, args.task)
            events = read_events(run.trainer / "training_lineage/events.jsonl")
            result = observe_lineage.build(
                root, run, rows, folds, events, args.out, args.task
            )
            snapshot = (
                args.out
                / "snapshots"
                / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
            )
            snapshot.mkdir(parents=True)
            layout.write_json_atomic(
                snapshot / "result.json",
                {
                    "inputs": config,
                    "observer_sha256": hashlib.sha256(
                        Path(__file__).read_bytes()
                    ).hexdigest(),
                    "implementation": {
                        name: hashlib.sha256(
                            Path(__file__).with_name(name).read_bytes()
                        ).hexdigest()
                        for name in (
                            "observe_rewards.py",
                            "observe_lineage.py",
                            "reward_charts.json",
                        )
                    },
                    "rows": rows,
                    "folds": folds,
                    "summary": result,
                },
            )
            if wb is not None:
                publish(wb, result, snapshot, charts)
            LOG.info(
                "poll done snapshot=%s groups=%s elapsed=%.3f",
                snapshot,
                len(rows),
                time.monotonic() - start,
            )
            if not args.watch:
                break
            if wb is not None:
                import wandb

                state = wandb.Api().run(args.source_wandb).state
                LOG.info("source state=%s", state)
                if state in {"finished", "failed", "crashed", "killed"}:
                    LOG.info("source ended; final snapshot published")
                    break
            time.sleep(args.interval)
    finally:
        if wb is not None:
            wb.finish()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOG.exception("observer failed")
        raise
