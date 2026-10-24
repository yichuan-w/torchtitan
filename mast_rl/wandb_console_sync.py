#!/usr/bin/env python3
"""Mirror controller console metrics from a MAST run into an online W&B run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import wandb


_LINE_PATTERN = re.compile(r"(Validation|Train) \| Step:\s*(\d+)\s+(.*)")
_METRIC_PATTERN = re.compile(r"(\S+):\s+([^\s]+)")
_DECODE_PATTERN = re.compile(
    r"INFO (\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
    r"gpus['\"]?: (\d+)/\d+.*"
    r"Avg generation throughput: ([0-9.]+) tokens/s.*"
    r"Running: (\d+) reqs"
)
_GENERATOR_ROLE_PATTERN = re.compile(r"VLLMGenerator (generator(?:_\d+)?)\{")


def parse_console_metrics(line: str) -> tuple[str, int, dict[str, float]] | None:
    match = _LINE_PATTERN.search(line)
    if match is None:
        return None

    metrics: dict[str, float] = {}
    for key, raw_value in _METRIC_PATTERN.findall(match.group(3)):
        try:
            metrics[key] = float(raw_value)
        except ValueError:
            continue
    if not metrics:
        return None
    return match.group(1).lower(), int(match.group(2)), metrics


def parse_decode_metrics(console_log: str) -> list[tuple[int, dict[str, float]]]:
    by_bucket: dict[int, dict[tuple[str, int], tuple[float, int]]] = defaultdict(
        dict
    )
    for line in console_log.splitlines():
        match = _DECODE_PATTERN.search(line)
        if match is None:
            continue
        role_match = _GENERATOR_ROLE_PATTERN.search(line)
        role = role_match.group(1) if role_match is not None else "generator"
        timestamp = datetime.strptime(
            f"{datetime.now().year}-{match.group(1)}", "%Y-%m-%d %H:%M:%S"
        )
        bucket = int(timestamp.timestamp()) // 10 * 10
        shard = (role, int(match.group(2)))
        by_bucket[bucket][shard] = (float(match.group(3)), int(match.group(4)))

    if not by_bucket:
        return []
    first_bucket = min(by_bucket)
    rows = []
    for bucket, shards in sorted(by_bucket.items()):
        if len(shards) < 4:
            continue
        throughputs = [value[0] for value in shards.values()]
        rows.append(
            (
                bucket,
                {
                    "decode_elapsed_minutes": (bucket - first_bucket) / 60,
                    "decode/shard_throughput_mean_tps": sum(throughputs)
                    / len(throughputs),
                    "decode/total_throughput_tps": sum(throughputs),
                    "decode/active_requests": sum(
                        value[1] for value in shards.values()
                    ),
                    "decode/num_shards": len(shards),
                },
            )
        )
    return rows


def fetch_console_log(mast_job: str) -> str | None:
    try:
        result = subprocess.run(
            [
                "mast",
                "get-logs",
                mast_job,
                "--file-path",
                "stdout",
                "--regex",
                r"Validation \| Step|Train \| Step|Avg generation throughput",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def load_seen(state_path: Path) -> set[str]:
    if not state_path.is_file():
        return set()
    return set(json.loads(state_path.read_text()))


def save_seen(state_path: Path, seen: set[str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(sorted(seen)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mast-job", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--entity", default="yichuan")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    cache_dir = Path("/tmp") / f"wandb_console_sync_{args.run_name}"
    state_path = cache_dir / "seen.json"
    trainer_step_path = cache_dir / "trainer_step.txt"
    seen = load_seen(state_path)
    latest_trainer_step = (
        int(trainer_step_path.read_text()) if trainer_step_path.is_file() else None
    )
    decode_origin = min(
        (
            int(fingerprint.removeprefix("decode:"))
            for fingerprint in seen
            if fingerprint.startswith("decode:")
            and fingerprint.removeprefix("decode:").isdigit()
        ),
        default=None,
    )

    run = wandb.init(
        entity=args.entity,
        project=args.project,
        id=args.run_id,
        name=args.run_name,
        resume="allow",
    )
    run.define_metric("trainer_step")
    run.define_metric("*", step_metric="trainer_step")
    run.define_metric("decode_elapsed_minutes")
    run.define_metric("decode/*", step_metric="decode_elapsed_minutes")

    try:
        while True:
            console_log = fetch_console_log(args.mast_job)
            if console_log is not None:
                num_logged = 0
                for line in console_log.splitlines():
                    parsed = parse_console_metrics(line)
                    if parsed is None:
                        continue
                    latest_trainer_step = (
                        parsed[1]
                        if latest_trainer_step is None
                        else max(parsed[1], latest_trainer_step)
                    )
                    fingerprint = hashlib.sha256(line.encode()).hexdigest()
                    if fingerprint in seen:
                        continue
                    record_type, trainer_step, metrics = parsed
                    run.log(
                        {
                            "trainer_step": trainer_step,
                            "record_type": record_type,
                            **metrics,
                        }
                    )
                    seen.add(fingerprint)
                    num_logged += 1
                decode_rows = parse_decode_metrics(console_log)
                if decode_rows and decode_origin is None:
                    decode_origin = decode_rows[0][0]
                for bucket, metrics in decode_rows:
                    fingerprint = f"decode:{bucket}"
                    if fingerprint in seen:
                        continue
                    if latest_trainer_step is not None:
                        metrics["trainer_step"] = latest_trainer_step
                    metrics["decode_elapsed_minutes"] = (
                        bucket - decode_origin
                    ) / 60
                    run.log(metrics)
                    seen.add(fingerprint)
                    num_logged += 1
                if num_logged == 0:
                    heartbeat = {"bridge/heartbeat": time.time()}
                    if latest_trainer_step is not None:
                        heartbeat["trainer_step"] = latest_trainer_step
                    run.log(heartbeat)
                    num_logged = 1
                save_seen(state_path, seen)
                if latest_trainer_step is not None:
                    trainer_step_path.write_text(str(latest_trainer_step))
                print(
                    f"{time.strftime('%H:%M:%S')} logged {num_logged} new metric rows",
                    flush=True,
                )
            else:
                print(f"{time.strftime('%H:%M:%S')} MAST logs unavailable", flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        run.finish()


if __name__ == "__main__":
    main()
