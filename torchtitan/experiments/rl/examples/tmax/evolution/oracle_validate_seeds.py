#!/usr/bin/env python3
"""Oracle validation of the seed dataset on Daytona: for each task, build the
environment from its (context-free) Dockerfile, upload solution + tests, run
the reference solution, then the verifier. A task passes when test.sh exits 0.

Resumable: tasks already present in the results file are skipped, one JSON line
per task is appended as soon as it finishes (independent checkpoints).

Usage:
  uv run python scripts/oracle_validate_seeds.py --limit 5          # smoke
  uv run python scripts/oracle_validate_seeds.py --workers 2        # full run
Results: results/oracle_validation.jsonl   Log: logs/oracle_validate.log
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import random
import sys
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAR = ROOT / "data" / "seed-dataset" / "data" / "tasks-00000.tar"
CTXFREE = ROOT / "data" / "seed-dataset-ctxfree"
RESULTS = ROOT / "results" / "oracle_validation.jsonl"
LOGS = ROOT / "logs"

SOLVE_TIMEOUT = 300
TEST_TIMEOUT = 180

# Infra-level failures (quota spikes, throttling) must be retried with backoff,
# never recorded as task verdicts. 2026-08-10: without this, a transient disk-cap
# spike cascaded into a rate-limit storm that burned 1,490 tasks in 20 minutes.
INFRA_MARKERS = ("RateLimit", "Throttler", "Total disk limit", "Total memory limit",
                 "HTTPSConnection", "ConnectionError", "ConnectTimeout",
                 "ReadTimeout", "Failure during")
CREATE_RETRIES = 6

log = logging.getLogger("oracle")
_consecutive_infra = 0
_infra_lock = None  # set in main


def _is_infra(err: Exception) -> bool:
    s = f"{type(err).__name__}: {err}"
    return any(m in s for m in INFRA_MARKERS)


def load_tasks() -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    with tarfile.open(TAR) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            tid, rel = parts[1], "/".join(parts[2:])
            tasks.setdefault(tid, {})[rel] = tf.extractfile(m).read()
    # swap in context-free dockerfiles where the rewriter produced one
    status = {}
    with open(CTXFREE / "report.jsonl") as f:
        for line in f:
            r = json.loads(line)
            status[r["task_id"]] = r["status"]
    for tid, t in tasks.items():
        if status.get(tid) == "rewritten":
            t["environment/Dockerfile"] = (
                CTXFREE / "dockerfiles" / f"{tid}.Dockerfile").read_bytes()
        t["_ctx_status"] = status.get(tid, "unchanged")
    return tasks


def validate_one(daytona, tid: str, t: dict) -> dict:
    global _consecutive_infra
    from daytona import CreateSandboxFromImageParams, Image

    rec = {"task_id": tid, "ctx_status": t["_ctx_status"], "t_start": time.time()}
    if t["_ctx_status"].startswith("failed"):
        rec.update(status="skipped", reason="no context-free dockerfile")
        return rec
    sandbox = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile") as df:
            df.write(t["environment/Dockerfile"].decode("utf-8", "replace"))
            df.flush()
            t0 = time.time()
            for attempt in range(CREATE_RETRIES):
                try:
                    sandbox = daytona.create(CreateSandboxFromImageParams(
                        image=Image.from_dockerfile(df.name)), timeout=600)
                    break
                except Exception as e:  # noqa: BLE001
                    if not _is_infra(e) or attempt == CREATE_RETRIES - 1:
                        raise
                    wait = min(300, 30 * 2 ** attempt) + random.uniform(0, 10)
                    log.warning("%s: infra error (%s), retry %d/%d in %.0fs",
                                tid, type(e).__name__, attempt + 1,
                                CREATE_RETRIES, wait)
                    time.sleep(wait)
        rec["build_s"] = round(time.time() - t0, 1)

        for rel, data in t.items():
            if rel.startswith(("solution/", "tests/")):
                sandbox.fs.upload_file(data, f"/oracle/{rel}")

        t0 = time.time()
        solve = sandbox.process.exec(
            "bash -lc 'cd /app 2>/dev/null || cd /; bash /oracle/solution/solve.sh'",
            timeout=SOLVE_TIMEOUT)
        rec["solve_s"] = round(time.time() - t0, 1)
        rec["solve_exit"] = solve.exit_code

        test = sandbox.process.exec(
            "bash -lc 'cd /app 2>/dev/null || cd /; bash /oracle/tests/test.sh'",
            timeout=TEST_TIMEOUT)
        rec["test_exit"] = test.exit_code
        rec["status"] = "pass" if test.exit_code == 0 else "fail"
        if rec["status"] == "fail":
            rec["test_tail"] = (test.result or "")[-400:]
            rec["solve_tail"] = (solve.result or "")[-400:]
    except Exception as e:  # noqa: BLE001 — record everything, never crash the run
        rec.update(status="error", error=f"{type(e).__name__}: {e}"[:400])
        # Breaker counts ANY create/exec failure streak: storms keep arriving in
        # flavors the marker list hasn't met yet (2026-08-12: HTTPSConnection).
        with _infra_lock:
            _consecutive_infra += 1
            if _consecutive_infra > 20:
                log.error("circuit breaker: >20 consecutive failures, "
                          "aborting run instead of burning the task list")
                os._exit(3)
    else:
        with _infra_lock:
            _consecutive_infra = 0
    finally:
        if sandbox is not None:
            try:
                daytona.delete(sandbox)
            except Exception:  # noqa: BLE001
                log.warning("cleanup failed for %s", tid)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only first N pending tasks")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    LOGS.mkdir(exist_ok=True)
    RESULTS.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(LOGS / "oracle_validate.log")])

    done = set()
    if RESULTS.exists():
        with open(RESULTS) as f:
            done = {json.loads(l)["task_id"] for l in f if l.strip()}

    tasks = load_tasks()
    pending = [(tid, t) for tid, t in sorted(tasks.items()) if tid not in done]
    if os.environ.get("ORACLE_REVERSE"):  # two hosts eating from opposite ends
        pending.reverse()
    if args.limit:
        pending = pending[:args.limit]
    log.info("tasks: %d total, %d done, %d pending this run",
             len(tasks), len(done), len(pending))

    global _infra_lock
    _infra_lock = threading.Lock()

    from daytona import Daytona
    daytona = Daytona()  # reads DAYTONA_API_KEY

    counts: dict[str, int] = {}
    with open(RESULTS, "a") as out, ThreadPoolExecutor(args.workers) as pool:
        futs = {pool.submit(validate_one, daytona, tid, t): tid for tid, t in pending}
        for i, fut in enumerate(as_completed(futs)):
            rec = fut.result()
            rec["t_end"] = time.time()
            out.write(json.dumps(rec) + "\n")
            out.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            log.info("[%d/%d] %s -> %s (build %.0fs solve %.0fs)",
                     i + 1, len(pending), rec["task_id"], rec["status"],
                     rec.get("build_s", 0), rec.get("solve_s", 0))
    log.info("RUN DONE: %s", counts)


if __name__ == "__main__":
    main()
