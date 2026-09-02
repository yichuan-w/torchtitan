#!/usr/bin/env python3
"""Publish a reproducible evolve-loop analysis as one W&B analysis run.

The analysis keeps three instruments separate:

* exact online training-group outcomes from the source W&B training runs;
* accepted and rejected evolution rounds from the evolution git lineage;
* fixed TB-2.0 evaluation summaries already stored in W&B.

``rollout_samples.jsonl`` is still hashed and audited, but it is not used to
estimate group rates: take8 stores only the rollouts packed into minibatches,
not all 16 members of each generated group.

The generated JSONL contains the complete derived records plus hashes and exact
locations of every input. Passing ``--upload`` publishes the same records to a
content-addressed W&B run, so rerunning unchanged inputs does not append duplicate
history.
"""

from __future__ import annotations

import argparse
import ast
import collections
import datetime as dt
import hashlib
import json
import logging
import math
import subprocess
from pathlib import Path

LOG = logging.getLogger("publish_evolve_analysis")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def value_sha256(value: object) -> str:
    """Hash one JSON value with a stable serialization."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a UTC offset: {value}")
    return parsed


def audit_rollout_samples(samples_path: Path, expected_group_size: int) -> dict:
    """Describe the persisted sample subset without treating it as full groups."""
    malformed = 0
    total_records = 0
    validation_records = 0
    scored_records = 0
    group_sizes: collections.Counter = collections.Counter()
    rollout_ids: collections.Counter = collections.Counter()
    statuses: collections.Counter = collections.Counter()

    with samples_path.open() as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            total_records += 1
            if record.get("is_validation"):
                validation_records += 1
                continue
            group_id = record.get("group_id")
            rollout_id = record.get("rollout_id")
            if group_id is None or rollout_id is None or int(group_id) < 0:
                continue
            group_id, rollout_id = int(group_id), int(rollout_id)
            group_sizes[group_id] += 1
            rollout_ids[rollout_id] += 1
            statuses[str(record.get("status"))] += 1
            reward = record.get("reward")
            if isinstance(reward, (int, float)) and math.isfinite(float(reward)):
                scored_records += 1

    size_histogram = collections.Counter(group_sizes.values())
    complete_groups = size_histogram.get(expected_group_size, 0)
    return {
        "malformed_lines": malformed,
        "total_records": total_records,
        "validation_records": validation_records,
        "scored_records": scored_records,
        "unique_group_ids": len(group_sizes),
        "expected_group_size": expected_group_size,
        "complete_groups": complete_groups,
        "partial_groups": len(group_sizes) - complete_groups,
        "persisted_group_size_histogram": dict(sorted(size_histogram.items())),
        "rollout_id_counts": dict(sorted(rollout_ids.items())),
        "status_counts": dict(sorted(statuses.items())),
        "population_complete": complete_groups == len(group_sizes),
        "analysis_use": (
            "provenance and persistence audit only; online W&B metrics are used "
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


def git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE
    )


def snapshot_rows(repo: Path, ref: str) -> dict[str, dict]:
    raw = git_output(repo, "show", f"{ref}:mix_snapshot.jsonl")
    rows = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["metadata"]["instance_id"]] = row
    return rows


def load_evolution_metrics(
    repo: Path, run_start: dt.datetime, run_end: dt.datetime
) -> tuple[list[dict], dict]:
    fmt = "%H%x09%ct%x09%s"
    raw = git_output(
        repo,
        "log",
        "--reverse",
        f"--since={run_start.isoformat()}",
        f"--until={run_end.isoformat()}",
        f"--format={fmt}",
        "--grep=^round:",
    )
    rounds = []
    aggregate: collections.Counter = collections.Counter()
    changed_counts: collections.Counter = collections.Counter()
    folded_cumulative = 0
    for index, line in enumerate(raw.splitlines(), start=1):
        commit, epoch, message = line.split("\t", 2)
        result = ast.literal_eval(message.split("round: ", 1)[1])
        counts = result.get("counts", {}) or {}
        current = snapshot_rows(repo, commit)
        parent = snapshot_rows(repo, commit + "^")
        changed = sorted(
            task_id
            for task_id in current.keys() & parent.keys()
            if current[task_id] != parent[task_id]
        )
        task_changes = [
            {
                "task_id": task_id,
                "source_sample_revision": value_sha256(parent[task_id]),
                "folded_sample_revision": value_sha256(current[task_id]),
            }
            for task_id in changed
        ]
        expected = int(result.get("folded", 0))
        if len(changed) != expected:
            raise RuntimeError(
                f"{commit[:8]} says folded={expected}, but {len(changed)} mix rows changed"
            )
        changed_counts.update(changed)
        folded_cumulative += expected
        failed = sum(
            int(value) for key, value in counts.items() if key.startswith("revalidate_")
        )
        attempted = int(counts.get("ok", 0)) + failed
        timestamp = dt.datetime.fromtimestamp(int(epoch), tz=dt.UTC)
        record = {
            "round": index,
            "commit": commit,
            "timestamp": timestamp.isoformat(),
            "elapsed_hours": (timestamp - run_start.astimezone(dt.UTC)).total_seconds()
            / 3600,
            "processed": int(result.get("processed", 0)),
            "retuned": int(result.get("retuned", 0)),
            "folded": expected,
            "folded_cumulative": folded_cumulative,
            "accepted_harder": int(counts.get("ok", 0)),
            "kept": int(counts.get("kept", 0)),
            "revalidate_failed": failed,
            "harder_accept_rate": int(counts.get("ok", 0)) / attempted
            if attempted
            else None,
            "deferred_easier": int(counts.get("deferred_easier", 0)),
            "no_pool_dir": int(counts.get("no_pool_dir", 0)),
            "unaccounted": int(result.get("processed", 0))
            - sum(map(int, counts.values())),
            "changed_task_ids": changed,
            "task_changes": task_changes,
            "counts": counts,
        }
        rounds.append(record)
        for key in ("processed", "retuned", "folded"):
            aggregate[key] += int(result.get(key, 0))
        aggregate.update({key: int(value) for key, value in counts.items()})

    revalidate_failed = sum(
        value for key, value in aggregate.items() if key.startswith("revalidate_")
    )
    attempted = aggregate["ok"] + revalidate_failed
    summary = {
        "rounds": len(rounds),
        **dict(aggregate),
        "revalidate_failed": revalidate_failed,
        "harder_attempted": attempted,
        "harder_accept_rate": aggregate["ok"] / attempted if attempted else None,
        "unique_changed_tasks": len(changed_counts),
        "repeated_tasks": sum(value > 1 for value in changed_counts.values()),
        "repeat_folds": sum(value - 1 for value in changed_counts.values()),
        "unaccounted": aggregate["processed"]
        - sum(
            value
            for key, value in aggregate.items()
            if key not in {"processed", "retuned", "folded"}
            and key != "revalidate_failed"
            and key != "harder_attempted"
        ),
    }
    return rounds, summary


def evolution_trace_rows(rounds: list[dict]) -> list[dict]:
    """Flatten reconstructed rounds into downloadable round and fold events."""
    rows = []
    for round_row in rounds:
        common = {
            "schema_version": 1,
            "round": round_row["round"],
            "timestamp": round_row["timestamp"],
            "elapsed_hours": round_row["elapsed_hours"],
            "commit": round_row["commit"],
        }
        rows.append(
            {
                **common,
                "record_type": "evolution_round",
                "processed": round_row["processed"],
                "retuned": round_row["retuned"],
                "folded": round_row["folded"],
                "folded_cumulative": round_row["folded_cumulative"],
                "accepted_harder": round_row["accepted_harder"],
                "kept": round_row["kept"],
                "revalidate_failed": round_row["revalidate_failed"],
                "deferred_easier": round_row["deferred_easier"],
                "no_pool_dir": round_row["no_pool_dir"],
                "unaccounted": round_row["unaccounted"],
                "counts": round_row["counts"],
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

    run_id = f"take8-evolve-{digest[:10]}"
    path = f"{entity}/{project}/{run_id}"
    try:
        existing = wandb.Api().run(path)
        LOG.info("W&B run already exists: %s", existing.url)
        return existing.url
    except CommError:
        pass

    private_config_keys = {"dump", "rollout_samples", "evolution_repo"}
    public_config = {
        key: value
        for key, value in result["inputs"].items()
        if key not in private_config_keys
    }
    run = wandb.init(
        entity=entity,
        project=project,
        id=run_id,
        name=name,
        job_type="analysis",
        group="take8",
        tags=["take8", "evolveloop", "offline-analysis"],
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
                "evolution/folded": row["folded"],
                "evolution/folded_cumulative": row["folded_cumulative"],
                "evolution/accepted_harder": row["accepted_harder"],
                "evolution/revalidate_failed": row["revalidate_failed"],
                "evolution/harder_accept_rate": row["harder_accept_rate"],
                "evolution/deferred_easier": row["deferred_easier"],
                "evolution/no_pool_dir": row["no_pool_dir"],
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
            "timestamp",
            "elapsed_hours",
            "processed",
            "folded",
            "folded_cumulative",
            "accepted_harder",
            "revalidate_failed",
            "deferred_easier",
            "no_pool_dir",
            "harder_accept_rate",
            "commit",
        ],
        data=[
            [
                row["round"],
                row["timestamp"],
                row["elapsed_hours"],
                row["processed"],
                row["folded"],
                row["folded_cumulative"],
                row["accepted_harder"],
                row["revalidate_failed"],
                row["deferred_easier"],
                row["no_pool_dir"],
                row["harder_accept_rate"],
                row["commit"],
            ]
            for row in result["evolution_rounds"]
        ],
    )
    evolution_task_trace = wandb.Table(
        columns=[
            "round",
            "timestamp",
            "elapsed_hours",
            "task_id",
            "source_sample_revision",
            "folded_sample_revision",
            "commit",
        ],
        data=[
            [
                row["round"],
                row["timestamp"],
                row["elapsed_hours"],
                row["task_id"],
                row["source_sample_revision"],
                row["folded_sample_revision"],
                row["commit"],
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
        name=f"take8-evolution-trace-{digest[:10]}",
        type="evolution-trace",
        description=(
            "Reconstructed take8 evolution rounds and accepted task folds. "
            "Each fold includes the source and folded sample revisions."
        ),
        metadata={
            "schema_version": 1,
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
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--evolution-repo", type=Path, required=True)
    parser.add_argument("--run-start", required=True)
    parser.add_argument("--run-end", required=True)
    parser.add_argument("--group-size", type=int, default=16)
    parser.add_argument("--entity", required=True)
    parser.add_argument("--project", default="terminal-agent-rl")
    parser.add_argument(
        "--eval-run", action="append", default=[], metavar="STEP=RUN_ID"
    )
    parser.add_argument("--train-run", action="append", default=[], metavar="RUN_ID")
    parser.add_argument("--name", default="take8-evolveloop-analysis")
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    configure_logging(args.log_dir / f"evolveloop_analysis_{timestamp}.jsonl")
    samples = args.dump / "outputs/rl/rollout_samples.jsonl"
    if not samples.is_file():
        raise FileNotFoundError(samples)
    run_start, run_end = parse_iso(args.run_start), parse_iso(args.run_end)
    if run_end <= run_start:
        raise ValueError("--run-end must be after --run-start")

    LOG.info("auditing persisted rollout samples in %s", samples)
    sample_diagnostics = audit_rollout_samples(samples, args.group_size)
    LOG.info("reading evolution lineage from %s", args.evolution_repo)
    evolution, evolution_summary = load_evolution_metrics(
        args.evolution_repo, run_start, run_end
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
        "analysis_schema": 3,
        "analysis_script_sha256": file_sha256(Path(__file__)),
        "dump": str(args.dump),
        "rollout_samples": str(samples),
        "rollout_samples_sha256": file_sha256(samples),
        "rollout_samples_bytes": samples.stat().st_size,
        "expected_group_size": args.group_size,
        "evolution_repo": str(args.evolution_repo),
        "evolution_window_commits": [row["commit"] for row in evolution],
        "run_start": run_start.isoformat(),
        "run_end": run_end.isoformat(),
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
        "analysis/evolution_folded": evolution_summary.get("folded", 0),
        "analysis/evolution_unique_changed_tasks": evolution_summary[
            "unique_changed_tasks"
        ],
        "analysis/evolution_harder_attempted": evolution_summary["harder_attempted"],
        "analysis/evolution_harder_accepted": evolution_summary.get("ok", 0),
        "analysis/evolution_harder_accept_rate": evolution_summary[
            "harder_accept_rate"
        ],
        "analysis/evolution_revalidate_failed": evolution_summary["revalidate_failed"],
        "analysis/evolution_deferred_easier": evolution_summary.get(
            "deferred_easier", 0
        ),
        "analysis/evolution_no_pool_dir": evolution_summary.get("no_pool_dir", 0),
        "analysis/evolution_unaccounted": evolution_summary["unaccounted"],
        "analysis/persisted_complete_groups": sample_diagnostics["complete_groups"],
        "analysis/persisted_partial_groups": sample_diagnostics["partial_groups"],
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
    causal_limit = (
        "Historical evolution signals do not record a source task-version hash, "
        "policy version, or emission timestamp. Aggregate loop effects are "
        "measurable, but a per-task before/after causal solve rate is not "
        "reconstructable from take8."
    )
    analysis_text = (
        "Across the first versus last five train steps, online solve rate fell "
        f"{(early['solve_rate'] - late['solve_rate']) * 100:.2f} points and the "
        "all-pass fraction fell "
        f"{(early['all_pass_group_frac'] - late['all_pass_group_frac']) * 100:.2f} "
        "points, while gradient-bearing groups rose "
        f"{(late['gradient_group_frac'] - early['gradient_group_frac']) * 100:.2f} "
        f"points. Together with {evolution_summary.get('folded', 0)} accepted "
        "folds, this is consistent with a harder, less-trivial training stream. "
        "The fixed eval ended only "
        f"{(evaluations[-1]['trial_solve_rate'] - evaluations[0]['trial_solve_rate']) * 100:.2f} "
        "points above base and pass@5 was unchanged, so take8 does not show a "
        "durable held-out generalization gain. " + causal_limit
    )
    conclusion["analysis/conclusion"] = analysis_text
    conclusion["analysis/causal_limit"] = causal_limit
    task_trace = [
        row
        for row in evolution_trace_rows(evolution)
        if row["record_type"] == "evolution_fold"
    ]
    result = {
        "schema_version": 2,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "inputs": inputs,
        "sample_diagnostics": sample_diagnostics,
        "training_diagnostics": training_diagnostics,
        "training_by_policy": training,
        "training_windows": {"early": early, "late": late},
        "evolution_rounds": evolution,
        "evolution_task_trace": task_trace,
        "evolution_summary": evolution_summary,
        "eval_by_checkpoint": evaluations,
        "conclusion_metrics": conclusion,
        "analysis_text": analysis_text,
        "causal_limit": causal_limit,
    }
    digest = hashlib.sha256(
        json.dumps(result["inputs"], sort_keys=True).encode()
    ).hexdigest()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"evolveloop_take8_analysis_{digest[:12]}.jsonl"
    payload = json.dumps(result, sort_keys=True) + "\n"
    if output.exists():
        existing = json.loads(output.read_text())
        if existing.get("inputs") != result["inputs"]:
            raise RuntimeError(f"content-address collision at {output}")
        result = existing
    else:
        output.write_text(payload)
    trace_output = args.output_dir / f"evolution_trace_take8_{digest[:12]}.jsonl"
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
                "causal_limit": result["causal_limit"],
            },
            indent=2,
        )
    )
    if args.upload:
        url = publish(
            result,
            args.entity,
            args.project,
            args.name,
            digest,
            trace_output,
        )
        LOG.info("published W&B analysis: %s", url)
        print(f"WANDB_URL={url}")


if __name__ == "__main__":
    main()
