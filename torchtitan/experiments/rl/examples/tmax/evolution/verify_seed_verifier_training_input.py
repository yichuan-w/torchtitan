# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Verify a downloaded seed release with the actual training data loader."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    from torchtitan.experiments.rl.examples.tmax.data import _load_samples

    metadata = json.loads(
        (
            args.directory / f"metadata/seed_verifier_repairs_{args.version}.json"
        ).read_text()
    )
    name = f"data/train-ready-seed-verifier-repaired-{args.version}.jsonl"
    path = args.directory / name
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    assert checksum == metadata["sha256"][name]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    samples, revisions, mix_revision = _load_samples(str(path))
    assert len(samples) == len(revisions) == len(rows) == metadata["train_ready"]
    excluded = {record["task_id"] for record in metadata["quarantined"]}
    assert excluded.isdisjoint(sample.instance_id for sample in samples)
    for sample, row in zip(samples, rows):
        md = row["metadata"]
        assert sample.instance_id == md["instance_id"]
        assert sample.tmax == md["tmax"], sample.instance_id
        assert sample.image == (md.get("image") or ""), sample.instance_id
        assert sample.workdir == (md.get("workdir") or "/workspace"), sample.instance_id
        assert sample.problem_statement == (
            md.get("problem_statement") or row["prompt"]
        ), sample.instance_id
        for key in (
            "dockerfile",
            "build_context",
            "entrypoint",
            "agent_timeout_sec",
            "verifier_timeout_sec",
            "daytona_cpu",
            "daytona_mem_gb",
            "daytona_disk_gb",
        ):
            assert getattr(sample, key) == md.get(key), (sample.instance_id, key)
    result = {
        "tasks_loaded": len(samples),
        "quarantined_absent": len(excluded),
        "grader_payloads_exact": True,
        "environment_and_resources_exact": True,
        "parser": "torchtitan.experiments.rl.examples.tmax.data._load_samples",
        "sha256": checksum,
        "mix_revision": mix_revision,
    }
    output = args.directory / "training-loader-verification.json"
    data = json.dumps(result, indent=2) + "\n"
    if output.exists():
        assert output.read_text() == data
    else:
        with output.open("x") as stream:
            stream.write(data)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
