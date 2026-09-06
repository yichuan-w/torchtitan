# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Seed sizing must not confuse a packaged seed with an evolved revision."""
import json
import os
import subprocess
import sys
from pathlib import Path

EVOLUTION = Path(__file__).resolve().parents[1]
CHECKOUT = EVOLUTION.parents[5]


def test_seed_sizing_preserves_revisions_holdout_and_history(tmp_path):
    sys.path.insert(0, str(EVOLUTION))
    from pack_to_dataset import _tmax_modules

    os.environ["TRL_TT"] = str(CHECKOUT)
    layout = _tmax_modules("layout")
    mix = layout.Root(tmp_path).mix
    rows = []
    for task, revision in (
        ("task_seed", 0),
        ("task_evolved", 1),
        ("tw_evolved", 2),
        ("task_holdout", 0),
    ):
        rows.append(
            {
                "metadata": {
                    "instance_id": task,
                    "rev": revision,
                    "dockerfile": "FROM ubuntu:22.04\n",
                    "daytona_cpu": 4,
                    "daytona_mem_gb": 8,
                    "daytona_disk_gb": 10,
                }
            }
        )
    _, original = mix.publish([json.dumps(row) for row in rows])
    original_bytes = original.read_bytes()
    sizing = tmp_path / "sizing.jsonl"
    sizing.write_text(
        "".join(
            json.dumps(
                {
                    "task_id": row["metadata"]["instance_id"],
                    "cpu": 1,
                    "mem_gb": 2,
                    "disk_gb": 2,
                }
            )
            + "\n"
            for row in rows
        )
    )
    command = [
        sys.executable,
        str(EVOLUTION / "apply_audit_sizing.py"),
        "--mix",
        str(mix.live),
        "--sizing",
        str(sizing),
        "--holdout-n",
        "1",
    ]
    environment = {**os.environ, "TRL_TT": str(CHECKOUT)}
    # -S also excludes torch: this is a stdlib-only data operation.
    command.insert(1, "-S")
    subprocess.run(command, env=environment, check=True, capture_output=True)
    assert mix.live.read_bytes() == original_bytes
    subprocess.run(
        command + ["--apply"], env=environment, check=True, capture_output=True
    )
    updated = [json.loads(line) for line in mix.live.read_text().splitlines()]
    assert updated[0]["metadata"] == {
        **rows[0]["metadata"],
        "daytona_cpu": 1,
        "daytona_mem_gb": 2,
        "daytona_disk_gb": 2,
    }
    assert updated[1:] == rows[1:]
    assert original.read_bytes() == original_bytes
    assert mix.live_version()[0] == 2


def test_mix_tools_help_does_not_import_training_runtime():
    for script in (
        "apply_audit_sizing.py",
        "drop_from_mix.py",
        "pin_bullseye_sources.py",
    ):
        subprocess.run(
            [sys.executable, "-S", str(EVOLUTION / script), "--help"],
            env={**os.environ, "TRL_TT": str(CHECKOUT)},
            check=True,
            capture_output=True,
        )
