# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a versioned seed release from explicitly reviewed repair evidence.

This program packages data; it never runs a solution, grader, or sandbox. Each
accepted JSON entry supplies task_id, an absolute manifest path, reviewed=true,
reviewed_package_sha256 frozen against the adjudicated package,
and evidence entries containing case_id, input_sha256, result_sha256 and
actual_reward. Optional evidence fields are expected_reward, kind, variant and
control_kind.
Reference edits additionally require allowed_reference_changes (solution paths).
The optional top-level quarantined list contains task_id and a public reason.
These tasks remain in the complete release but are excluded from train-ready
rows and IDs; a repaired task may also be quarantined.

Run from the repository environment on the validation host when references
change: their command counts use prepare_rts_data's existing parser. Outputs
are immutable: repeating identical inputs succeeds; conflicting files fail.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

BASELINE_JSONL = "data/oracle-validated-20260909.jsonl"
BASELINE_ARCHIVES = {
    "terminalworld": "data/tasks-terminalworld-terminus-20260909.tar",
    "tmax": "data/tasks-tmax-terminus-20260909.tar",
}
BASELINE_METADATA = "metadata/terminus_release_20260909.json"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode()


def relative_file(value: str) -> str:
    require(isinstance(value, str) and bool(value), "Empty package path")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and ".." not in path.parts
        and str(path) == value
        and "\\" not in value,
        f"Unsafe package path: {value!r}",
    )
    return value


def package_hash(files: dict[str, bytes]) -> str:
    # Same framing as prepare_tmax_reaudit_data._package_sha256.
    result = hashlib.sha256()
    for name, content in sorted(files.items()):
        result.update(name.encode() + b"\0" + content + b"\0")
    return result.hexdigest()


def read_package(directory: str) -> dict[str, bytes]:
    root = Path(directory)
    require(
        root.is_absolute() and root.is_dir() and not root.is_symlink(),
        "Manifest package must be an absolute directory",
    )
    files = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Package symlinks are unsupported")
        if path.is_dir():
            continue
        require(path.is_file(), "Unsupported package entry")
        files[relative_file(path.relative_to(root).as_posix())] = path.read_bytes()
    return files


def read_manifest_package(task: dict, field: str, row: dict) -> dict[str, bytes]:
    files = read_package(task[field])
    if "published-row.json" in files:
        # Initial TMax audit drafts stored an explicitly named baseline-row
        # sidecar beside package files. It is evidence, not an archive member.
        expected = Path(task["original_package"]) / "published-row.json"
        require(
            task.get("published_row") == str(expected)
            and json.loads(files["published-row.json"]) == row,
            "Unexpected or changed published-row.json sidecar",
        )
        del files["published-row.json"]
    return files


def read_archives(
    baseline: Path, archives: dict | None = None
) -> tuple[dict, dict, dict]:
    packages, modes, families = {}, {}, {}
    for family, filename in (archives or BASELINE_ARCHIVES).items():
        with tarfile.open(baseline / filename, "r:") as archive:
            for member in archive:
                name = relative_file(
                    member.name.rstrip("/") if member.isdir() else member.name
                )
                if member.isdir():
                    continue
                require(member.isfile(), "Baseline archives must contain regular files")
                parts = PurePosixPath(name).parts
                require(
                    len(parts) >= 3 and parts[0] == "tasks",
                    "Unknown baseline archive prefix",
                )
                task_id, relative = parts[1], "/".join(parts[2:])
                require(bool(IDENTIFIER.fullmatch(task_id)), "Invalid task identifier")
                require(
                    task_id not in families or families[task_id] == family,
                    f"Task in both archives: {task_id}",
                )
                families[task_id] = family
                files = packages.setdefault(task_id, {})
                require(relative not in files, f"Duplicate archive member: {name}")
                stream = archive.extractfile(member)
                require(stream is not None, "Unreadable archive member")
                files[relative] = stream.read()
                modes[(task_id, relative)] = member.mode
    return packages, modes, families


def check_row(row: dict, files: dict[str, bytes]) -> None:
    task_id = row["metadata"]["instance_id"]
    metadata = row["metadata"]
    instruction = re.sub(
        r"^.*harbor-canary.*$\n?",
        "",
        files["instruction.md"].decode(),
        flags=re.MULTILINE,
    )
    require(instruction == row["prompt"], f"Prompt/archive mismatch: {task_id}")
    require(
        files["environment/Dockerfile"].decode() == metadata["dockerfile"],
        f"Dockerfile/archive mismatch: {task_id}",
    )
    environment = {"environment/Dockerfile": metadata["dockerfile"].encode()}
    for name, encoded in metadata.get("build_context", {}).items():
        name = relative_file(name)
        require(name != "Dockerfile", "Dockerfile duplicated in build context")
        environment["environment/" + name] = base64.b64decode(encoded, validate=True)
    require(
        environment == {k: v for k, v in files.items() if k.startswith("environment/")},
        f"Build context/archive mismatch: {task_id}",
    )
    tests = {"tests/test.sh": metadata["tmax"]["test_sh"].encode()}
    for name, content in metadata["tmax"].get("fixtures", {}).items():
        relative_file(name)
        require(
            name.startswith("tests/") and name != "tests/test.sh",
            f"Unexpected fixture path: {task_id}",
        )
        tests[name] = content.encode()
    require(
        tests == {k: v for k, v in files.items() if k.startswith("tests/")},
        f"Grading fixtures/archive mismatch: {task_id}",
    )


def public_evidence(entries: list) -> list[dict]:
    require(
        isinstance(entries, list) and bool(entries), "Accepted repair needs evidence"
    )
    result, seen = [], set()
    required = {"case_id", "input_sha256", "result_sha256", "actual_reward"}
    optional = {"expected_reward", "kind", "variant", "control_kind", "classification"}
    for entry in entries:
        require(
            isinstance(entry, dict) and required <= entry.keys(),
            "Evidence needs case_id, input_sha256, result_sha256, actual_reward",
        )
        # Only structured public fields cross the release boundary. Host traces
        # and paths in private acceptance records are deliberately not copied.
        record = {key: entry[key] for key in required | optional if key in entry}
        relative_file(record["case_id"])
        require(
            all(
                bool(IDENTIFIER.fullmatch(part))
                for part in record["case_id"].split("/")
            ),
            "Evidence case_id must be a public relative identifier",
        )
        for key in ("kind", "variant", "control_kind", "classification"):
            if key in record:
                require(
                    isinstance(record[key], str)
                    and bool(IDENTIFIER.fullmatch(record[key])),
                    f"Evidence {key} must be a public identifier",
                )
        for key in ("input_sha256", "result_sha256"):
            require(
                isinstance(record[key], str) and bool(SHA256.fullmatch(record[key])),
                f"Evidence {key} must be a SHA-256 digest",
            )
        for key in ("actual_reward", "expected_reward"):
            if key in record:
                value = record[key]
                require(
                    type(value) in (int, float)
                    and math.isfinite(value)
                    and 0 <= value <= 1,
                    f"Invalid evidence {key}",
                )
        require(record["case_id"] not in seen, "Duplicate evidence case_id")
        seen.add(record["case_id"])
        result.append(record)
    return sorted(result, key=lambda record: record["case_id"])


def quarantine_records(entries: list, task_ids: set[str]) -> list[dict]:
    require(isinstance(entries, list), "quarantined must be a list")
    records, seen = [], set()
    for entry in entries:
        task_id, reason = entry["task_id"], entry["reason"]
        require(
            task_id in task_ids and task_id not in seen,
            "Quarantine task must occur once and belong to the release",
        )
        require(
            isinstance(reason, str) and bool(reason.strip()) and len(reason) <= 2000,
            "Quarantine requires a concise public reason",
        )
        require(
            not re.search(
                r"/Users/|/scratch/|/home/(?!user(?:/|\b))|https?://|"
                r"\b(?:token|password|secret|api_key)\s*[:=]",
                reason,
                re.I,
            ),
            "Quarantine reason contains private provenance or credential syntax",
        )
        seen.add(task_id)
        records.append({"task_id": task_id, "reason": reason})
    return sorted(records, key=lambda record: record["task_id"])


def recount_reference(source: str) -> int:
    # Lazy import keeps the packaging path independent of torch. Do not replace
    # this parser with a release-specific approximation of reference difficulty.
    from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
        _oracle_commands,
    )

    return _oracle_commands(source)


def apply_repair(
    entry: dict, row: dict, original: dict[str, bytes]
) -> tuple[dict, dict, dict]:
    task_id = entry["task_id"]
    require(entry.get("reviewed") is True, f"Repair not reviewed: {task_id}")
    manifest_path = Path(entry["manifest"])
    require(manifest_path.is_absolute(), "Accepted manifest path must be absolute")
    manifest_bytes = manifest_path.read_bytes()
    candidates = [
        task
        for task in json.loads(manifest_bytes)["tasks"]
        if task["task_id"] == task_id
    ]
    require(len(candidates) == 1, f"Manifest task must occur once: {task_id}")
    manifest = json.loads(manifest_bytes)
    task = candidates[0]
    require(
        read_manifest_package(task, "original_package", row) == original,
        f"Manifest original differs from frozen release: {task_id}",
    )
    patched = read_manifest_package(task, "patched_package", row)
    reviewed_hash = entry.get("reviewed_package_sha256")
    require(
        isinstance(reviewed_hash, str)
        and bool(SHA256.fullmatch(reviewed_hash))
        and package_hash(patched) == reviewed_hash,
        f"Current patched package differs from reviewed_package_sha256: {task_id}",
    )
    changed = {
        name
        for name in original.keys() | patched.keys()
        if original.get(name) != patched.get(name)
    }
    declared_list = task["changed_files"]
    declared = {relative_file(name) for name in declared_list}
    require(
        changed and changed == declared and len(declared) == len(declared_list),
        f"Declared changed_files differs from actual diff: {task_id}",
    )
    references = entry.get("allowed_reference_changes", [])
    require(isinstance(references, list), "allowed_reference_changes must be a list")
    allowed_references = {relative_file(name) for name in references}
    require(
        all(name.startswith("solution/") for name in allowed_references)
        and allowed_references <= changed,
        f"Reference authorization must name changed solution files: {task_id}",
    )
    require(
        all(
            name.startswith("tests/") or name in allowed_references for name in changed
        ),
        f"Repair changes instructions, environment, or undeclared reference: {task_id}",
    )
    require(
        "tests/test.sh" in patched and "solution/solve.sh" in patched,
        f"Repair removes required entry point: {task_id}",
    )
    repaired = copy.deepcopy(row)
    grading = repaired["metadata"]["tmax"]
    grading["test_sh"] = patched["tests/test.sh"].decode()
    fixtures = {
        name: content.decode()
        for name, content in patched.items()
        if name.startswith("tests/") and name != "tests/test.sh"
    }
    if fixtures or "fixtures" in grading:
        grading["fixtures"] = fixtures
    if allowed_references:
        repaired["metadata"]["oracle_commands"] = recount_reference(
            patched["solution/solve.sh"].decode()
        )
    check_row(repaired, patched)
    # All other fields, including pre_test and protected identity settings,
    # survive the deep copy without importing metadata from an old repair row.
    record = {
        "task_id": task_id,
        "reviewed": True,
        "reviewed_package_sha256": reviewed_hash,
        "manifest_sha256": digest(manifest_bytes),
        "before_package_sha256": package_hash(original),
        "after_package_sha256": package_hash(patched),
        "before_row_sha256": digest(json_bytes(row)),
        "after_row_sha256": digest(json_bytes(repaired)),
        "allowed_reference_changes": sorted(allowed_references),
        "changed_files": [
            {
                "path": name,
                "before_sha256": digest(original[name]) if name in original else None,
                "after_sha256": digest(patched[name]) if name in patched else None,
            }
            for name in sorted(changed)
        ],
        "evidence": public_evidence(entry["evidence"]),
    }
    for key in ("source_manifest_sha256", "review_sha256"):
        if key in manifest:
            require(
                isinstance(manifest[key], str)
                and bool(SHA256.fullmatch(manifest[key])),
                f"Invalid {key}",
            )
            record[key] = manifest[key]
    return repaired, patched, record


def archive_bytes(packages: dict, modes: dict, task_ids: list[str]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for task_id in sorted(task_ids):
            for name, content in sorted(packages[task_id].items()):
                member = tarfile.TarInfo(f"tasks/{task_id}/{name}")
                member.size = len(content)
                member.mode = modes.get(
                    (task_id, name), 0o755 if name.endswith(".sh") else 0o644
                )
                archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def write_outputs(directory: Path, outputs: dict[str, bytes]) -> None:
    # Preflight all existing outputs before writing any new release file.
    for name, content in outputs.items():
        path = directory / name
        require(
            path.resolve().is_relative_to(directory.resolve()),
            "Output directory contains an escaping symlink",
        )
        require(not path.is_symlink(), "Output symlinks are unsupported")
        require(
            not path.exists() or path.read_bytes() == content,
            f"Existing release file differs: {name}",
        )
    for name, content in outputs.items():
        path = directory / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


def build_release(
    baseline: Path,
    accepted_path: Path,
    output: Path,
    version: str,
    source_repo: str,
    source_revision: str,
) -> dict:
    baseline, output = baseline.resolve(), output.resolve()
    require(
        not output.is_relative_to(baseline),
        "Output must be outside the frozen baseline",
    )
    for ancestor in Path(__file__).resolve().parents:
        if (ancestor / ".git").exists():
            require(
                not output.is_relative_to(ancestor),
                "Bulk outputs must be outside the code repository",
            )
            break
    require(bool(IDENTIFIER.fullmatch(version)), "Invalid version identifier")
    require(
        bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source_repo)),
        "Source repository must be a public owner/name identifier",
    )
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", source_revision)),
        "Source revision must be a full commit SHA",
    )
    source_paths = [BASELINE_JSONL, *BASELINE_ARCHIVES.values(), BASELINE_METADATA]
    source_hashes = {
        name: digest((baseline / name).read_bytes()) for name in source_paths
    }
    source_metadata = json.loads((baseline / BASELINE_METADATA).read_bytes())
    for name in source_paths[:-1]:
        require(
            source_metadata["sha256"].get(name) == source_hashes[name],
            f"Frozen release file hash mismatch: {name}",
        )
    rows = [
        json.loads(line)
        for line in (baseline / BASELINE_JSONL).read_bytes().splitlines()
    ]
    task_ids = [row["metadata"]["instance_id"] for row in rows]
    require(len(task_ids) == len(set(task_ids)), "Duplicate prepared row task ID")
    packages, modes, families = read_archives(baseline)
    require(
        set(task_ids) == set(packages),
        "Prepared rows and archives have different task sets",
    )
    require(len(rows) == source_metadata["total"], "Frozen release count mismatch")
    for row in rows:
        task_id = row["metadata"]["instance_id"]
        check_row(row, packages[task_id])
        require(
            package_hash(packages[task_id])
            == source_metadata["package_sha256"][task_id],
            f"Frozen task hash mismatch: {task_id}",
        )
    acceptance = json.loads(accepted_path.read_bytes())
    accepted = acceptance["tasks"]
    quarantined = quarantine_records(acceptance.get("quarantined", []), set(task_ids))
    quarantined_ids = {record["task_id"] for record in quarantined}
    accepted_by_id = {entry["task_id"]: entry for entry in accepted}
    require(
        (bool(accepted) or bool(quarantined)) and len(accepted_by_id) == len(accepted),
        "Release needs repairs or quarantine, without duplicate accepted tasks",
    )
    require(
        accepted_by_id.keys() <= packages.keys(),
        "Accepted task absent from frozen release",
    )
    records = []
    for index, row in enumerate(rows):
        task_id = row["metadata"]["instance_id"]
        if task_id in accepted_by_id:
            rows[index], packages[task_id], record = apply_repair(
                accepted_by_id[task_id], row, packages[task_id]
            )
            records.append(record)
    outputs = {
        f"data/seed-verifier-repaired-{version}.jsonl": b"".join(
            (
                json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
                + "\n"
            ).encode()
            for row in rows
        ),
    }
    ready_rows = [
        row for row in rows if row["metadata"]["instance_id"] not in quarantined_ids
    ]
    outputs[f"data/train-ready-seed-verifier-repaired-{version}.jsonl"] = b"".join(
        (
            json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        ).encode()
        for row in ready_rows
    )
    outputs[f"metadata/train-ready-seed-verifier-repaired-{version}-ids.txt"] = "".join(
        row["metadata"]["instance_id"] + "\n" for row in ready_rows
    ).encode()
    counts = {
        family: sum(value == family for value in families.values())
        for family in BASELINE_ARCHIVES
    }
    for family in BASELINE_ARCHIVES:
        outputs[f"data/tasks-{family}-verifier-repaired-{version}.tar"] = archive_bytes(
            packages,
            modes,
            [task_id for task_id in task_ids if families[task_id] == family],
        )
    metadata = {
        "schema_version": 1,
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "version": version,
        "source_hf_repository": source_repo,
        "source_hf_revision": source_revision,
        "source_files_sha256": source_hashes,
        "total": len(rows),
        "train_ready": len(ready_rows),
        "quarantined_count": len(quarantined),
        "quarantined": quarantined,
        **counts,
        "repaired": len(records),
        "unchanged": len(rows) - len(records),
        "semantic_verifier_certification": False,
        "validation_scope": "Reviewer-accepted cases listed per repair; no general non-hackability claim.",
        "repairs": sorted(records, key=lambda record: record["task_id"]),
        "package_sha256": {
            task_id: package_hash(files) for task_id, files in sorted(packages.items())
        },
        "package_file_sha256": {
            task_id: {name: digest(data) for name, data in sorted(files.items())}
            for task_id, files in sorted(packages.items())
        },
        "sha256": {name: digest(data) for name, data in sorted(outputs.items())},
    }
    outputs[f"metadata/seed_verifier_repairs_{version}.json"] = json_bytes(metadata)
    write_outputs(output, outputs)
    return {
        "total": len(rows),
        "train_ready": len(ready_rows),
        "quarantined": len(quarantined),
        "repaired": len(records),
        "sha256": {name: digest(data) for name, data in sorted(outputs.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--accepted", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    result = build_release(
        args.baseline_dir,
        args.accepted,
        args.out_dir,
        args.version,
        args.source_repo,
        args.source_revision,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
