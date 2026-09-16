#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Stage harder signals for the tasks a validation eval found still too easy.

offline_select.py picks tasks out of a training run, where the trainer has
already written the signals and offline_stage.py only has to copy them. The
round after that has no training run to read: what scored the hardened rows is
a validation eval, which writes no signals at all, just K attempts per task
under trainer/validation_traces/step-<n>/index.json with each attempt's
rollout record beside it. This reads that index, picks the tasks whose graded
pass rate is at or above --min-rate (attempts that failed on infrastructure
are excluded, as offline_eval_compare.py excludes them), and stages one harder
signal per task into <root>/runs/<name>/ at the task's current revision, with
the eval's rollout records hardlinked -- select and stage in one step, because
with no source signals there is nothing to copy.

    offline_select_eval.py --eval <eval dir> --root <root> --name <run name> \
        [--min-rate 0.9] [--step 0] [--dry]

Writes <root>/runs/<name>/{launch.json, signals/<task>--g<N>.json,
rollouts/<task>/g<N>-r<i>.jsonl} and one log line per task under <root>/logs/.
Refuses a run directory that exists. A task still at r0 is skipped and logged:
nothing has hardened it yet, so the first round is offline_select.py's job and
a signal at the wrong rev would only be superseded. A signal the ledger has
closed is never handled again, so a retry goes under a new --name, and
--only-failed restages just the tasks an infrastructure loss took down.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import shutil
import sys
import time

RECORD_RE = re.compile(r"g(-?\d+)-r(\d+)\.jsonl$")


def _latest_rev(task_dir: str) -> int:
    """The task's current accepted revision: the highest r<N>/ it holds."""
    if not os.path.isdir(task_dir):
        return 0
    revs = [
        int(d[1:])
        for d in os.listdir(task_dir)
        if re.fullmatch(r"r\d+", d) and os.path.isdir(os.path.join(task_dir, d))
    ]
    return max(revs) if revs else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--eval", required=True, help="the eval directory (evals/<name>)")
    ap.add_argument("--root", required=True, help="the experiment root to stage into")
    ap.add_argument("--name", required=True, help="run-dir name to stage under")
    ap.add_argument(
        "--min-rate",
        type=float,
        default=0.9,
        help="stage a task whose graded pass rate is at or above this (default 0.9: "
        "12/12 and 11/12 of a K12 eval, where the group carries no learning signal)",
    )
    ap.add_argument("--step", type=int, default=0, help="validation step to read")
    ap.add_argument(
        "--only-failed",
        action="store_true",
        help="restage only the tasks whose newest rewrite ended failed or interrupted "
        "(an infrastructure loss); kept, rejected and blocked are verdicts and stay",
    )
    ap.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="with --only-failed: skip a task with this many rewrites stamped at or "
        "after --attempts-since, so one failing on its own merits is not retried forever",
    )
    ap.add_argument(
        "--attempts-since",
        default="",
        help="rewrite-dir stamp prefix from which attempts count",
    )
    ap.add_argument("--dry", action="store_true", help="print the picks, write nothing")
    a = ap.parse_args()

    ev = a.eval.rstrip("/")
    index = os.path.join(
        ev, "trainer", "validation_traces", f"step-{a.step}", "index.json"
    )
    rows = json.load(open(index))
    per: dict[str, dict] = collections.defaultdict(
        lambda: {"pass": 0, "graded": 0, "records": []}
    )
    for r in rows:
        t = per[r["task"]]
        t["records"].append(r["record"])
        if r.get("infra_failed"):
            continue
        t["graded"] += 1
        t["pass"] += 1 if r["state"] == "PASS" else 0

    picks = []
    for tid, v in sorted(per.items()):
        if not v["graded"]:
            continue
        rate = v["pass"] / v["graded"]
        if rate >= a.min_rate:
            picks.append((tid, v, rate))
    print(
        f"eval {os.path.basename(ev)} step {a.step}: {len(per)} tasks, "
        f"{len(picks)} at or above {a.min_rate:.0%}"
    )
    if a.dry:
        for tid, v, rate in picks:
            print(f"  {tid} {v['pass']}/{v['graded']} ({rate:.0%})")
        return

    dst = os.path.join(a.root, "runs", a.name)
    if os.path.exists(dst):
        sys.exit(f"{dst} exists; refusing to stage over it")
    os.makedirs(os.path.join(dst, "signals"))
    os.makedirs(os.path.join(dst, "rollouts"))
    shutil.copy2(os.path.join(ev, "launch.json"), os.path.join(dst, "launch.json"))
    os.makedirs(os.path.join(a.root, "logs"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    log = open(os.path.join(a.root, "logs", f"offline_select_eval--{stamp}.log"), "w")

    def log_line(msg: str) -> None:
        log.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")
        log.flush()

    log_line(
        f"eval={ev} step={a.step} root={a.root} name={a.name} min_rate={a.min_rate} "
        f"tasks={len(per)} picked={len(picks)}"
    )
    n_sig = n_rec = n_skip = 0
    for tid, v, rate in picks:
        task_dir = os.path.join(a.root, "evolution", "tasks", tid)
        rev = _latest_rev(task_dir)
        if rev == 0:
            n_skip += 1
            log_line(f"task={tid} skipped: still at r0, nothing to harden further")
            continue
        if a.only_failed:
            rws = sorted(
                glob.glob(os.path.join(task_dir, "rewrites", "*", "rewrite.json"))
            )
            st = json.load(open(rws[-1]))["status"] if rws else "never_handled"
            if st not in ("failed", "interrupted", "never_handled"):
                n_skip += 1
                log_line(f"task={tid} skipped: newest rewrite is {st}")
                continue
            tried = [
                r
                for r in rws
                if os.path.basename(os.path.dirname(r)) >= a.attempts_since
            ]
            if a.max_attempts and len(tried) >= a.max_attempts:
                n_skip += 1
                log_line(
                    f"task={tid} skipped: {len(tried)} attempts since "
                    f"{a.attempts_since} (newest {st})"
                )
                continue
        groups = {
            int(m.group(1)) for m in (RECORD_RE.search(p) for p in v["records"]) if m
        }
        if len(groups) != 1:
            n_skip += 1
            log_line(f"task={tid} skipped: records span groups {sorted(groups)}")
            continue
        group = groups.pop()
        attempts = []
        for rel in sorted(v["records"]):
            src = os.path.join(ev, rel)
            rel_dst = os.path.join("rollouts", tid, os.path.basename(rel))
            d = os.path.join(dst, rel_dst)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            os.link(src, d)
            attempts.append(rel_dst)
            n_rec += 1
        signal = {
            "attempts": attempts,
            "created": stamp,
            "direction": "harder",
            "group": group,
            "rev": rev,
            "run": a.name,
            "solved": v["pass"],
            "task": tid,
            "total": v["graded"],
        }
        with open(os.path.join(dst, "signals", f"{tid}--g{group}.json"), "w") as f:
            json.dump(signal, f, indent=1, sort_keys=True)
            f.write("\n")
        n_sig += 1
        log_line(
            f"signal={a.name}/{tid}--g{group} rev={rev} solved={v['pass']}/{v['graded']} "
            f"({rate:.0%}) attempts={len(attempts)} staged"
        )
    log_line(f"done signals={n_sig} records={n_rec} skipped={n_skip}")
    print(
        f"staged {n_sig} signals, {n_rec} rollout records into {dst} "
        f"(skipped {n_skip}); log {log.name}"
    )


if __name__ == "__main__":
    main()
