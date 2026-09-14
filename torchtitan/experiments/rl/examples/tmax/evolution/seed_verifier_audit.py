#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Replay frozen seed verifier controls through Daytona and Terminus."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import importlib.metadata
import json
import logging
import math
import os
import time
from pathlib import Path

from environment_sweep import digest, revision, write_json


def accepted(
    result: dict,
    expected_reward: float,
    *,
    completion_marker: str | None = None,
    required_observation: str | None = None,
) -> bool:
    """Compatibility name for a preliminary execution match, never semantic acceptance.

    Every result still requires independent review of the actual verifier
    behavior. A numeric zero alone cannot establish the intended rejection.
    """
    reward = result.get("reward")
    grading_exit = (result.get("grading") or {}).get("exit_code")
    output = result.get("solve_stdout") or ""
    return (
        result.get("stage") == "daytona_oracle"
        and result.get("solve_exit") == 0
        and result.get("execution_harness") == "terminus"
        and result.get("terminal", {}).get("submitted") is True
        and isinstance(reward, (int, float))
        and math.isfinite(reward)
        and reward == expected_reward
        and type(grading_exit) is int
        and grading_exit not in (124, 127, 137)
        and (completion_marker is None or completion_marker in output.splitlines())
        and (required_observation is None or required_observation in output)
    )


def save_identical(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != content:
            raise ValueError(f"frozen file changed: {path}")
    else:
        with path.open("x") as stream:
            stream.write(content)


async def run(args) -> int:
    config = json.loads(args.config.read_text())
    os.environ["TT_DAYTONA_LABEL"] = config["label"]
    import daytona_revalidate as dr

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    logging.basicConfig(
        filename=output / "events.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    log = logging.getLogger("seed_verifier_audit")
    stamp = {
        "revision": revision(),
        "config": config,
        "dependencies": dict(
            sorted(
                (d.metadata["Name"], d.version)
                for d in importlib.metadata.distributions()
                if d.metadata["Name"]
            )
        ),
    }
    save_identical(output / "run.json", json.dumps(stamp, sort_keys=True) + "\n")
    ids = [case["case_id"] for case in config["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    for case in config["cases"]:
        if Path(case["case_id"]).name != case["case_id"]:
            raise ValueError("case ID must be a file name")
        if case["expected_reward"] not in (0, 1):
            raise ValueError("controls require binary expected rewards")
        if case["solve_timeout"] <= 0:
            raise ValueError("control timeout must be positive")
        for key in ("completion_marker", "required_observation"):
            if key in case and (
                not isinstance(case[key], str) or not case[key].strip()
            ):
                raise ValueError(f"{key} must be a nonempty string")
        md = case["row"]["metadata"]
        for key in ("daytona_cpu", "daytona_mem_gb", "daytona_disk_gb"):
            if not isinstance(md.get(key), (int, float)) or md[key] <= 0:
                raise ValueError(f"case requires explicit resource metadata: {key}")
    sem = asyncio.Semaphore(config["concurrency"])

    async def one(case):
        name = case["case_id"]
        item = output / "cases" / name
        item.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(case, sort_keys=True) + "\n"
        checksum = digest(serialized.encode())
        save_identical(item / "input.json", serialized)
        result_path = item / "result.json"
        if result_path.exists():
            saved = json.loads(result_path.read_text())
            if saved["input_sha256"] != checksum or saved["run"] != stamp:
                raise ValueError(f"checkpoint changed: {name}")
            log.info("item=%s status=resume_skip run_match=%s", name, saved["accepted"])
            return saved
        # The reference and the controlled mutation are frozen together. The
        # prepared row remains authoritative for environment and grading files.
        package = item / "package"
        for rel, text in case["solution"].items():
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise ValueError("solution path escapes package")
            save_identical(package / "solution" / rel, text)
        async with sem:
            started = time.time()
            log.info(
                "item=%s status=start expected=%s input=%s sha256=%s revision=%s",
                name,
                case["expected_reward"],
                item / "input.json",
                checksum,
                stamp["revision"],
            )
            md = case["row"]["metadata"]
            resources = {
                key: md["daytona_" + key] for key in ("cpu", "mem_gb", "disk_gb")
            }
            try:
                async with asyncio.timeout(case["solve_timeout"] + 1800):
                    result = await dr.probe(
                        package,
                        None,
                        case["solve_timeout"],
                        resources=resources,
                        prepared_row=case["row"],
                    )
            except Exception as exc:
                log.exception("item=%s status=execution_error", name)
                result = {
                    "stage": "environment_error",
                    "why": f"{type(exc).__name__}: {exc}",
                    **getattr(exc, "validation", {}),
                }
            run_match = accepted(
                result,
                case["expected_reward"],
                completion_marker=case.get("completion_marker"),
                required_observation=case.get("required_observation"),
            )
            record = {
                "input": case,
                "input_sha256": checksum,
                "run": stamp,
                "started_at": started,
                "finished_at": time.time(),
                "result": result,
                # Retained for existing result readers; this is not release approval.
                "accepted": run_match,
                "run_match": run_match,
                "assessment": "preliminary_execution_match",
                "review_required": True,
            }
            pending = item / f"result-{time.time_ns()}.pending"
            write_json(pending, record)
            pending.rename(result_path)
            log.info(
                "item=%s status=%s expected=%s reward=%s stage=%s elapsed=%.1f",
                name,
                "preliminary_match" if run_match else "execution_mismatch",
                case["expected_reward"],
                result.get("reward"),
                result.get("stage"),
                time.time() - started,
            )
            return record

    records = await asyncio.gather(*(one(case) for case in config["cases"]))
    summary = {
        "total": len(records),
        "accepted": sum(r["accepted"] for r in records),
        "run_matches": sum(r["accepted"] for r in records),
        "assessment": "preliminary_execution_match",
        "review_required": True,
        "failed": [r["input"]["case_id"] for r in records if not r["accepted"]],
    }
    log.info("summary=%s", json.dumps(summary))
    print(json.dumps(summary), flush=True)
    return int(bool(summary["failed"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
