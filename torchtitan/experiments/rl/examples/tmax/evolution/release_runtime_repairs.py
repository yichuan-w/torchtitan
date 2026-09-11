# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Package runtime seed corrections against complete, preserved oracle results."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from release_seed_verifier_repairs import (
    archive_bytes,
    check_row,
    digest,
    json_bytes,
    package_hash,
    read_archives,
    read_package,
    recount_reference,
    require,
    write_outputs,
)


def read_result(path: Path, task_id: str, revision: str) -> dict:
    result = json.loads(path.read_bytes())
    require(result["task_id"] == task_id, "Result task mismatch")
    require(result["run"]["revision"] == revision, "Result harness mismatch")
    payload = (
        json.dumps(result["input"], ensure_ascii=False, sort_keys=True) + "\n"
    ).encode()
    require(digest(payload) == result["input_sha256"], "Result input hash mismatch")
    return result


def require_pass(result: dict) -> None:
    require(result["ok"] and result["reward"] == 1, "Selected result did not pass")
    require(result["solve_exit"] == 0, "Selected reference failed")
    require(result["execution_harness"] == "terminus", "Wrong execution harness")
    terminal = result["terminal"]
    require(
        terminal["submitted"]
        and terminal["finish_reason"] == "submit"
        and terminal["turns"] > 0
        and not result.get("pane_error"),
        "Selected result lacks a complete terminal submission",
    )
    measured = result["measured"]
    require(measured["oom_kill"] == 0, "Selected result contains an OOM")
    require(measured["disk_exhausted"] is False, "Selected result exhausted disk")
    for key, default in result["run"]["resource_defaults"].items():
        expected = result["input"]["row"]["metadata"].get("daytona_" + key) or default
        require(result["resources"][key] == expected, "Measured resource mismatch")


def summary(result: dict, path: Path) -> dict:
    return {
        "result_sha256": digest(path.read_bytes()),
        "input_sha256": result["input_sha256"],
        "harness_revision": result["run"]["revision"],
        "ok": result["ok"],
        "stage": result["stage"],
        "reward": result.get("reward"),
        "solve_exit": result.get("solve_exit"),
        "resources": result.get("resources"),
        "measured": result.get("measured"),
        "submitted": result.get("terminal", {}).get("submitted", False),
        "turns": result.get("terminal", {}).get("turns", 0),
    }


def build(args) -> dict:
    selection = json.loads(args.selection.read_bytes())
    source_version = selection["source_version"]
    source_name = f"data/seed-verifier-repaired-{source_version}.jsonl"
    source_meta_name = f"metadata/seed_verifier_repairs_{source_version}.json"
    source_meta = json.loads((args.baseline / source_meta_name).read_bytes())
    archives = {
        family: f"data/tasks-{family}-verifier-repaired-{source_version}.tar"
        for family in ("terminalworld", "tmax")
    }
    source_hashes = {}
    for name in [source_name, *archives.values()]:
        source_hashes[name] = digest((args.baseline / name).read_bytes())
        require(source_hashes[name] == source_meta["sha256"][name], "Source changed")
    source_hashes[source_meta_name] = digest(
        (args.baseline / source_meta_name).read_bytes()
    )
    rows = [
        json.loads(line)
        for line in (args.baseline / source_name).read_bytes().splitlines()
    ]
    packages, modes, families = read_archives(args.baseline, archives)
    ids = [row["metadata"]["instance_id"] for row in rows]
    require(
        len(ids) == len(set(ids)) == source_meta["total"], "Duplicate or missing rows"
    )
    require(set(ids) == set(packages), "Archive task set differs")
    excluded = {item["task_id"] for item in source_meta["quarantined"]}
    admitted = set(ids) - excluded
    manifest = json.loads(args.manifest.read_bytes())
    require(set(manifest["ids"]) == admitted, "Campaign task set differs from release")
    require(len(admitted) == source_meta["train_ready"], "Admitted count differs")
    replacements = selection["replacements"]
    require(set(replacements) <= admitted, "Replacement is outside admitted set")
    evidence, repairs = [], []
    for index, original in enumerate(rows):
        task_id = ids[index]
        files = packages[task_id]
        check_row(original, files)
        require(
            package_hash(files) == source_meta["package_sha256"][task_id],
            "Source package changed",
        )
        if task_id in excluded:
            continue
        initial_path = args.initial / f"{task_id}.json"
        initial = read_result(initial_path, task_id, selection["harness_revision"])
        require(
            initial["input_sha256"] == manifest["input_sha256"][task_id],
            "Initial input differs from manifest",
        )
        require(initial["input"]["row"] == original, "Initial row differs from source")
        source_solution = {
            name[9:]: data.decode()
            for name, data in files.items()
            if name.startswith("solution/")
        }
        require(
            initial["input"]["solution"] == source_solution,
            "Initial reference differs from source",
        )
        entry = replacements.get(task_id)
        selected_path = Path(entry["result"]) if entry else initial_path
        selected = read_result(selected_path, task_id, selection["harness_revision"])
        require_pass(selected)
        row = copy.deepcopy(selected["input"]["row"])
        patched = (
            read_package(entry["package"]) if entry and entry.get("package") else files
        )
        solution = {
            name[9:]: data.decode()
            for name, data in patched.items()
            if name.startswith("solution/")
        }
        require(
            selected["input"]["solution"] == solution,
            "Selected reference differs from package",
        )
        require(
            row["prompt"] == original["prompt"],
            "Runtime repair changes task instructions",
        )
        check_row(row, patched)
        changed_files = sorted(
            name
            for name in files.keys() | patched.keys()
            if files.get(name) != patched.get(name)
        )
        changed_fields = sorted(
            key
            for key in original["metadata"].keys() | row["metadata"].keys()
            if original["metadata"].get(key) != row["metadata"].get(key)
        )
        if entry:
            require(
                changed_files == sorted(entry["changed_files"]),
                "Undeclared package changes",
            )
            require(
                changed_fields == sorted(entry["changed_metadata"]),
                "Undeclared metadata changes",
            )
            require(entry["reviewed"] is True, "Replacement needs review")
        else:
            require(
                not changed_files and not changed_fields,
                "Initial result changed source",
            )
        other = copy.deepcopy(row)
        other["metadata"] = original["metadata"]
        require(other == original, "Unexpected top-level row change")
        counted = None
        if any(name.startswith("solution/") for name in changed_files):
            counted = recount_reference(patched["solution/solve.sh"].decode())
            row["metadata"]["oracle_commands"] = counted
        published_fields = sorted(
            key
            for key in original["metadata"].keys() | row["metadata"].keys()
            if original["metadata"].get(key) != row["metadata"].get(key)
        )
        if changed_files or changed_fields:
            repairs.append(
                {
                    "task_id": task_id,
                    "reason": entry["reason"],
                    "changed_files": changed_files,
                    "changed_metadata": published_fields,
                    "selected_input_metadata_changes": changed_fields,
                    "reference_command_count": counted,
                    "before_package_sha256": package_hash(files),
                    "after_package_sha256": package_hash(patched),
                    "before_row_sha256": digest(json_bytes(original)),
                    "after_row_sha256": digest(json_bytes(row)),
                }
            )
        rows[index], packages[task_id] = row, patched
        evidence.append(
            {
                "task_id": task_id,
                "initial": summary(initial, initial_path),
                "selected": summary(selected, selected_path),
                "published_row_sha256": digest(json_bytes(row)),
                "reference_count_recomputed": counted,
            }
        )
    ready = [row for row in rows if row["metadata"]["instance_id"] in admitted]
    version = args.version
    prefix = f"runtime-repaired-{version}"

    def jsonl(values):
        return b"".join(
            (
                json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
                + "\n"
            ).encode()
            for value in values
        )

    outputs = {
        f"data/{prefix}.jsonl": jsonl(rows),
        f"data/train-ready-{prefix}.jsonl": jsonl(ready),
        f"metadata/train-ready-{prefix}-ids.txt": "".join(
            row["metadata"]["instance_id"] + "\n" for row in ready
        ).encode(),
        f"metadata/oracle-validation-{version}.jsonl": jsonl(evidence),
    }
    for family in archives:
        outputs[f"data/tasks-{family}-{prefix}.tar"] = archive_bytes(
            packages, modes, [tid for tid in ids if families[tid] == family]
        )
    metadata = {
        "schema_version": 1,
        "version": version,
        "source_hf_repository": selection["source_repository"],
        "source_hf_revision": selection["source_revision"],
        "source_files_sha256": source_hashes,
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "campaign_manifest_sha256": digest(args.manifest.read_bytes()),
        "harness_revision": selection["harness_revision"],
        "total": len(rows),
        "train_ready": len(ready),
        "quarantined": source_meta["quarantined"],
        "quarantined_count": len(excluded),
        "repaired": len(repairs),
        "unchanged": len(rows) - len(repairs),
        "initial_passed": sum(item["initial"]["ok"] for item in evidence),
        "selected_passed": len(evidence),
        "semantic_verifier_certification": False,
        "validation_scope": "One selected passing Terminus oracle result per admitted task, with preserved initial results and targeted corrections. No general verifier non-hackability claim.",
        "repairs": repairs,
        "package_sha256": {
            tid: package_hash(files) for tid, files in sorted(packages.items())
        },
        "package_file_sha256": {
            tid: {name: digest(data) for name, data in sorted(files.items())}
            for tid, files in sorted(packages.items())
        },
        "sha256": {name: digest(data) for name, data in sorted(outputs.items())},
    }
    outputs[f"metadata/runtime_repairs_{version}.json"] = json_bytes(metadata)
    write_outputs(args.output, outputs)
    return {
        key: metadata[key]
        for key in (
            "total",
            "train_ready",
            "repaired",
            "initial_passed",
            "selected_passed",
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "initial", "manifest", "selection", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--version", required=True)
    print(json.dumps(build(parser.parse_args())))


if __name__ == "__main__":
    main()
