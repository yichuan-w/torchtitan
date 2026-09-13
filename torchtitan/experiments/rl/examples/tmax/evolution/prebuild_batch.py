# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Resume a prepared image batch, with an optional compute budget.

The token file must contain a current repository-scoped registry bearer token.
Refresh it atomically outside this process; no long-lived registry credential
is needed on the host. Failed attempts retain their full budget reservation.
"""

import argparse
import concurrent.futures
import fcntl
import json
import resource
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

    def reserve(self, key, amount, *, retry=False, attempt_dir=None):
        with self.lock:
            previous = self.entries.get(key)
            if previous and (not retry or previous["status"] != "failed"):
                return False
            if self.budget is not None and (
                sum(item["charged_usd"] for item in self.entries.values()) + amount
                > self.budget
            ):
                return False
            prior_charge = previous["charged_usd"] if previous else 0
            self.entries[key] = {
                "status": "running",
                "charged_usd": prior_charge + amount,
                "previous_charged_usd": prior_charge,
            }
            if attempt_dir is not None:
                self.entries[key]["attempt_dir"] = str(attempt_dir)
            release.write_json(self.path, self.entries)
            return True

    def finish(self, key, status, amount):
        with self.lock:
            self.entries[key].update(
                status=status,
                charged_usd=self.entries[key].get("previous_charged_usd", 0) + amount,
            )
            release.write_json(self.path, self.entries)


def validate_resume(previous, current):
    runtime = {"workers", "code_commit", "budget_usd"}
    if {k: v for k, v in previous.items() if k not in runtime} != {
        k: v for k, v in current.items() if k not in runtime
    }:
        raise ValueError("batch data changed; use a separate output directory")


def run(args):
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and soft < args.workers * 4 + 256:
        raise RuntimeError(
            f"workers={args.workers} requires LimitNOFILE >= {args.workers * 4 + 256}; got {soft}"
        )
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
        if input_path.exists():
            validate_resume(json.loads(input_path.read_text()), inputs)
        else:
            release.write_json(input_path, inputs)
        release.write_json(
            args.out / f"execution-{time.time_ns()}.json",
            {
                **inputs,
                "retry_failed": args.retry_failed,
                "nofile_soft": soft,
                "task": args.task,
                "limit": args.limit,
            },
        )
        ledger = Ledger(args.out / "budget.json", args.budget)
        images.log(
            args.out,
            "batch-start",
            tasks=len(rows),
            budget_usd=args.budget,
            workers=args.workers,
        )

        def worker(row, key, reserved, rate, out, previous):
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
                if previous is not None:
                    images.recover_artifacts(previous, out)
                if (
                    not (out / "built.json").exists()
                    and not (out / "publication.json").exists()
                ):
                    images.build(
                        args.release,
                        row["metadata"]["instance_id"],
                        out,
                        args.repository,
                        prepared=(manifest, row),
                    )
                if not (out / "publication.json").exists():
                    token = args.token_file.read_text().strip()
                    if not token:
                        raise ValueError("empty registry token file")
                    images.push(out, token, token_file=args.token_file)
                images.verify(out)
                images.validate_owner_repair(out, args.release)
                # Only successful, confirmed deletions return unused budget.
                if not all(
                    (out / name).exists()
                    for name in ("builder-deleted.json", "verifier-deleted.json")
                ):
                    raise RuntimeError("missing confirmed deletion evidence")
                charged = min(reserved, (time.monotonic() - started) / 3600 * rate)
                status = "verified"
            except Exception as error:
                # Stop admitting work on an unresolved failure; otherwise a
                # registry outage could consume the batch on identical errors.
                stop.set()
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
        stop = threading.Event()
        admitted = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for row in rows:
                if (
                    args.task is not None
                    and row["metadata"]["instance_id"] != args.task
                ):
                    continue
                if stop.is_set():
                    images.log(args.out, "failure-stop")
                    break
                if args.limit is not None and admitted >= args.limit:
                    break
                key = images.image_key(row)
                entry = ledger.entries.get(key)
                retry = (
                    entry is not None
                    and entry["status"] == "failed"
                    and args.retry_failed
                )
                if entry is not None and not retry:
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
                if stop.is_set():
                    images.log(args.out, "failure-stop")
                    break
                reserved, rate = reservation(row)
                original = args.out / "tasks" / key
                previous = Path(entry.get("attempt_dir", original)) if retry else None
                out = original / f"retry-{time.time_ns()}" if retry else original
                if not ledger.reserve(key, reserved, retry=retry, attempt_dir=out):
                    # Completed workers may return unused reservations.
                    for future in pending:
                        future.result()
                    pending.clear()
                    if not ledger.reserve(key, reserved, retry=retry, attempt_dir=out):
                        images.log(
                            args.out,
                            "budget-stop",
                            next_task=row["metadata"]["instance_id"],
                        )
                        break
                pending.add(
                    pool.submit(worker, row, key, reserved, rate, out, previous)
                )
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
    parser.add_argument(
        "--budget", type=float, help="Optional total compute spending cap"
    )
    parser.add_argument("--workers", type=int, default=1000)
    parser.add_argument("--limit", type=int, help="Maximum new tasks for a smoke run")
    parser.add_argument(
        "--task", help="Attempt only this task for a recovery smoke check"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Recover failed tasks into new attempt directories",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 1000 or (args.budget is not None and args.budget <= 0):
        parser.error("workers must be 1..1000; an explicit budget must be positive")
    run(args)


if __name__ == "__main__":
    main()
