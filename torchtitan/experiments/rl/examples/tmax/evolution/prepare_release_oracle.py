# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Download a pinned seed release and freeze its admitted oracle inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import environment_sweep as sweep
from huggingface_hub import hf_hub_download
from release_seed_verifier_repairs import check_row, relative_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / "published"

    def download(name):
        return Path(
            hf_hub_download(
                args.repository,
                name,
                repo_type="dataset",
                revision=args.revision,
                local_dir=source,
                token=False,
            )
        )

    metadata_name = f"metadata/seed_verifier_repairs_{args.version}.json"
    metadata = json.loads(download(metadata_name).read_text())
    mix_name = f"data/train-ready-seed-verifier-repaired-{args.version}.jsonl"
    archives = [
        f"data/tasks-{family}-verifier-repaired-{args.version}.tar"
        for family in ("terminalworld", "tmax")
    ]
    for name in [mix_name, *archives]:
        path = download(name)
        if sweep.digest(path.read_bytes()) != metadata["sha256"][name]:
            raise ValueError(f"Release checksum mismatch: {name}")
    rows = [json.loads(line) for line in (source / mix_name).read_text().splitlines()]
    by_id = {row["metadata"]["instance_id"]: row for row in rows}
    if len(rows) != len(by_id) or len(rows) != metadata["train_ready"]:
        raise ValueError("Release task count mismatch")
    excluded = {row["task_id"] for row in metadata["quarantined"]}
    if excluded.intersection(by_id):
        raise ValueError("Excluded task present in admitted release")

    packages = args.output / "published-packages"
    packages.mkdir(exist_ok=True)
    found = set()
    for name in archives:
        with tarfile.open(source / name) as archive:
            for member in archive:
                parts = PurePosixPath(relative_file(member.name.rstrip("/"))).parts
                if len(parts) < 3 or parts[0] != "tasks" or parts[1] not in by_id:
                    continue
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError(f"Non-regular package member: {member.name}")
                content = archive.extractfile(member).read()
                dest = packages.joinpath(*parts[1:])
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    if dest.read_bytes() != content:
                        raise ValueError(f"Changed package member: {member.name}")
                else:
                    with dest.open("xb") as stream:
                        stream.write(content)
                    dest.chmod(member.mode & 0o777)
                found.add(parts[1])
    if found != set(by_id):
        raise ValueError(f"Missing packages: {sorted(set(by_id) - found)}")
    for tid, row in by_id.items():
        files = {
            path.relative_to(packages / tid).as_posix(): path.read_bytes()
            for path in (packages / tid).rglob("*")
            if path.is_file()
        }
        check_row(row, files)
        if "solution/solve.sh" not in files:
            raise ValueError(f"Missing reference solution: {tid}")

    frozen = args.output / "frozen"
    if frozen.exists():
        raise ValueError(
            "Frozen inventory already exists; resume environment_sweep run"
        )
    sweep.freeze(
        SimpleNamespace(
            output=frozen,
            mix=source / mix_name,
            packages=[packages],
        )
    )
    repo = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
    )
    shutil.copyfile(repo / "uv.lock", args.output / "uv.lock")
    sweep.write_json(
        args.output / "release.json",
        {
            "repository": args.repository,
            "revision": args.revision,
            "version": args.version,
            "tasks": len(rows),
            "excluded": len(excluded),
            "sha256": {
                name: sweep.digest((source / name).read_bytes())
                for name in [metadata_name, mix_name, *archives]
            },
            "uv_lock_sha256": hashlib.sha256(
                (repo / "uv.lock").read_bytes()
            ).hexdigest(),
            "harness_revision": sweep.revision(),
        },
    )


if __name__ == "__main__":
    main()
