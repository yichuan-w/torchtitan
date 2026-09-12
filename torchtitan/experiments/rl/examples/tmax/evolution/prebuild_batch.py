# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Resume a prepared image batch within a conservative sandbox compute budget.

The token file must contain a current repository-scoped registry bearer token.
Refresh it atomically outside this process; no long-lived registry credential
is needed on the host. Failed attempts retain their full budget reservation.
"""

import argparse
import concurrent.futures
import fcntl
import json
import subprocess
import threading
import time
from pathlib import Path

import data_release as release
import prebuild_images as images


# Published Linux sandbox rates, USD per resource-hour, checked 2026-09-12.
# Charge all disk here rather than subtracting the account's free allowance.
RATES = {"cpu": 0.0504, "memory": 0.0162, "disk": 0.000108}
BUILDER = {"cpu": 2, "memory": 4, "disk": 10}


def hourly(resources):
    return sum(RATES[key] * resources[key] for key in RATES)


def reservation(row):
    md = row["metadata"]
    verifier = {
        "cpu": md["daytona_cpu"],
        "memory": md["daytona_mem_gb"],
        "disk": md["daytona_disk_gb"],
    }
    # Builder TTL is 120 minutes and verifier TTL is 15 minutes. Reserving
    # both complete lifetimes covers a lost worker or an uncertain deletion.
    return hourly(BUILDER) * 2 + hourly(verifier) / 4, max(
        hourly(BUILDER), hourly(verifier)
    )


class Ledger:
    def __init__(self, path, budget):
        self.path, self.budget = path, budget
        self.lock = threading.Lock()
        self.entries = json.loads(path.read_text()) if path.exists() else {}

    def reserve(self, key, amount):
        with self.lock:
            if key in self.entries:
                return False
            if (
                sum(item["charged_usd"] for item in self.entries.values()) + amount
                > self.budget
            ):
                return False
            self.entries[key] = {"status": "running", "charged_usd": amount}
            release.write_json(self.path, self.entries)
            return True

    def finish(self, key, status, amount):
        with self.lock:
            self.entries[key] = {"status": status, "charged_usd": amount}
            release.write_json(self.path, self.entries)


def run(args):
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "batch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = release.verify(args.release)
        rows = [
            json.loads(line)
            for line in (args.release / "mix.jsonl").read_text().splitlines()
        ]
        tasks = [row["metadata"]["instance_id"] for row in rows]
        if len(set(tasks)) != len(tasks):
            raise ValueError("batch requires unique task IDs")
        inputs = {
            "release_sha256": manifest["release_sha256"],
            "repository": args.repository,
            "budget_usd": args.budget,
            "workers": args.workers,
            "rates": RATES,
            "code_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True
            ).strip(),
            "rows": rows,
        }
        input_path = args.out / "batch-input.json"
        if input_path.exists() and json.loads(input_path.read_text()) != inputs:
            raise ValueError("batch inputs changed; use a separate output directory")
        release.write_json(input_path, inputs)
        ledger = Ledger(args.out / "budget.json", args.budget)
        images.log(
            args.out,
            "batch-start",
            tasks=len(rows),
            budget_usd=args.budget,
            workers=args.workers,
        )

        def worker(row, key, reserved, rate):
            out = args.out / "tasks" / key
            started = time.monotonic()
            status, charged = "failed", reserved
            images.log(
                args.out,
                "task-start",
                task=row["metadata"]["instance_id"],
                key=key,
                reserved_usd=reserved,
            )
            try:
                images.build(
                    args.release,
                    row["metadata"]["instance_id"],
                    out,
                    args.repository,
                    prepared=(manifest, row),
                )
                token = args.token_file.read_text().strip()
                if not token:
                    raise ValueError("empty registry token file")
                images.push(out, token)
                images.verify(out)
                # Only successful, confirmed deletions return unused budget.
                if not all(
                    (out / name).exists()
                    for name in ("builder-deleted.json", "verifier-deleted.json")
                ):
                    raise RuntimeError("missing confirmed deletion evidence")
                charged = min(reserved, (time.monotonic() - started) / 3600 * rate)
                status = "verified"
            except Exception as error:
                out.mkdir(parents=True, exist_ok=True)
                # SDK exceptions can include the push command's bearer token.
                release.write_json(
                    out / "failure.json", {"type": type(error).__name__, "input": row}
                )
            finally:
                ledger.finish(key, status, charged)
                images.log(
                    args.out,
                    "task-end",
                    task=row["metadata"]["instance_id"],
                    key=key,
                    status=status,
                    elapsed_s=time.monotonic() - started,
                    charged_usd=charged,
                )

        pending = set()
        admitted = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for row in rows:
                if args.limit is not None and admitted >= args.limit:
                    break
                key = images.image_key(row)
                if key in ledger.entries:
                    images.log(
                        args.out,
                        "task-skip",
                        task=row["metadata"]["instance_id"],
                        key=key,
                        reason=ledger.entries[key]["status"],
                    )
                    continue
                while len(pending) >= args.workers:
                    done, pending = concurrent.futures.wait(
                        pending, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for future in done:
                        future.result()
                reserved, rate = reservation(row)
                if not ledger.reserve(key, reserved):
                    # Completed workers may return unused reservations.
                    for future in pending:
                        future.result()
                    pending.clear()
                    if not ledger.reserve(key, reserved):
                        images.log(
                            args.out,
                            "budget-stop",
                            next_task=row["metadata"]["instance_id"],
                        )
                        break
                pending.add(pool.submit(worker, row, key, reserved, rate))
                admitted += 1
            for future in pending:
                future.result()
        counts = {
            status: sum(item["status"] == status for item in ledger.entries.values())
            for status in ("verified", "failed", "running")
        }
        summary = {
            "input": inputs,
            "counts": counts,
            "unattempted": len(rows) - len(ledger.entries),
            "conservative_compute_usd": sum(
                item["charged_usd"] for item in ledger.entries.values()
            ),
        }
        release.write_json(args.out / "summary.json", summary)
        images.log(
            args.out,
            "batch-end",
            counts=counts,
            unattempted=summary["unattempted"],
            charged_usd=summary["conservative_compute_usd"],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, help="Maximum new tasks for a smoke run")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or not 0 < args.budget <= 100:
        parser.error("workers must be 1..8 and budget must be in (0, 100]")
    run(args)


if __name__ == "__main__":
    main()
