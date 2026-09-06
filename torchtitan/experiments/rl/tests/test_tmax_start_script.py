# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Exercise the startup script without GPUs or real systemd services."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def starter(tmp_path):
    source = Path(__file__).parents[1] / "examples/tmax/runbook"
    checkout = tmp_path / "checkout"
    runbook = checkout / "torchtitan/experiments/rl/examples/tmax/runbook"
    runbook.mkdir(parents=True)
    for name in ("start.sh", "rltrain.env"):
        shutil.copy2(source / name, runbook / name)
    (runbook / "profiles").mkdir()
    (runbook / "profiles/test.env").write_text(f"TRL_TT={checkout}\n")
    root = tmp_path / "experiment"
    binaries = tmp_path / "tools"
    binaries.mkdir()

    def script(path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
        path.chmod(0o755)

    script(binaries / "sleep", "exit 0\n")
    script(
        binaries / "systemctl",
        """
echo "systemctl $*" >> "$TEST_TRACE"
case "$2" in
  show-environment) exit 0 ;;
  is-active) test -f "$TEST_STATE/${!#}" ;;
  stop) rm "$TEST_STATE/${!#}" ;;
esac
""",
    )
    script(
        binaries / "systemd-run",
        """
echo "systemd-run" >> "$TEST_TRACE"
if [ "${FAIL_TRAIN:-0}" = 1 ]; then exit 1; fi
touch "$TEST_STATE/train-experiment"
while [ "$1" != bash ]; do shift; done
"$@"
""",
    )
    script(
        runbook / "launch_9b.sh",
        """
echo "train ${1:-start} TB=$SWE_VAL_SAMPLES K=$SWE_TB2_VAL_K eval=$SWE_NUM_EVAL_GENERATORS" >> "$TEST_TRACE"
""",
    )
    script(
        runbook.parent / "evolution/restart_evolve.sh",
        """
echo "evolve $*" >> "$TEST_TRACE"
touch "$TEST_STATE/evolve-experiment"
""",
    )
    for relative in ("venv/bin/python", "experiment/bin/codex", "experiment/bin/jq"):
        script(tmp_path / relative, "exit 0\n")
    for relative in (
        "model/config.json",
        "experiment/data/mix/live.jsonl",
        "tb.jsonl",
        "synth.env",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    config = tmp_path / "run.env"
    config.write_text(
        f"""TRL_PROFILE=test
TRL_BASE={root}
TRL_VENV={tmp_path}/venv
TRL_MODEL={tmp_path}/model
SWE_TB2_VAL_DATA={tmp_path}/tb.jsonl
SYNTH_ENV_FILE={tmp_path}/synth.env
RL_GPUS=0,1,2,3,4,5
SWE_VAL_SAMPLES=89
SWE_TB2_VAL_K=5
SWE_NUM_EVAL_GENERATORS=1
SWE_EVAL_GEN_DP=1
"""
    )
    state = tmp_path / "state"
    state.mkdir()
    trace = tmp_path / "trace"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "DAYTONA_API_KEY": "test-key",
        "TEST_TRACE": str(trace),
        "TEST_STATE": str(state),
    }

    def run(*args, **overrides):
        result = subprocess.run(
            ["bash", str(runbook / "start.sh"), str(config), *args],
            env={**env, **overrides},
            text=True,
            capture_output=True,
        )
        return result, trace.read_text()

    return run, state


def test_dry_run_starts_no_services(starter):
    run, _ = starter
    result, trace = run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert "train --dry-run TB=89 K=5 eval=1" in trace
    assert "systemd-run" not in trace
    assert "evolve 2 120" not in trace


def test_start_passes_eval_config_to_trainer_and_starts_loop(starter):
    run, state = starter
    result, trace = run()
    assert result.returncode == 0, result.stderr
    assert "evolve 2 120" in trace
    assert "train start TB=89 K=5 eval=1" in trace
    assert (state / "train-experiment").exists()


def test_existing_training_is_not_replaced(starter):
    run, state = starter
    (state / "train-experiment").touch()
    result, trace = run()
    assert result.returncode == 2
    assert "already running" in result.stderr
    assert "systemd-run" not in trace


def test_failed_training_launch_stops_the_new_loop(starter):
    run, state = starter
    result, trace = run(FAIL_TRAIN="1")
    assert result.returncode == 1
    assert "systemctl --user stop evolve-experiment" in trace
    assert not (state / "evolve-experiment").exists()
