"""Prepare and verify an isolated mixed-corpus evolution canary."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from torchtitan.experiments.rl.examples.tmax import layout, new_root, rollout_record


@dataclass(frozen=True)
class CanaryCase:
    corpus: str
    task: str
    group: int
    evidence: str
    direction: str = "harder"


TMAX_CANARY_CASES = (
    CanaryCase("tmax", "task_000334_af1152bf", 3, "evolution"),
    CanaryCase("tmax", "task_000065_8c15f360", 120, "evolution"),
    CanaryCase("tmax", "task_000085_f540b211", 75, "evolution"),
    CanaryCase("tmax", "task_000143_91a9a57b", 139, "evolution"),
    CanaryCase("tmax", "task_000109_9dcdc97b", 691, "evolution"),
)
REBENCH_CANARY_CASES = (
    CanaryCase("swe-rebench", "cs-si__eodag-201", 30, "static"),
    CanaryCase("swe-rebench", "corteva__rioxarray-559", 771, "static"),
)
# Groups of the static agentpick run 0f850b (LR 3e-6) whose two sampled rollouts
# both solved the task, alternating corpus and solution format.
AGENTPICK_CORPORA = {"agentpick-clean", "agentpick-calibforge-clean"}
AGENTPICK_CANARY_CASES = (
    CanaryCase("agentpick-clean", "task_004227_00e8d97f", 176, "static"),
    CanaryCase(
        "agentpick-calibforge-clean",
        "system-administration_20260416_020953_009",
        403,
        "static",
    ),
    CanaryCase("agentpick-clean", "tw_460725", 1489, "static"),
    CanaryCase(
        "agentpick-calibforge-clean",
        "machine-learning_20260508_153434_002",
        1761,
        "static",
    ),
    CanaryCase("agentpick-clean", "task_006111_f9cc96c1", 397, "static"),
)
CANARY_CASES = (
    TMAX_CANARY_CASES[0],
    REBENCH_CANARY_CASES[0],
    TMAX_CANARY_CASES[2],
    REBENCH_CANARY_CASES[1],
    TMAX_CANARY_CASES[3],
)
TMAX_EASIER_CANARY_CASES = (
    CanaryCase("tmax", "task_000088_4047adc3", 267, "static", "easier"),
    CanaryCase("tmax", "task_000235_d861beea", 227, "static", "easier"),
    CanaryCase("tmax", "task_000846_c66cf208", 150, "static", "easier"),
    CanaryCase("tmax", "task_002918_3e5568e4", 226, "static", "easier"),
    CanaryCase("tmax", "task_000629_640a6cba", 231, "static", "easier"),
)
REBENCH_EASIER_CANARY_CASES = (
    CanaryCase("swe-rebench", "pypa__setuptools-1650", 26, "static", "easier"),
    CanaryCase("swe-rebench", "cs-si__eodag-634", 10, "static", "easier"),
)
# Run B's rollout_samples keep only 2 rollouts per group, so easier evidence comes
# from the agentpick evolve run big1015 (same 743-row seed): rev-0 easier signals,
# 0/16 solved, no infra failures, earliest groups. Each task was solved once
# (1/48 or 1/64) in run B and ships a reference solution, so it is solvable.
AGENTPICK_EASIER_CANARY_CASES = (
    CanaryCase("agentpick-clean", "task_004494_dbb6143e", 567, "evolution", "easier"),
    CanaryCase(
        "agentpick-calibforge-clean",
        "debugging_20260508_204815_005",
        186,
        "evolution",
        "easier",
    ),
    CanaryCase(
        "agentpick-clean",
        "rts_task_6a81762bd5745f498f36b83e",
        411,
        "evolution",
        "easier",
    ),
    CanaryCase(
        "agentpick-calibforge-clean",
        "security_20260606_153537_003",
        677,
        "evolution",
        "easier",
    ),
    CanaryCase(
        "agentpick-clean", "pypa__bandersnatch-1557", 761, "evolution", "easier"
    ),
)
EASIER_CANARY_CASES = (
    TMAX_EASIER_CANARY_CASES[0],
    REBENCH_EASIER_CANARY_CASES[0],
    TMAX_EASIER_CANARY_CASES[1],
    REBENCH_EASIER_CANARY_CASES[1],
    TMAX_EASIER_CANARY_CASES[2],
)
CANARY_CASES_BY_TASK = {
    case.task: case
    for case in (
        *TMAX_CANARY_CASES,
        *REBENCH_CANARY_CASES,
        *TMAX_EASIER_CANARY_CASES,
        *REBENCH_EASIER_CANARY_CASES,
        *AGENTPICK_CANARY_CASES,
        *AGENTPICK_EASIER_CANARY_CASES,
    )
}
INFRASTRUCTURE_STAGES = {
    "build",
    "daytona_error",
    "no_docker",
    "package_error",
    "setup",
    "error",
}
FORMAT_FAILURE_MARKERS = (
    "without a passing ./sandbox check",
    "malformed",
    "format error",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".incoming")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _selected_rows(seed_mix: Path, count: int, direction: str = "harder") -> list[dict]:
    rows = [
        json.loads(line) for line in seed_mix.read_text().splitlines() if line.strip()
    ]
    seed_corpora = {row.get("metadata", {}).get("corpus") for row in rows}
    if direction not in {"harder", "easier"}:
        raise ValueError(f"unsupported canary direction: {direction}")
    if {"swe-rebench", "tmax"}.issubset(seed_corpora):
        cases = (
            CANARY_CASES[:count]
            if direction == "harder"
            else EASIER_CANARY_CASES[:count]
        )
    elif seed_corpora == AGENTPICK_CORPORA:
        cases = (
            AGENTPICK_CANARY_CASES[:count]
            if direction == "harder"
            else AGENTPICK_EASIER_CANARY_CASES[:count]
        )
    elif seed_corpora == {"tmax"}:
        cases = (
            TMAX_CANARY_CASES[:count]
            if direction == "harder"
            else TMAX_EASIER_CANARY_CASES[:count]
        )
    else:
        raise ValueError(
            f"no canary fixture set supports seed corpora {sorted(seed_corpora)}"
        )
    task_ids = [case.task for case in cases]
    requested = set(task_ids)
    selected = {
        row["metadata"]["instance_id"]: row
        for row in rows
        if row.get("metadata", {}).get("instance_id") in requested
    }
    missing = requested - set(selected)
    if missing:
        raise ValueError(
            f"canary tasks are absent from the seed mix: {sorted(missing)}"
        )
    for case in cases:
        actual_corpus = selected[case.task].get("metadata", {}).get("corpus")
        if actual_corpus != case.corpus:
            raise ValueError(
                f"canary task {case.task} belongs to {actual_corpus}, "
                f"expected {case.corpus}"
            )
    return [selected[task_id] for task_id in task_ids]


def _completion_text(message: dict) -> str:
    if message.get("role") != "assistant":
        raise ValueError(f"expected assistant completion, got {message.get('role')}")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise TypeError("completion content is not text or a content-block list")
    thinking: list[str] = []
    text: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            raise TypeError("completion content block is not an object")
        kind = block.get("type")
        if kind == "thinking" and isinstance(block.get("thinking"), str):
            thinking.append(block["thinking"])
        elif kind == "text" and isinstance(block.get("text"), str):
            text.append(block["text"])
        else:
            raise ValueError(f"unsupported completion content block: {kind!r}")
    parts: list[str] = []
    if thinking:
        thinking_text = "\n".join(thinking)
        parts.append(f"<think>\n{thinking_text}\n</think>")
    parts.extend(text)
    return "\n".join(parts)


def _environment_output(messages: list[dict]) -> str:
    output: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            raise TypeError("environment message content is not text")
        output.append(content)
    return "\n".join(output)


def _turns_from_static_sample(sample: dict) -> list[dict]:
    turns = []
    for expected_id, turn in enumerate(sample.get("turns") or []):
        if turn.get("turn_id") != expected_id:
            raise ValueError(
                f"rollout {sample.get('rollout_id')} has nonsequential turn IDs"
            )
        completion = _completion_text(turn.get("completion_message") or {})
        output = _environment_output(turn.get("env_messages") or [])
        turns.append(rollout_record.turn_line(expected_id + 1, completion, output))
    if not turns:
        raise ValueError(f"rollout {sample.get('rollout_id')} has no turns")
    return turns


def _static_attempts(
    cases: tuple[CanaryCase, ...],
    *,
    rollout_samples: Path,
    lineage_events: Path,
) -> dict[str, list[tuple[dict, list[dict]]]]:
    static_cases = {case.group: case for case in cases if case.evidence == "static"}
    if not static_cases:
        return {}
    observed_tasks = {group: set() for group in static_cases}
    with lineage_events.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            group = event.get("group_id")
            if event.get("event") == "admitted" and group in observed_tasks:
                observed_tasks[group].add(event.get("task_id"))
    for group, case in static_cases.items():
        if observed_tasks[group] != {case.task}:
            raise ValueError(
                f"static group {group} maps to {sorted(observed_tasks[group])}, "
                f"expected only {case.task}"
            )

    attempts = {case.task: [] for case in static_cases.values()}
    required = {
        case.task: 2 if case.direction == "harder" else 4
        for case in static_cases.values()
    }
    with rollout_samples.open(encoding="utf-8") as stream:
        for line in stream:
            sample = json.loads(line)
            case = static_cases.get(sample.get("group_id"))
            if case is None or len(attempts[case.task]) >= required[case.task]:
                continue
            reward = float(sample.get("reward", 0.0))
            expected_outcome = reward > 0 if case.direction == "harder" else reward == 0
            if sample.get("status") != "completed" or not expected_outcome:
                continue
            header = {
                "task": case.task,
                "rev": 0,
                "group": case.group,
                "source_rollout_id": sample.get("rollout_id"),
                "reward": reward,
                "turns": len(sample.get("turns") or []),
                "status": sample.get("status"),
                "corpus": case.corpus,
                "source_rollout_dump": str(rollout_samples),
            }
            attempts[case.task].append((header, _turns_from_static_sample(sample)))
            if all(len(values) >= required[task] for task, values in attempts.items()):
                break
    missing = [
        task for task, values in attempts.items() if len(values) < required[task]
    ]
    if missing:
        raise ValueError(
            "static canary tasks lack enough direction-matched attempts: "
            f"{sorted(missing)}"
        )
    return attempts


def _evolution_attempts(
    reference_run: layout.Run, case: CanaryCase
) -> list[tuple[dict, list[dict]]]:
    """Direction-matched rev-0 records behind a reference run's signal."""
    source_signal = reference_run.signal(case.task, case.group)
    if not source_signal.is_file():
        raise ValueError(f"reference signal is missing: {source_signal}")
    source_record = json.loads(source_signal.read_text())
    if (
        source_record.get("task") != case.task
        or source_record.get("rev") != 0
        or source_record.get("direction") != case.direction
        or (case.direction == "easier" and source_record.get("solved") != 0)
    ):
        raise ValueError(f"reference signal is incompatible: {source_signal}")
    required = 2 if case.direction == "harder" else 4
    source_rows = []
    for source_attempt in source_record.get("attempts", []):
        header, turns = rollout_record.read_record(reference_run.path / source_attempt)
        reward = float(header.get("reward", 0.0))
        if case.direction == "harder":
            matched = reward > 0
        else:
            matched = (
                reward == 0
                and header.get("status") == "completed"
                and not header.get("infra_failed")
                and header.get("rev") == 0
            )
        if matched:
            source_rows.append((header, turns))
        if len(source_rows) == required:
            break
    return source_rows


def _validate_evolution_evidence_package(
    current_package: Path, reference_package: Path
) -> None:
    # setup.sh is optional (agentpick packages have none) but must match when present.
    for relative in ("instruction.md", "environment/Dockerfile", "setup.sh"):
        current = current_package / relative
        reference = reference_package / relative
        if relative == "setup.sh" and not current.exists() and not reference.exists():
            continue
        if not current.is_file() or not reference.is_file():
            raise ValueError(
                f"canary evidence package is missing the comparable file {relative}"
            )
        if current.read_bytes() != reference.read_bytes():
            raise ValueError(
                f"canary evidence package differs from the seed package: {relative}"
            )


def _write_signals(
    root: layout.Root,
    rows: list[dict],
    *,
    reference_root: Path,
    reference_run_name: str,
    rollout_samples: Path,
    lineage_events: Path,
) -> tuple[list[str], list[str]]:
    run_name = "prelaunch-multiseed"
    run = root.run(run_name)
    reference_run = layout.Root(reference_root).run(reference_run_name)
    cases = {
        row["metadata"]["instance_id"]: CANARY_CASES_BY_TASK[
            row["metadata"]["instance_id"]
        ]
        for row in rows
    }
    static_attempts = _static_attempts(
        tuple(cases.values()),
        rollout_samples=rollout_samples,
        lineage_events=lineage_events,
    )
    signals = []
    source_evidence = []
    for row in rows:
        task = row["metadata"]["instance_id"]
        case = cases[task]
        group = case.group
        attempts = []
        evidence: str
        if case.evidence == "evolution":
            _validate_evolution_evidence_package(
                root.data / "sources" / case.corpus / "tasks" / task,
                reference_root / "data" / "sources" / case.corpus / "tasks" / task,
            )
            source_rows = _evolution_attempts(reference_run, case)
            evidence = layout.signal_id(reference_run_name, task, group)
        elif case.evidence == "static":
            source_rows = static_attempts[task]
            evidence = f"{rollout_samples}#group={group}"
        else:
            raise ValueError(f"unsupported canary evidence kind: {case.evidence}")

        required_attempts = 2 if case.direction == "harder" else 4
        for header, turns in source_rows:
            index = len(attempts)
            path = run.rollout_record(task, group, index)
            rollout_record.write_record(
                path,
                {
                    **header,
                    "run": run_name,
                    "group": group,
                    "rollout": index,
                    "reward": float(header["reward"]),
                    "canary": True,
                    "corpus": case.corpus,
                    "source_evidence": evidence,
                },
                turns,
            )
            attempts.append(str(path.relative_to(run.path)))
            if len(attempts) == required_attempts:
                break
        if len(attempts) != required_attempts:
            raise ValueError(
                f"canary evidence needs {required_attempts} "
                f"{case.direction} attempts: {evidence}"
            )
        solved = sum(
            float(rollout_record.read_header(run.path / attempt)["reward"]) > 0
            for attempt in attempts
        )
        layout.write_json_atomic(
            run.signal(task, group),
            {
                "task": task,
                "rev": 0,
                "run": run_name,
                "group": group,
                "direction": case.direction,
                "solved": solved,
                "total": len(attempts),
                "created": layout.stamp(),
                "attempts": attempts,
                "canary": True,
                "corpus": case.corpus,
                "source_evidence": evidence,
            },
        )
        signals.append(layout.signal_id(run_name, task, group))
        source_evidence.append(evidence)
    return signals, source_evidence


def prepare(
    experiment: Path,
    count: int,
    *,
    reference_root: Path,
    reference_run_name: str,
    rollout_samples: Path,
    lineage_events: Path,
    direction: str = "harder",
) -> dict:
    production = layout.Root(experiment / "root")
    production_metadata = json.loads(production.experiment_json.read_text())
    seed_mix = (production.path / production_metadata["seed_mix"]["path"]).resolve()
    if not seed_mix.is_file():
        raise ValueError(f"production seed mix is missing: {seed_mix}")
    sources = sorted(
        path
        for path in (production.data / "sources").iterdir()
        if path.resolve().is_dir()
    )
    if not sources:
        raise ValueError("production root has no usable source links")
    destination_name = "canary-batch" if direction == "harder" else "canary-easier"
    destination = experiment / destination_name
    if destination.exists():
        raise ValueError(f"batch canary already exists: {destination}")
    rows = _selected_rows(seed_mix, count, direction)

    staging = destination.with_name(
        f".{destination.name}.incoming-{uuid.uuid4().hex[:8]}"
    )
    try:
        mix = staging / "seed/mix.jsonl"
        mix.parent.mkdir(parents=True, exist_ok=True)
        mix.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
        root = new_root.create(
            staging / "root",
            mix=mix,
            sources=sources,
            bin_dir=production.bin.resolve(),
            name=f"{experiment.name}-{direction}-multiseed-canary",
            purpose=f"Dry multi-seed prelaunch {direction} evolution canary",
            profile=os.environ.get("TRL_PROFILE")
            or f"{os.environ.get('TMAX_OPERATOR') or getpass.getuser()}-mast",
            fork_from=None,
        )
        metadata = json.loads(root.experiment_json.read_text())
        metadata["seed_mix"]["path"] = os.path.relpath(mix, root.path)
        metadata["dry_canary"] = True
        metadata["canary_corpora"] = sorted({row["metadata"]["corpus"] for row in rows})
        layout.write_json_atomic(root.experiment_json, metadata)
        signals, source_evidence = _write_signals(
            root,
            rows,
            reference_root=reference_root,
            reference_run_name=reference_run_name,
            rollout_samples=rollout_samples,
            lineage_events=lineage_events,
        )
        production_live = production.mix.live
        manifest = {
            "git_commit": subprocess.check_output(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "direction": direction,
            "spec_repair_enabled": os.environ.get("EVOLVE_SPEC_REPAIR", "0") == "1",
            "tasks": [row["metadata"]["instance_id"] for row in rows],
            "task_corpora": {
                row["metadata"]["instance_id"]: row["metadata"]["corpus"]
                for row in rows
            },
            "expected_corpora": sorted({row["metadata"]["corpus"] for row in rows}),
            "signals": signals,
            "source_evidence": source_evidence,
            "production_mix_sha256_before": _sha256(production_live),
            "production_mix_history_before": sorted(
                path.name for path in production.mix.history.glob("*.jsonl")
            ),
        }
        _write_json(staging / "manifest.json", manifest)
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**manifest, "root": str(destination / "root")}


def verify(experiment: Path, direction: str = "harder") -> dict:
    destination_name = "canary-batch" if direction == "harder" else "canary-easier"
    destination = experiment / destination_name
    manifest = json.loads((destination / "manifest.json").read_text())
    if manifest.get("direction") != direction:
        raise ValueError(
            f"canary manifest direction is {manifest.get('direction')!r}, "
            f"expected {direction!r}"
        )
    production = layout.Root(experiment / "root")
    production_hash_after = _sha256(production.mix.live)
    production_history_after = sorted(
        path.name for path in production.mix.history.glob("*.jsonl")
    )
    production_unchanged = (
        production_hash_after == manifest["production_mix_sha256_before"]
        and production_history_after == manifest["production_mix_history_before"]
    )
    expected_action = "evolve" if direction == "harder" else "simplify"
    allowed_actions = {expected_action}
    if direction == "easier" and manifest.get("spec_repair_enabled"):
        allowed_actions.add("repair_spec")

    root = layout.Root(destination / "root")
    results = []
    for task_id in manifest["tasks"]:
        corpus = manifest["task_corpora"][task_id]
        rewrites = list(root.evolution.task(task_id).rewrite_dirs())
        if len(rewrites) != 1:
            results.append(
                {
                    "task": task_id,
                    "corpus": corpus,
                    "status": "missing" if not rewrites else "ambiguous",
                    "stage": None,
                    "reason": f"expected one rewrite, found {len(rewrites)}",
                }
            )
            continue
        record = json.loads(rewrites[0].meta.read_text())
        sessions = rewrites[0].session_dirs() if rewrites[0].sessions.is_dir() else []
        session_statuses = []
        num_trace_files = len(list(rewrites[0].traces.glob("attempt-*.jsonl")))
        num_codex_transcripts = 0
        for session in sessions:
            if session.meta.is_file():
                session_statuses.append(
                    json.loads(session.meta.read_text()).get("status")
                )
            num_codex_transcripts += len(
                list(session.codex_home.glob("sessions/**/*.jsonl"))
            )
        results.append(
            {
                "task": task_id,
                "corpus": corpus,
                "status": record.get("status"),
                "stage": record.get("stage"),
                "reason": record.get("reason"),
                "arm": record.get("arm"),
                "job": record.get("job"),
                "action": record.get("action"),
                "route": record.get("route"),
                "family": record.get("family"),
                "operator": record.get("operator"),
                "usage": record.get("usage"),
                "num_sessions": len(sessions),
                "session_statuses": session_statuses,
                "num_trace_files": num_trace_files,
                "num_codex_transcripts": num_codex_transcripts,
            }
        )

    accepted = sum(result["status"] == "accepted" for result in results)
    accepted_adjustments = sum(
        result["status"] == "accepted" and result.get("action") == expected_action
        for result in results
    )
    accepted_repairs = sum(
        result["status"] == "accepted" and result.get("action") == "repair_spec"
        for result in results
    )
    failures = [
        result
        for result in results
        if result["stage"] in INFRASTRUCTURE_STAGES
        or result.get("arm") != "codex"
        or result.get("job") != direction
        or result.get("action") not in allowed_actions
        or not result.get("num_sessions")
        or not result.get("num_trace_files")
        or not result.get("num_codex_transcripts")
        or "timed_out" in result.get("session_statuses", [])
        or any(
            status not in {"completed", "blocked", "timed_out"}
            for status in result.get("session_statuses", [])
        )
        or result["status"] == "failed"
        or any(
            marker in str(result.get("reason") or "").lower()
            for marker in FORMAT_FAILURE_MARKERS
        )
    ]
    task_timeouts = [
        result["task"]
        for result in results
        if "timed_out" in result.get("session_statuses", [])
    ]
    canary_mix_versions = list(root.mix.history.glob("*.jsonl"))
    dry_mix_unchanged = len(canary_mix_versions) == 1
    covered_corpora = sorted({result["corpus"] for result in results})
    corpus_coverage = covered_corpora == manifest["expected_corpora"]
    corpus_outcomes = {
        corpus: {
            "tested": sum(result["corpus"] == corpus for result in results),
            "accepted": sum(
                result["corpus"] == corpus and result["status"] == "accepted"
                for result in results
            ),
        }
        for corpus in manifest["expected_corpora"]
    }
    passed = (
        len(results) == len(manifest["tasks"])
        and accepted_adjustments >= 1
        and corpus_coverage
        and not failures
        and production_unchanged
        and dry_mix_unchanged
    )
    report = {
        "passed": passed,
        "direction": direction,
        "expected_action": expected_action,
        "allowed_actions": sorted(allowed_actions),
        "accepted": accepted,
        "accepted_adjustments": accepted_adjustments,
        "accepted_repairs": accepted_repairs,
        "tested": len(manifest["tasks"]),
        "expected_corpora": manifest["expected_corpora"],
        "covered_corpora": covered_corpora,
        "corpus_coverage": corpus_coverage,
        "corpus_outcomes": corpus_outcomes,
        "task_timeouts": task_timeouts,
        "failures": failures,
        "production_mix_unchanged": production_unchanged,
        "production_mix_sha256_after": production_hash_after,
        "dry_canary_mix_unchanged": dry_mix_unchanged,
        "results": results,
    }
    report_name = "canary-batch.json" if direction == "harder" else "canary-easier.json"
    _write_json(experiment / "preflight" / report_name, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--count", type=int, default=4, choices=range(3, 6))
    parser.add_argument("--direction", choices=("harder", "easier"), default="harder")
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--reference-run")
    parser.add_argument("--rebench-rollouts", type=Path)
    parser.add_argument("--rebench-events", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.reference_root is None or not args.reference_run:
            parser.error("prepare requires --reference-root and --reference-run")
        if args.rebench_rollouts is None or args.rebench_events is None:
            parser.error("prepare requires --rebench-rollouts and --rebench-events")
        result = prepare(
            args.experiment,
            args.count,
            reference_root=args.reference_root,
            reference_run_name=args.reference_run,
            rollout_samples=args.rebench_rollouts,
            lineage_events=args.rebench_events,
            direction=args.direction,
        )
    else:
        result = verify(args.experiment, args.direction)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.action == "verify" and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
