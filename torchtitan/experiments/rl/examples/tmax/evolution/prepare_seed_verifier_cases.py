#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Freeze reviewed grader repairs and controls against published training rows."""

import argparse
import copy
import hashlib
import json
from pathlib import Path


def files(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task", action="append")
    parser.add_argument("--probe", action="append")
    parser.add_argument("--version", action="append", choices=["original", "patched"])
    parser.add_argument("--label", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    rows = {
        row["metadata"]["instance_id"]: row
        for row in map(json.loads, args.baseline.read_text().splitlines())
    }
    manifest = json.loads(args.manifest.read_text())
    cases = []
    for task in manifest["tasks"]:
        tid = task["task_id"]
        if args.task and tid not in args.task:
            continue
        if task["status"] != "draft_grader_repair":
            continue
        original = files(Path(task["original_package"]))
        patched = files(Path(task["patched_package"]))
        changed = {
            rel
            for rel in set(original) | set(patched)
            if original.get(rel) != patched.get(rel)
        }
        reference_changes = set(task.get("allowed_reference_changes", []))
        if any(not rel.startswith("solution/") for rel in reference_changes):
            raise ValueError("reference changes must be solution files")
        if changed != set(task["changed_files"]) or any(
            not rel.startswith("tests/") and rel not in reference_changes
            for rel in changed
        ):
            raise ValueError(f"undeclared or non-grader changes: {tid}")
        baseline = rows[tid]
        old_tmax = baseline["metadata"]["tmax"]
        if original["tests/test.sh"].decode() != old_tmax["test_sh"]:
            raise ValueError(f"original test.sh differs from published row: {tid}")
        for rel, text in old_tmax.get("fixtures", {}).items():
            if rel.startswith("tests/") and original.get(rel) != text.encode():
                raise ValueError(f"original fixture differs: {tid}/{rel}")
        for version in args.version or ("original", "patched"):
            row = copy.deepcopy(baseline)
            if version == "patched":
                tmax = row["metadata"]["tmax"]
                for rel in changed:
                    if rel in reference_changes:
                        continue
                    if rel == "tests/test.sh":
                        tmax["test_sh"] = patched[rel].decode()
                    elif rel in patched:
                        tmax.setdefault("fixtures", {})[rel] = patched[rel].decode()
                    else:
                        del tmax["fixtures"][rel]
            probes = list(task["probes"])
            if not any(probe["name"] == "reference" for probe in probes):
                package = Path(task[f"{version}_package"])
                script = package / "solution/solve.sh"
                probes.append(
                    {
                        "name": "reference",
                        "kind": "positive",
                        "script": str(script),
                        "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                        "expected_original_reward": 1,
                        "expected_patched_reward": 1,
                    }
                )
            for probe in probes:
                if args.probe and probe["name"] not in args.probe:
                    continue
                expected = probe[f"expected_{version}_reward"]
                if expected is None:
                    continue
                script = Path(probe["script"]).read_bytes()
                if hashlib.sha256(script).hexdigest() != probe["sha256"]:
                    raise ValueError("probe changed after review")
                solution = {
                    rel.removeprefix("solution/"): content.decode()
                    for rel, content in original.items()
                    if rel.startswith("solution/")
                }
                solution["solve.sh"] = script.decode()
                cases.append(
                    {
                        "case_id": f"{tid}--{version}--{probe['name']}",
                        "task_id": tid,
                        "version": version,
                        "probe": probe["name"],
                        "kind": probe["kind"],
                        "expected_reward": expected,
                        "row": row,
                        "solution": solution,
                        "solve_timeout": max(
                            900, int(row["metadata"].get("agent_timeout_sec") or 0)
                        ),
                        "requirement": task["requirement_excerpt"],
                        "bug": task["bug"],
                        "source_revision": manifest.get("published_revision"),
                    }
                )
    if not cases:
        raise ValueError("no reviewed cases selected")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(
            {"label": args.label, "concurrency": args.concurrency, "cases": cases},
            stream,
        )
        stream.write("\n")
    print(json.dumps({"cases": len(cases), "output": str(args.output)}))


if __name__ == "__main__":
    main()
