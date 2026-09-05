#!/usr/bin/env python3
"""Publish a reproducible evolve-loop analysis as one W&B analysis run.

The analysis keeps three instruments separate:

* exact online training-group outcomes from the source W&B training runs;
* the loop's own records under the experiment root: one round per mix
  version in ``data/mix/history/``, folds and rewrites from
  ``evolution/tasks/``, signals from ``evolution/ledger.jsonl``;
* fixed TB-2.0 evaluation summaries already stored in W&B.

The runs' rollout records are hashed and audited for completeness, but not
used to estimate group rates: a record carries no policy version, and the
per-policy curves are the online metrics.

The generated JSONL contains the complete derived records plus hashes and exact
locations of every input. Passing ``--upload`` publishes the same records to a
content-addressed W&B run, so rerunning unchanged inputs does not append duplicate
history.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import logging
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import layout  # noqa: E402
import rollout_record  # noqa: E402

LOG = logging.getLogger("publish_evolve_analysis")
REWRITE_STATUSES = ("accepted", "rejected", "blocked", "failed", "kept")
SIGNAL_OUTCOMES = ("handled", "deferred", "junk")


def _iso(stamp: str) -> str:
    return dt.datetime.fromtimestamp(layout.parse_stamp(stamp), tz=dt.UTC).isoformat()


def audit_rollouts(run: layout.Run, expected_group_size: int) -> dict:
    """Describe one run's rollout records: whether every group is whole, and a
    digest over the rollout headers so the input is pinned by content."""
    malformed = 0
    statuses: collections.Counter = collections.Counter()
    group_sizes: collections.Counter = collections.Counter()
    infra_failed = 0
    scored = 0
    digest = hashlib.sha256()
    for path in sorted(run.rollouts.glob("*/g*-r*.jsonl")) if run.rollouts.exists() else []:
        try:
            header, _turns = rollout_record.read_record(path)
        except (OSError, ValueError):
            malformed += 1
            continue
        digest.update(path.relative_to(run.path).as_posix().encode())
        digest.update(json.dumps(header, sort_keys=True, ensure_ascii=False).encode())
        group_sizes[int(header["group"])] += 1
        statuses[str(header.get("status"))] += 1
        infra_failed += bool(header.get("infra_failed"))
        reward = header.get("reward")
        if isinstance(reward, (int, float)) and math.isfinite(float(reward)):
            scored += 1
    size_histogram = collections.Counter(group_sizes.values())
    complete = size_histogram.get(expected_group_size, 0)
    return {
        "run": run.name,
        "malformed_records": malformed,
        "records": sum(group_sizes.values()),
        "scored_records": scored,
        "infra_failed_records": infra_failed,
        "groups": len(group_sizes),
        "expected_group_size": expected_group_size,
        "complete_groups": complete,
        "partial_groups": len(group_sizes) - complete,
        "group_size_histogram": dict(sorted(size_histogram.items())),
        "status_counts": dict(sorted(statuses.items())),
        "population_complete": complete == len(group_sizes),
        "records_sha256": digest.hexdigest(),
        "analysis_use": (
            "provenance and completeness audit only; online W&B metrics are used "
            "for training solve and group-fraction curves"
        ),
    }


def _finite(row: dict, key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def load_training_metrics(
    entity: str, project: str, run_ids: list[str]
) -> tuple[list[dict], dict]:
    """Merge exact per-step rollout metrics across restarted W&B runs."""
    import wandb

    api = wandb.Api()
    candidates: list[dict] = []
    source_counts: dict[str, int] = {}
    for run_order, run_id in enumerate(run_ids):
        run = api.run(f"{entity}/{project}/{run_id}")
        count = 0
        for row in run.scan_history(page_size=1000):
            policy = _finite(row, "train/policy_version")
            reward_mean = _finite(row, "rollout_reward/_mean")
            solve_rate = _finite(row, "rollout_reward/component/RewardTMax/mean")
            if policy is None or (solve_rate is None and reward_mean is None):
                continue
            solve_rate = solve_rate if solve_rate is not None else reward_mean
            record = {
                "policy_version": int(policy),
                "solve_rate": solve_rate,
                "rollout_reward_mean": reward_mean,
                "gradient_group_frac": None,
                "zero_std_group_frac": _finite(row, "rollout/zero_std_group_frac/mean"),
                "all_fail_group_frac": _finite(row, "rollout/all_fail_group_frac/mean"),
                "all_pass_group_frac": _finite(row, "rollout/all_pass_group_frac/mean"),
                "source_run_id": run_id,
                "source_run_name": run.name,
                "source_step": int(row.get("_step", 0)),
                "source_timestamp": _finite(row, "_timestamp"),
                "source_order": run_order,
            }
            if record["zero_std_group_frac"] is not None:
                record["gradient_group_frac"] = 1.0 - record["zero_std_group_frac"]
            candidates.append(record)
            count += 1
        source_counts[run_id] = count

    if not candidates:
        raise ValueError("no train/policy_version rollout metrics found in W&B runs")
    by_policy: dict[int, list[dict]] = collections.defaultdict(list)
    for row in candidates:
        by_policy[row["policy_version"]].append(row)
    selected = []
    for policy in sorted(by_policy):
        choices = by_policy[policy]
        selected.append(
            max(
                choices,
                key=lambda row: (
                    row["source_timestamp"] or float("-inf"),
                    row["source_order"],
                    row["source_step"],
                ),
            )
        )
    for row in selected:
        row.pop("source_order")
    missing_group_metrics = [
        row["policy_version"]
        for row in selected
        if row["zero_std_group_frac"] is None
        or row["all_fail_group_frac"] is None
        or row["all_pass_group_frac"] is None
    ]
    if missing_group_metrics:
        raise ValueError(
            "training rows missing online group metrics at policy versions "
            + ",".join(map(str, missing_group_metrics))
        )
    diagnostics = {
        "source_history_rows": source_counts,
        "candidate_rows": len(candidates),
        "selected_policy_versions": len(selected),
        "duplicate_policy_versions": {
            str(policy): len(rows)
            for policy, rows in by_policy.items()
            if len(rows) > 1
        },
        "duplicate_resolution": "latest source timestamp, then CLI run order and step",
    }
    return selected, diagnostics


def _revs_of(version_file: Path) -> dict[str, int]:
    """instance_id -> metadata.rev for one mix version; the seed is rev 0."""
    revs = {}
    with open(version_file, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                md = json.loads(line)["metadata"]
                revs[md["instance_id"]] = int(md.get("rev", 0))
    return revs


def _folds_by_version(evo: layout.Evolution) -> dict[int, list[dict]]:
    folds: dict[int, list[dict]] = collections.defaultdict(list)
    for task in evo.task_dirs():
        for event in layout.read_jsonl(task.lineage):
            if event.get("event") == "fold":
                folds[int(event["mix_version"])].append({"task": task.task_id, **event})
    return folds


def _finished_rewrites(evo: layout.Evolution) -> list[tuple[float, str, dict]]:
    """(finished epoch, task, rewrite.json) for every rewrite that finished,
    oldest first. A rewrite still running has no ``finished`` and is not here."""
    out = []
    for task in evo.task_dirs():
        for rw in task.rewrite_dirs():
            try:
                meta = json.loads(rw.meta.read_text())
            except (OSError, ValueError):
                continue
            if meta.get("finished"):
                out.append((layout.parse_stamp(meta["finished"]), task.task_id, meta))
    return sorted(out, key=lambda t: t[0])


def _rewrite_counts(metas: list[dict]) -> dict:
    by_status = collections.Counter(m.get("status") for m in metas)
    by_job: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in metas:
        by_job[str(m.get("job"))][str(m.get("status"))] += 1
    harder = by_job.get("harder", collections.Counter())
    decided = sum(harder[s] for s in ("accepted", "rejected", "blocked", "failed"))
    return {
        "rewrites": {s: by_status[s] for s in REWRITE_STATUSES},
        "rewrites_by_job": {job: dict(c) for job, c in sorted(by_job.items())},
        "harder_attempted": decided,
        "harder_accept_rate": harder["accepted"] / decided if decided else None,
    }


def _signal_counts(entries: list[dict]) -> dict:
    by_outcome = collections.Counter(e.get("outcome") for e in entries)
    by_direction: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for e in entries:
        by_direction[str(e.get("direction"))][str(e.get("outcome"))] += 1
    return {
        "signals": {o: by_outcome[o] for o in SIGNAL_OUTCOMES},
        "signals_by_direction": {d: dict(c) for d, c in sorted(by_direction.items())},
    }


def load_evolution_metrics(
    root: layout.Root, start: float, end: float
) -> tuple[list[dict], dict]:
    """One round per mix version the loop published inside [start, end].

    A round's rewrites and signals are the ones that finished between the
    previous version's stamp and this one's; its folds are the lineage lines
    that name this version. The row diff between the version and its parent
    is checked against those fold lines: the two are written by one loop in
    one round, and an analysis published on a disagreement would be wrong.
    """
    evo = root.evolution
    folds = _folds_by_version(evo)
    rewrites = _finished_rewrites(evo)
    ledger = [e for e in layout.read_jsonl(evo.ledger) if start <= layout.parse_stamp(e["stamp"]) <= end]
    versions = []
    for version, path in root.mix.versions():
        manifest = json.loads(layout.MixDir.manifest_of(path).read_text())
        if manifest.get("parent_version") is None:
            continue  # the seed version is nobody's round
        if start <= layout.parse_stamp(manifest["stamp"]) <= end:
            versions.append((version, path, manifest))

    by_version = {v: p for v, p in root.mix.versions()}
    revs_cache: dict[int, dict[str, int]] = {}

    def revs(version: int) -> dict[str, int]:
        if version not in revs_cache:
            revs_cache[version] = _revs_of(by_version[version])
        return revs_cache[version]

    rounds = []
    changed_counts: collections.Counter = collections.Counter()
    folded_cumulative = 0
    window_start = start
    for index, (version, path, manifest) in enumerate(versions, start=1):
        at = layout.parse_stamp(manifest["stamp"])
        in_window = [m for t, _task, m in rewrites if window_start < t <= at]
        signals = [e for e in ledger if window_start < layout.parse_stamp(e["stamp"]) <= at]
        these = sorted(folds.get(version, []), key=lambda f: f["task"])
        current, parent = revs(version), revs(manifest["parent_version"])
        changed = sorted(
            task for task in current.keys() & parent.keys() if current[task] != parent[task]
        )
        if changed != sorted(f["task"] for f in these):
            raise RuntimeError(
                f"mix v{version}: lineage folds {sorted(f['task'] for f in these)} "
                f"but rows whose rev changed are {changed}"
            )
        changed_counts.update(changed)
        folded_cumulative += len(these)
        rounds.append(
            {
                "round": index,
                "version": version,
                "parent_version": manifest["parent_version"],
                "stamp": manifest["stamp"],
                "timestamp": _iso(manifest["stamp"]),
                "elapsed_hours": (at - start) / 3600,
                "sha256": manifest["sha256"],
                "rows": manifest["rows"],
                **_signal_counts(signals),
                **_rewrite_counts(in_window),
                "folded": len(these),
                "folded_cumulative": folded_cumulative,
                "changed_task_ids": changed,
                "task_changes": [
                    {
                        "task": f["task"],
                        "from_rev": f["from_rev"],
                        "to_rev": f["to_rev"],
                        "rewrite": f.get("rewrite"),
                    }
                    for f in these
                ],
            }
        )
        window_start = at

    all_rewrites = [m for t, _task, m in rewrites if start <= t <= end]
    summary = {
        "rounds": len(rounds),
        **_signal_counts(ledger),
        **_rewrite_counts(all_rewrites),
        "folded": folded_cumulative,
        "unique_changed_tasks": len(changed_counts),
        "repeated_tasks": sum(value > 1 for value in changed_counts.values()),
        "repeat_folds": sum(value - 1 for value in changed_counts.values()),
    }
    return rounds, summary


def evolution_trace_rows(rounds: list[dict]) -> list[dict]:
    """Flatten rounds into downloadable round and fold events."""
    rows = []
    for round_row in rounds:
        common = {
            "schema_version": 2,
            "round": round_row["round"],
            "version": round_row["version"],
            "timestamp": round_row["timestamp"],
            "elapsed_hours": round_row["elapsed_hours"],
        }
        rows.append(
            {
                **common,
                "record_type": "evolution_round",
                "signals": round_row["signals"],
                "rewrites": round_row["rewrites"],
                "harder_attempted": round_row["harder_attempted"],
                "harder_accept_rate": round_row["harder_accept_rate"],
                "folded": round_row["folded"],
                "folded_cumulative": round_row["folded_cumulative"],
                "changed_task_ids": round_row["changed_task_ids"],
            }
        )
        rows.extend(
            {
                **common,
                "record_type": "evolution_fold",
                **change,
            }
            for change in round_row["task_changes"]
        )
    return rows


def write_evolution_trace(path: Path, rounds: list[dict]) -> None:
    """Write an immutable public trace, rejecting content-address collisions."""
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in evolution_trace_rows(rounds)
    )
    if path.exists():
        if path.read_text() != payload:
            raise RuntimeError(f"content-address collision at {path}")
        return
    path.write_text(payload)


def parse_eval_specs(values: list[str]) -> list[tuple[int, str]]:
    parsed = []
    for value in values:
        checkpoint, run_id = value.split("=", 1)
        parsed.append((int(checkpoint), run_id))
    return sorted(parsed)


def load_eval_metrics(
    entity: str, project: str, specs: list[tuple[int, str]]
) -> list[dict]:
    import wandb

    api = wandb.Api()
    records = []
    for checkpoint, run_id in specs:
        run = api.run(f"{entity}/{project}/{run_id}")
        summary = run.summary._json_dict
        validation = (run.config.get("async_loop", {}) or {}).get(
            "validation", {}
        ) or {}
        num_samples = validation.get("num_samples")
        group_size = validation.get("group_size")
        if num_samples is not None and group_size is not None:
            trials = int(num_samples) * int(group_size)
        else:
            mean = float(summary["validation_reward/_mean"])
            successes = int(summary["validation_reward/_sum"])
            if mean <= 0:
                raise ValueError(
                    f"cannot derive trial count for zero-mean eval run {run_id}"
                )
            trials = round(successes / mean)
        records.append(
            {
                "checkpoint": checkpoint,
                "run_id": run_id,
                "run_name": run.name,
                "state": run.state,
                "successes": int(summary["validation_reward/_sum"]),
                "trials": trials,
                "trial_solve_rate": float(summary["validation_reward/_mean"]),
                "pass_at_5": float(summary["validation/pass_at_k/mean"]),
                "infra_failed_frac": float(
                    summary.get("validation/infra_failed_frac/mean", 0.0)
                ),
                "raw_summary": summary,
            }
        )
    return records


def window_summary(records: list[dict], policies: list[int]) -> dict:
    selected = [row for row in records if row["policy_version"] in policies]

    def mean(key: str) -> float:
        values = [row[key] for row in selected if row.get(key) is not None]
        if not values:
            raise ValueError(f"no finite {key} values in policy window {policies}")
        return sum(values) / len(values)

    return {
        "policy_versions": policies,
        "steps": len(selected),
        "solve_rate": mean("solve_rate"),
        "gradient_group_frac": mean("gradient_group_frac"),
        "all_fail_group_frac": mean("all_fail_group_frac"),
        "all_pass_group_frac": mean("all_pass_group_frac"),
        "aggregation": "unweighted mean of exact online metrics across train steps",
    }


def publish(
    result: dict,
    entity: str,
    project: str,
    name: str,
    digest: str,
    trace_path: Path,
) -> str:
    import wandb
    from wandb.errors import CommError

    experiment = result["inputs"]["experiment"]
    run_id = f"{experiment}-evolve-{digest[:10]}"
    path = f"{entity}/{project}/{run_id}"
    try:
        existing = wandb.Api().run(path)
        LOG.info("W&B run already exists: %s", existing.url)
        return existing.url
    except CommError:
        pass

    # The root is a path on the training host; everything else in inputs is
    # a name or a hash.
    public_config = {
        key: value for key, value in result["inputs"].items() if key != "root"
    }
    run = wandb.init(
        entity=entity,
        project=project,
        id=run_id,
        name=name,
        job_type="analysis",
        group=experiment,
        tags=[experiment, "evolveloop", "offline-analysis"],
        config=public_config,
        notes=result["analysis_text"],
        resume="never",
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
    run.define_metric("policy_version")
    run.define_metric("training/*", step_metric="policy_version")
    run.define_metric("elapsed_hours")
    run.define_metric("evolution/*", step_metric="elapsed_hours")
    run.define_metric("checkpoint")
    run.define_metric("eval/*", step_metric="checkpoint")

    for row in result["training_by_policy"]:
        run.log(
            {
                "policy_version": row["policy_version"],
                "training/solve_rate": row["solve_rate"],
                "training/gradient_group_frac": row["gradient_group_frac"],
                "training/zero_std_group_frac": row["zero_std_group_frac"],
                "training/all_fail_group_frac": row["all_fail_group_frac"],
                "training/all_pass_group_frac": row["all_pass_group_frac"],
                "training/rollout_reward_mean": row["rollout_reward_mean"],
            }
        )
    for row in result["evolution_rounds"]:
        run.log(
            {
                "elapsed_hours": row["elapsed_hours"],
                "evolution/mix_version": row["version"],
                "evolution/folded": row["folded"],
                "evolution/folded_cumulative": row["folded_cumulative"],
                **{f"evolution/{s}": row["rewrites"][s] for s in REWRITE_STATUSES},
                "evolution/harder_accept_rate": row["harder_accept_rate"],
                **{f"evolution/signals_{o}": row["signals"][o] for o in SIGNAL_OUTCOMES},
            }
        )
    for row in result["eval_by_checkpoint"]:
        run.log(
            {
                "checkpoint": row["checkpoint"],
                "eval/trial_solve_rate": row["trial_solve_rate"],
                "eval/pass_at_5": row["pass_at_5"],
                "eval/successes": row["successes"],
                "eval/infra_failed_frac": row["infra_failed_frac"],
            }
        )

    training_table = wandb.Table(
        columns=[
            "policy_version",
            "solve_rate",
            "gradient_group_frac",
            "all_fail_group_frac",
            "all_pass_group_frac",
            "source_run_id",
            "source_step",
        ],
        data=[
            [
                row["policy_version"],
                row["solve_rate"],
                row["gradient_group_frac"],
                row["all_fail_group_frac"],
                row["all_pass_group_frac"],
                row["source_run_id"],
                row["source_step"],
            ]
            for row in result["training_by_policy"]
        ],
    )
    eval_table = wandb.Table(
        columns=[
            "checkpoint",
            "run_id",
            "successes",
            "trials",
            "trial_solve_rate",
            "pass_at_5",
            "infra_failed_frac",
        ],
        data=[
            [
                row["checkpoint"],
                row["run_id"],
                row["successes"],
                row["trials"],
                row["trial_solve_rate"],
                row["pass_at_5"],
                row["infra_failed_frac"],
            ]
            for row in result["eval_by_checkpoint"]
        ],
    )
    evolution_table = wandb.Table(
        columns=[
            "round",
            "version",
            "timestamp",
            "elapsed_hours",
            "signals_handled",
            "signals_deferred",
            "folded",
            "folded_cumulative",
            *REWRITE_STATUSES,
            "harder_accept_rate",
        ],
        data=[
            [
                row["round"],
                row["version"],
                row["timestamp"],
                row["elapsed_hours"],
                row["signals"]["handled"],
                row["signals"]["deferred"],
                row["folded"],
                row["folded_cumulative"],
                *(row["rewrites"][s] for s in REWRITE_STATUSES),
                row["harder_accept_rate"],
            ]
            for row in result["evolution_rounds"]
        ],
    )
    evolution_task_trace = wandb.Table(
        columns=[
            "round",
            "version",
            "timestamp",
            "elapsed_hours",
            "task",
            "from_rev",
            "to_rev",
            "rewrite",
        ],
        data=[
            [
                row["round"],
                row["version"],
                row["timestamp"],
                row["elapsed_hours"],
                row["task"],
                row["from_rev"],
                row["to_rev"],
                row["rewrite"],
            ]
            for row in result["evolution_task_trace"]
        ],
    )
    run.log(
        {
            "tables/training_by_policy": training_table,
            "tables/evolution_rounds": evolution_table,
            "tables/evolution_task_trace": evolution_task_trace,
            "tables/eval_by_checkpoint": eval_table,
        }
    )
    artifact = wandb.Artifact(
        name=f"{experiment}-evolution-trace-{digest[:10]}",
        type="evolution-trace",
        description=(
            f"Evolution rounds (mix versions) and accepted task folds of {experiment}. "
            "Each fold names the task, the revision it left and entered, and the rewrite."
        ),
        metadata={
            "schema_version": 2,
            "rounds": len(result["evolution_rounds"]),
            "folds": len(result["evolution_task_trace"]),
        },
    )
    artifact.add_file(str(trace_path), name="evolution_trace.jsonl")
    run.log_artifact(artifact)
    for key, value in result["conclusion_metrics"].items():
        run.summary[key] = value
    url = run.url
    run.finish()
    return url


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        '{"timestamp":"%(asctime)s","level":"%(levelname)s","message":%(message_json)s}'
    )

    class JsonMessageFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.message_json = json.dumps(record.getMessage())
            return True

    handler = logging.FileHandler(path)
    handler.addFilter(JsonMessageFilter())
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=os.environ.get("TRL_BASE"),
                        help="experiment root (default: $TRL_BASE)")
    parser.add_argument("--run", action="append", default=[], metavar="NAME",
                        help="run directories to audit and to date the window from "
                             "(default: every run under the root)")
    parser.add_argument("--run-start", metavar="STAMP",
                        help="window start (default: the earliest run's launch.json started)")
    parser.add_argument("--run-end", metavar="STAMP", help="window end (default: now)")
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--entity", required=True)
    parser.add_argument("--project", default="terminal-agent-rl")
    parser.add_argument(
        "--eval-run", action="append", default=[], metavar="STEP=RUN_ID"
    )
    parser.add_argument("--train-run", action="append", default=[], metavar="RUN_ID")
    parser.add_argument("--name", help="W&B run name (default: <experiment>-evolveloop-analysis)")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    if args.root is None:
        parser.error("--root or TRL_BASE names the experiment root")

    root = layout.Root(args.root)
    experiment = json.loads(root.experiment_json.read_text())["name"]
    # One invocation, one directory: the log, the analysis and the trace. The
    # W&B run id is the content address, so a rerun on unchanged inputs finds
    # its run instead of making a second one.
    out_dir = root.logs / f"publish_evolve_analysis--{layout.stamp()}"
    configure_logging(out_dir / "analysis.log")
    runs = [root.run(name) for name in args.run] or root.run_dirs()
    if not runs:
        raise FileNotFoundError(f"no runs under {root.runs}")
    launches = {run.name: json.loads(run.launch_json.read_text()) for run in runs}
    run_start = args.run_start or min(l["started"] for l in launches.values())
    run_end = args.run_end or layout.stamp()
    if layout.parse_stamp(run_end) <= layout.parse_stamp(run_start):
        raise ValueError("--run-end must be after --run-start")

    audits = []
    for run in runs:
        LOG.info("auditing rollout records in %s", run.rollouts)
        audits.append(audit_rollouts(run, args.group_size))
    LOG.info("reading evolution records under %s", root.evolution.path)
    evolution, evolution_summary = load_evolution_metrics(
        root, layout.parse_stamp(run_start), layout.parse_stamp(run_end)
    )
    eval_specs = parse_eval_specs(args.eval_run)
    if len(eval_specs) < 2:
        raise ValueError("at least two --eval-run STEP=RUN_ID values are required")
    if not args.train_run:
        raise ValueError("at least one --train-run RUN_ID is required")
    LOG.info("reading training history from %d W&B runs", len(args.train_run))
    training, training_diagnostics = load_training_metrics(
        args.entity, args.project, args.train_run
    )
    LOG.info("reading %d eval summaries from W&B", len(eval_specs))
    evaluations = load_eval_metrics(args.entity, args.project, eval_specs)

    policies = [row["policy_version"] for row in training]
    window_size = min(5, len(policies))
    early = window_summary(training, policies[:window_size])
    late = window_summary(training, policies[-window_size:])
    inputs = {
        "analysis_schema": 4,
        "analysis_script_sha256": layout.sha256_file(Path(__file__)),
        "root": str(root.path),
        "experiment": experiment,
        "runs": [run.name for run in runs],
        "launches": {
            name: {k: l.get(k) for k in ("tt_commit", "mix_version", "mix_sha256", "resumed_from")}
            for name, l in launches.items()
        },
        "rollout_records": {a["run"]: {"records": a["records"], "sha256": a["records_sha256"]}
                            for a in audits},
        "expected_group_size": args.group_size,
        "mix_versions": [row["version"] for row in evolution],
        "run_start": run_start,
        "run_end": run_end,
        "entity": args.entity,
        "project": args.project,
        "train_runs": args.train_run,
        "training_history_sha256": hashlib.sha256(
            json.dumps(training, sort_keys=True).encode()
        ).hexdigest(),
        "eval_runs": {str(step): run_id for step, run_id in eval_specs},
        "eval_summaries_sha256": hashlib.sha256(
            json.dumps(
                [row["raw_summary"] for row in evaluations], sort_keys=True
            ).encode()
        ).hexdigest(),
    }
    conclusion = {
        "analysis/training_solve_rate_early": early["solve_rate"],
        "analysis/training_solve_rate_late": late["solve_rate"],
        "analysis/training_solve_rate_delta": late["solve_rate"] - early["solve_rate"],
        "analysis/gradient_group_frac_early": early["gradient_group_frac"],
        "analysis/gradient_group_frac_late": late["gradient_group_frac"],
        "analysis/gradient_group_frac_delta": late["gradient_group_frac"]
        - early["gradient_group_frac"],
        "analysis/all_fail_group_frac_early": early["all_fail_group_frac"],
        "analysis/all_fail_group_frac_late": late["all_fail_group_frac"],
        "analysis/all_fail_group_frac_delta": late["all_fail_group_frac"]
        - early["all_fail_group_frac"],
        "analysis/all_pass_group_frac_early": early["all_pass_group_frac"],
        "analysis/all_pass_group_frac_late": late["all_pass_group_frac"],
        "analysis/all_pass_group_frac_delta": late["all_pass_group_frac"]
        - early["all_pass_group_frac"],
        "analysis/evolution_rounds": evolution_summary["rounds"],
        "analysis/evolution_folded": evolution_summary["folded"],
        "analysis/evolution_unique_changed_tasks": evolution_summary[
            "unique_changed_tasks"
        ],
        "analysis/evolution_harder_attempted": evolution_summary["harder_attempted"],
        "analysis/evolution_harder_accept_rate": evolution_summary[
            "harder_accept_rate"
        ],
        **{f"analysis/evolution_{s}": evolution_summary["rewrites"][s] for s in REWRITE_STATUSES},
        **{f"analysis/evolution_signals_{o}": evolution_summary["signals"][o]
           for o in SIGNAL_OUTCOMES},
        "analysis/rollout_complete_groups": sum(a["complete_groups"] for a in audits),
        "analysis/rollout_partial_groups": sum(a["partial_groups"] for a in audits),
        "analysis/eval_base_solve_rate": evaluations[0]["trial_solve_rate"],
        "analysis/eval_final_solve_rate": evaluations[-1]["trial_solve_rate"],
        "analysis/eval_solve_rate_delta": evaluations[-1]["trial_solve_rate"]
        - evaluations[0]["trial_solve_rate"],
        "analysis/eval_base_pass_at_5": evaluations[0]["pass_at_5"],
        "analysis/eval_final_pass_at_5": evaluations[-1]["pass_at_5"],
    }
    best_solve = max(evaluations, key=lambda row: row["trial_solve_rate"])
    best_pass = max(evaluations, key=lambda row: row["pass_at_5"])
    conclusion.update(
        {
            "analysis/eval_best_solve_checkpoint": best_solve["checkpoint"],
            "analysis/eval_best_solve_rate": best_solve["trial_solve_rate"],
            "analysis/eval_best_pass_at_5_checkpoint": best_pass["checkpoint"],
            "analysis/eval_best_pass_at_5": best_pass["pass_at_5"],
        }
    )
    # Signed deltas, no verdict: the numbers are the same whichever way they
    # went, and the reader draws the conclusion.
    analysis_text = (
        f"{experiment}: between the first and last {window_size} train steps the online "
        f"solve rate moved {(late['solve_rate'] - early['solve_rate']) * 100:+.2f} points, "
        "the all-pass fraction "
        f"{(late['all_pass_group_frac'] - early['all_pass_group_frac']) * 100:+.2f} points, "
        "and gradient-bearing groups "
        f"{(late['gradient_group_frac'] - early['gradient_group_frac']) * 100:+.2f} points. "
        f"The loop published {evolution_summary['rounds']} mix versions folding "
        f"{evolution_summary['folded']} revisions into "
        f"{evolution_summary['unique_changed_tasks']} tasks "
        f"({evolution_summary['rewrites']['accepted']} accepted of "
        f"{evolution_summary['harder_attempted']} harder rewrites decided). "
        "The fixed eval moved "
        f"{(evaluations[-1]['trial_solve_rate'] - evaluations[0]['trial_solve_rate']) * 100:+.2f} "
        f"points from base and pass@5 {evaluations[-1]['pass_at_5'] - evaluations[0]['pass_at_5']:+.3f}."
    )
    conclusion["analysis/conclusion"] = analysis_text
    task_trace = [
        row
        for row in evolution_trace_rows(evolution)
        if row["record_type"] == "evolution_fold"
    ]
    result = {
        "schema_version": 3,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "inputs": inputs,
        "rollout_audits": audits,
        "training_diagnostics": training_diagnostics,
        "training_by_policy": training,
        "training_windows": {"early": early, "late": late},
        "evolution_rounds": evolution,
        "evolution_task_trace": task_trace,
        "evolution_summary": evolution_summary,
        "eval_by_checkpoint": evaluations,
        "conclusion_metrics": conclusion,
        "analysis_text": analysis_text,
    }
    digest = hashlib.sha256(
        json.dumps(result["inputs"], sort_keys=True).encode()
    ).hexdigest()
    output = out_dir / f"{experiment}_analysis_{digest[:12]}.jsonl"
    output.write_text(json.dumps(result, sort_keys=True) + "\n")
    trace_output = out_dir / f"{experiment}_evolution_trace_{digest[:12]}.jsonl"
    write_evolution_trace(trace_output, result["evolution_rounds"])
    LOG.info("analysis written to %s", output)
    LOG.info("evolution trace written to %s", trace_output)
    print(
        json.dumps(
            {
                "output": str(output),
                "evolution_trace": str(trace_output),
                "digest": digest,
                "training_windows": result["training_windows"],
                "evolution_summary": result["evolution_summary"],
                "eval": [
                    {
                        key: row[key]
                        for key in (
                            "checkpoint",
                            "successes",
                            "trial_solve_rate",
                            "pass_at_5",
                        )
                    }
                    for row in evaluations
                ],
            },
            indent=2,
        )
    )
    if args.upload:
        url = publish(
            result,
            args.entity,
            args.project,
            args.name or f"{experiment}-evolveloop-analysis",
            digest,
            trace_output,
        )
        LOG.info("published W&B analysis: %s", url)
        print(f"WANDB_URL={url}")


if __name__ == "__main__":
    main()
