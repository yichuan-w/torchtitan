from __future__ import annotations

import json
from pathlib import Path

import pytest

from mast_rl.tmax_evolution_batch_canary import (
    AGENTPICK_EASIER_CANARY_CASES,
    CANARY_CASES,
    EASIER_CANARY_CASES,
    TMAX_CANARY_CASES,
    _evolution_attempts,
    _selected_rows,
    _sha256,
    _static_attempts,
    _validate_evolution_evidence_package,
    verify,
)
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_selected_rows_interleave_tmax_and_rebench(tmp_path: Path) -> None:
    cases = CANARY_CASES[:4]
    rows = [
        {
            "metadata": {
                "corpus": case.corpus,
                "instance_id": case.task,
            }
        }
        for case in reversed(cases)
    ]
    seed_mix = tmp_path / "mix.jsonl"
    _write_jsonl(seed_mix, rows)

    selected = _selected_rows(seed_mix, 4)

    assert [row["metadata"]["instance_id"] for row in selected] == [
        case.task for case in cases
    ]
    assert [row["metadata"]["corpus"] for row in selected] == [
        "tmax",
        "swe-rebench",
        "tmax",
        "swe-rebench",
    ]


def test_selected_rows_preserve_tmax_only_canary(tmp_path: Path) -> None:
    rows = [
        {
            "metadata": {
                "corpus": case.corpus,
                "instance_id": case.task,
            }
        }
        for case in reversed(TMAX_CANARY_CASES)
    ]
    seed_mix = tmp_path / "mix.jsonl"
    _write_jsonl(seed_mix, rows)

    selected = _selected_rows(seed_mix, 5)

    assert [row["metadata"]["instance_id"] for row in selected] == [
        case.task for case in TMAX_CANARY_CASES
    ]
    assert {row["metadata"]["corpus"] for row in selected} == {"tmax"}


def test_selected_rows_interleave_mixed_easier_cases(tmp_path: Path) -> None:
    cases = EASIER_CANARY_CASES[:4]
    rows = [
        {
            "metadata": {
                "corpus": case.corpus,
                "instance_id": case.task,
            }
        }
        for case in reversed(cases)
    ]
    seed_mix = tmp_path / "mix.jsonl"
    _write_jsonl(seed_mix, rows)

    selected = _selected_rows(seed_mix, 4, "easier")

    assert [row["metadata"]["instance_id"] for row in selected] == [
        case.task for case in cases
    ]
    assert [row["metadata"]["corpus"] for row in selected] == [
        "tmax",
        "swe-rebench",
        "tmax",
        "swe-rebench",
    ]


def test_selected_rows_interleave_agentpick_easier_cases(tmp_path: Path) -> None:
    cases = AGENTPICK_EASIER_CANARY_CASES
    rows = [
        {"metadata": {"corpus": case.corpus, "instance_id": case.task}}
        for case in reversed(cases)
    ]
    seed_mix = tmp_path / "mix.jsonl"
    _write_jsonl(seed_mix, rows)

    selected = _selected_rows(seed_mix, 5, "easier")

    assert [row["metadata"]["instance_id"] for row in selected] == [
        case.task for case in cases
    ]
    assert [row["metadata"]["corpus"] for row in selected] == [
        "agentpick-clean",
        "agentpick-calibforge-clean",
        "agentpick-clean",
        "agentpick-calibforge-clean",
        "agentpick-clean",
    ]
    assert {(case.evidence, case.direction) for case in cases} == {
        ("evolution", "easier")
    }


def test_evolution_attempts_keep_four_clean_failed_easier_records(
    tmp_path: Path,
) -> None:
    case = AGENTPICK_EASIER_CANARY_CASES[0]
    run = layout.Root(tmp_path / "reference").run("reference-run")
    turn = rollout_record.turn_line(1, "<response></response>", "out")
    headers = [
        {"reward": 0.0, "status": "completed", "infra_failed": True},
        {"reward": 0.0, "status": "completed"},
        {"reward": 0.0, "status": "error"},
        {"reward": 0.0, "status": "completed"},
        {"reward": 0.0, "status": "completed", "rev": 1},
        {"reward": 0.0, "status": "completed"},
        {"reward": 0.0, "status": "completed"},
        {"reward": 0.0, "status": "completed"},
    ]
    attempts = []
    for index, header in enumerate(headers):
        path = run.rollout_record(case.task, case.group, index)
        rollout_record.write_record(
            path,
            {"rev": 0, **header, "task": case.task, "rollout": index},
            [turn],
        )
        attempts.append(str(path.relative_to(run.path)))
    signal = {
        "task": case.task,
        "rev": 0,
        "group": case.group,
        "direction": "easier",
        "solved": 0,
        "total": len(attempts),
        "attempts": attempts,
    }
    layout.write_json_atomic(run.signal(case.task, case.group), signal)

    rows = _evolution_attempts(run, case)

    assert [header["rollout"] for header, _turns in rows] == [1, 3, 5, 6]

    layout.write_json_atomic(
        run.signal(case.task, case.group), {**signal, "direction": "harder"}
    )
    with pytest.raises(ValueError, match="incompatible"):
        _evolution_attempts(run, case)


def test_evolution_evidence_accepts_packages_without_setup(tmp_path: Path) -> None:
    current = tmp_path / "current"
    reference = tmp_path / "reference"
    for package in (current, reference):
        (package / "environment").mkdir(parents=True)
        (package / "instruction.md").write_text("Fix the task.\n")
        (package / "environment/Dockerfile").write_text("FROM image\n")

    _validate_evolution_evidence_package(current, reference)

    (reference / "setup.sh").write_text("#!/bin/sh\n")
    with pytest.raises(ValueError, match="setup.sh"):
        _validate_evolution_evidence_package(current, reference)


def test_static_attempts_convert_successful_rebench_rollouts(
    tmp_path: Path,
) -> None:
    case = next(case for case in CANARY_CASES if case.evidence == "static")
    events = tmp_path / "events.jsonl"
    _write_jsonl(
        events,
        [
            {
                "event": "admitted",
                "group_id": case.group,
                "task_id": case.task,
            }
        ],
    )
    completion = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Inspect the checkout."},
            {
                "type": "text",
                "text": (
                    "<response><analysis>Inspect.</analysis>"
                    "<plan>List files.</plan><commands>"
                    '<keystrokes duration="0.1">ls -la\n</keystrokes>'
                    "</commands></response>"
                ),
            },
        ],
    }
    samples = tmp_path / "rollout_samples.jsonl"
    _write_jsonl(
        samples,
        [
            {
                "group_id": case.group,
                "rollout_id": rollout_id,
                "status": "completed",
                "reward": 1.0,
                "turns": [
                    {
                        "turn_id": 0,
                        "completion_message": completion,
                        "env_messages": [
                            {"role": "user", "content": "New Terminal Output:\nfile"}
                        ],
                    }
                ],
            }
            for rollout_id in (2, 5)
        ],
    )

    attempts = _static_attempts(
        (case,), rollout_samples=samples, lineage_events=events
    )[case.task]

    assert len(attempts) == 2
    assert [header["source_rollout_id"] for header, _turns in attempts] == [2, 5]
    assert attempts[0][1] == [
        {
            "turn": 1,
            "keystrokes": ["ls -la\n"],
            "output": "New Terminal Output:\nfile",
            "analysis": "Inspect.",
            "plan": "List files.",
            "think": "Inspect the checkout.",
        }
    ]


def test_static_attempts_preserve_four_failed_easier_rollouts(
    tmp_path: Path,
) -> None:
    case = EASIER_CANARY_CASES[1]
    events = tmp_path / "events.jsonl"
    _write_jsonl(
        events,
        [
            {
                "event": "admitted",
                "group_id": case.group,
                "task_id": case.task,
            }
        ],
    )
    completion = {
        "role": "assistant",
        "content": (
            "<response><analysis>Inspect.</analysis><plan>Run.</plan><commands>"
            '<keystrokes duration="0.1">pytest -q\n</keystrokes>'
            "</commands></response>"
        ),
    }
    samples = tmp_path / "rollout_samples.jsonl"
    rows = []
    for rollout_id, reward in enumerate((0.0, 0.0, 1.0, 0.0, 0.0)):
        rows.append(
            {
                "group_id": case.group,
                "rollout_id": rollout_id,
                "status": "completed",
                "reward": reward,
                "turns": [
                    {
                        "turn_id": 0,
                        "completion_message": completion,
                        "env_messages": [
                            {"role": "user", "content": "New Terminal Output:\nfailed"}
                        ],
                    }
                ],
            }
        )
    _write_jsonl(samples, rows)

    attempts = _static_attempts(
        (case,), rollout_samples=samples, lineage_events=events
    )[case.task]

    assert len(attempts) == 4
    assert [header["source_rollout_id"] for header, _turns in attempts] == [
        0,
        1,
        3,
        4,
    ]
    assert {header["reward"] for header, _turns in attempts} == {0.0}


def test_evolution_evidence_rejects_changed_task_contract(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    reference = tmp_path / "reference"
    for package in (current, reference):
        (package / "environment").mkdir(parents=True)
        (package / "instruction.md").write_text("Fix the task.\n")
        (package / "environment/Dockerfile").write_text("FROM image\n")
        (package / "setup.sh").write_text("#!/bin/sh\n")
    (current / "instruction.md").write_text("Fix a different task.\n")

    with pytest.raises(ValueError, match="instruction.md"):
        _validate_evolution_evidence_package(current, reference)


def _canary_results(
    experiment: Path, actions: tuple[str, ...], *, repairs_enabled: bool
) -> None:
    production = layout.Root(experiment / "root")
    production.mix.history.mkdir(parents=True)
    production.mix.live.write_text("{}\n")
    production.mix.version_path(1, "fixture").write_text("{}\n")
    destination = experiment / "canary-easier"
    root = layout.Root(destination / "root")
    root.mix.history.mkdir(parents=True)
    root.mix.version_path(1, "fixture").write_text("{}\n")
    corpora = ("tmax", "swe-rebench", "tmax")
    task_corpora = {}
    for index, (action, corpus) in enumerate(zip(actions, corpora, strict=True)):
        task_id = f"task-{index}"
        task_corpora[task_id] = corpus
        rewrite = root.evolution.task(task_id).rewrite("easier", "fixture")
        layout.write_json_atomic(
            rewrite.meta,
            {
                "status": "accepted",
                "stage": "accepted",
                "arm": "codex",
                "job": "easier",
                "action": action,
                "route": action,
            },
        )
        rewrite.traces.mkdir(parents=True)
        (rewrite.traces / "attempt-000.jsonl").write_text("{}\n")
        session = rewrite.session("agent", "fixture")
        layout.write_json_atomic(session.meta, {"status": "completed"})
        transcripts = session.codex_home / "sessions"
        transcripts.mkdir(parents=True)
        (transcripts / "turns.jsonl").write_text("{}\n")
    layout.write_json_atomic(
        destination / "manifest.json",
        {
            "direction": "easier",
            "spec_repair_enabled": repairs_enabled,
            "tasks": list(task_corpora),
            "task_corpora": task_corpora,
            "expected_corpora": sorted(set(corpora)),
            "production_mix_sha256_before": _sha256(production.mix.live),
            "production_mix_history_before": sorted(
                path.name for path in production.mix.history.glob("*.jsonl")
            ),
        },
    )


def test_easier_canary_counts_repairs_separately(tmp_path: Path) -> None:
    _canary_results(
        tmp_path, ("simplify", "repair_spec", "simplify"), repairs_enabled=True
    )

    report = verify(tmp_path, "easier")

    assert report["passed"]
    assert report["accepted"] == 3
    assert report["accepted_adjustments"] == 2
    assert report["accepted_repairs"] == 1
    assert report["production_mix_unchanged"]
    assert report["dry_canary_mix_unchanged"]


def test_easier_canary_requires_an_accepted_simplification(tmp_path: Path) -> None:
    _canary_results(
        tmp_path, ("repair_spec", "repair_spec", "repair_spec"), repairs_enabled=True
    )

    report = verify(tmp_path, "easier")

    assert not report["passed"]
    assert report["accepted_repairs"] == 3
    assert report["accepted_adjustments"] == 0
    assert report["failures"] == []


@pytest.mark.parametrize(
    ("action", "repairs_enabled"), (("repair_spec", False), ("invalid_signal", True))
)
def test_easier_canary_rejects_unexpected_routes(
    tmp_path: Path, action: str, repairs_enabled: bool
) -> None:
    _canary_results(
        tmp_path, ("simplify", action, "simplify"), repairs_enabled=repairs_enabled
    )

    report = verify(tmp_path, "easier")

    assert not report["passed"]
    assert [result["task"] for result in report["failures"]] == ["task-1"]
