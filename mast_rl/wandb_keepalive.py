#!/usr/bin/env python3
"""Keep a W&B mirror run online while a separate process appends history."""

from __future__ import annotations

import argparse
import signal
import threading
import time

import wandb


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--name")
    args = parser.parse_args()

    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    run = wandb.init(
        entity=args.entity,
        project=args.project,
        id=args.run_id,
        name=args.name,
        resume="allow",
        settings=wandb.Settings(
            console="off",
            x_disable_meta=True,
            x_disable_stats=True,
        ),
    )
    try:
        run.summary["mirror/keepalive_unix"] = int(time.time())
        while not stop.wait(30):
            # A summary-only update keeps the backend heartbeat current without
            # adding history rows that would offset mirrored training steps.
            run.summary["mirror/keepalive_unix"] = int(time.time())
    finally:
        run.finish()


if __name__ == "__main__":
    main()
