"""Check recipe dispatch with isolated child scripts, without starting any jobs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "action,child,forwarded_action",
    [
        ("plan", "submit_tmax_evolving_150.sh", "--plan"),
        ("validate", "submit_tmax_evolving_150.sh", "--validate"),
        ("launch", "submit_tmax_evolving_150.sh", "--launch"),
        ("start", "tmax_evolution_sidecar.sh", "start"),
        ("easier-canary", "tmax_evolution_sidecar.sh", "easier-canary"),
        ("batch-canary", "tmax_evolution_sidecar.sh", "batch-canary"),
    ],
)
def test_recipe_pins_training_feedback_for_trainer_and_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    child: str,
    forwarded_action: str,
) -> None:
    launcher = tmp_path / "run_tmax_evolving_v7_r2.sh"
    launcher.write_text(Path(__file__).with_name(launcher.name).read_text())
    (tmp_path / child).write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\n\' "$1" "$SWE_POLICY_CANARY_K" '
        '"$EVOLVE_SPEC_REPAIR" "$SWE_EVOLVE_SIMPLIFY" '
        '"$EVOLVE_WORKERS" "$METAGEN_PROXY_MAX_CONCURRENCY" '
        '"$TT_DAYTONA_CREATE_CONCURRENCY" "$SYNTH_MODEL" '
        '"$METAGEN_SUBMISSION_MODE" "$EVOLVE_EASIER_MIN_SCORED_ATTEMPTS" '
        '"$SWE_CKPT_INTERVAL" "$SWE_CKPT_FIRST_STEP" "$SWE_CKPT_CONTROLLER_DATA"\n'
    )
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "8")
    monkeypatch.setenv("EVOLVE_WORKERS", "20")
    monkeypatch.setenv("METAGEN_PROXY_MAX_CONCURRENCY", "20")

    result = subprocess.run(
        ["bash", str(launcher), action],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == [
        forwarded_action,
        "0",
        "1",
        "1",
        "100",
        "100",
        "8",
        "gpt-5-6-sol-genai-responses",
        "foreground",
        "16",
        "2",
        "1",
        "1",
    ]
