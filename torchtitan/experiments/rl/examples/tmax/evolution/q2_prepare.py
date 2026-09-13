#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Freeze the Q2 cohort and published seed packages before prompt iteration."""

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

CHANGES = {
    "tw_326975": (
        "database",
        "harder",
        "Preserve the complete database export. Also write /app/table_counts.json mapping every exported "
        "table name to its integer row count in the original database. This is a final-artifact task.",
    ),
    "tw_220270": (
        "database",
        "harder",
        "Preserve SQLAlchemy table creation and table listing. Populate EX1 with exactly the rows (1, "
        "Alice), (2, Bob), and (3, Carol). Leave any pre-existing unrelated tables and their data "
        "intact.",
    ),
    "tw_621364": (
        "database",
        "easier",
        "Remove the requirement for user-level views and their INSTEAD OF triggers. Preserve the base "
        "tables, balance-maintenance triggers, sample transactions, phone update, index, and "
        "reproducible schema script. Declare the removed checks in run/simplify.json.",
    ),
    "tw_6185": (
        "structured_data",
        "harder",
        "Save a reusable Python program /app/read_header.py that reads the current /app/1.csv, preserves "
        "the original delimiter and quote-removal semantics, and writes /app/result.txt. Add optional "
        "--drop-empty to discard empty column names after removing single quotes. Accept headers with "
        "empty fields and single quotes; preserve order and duplicate nonempty names. With no arguments "
        "preserve the original output, and leave that default output at completion.",
    ),
    "tw_276310": (
        "structured_data",
        "harder",
        "Save a reusable Python program /app/analyze.py. Add optional --min-pt-x NUMBER, a finite "
        "threshold that keeps entries whose pt_x is greater than or equal to it before computing means. "
        "Keep the two original output keys and branch order. For no selected entries use null for "
        "mean_pt_x and a list of nulls, one per branch, for mean_all_columns. Read the current ROOT file "
        "on every invocation. No arguments must preserve the original result; leave this default output "
        "at completion.",
    ),
    "tw_533496": (
        "structured_data",
        "easier",
        "Remove the pkg_info parsing and /app/pkg_info.json deliverable. Preserve complete fstab parsing "
        "and its six required keys. Declare removed checks in run/simplify.json.",
    ),
    "tw_277215": (
        "logs",
        "harder",
        "Preserve the complete sorted recursive file listing. Also write /app/log_sizes.json mapping "
        "each listed absolute path to the integer byte length of that original file. This remains a "
        "final-artifact task.",
    ),
    "task_001128_45291ecf": (
        "logs",
        "easier",
        "Remove the compressed archive deliverable. Preserve selection by original modification time, "
        "TRACE removal only in selected files, ERROR extraction, and unchanged older files. Declare "
        "removed checks in run/simplify.json.",
    ),
    "task_003557_fd54e309": (
        "logs",
        "easier",
        "Remove the temporary-file and atomic-rename requirement for writing critical_summary.txt. "
        "Preserve the C++ program, complete multiline CRITICAL records, and moving every original log to "
        "its archived name without changing its content. Declare removed checks in run/simplify.json.",
    ),
    "tw_105601": (
        "files",
        "harder",
        "Preserve complete archive extraction. Also write /app/extracted_manifest.json mapping each "
        "regular file member path, relative to the archive root, to its lowercase SHA256 content digest. "
        "The manifest covers original archive members only. This remains a final-artifact task.",
    ),
    "tw_104869": (
        "files",
        "easier",
        "Require mono conversion only for the lexicographically first full path matching audio_*.wav "
        "under /tmp/1 after extraction. Leave the other audio files unchanged. Preserve complete "
        "extraction, the sorted directory listing, and the count of all matching audio files. Declare "
        "removed or relaxed checks in run/simplify.json.",
    ),
    "tw_576477": (
        "files",
        "easier",
        "Keep only the JSON-format diff between file1.json and file2.json at /app/result_json.txt, using "
        "gendiff as required. Remove the stylish and plain output requirements and declare their removed "
        "checks in run/simplify.json.",
    ),
}


def freeze(path, data):
    if isinstance(data, str):
        data = data.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_bytes() == data, path
    else:
        with path.open("xb") as stream:
            stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.release / "train-ready-runtime-repaired-20260911-v3.jsonl"
    raw = source.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "decd306ef30c8c3bc440f47970246d05e9d19411c57d734f989c32cd249e5ecd"
    )
    rows = {r["metadata"]["instance_id"]: r for r in map(json.loads, raw.splitlines())}
    traffic_id = "task_000523_f1d234c6"
    old = json.loads((args.previous / "input.json").read_text())
    changes = {
        traffic_id: (
            "regression",
            "harder",
            old["tasks"][traffic_id + "--r1"]["change"],
        ),
        **CHANGES,
    }
    assert len(CHANGES) == 12 and sum(v[1] == "harder" for v in CHANGES.values()) == 6
    for archive in args.release.glob("tasks-*-runtime-repaired-20260911-v3.tar"):
        with tarfile.open(archive) as tar:
            for member in tar:
                parts = Path(member.name).parts
                if len(parts) < 3 or parts[1] not in changes or not member.isfile():
                    continue
                relative = Path(*parts[2:])
                assert not relative.is_absolute() and ".." not in relative.parts
                freeze(
                    args.output / "seeds" / parts[1] / relative,
                    tar.extractfile(member).read(),
                )
    tasks = {}
    for seed_id, (category, job, change) in changes.items():
        package = args.output / "seeds" / seed_id
        assert (package / "instruction.md").exists()
        assert (package / "solution/solve.sh").exists()
        hashes = {
            str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.rglob("*"))
            if p.is_file()
        }
        for repetition in (1, 2):
            tasks[f"{seed_id}--r{repetition}"] = dict(
                seed_id=seed_id,
                category=category,
                job=job,
                change=change,
                repetition=repetition,
                row=rows[seed_id],
                seed_sha256=hashes,
            )
    config = dict(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        session_timeout_sec=1200,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        tasks=tasks,
        selection="Purposive coverage by public task, 3 seeds in each of 4 categories; no Q2 result-based selection.",
        control_policy=[
            "reference",
            "valid_alternative",
            "retained_requirement_violation",
            "changed_requirement_violation",
            "applicable_boundary",
            "applicable_input_tampering",
        ],
        acceptance=(
            "Every predefined applicable control must meet its semantic expectation. Generation failures and "
            "incomplete measurements remain in the denominator. No replacement seeds."
        ),
        comparison="Revised configuration only; no causal improvement estimate.",
        attempt_policy=(
            "One independent generation per repetition; native bounded repairs retained. A changed shared "
            "prompt requires a new campaign version. Infrastructure replays retain their original attempt."
        ),
        prepared_at=datetime.now(timezone.utc).isoformat(),
    )
    target = args.output / "input.json"
    if target.exists():
        config["prepared_at"] = json.loads(target.read_text())["prepared_at"]
    freeze(target, json.dumps(config, indent=2) + "\n")
    for name in ("source-tamper-controls.json", "input.json"):
        freeze(
            args.output / "regression-evidence" / name,
            (args.previous / name).read_bytes(),
        )
    print(
        json.dumps(dict(tasks=len(tasks), fresh_seeds=len(CHANGES), output=str(target)))
    )


if __name__ == "__main__":
    main()
