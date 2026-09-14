#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Freeze prepared task rows, then validate every environment on Daytona."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import importlib.metadata
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: dict) -> None:
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def freeze(args) -> None:
    from pack_to_dataset import declared_solve_budget

    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "inputs").mkdir()
    (args.output / "packages").mkdir()
    rows = [
        json.loads(line) for line in args.mix.read_text().splitlines() if line.strip()
    ]
    ids = [r["metadata"]["instance_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task IDs in input mix")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": revision(),
        "mix": str(args.mix.resolve()),
        "mix_sha256": digest(args.mix.read_bytes()),
        "ids": ids,
        "package_roots": [str(p.resolve()) for p in args.packages],
        "dependencies": dict(
            sorted(
                (d.metadata["Name"], d.version)
                for d in importlib.metadata.distributions()
                if d.metadata["Name"]
            )
        ),
    }
    shutil.copyfile(args.mix, args.output / "candidate.jsonl")
    for row in rows:
        tid = row["metadata"]["instance_id"]
        if Path(tid).name != tid:
            raise ValueError(f"invalid task ID: {tid!r}")
        source = next((p / tid for p in args.packages if (p / tid).is_dir()), None)
        payload = {
            "row": row,
            "source": str(source) if source else None,
            "solution": {},
            "solve_timeout": max(
                900, int(row["metadata"].get("agent_timeout_sec") or 0)
            ),
        }
        if source is not None:
            payload["solve_timeout"] = declared_solve_budget(
                source, payload["solve_timeout"]
            )
        if source is not None:
            shutil.copytree(source, args.output / "packages" / tid, symlinks=False)
            for path in sorted((source / "solution").rglob("*")):
                if path.is_file():
                    payload["solution"][
                        str(path.relative_to(source / "solution"))
                    ] = path.read_text()
        write_json(args.output / "inputs" / f"{tid}.json", payload)
    manifest["input_sha256"] = {
        tid: digest((args.output / "inputs" / f"{tid}.json").read_bytes())
        for tid in ids
    }
    write_json(args.output / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "frozen": len(ids),
                "output": str(args.output),
                "no_package": sum(
                    not (args.output / "packages" / tid).is_dir() for tid in ids
                ),
            }
        ),
        flush=True,
    )


async def run(args) -> int:
    os.environ["TT_DAYTONA_LABEL"] = args.label
    # The harness reads its label at import time.
    import daytona_revalidate as dr

    manifest = json.loads((args.output / "manifest.json").read_text())
    attempt = args.output / "attempts" / args.attempt
    attempt.mkdir(parents=True, exist_ok=True)
    lock = (attempt / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    logging.basicConfig(
        filename=attempt / "events.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    log = logging.getLogger("environment_sweep")
    config = {
        "revision": revision(),
        "execution_harness": "terminus",
        "label": args.label,
        "concurrency": args.concurrency,
        "ids": args.ids,
        "resource_defaults": {
            "cpu": int(os.environ.get("TT_DAYTONA_CPU", "2")),
            "mem_gb": int(os.environ.get("TT_DAYTONA_MEM_GB", "4")),
            "disk_gb": int(os.environ.get("TT_DAYTONA_DISK_GB", "6")),
        },
        "manifest_sha256": digest((args.output / "manifest.json").read_bytes()),
    }
    config_file = attempt / "run.json"
    if config_file.exists():
        if json.loads(config_file.read_text()) != config:
            raise ValueError("resume configuration changed; use a new attempt")
    else:
        write_json(config_file, config)
    ids = args.ids or manifest["ids"]
    if set(ids) - set(manifest["ids"]):
        raise ValueError("requested task not present in frozen inventory")
    sem = asyncio.Semaphore(args.concurrency)

    async def one(tid):
        input_file = args.output / "inputs" / f"{tid}.json"
        raw = input_file.read_bytes()
        if digest(raw) != manifest["input_sha256"][tid]:
            raise ValueError(f"frozen input changed: {tid}")
        payload = json.loads(raw)
        result_path = attempt / f"{tid}.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            if result["input_sha256"] != digest(raw):
                raise ValueError(f"checkpoint input mismatch: {tid}")
            log.info("item=%s status=resume_skip previous_ok=%s", tid, result["ok"])
            return result
        async with sem:
            started = time.time()
            log.info(
                "item=%s status=start input=%s sha256=%s revision=%s",
                tid,
                input_file,
                digest(raw),
                config["revision"],
            )
            md = payload["row"]["metadata"]
            resources = {
                key: md.get(f"daytona_{key}") or default
                for key, default in config["resource_defaults"].items()
            }
            try:
                async with asyncio.timeout(payload["solve_timeout"] + 1800):
                    result = await dr.probe(
                        args.output / "packages" / tid,
                        None,
                        payload["solve_timeout"],
                        resources=resources,
                        prepared_row=payload["row"],
                        check_terminal=True,
                    )
                    if (
                        result.get("stage") == "daytona_oracle"
                        and result["solve_exit"] != 0
                    ):
                        result["ok"] = False
                        result["stage"] = "reference_error"
                        result.setdefault(
                            "why",
                            f"reference solution exited with {result['solve_exit']}",
                        )
            except Exception as exc:
                log.exception("item=%s validation failed", tid)
                result = {
                    "ok": False,
                    "stage": "environment_error",
                    "why": f"{type(exc).__name__}: {exc}",
                    **getattr(exc, "validation", {}),
                }
            result.update(
                task_id=tid,
                started_at=started,
                finished_at=time.time(),
                input=payload,
                input_sha256=digest(raw),
                run=config,
            )
            temp = result_path.with_suffix(".pending")
            if temp.exists():
                # A crash can interrupt the write; retain it as evidence.
                temp.rename(temp.with_name(f"{tid}.{time.time_ns()}.interrupted"))
            write_json(temp, result)
            temp.rename(result_path)
            log.info(
                "item=%s status=%s stage=%s elapsed=%.1f reason=%s",
                tid,
                "pass" if result["ok"] else "fail",
                result.get("stage"),
                time.time() - started,
                result.get("why", ""),
            )
            return result

    results = await asyncio.gather(*(one(tid) for tid in ids))
    summary = {
        "total": len(results),
        "passed": sum(r["ok"] for r in results),
        "failed": [r["task_id"] for r in results if not r["ok"]],
    }
    log.info("summary=%s", json.dumps(summary))
    print(json.dumps(summary), flush=True)
    return int(bool(summary["failed"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mix", type=Path)
    parser.add_argument("--packages", type=Path, action="append", default=[])
    parser.add_argument("--attempt", default="initial")
    parser.add_argument("--label", default="environment_validation")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--ids", nargs="+")
    args = parser.parse_args()
    if args.action == "freeze":
        if args.mix is None or not args.packages:
            parser.error("freeze requires --mix and --packages")
        freeze(args)
    else:
        sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
