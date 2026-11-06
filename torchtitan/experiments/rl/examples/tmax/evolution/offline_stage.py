#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Stage a chosen subset of one run's signals into an experiment root.

The loop over that root then handles exactly those signals. Writes
<root>/runs/<name>/{launch.json, signals/<task>--g<N>.json,
rollouts/<task>/g<N>-r<i>.jsonl}: signal files copied, rollout records
hardlinked (same filesystem), launch.json copied for provenance. Refuses to
write over a run directory that exists.

    offline_stage.py --run <source run dir> --selected selected.json --root <root> \
        [--name <run-dir name>] [--skip-accepted] [--only-failed \
         --max-attempts 2 --attempts-since 20260914-0850]

A signal the ledger already closed is never handled again, so a retry is
staged under a NEW run-dir name (--name): the signal ids change, and the
loop handles each staged signal. --only-failed restages the tasks whose newest
rewrite ended failed or interrupted (an infrastructure loss); kept and rejected
are verdicts and stay. --max-attempts
counts rewrites stamped at or after --attempts-since, so a task that keeps
failing on its own merits is not retried forever, while attempts lost to a
fault you have since fixed are excluded by moving the stamp.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--run", required=True)
    ap.add_argument(
        "--selected", required=True, help="selected.json from offline_select.py"
    )
    ap.add_argument("--root", required=True)
    ap.add_argument(
        "--name", help="run-dir name to stage under (default: the source run's)"
    )
    ap.add_argument(
        "--skip-accepted",
        action="store_true",
        help="skip tasks already revised past the revision the signal's attempts ran on",
    )
    ap.add_argument("--only-failed", action="store_true")
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument(
        "--attempts-since",
        default="",
        help="rewrite-dir stamp prefix from which attempts count",
    )
    a = ap.parse_args()

    src = a.run.rstrip("/")
    name = a.name or os.path.basename(src)
    dst = os.path.join(a.root, "runs", name)
    if os.path.exists(dst):
        sys.exit(f"{dst} exists; refusing to stage over it")
    sel = json.load(open(a.selected))
    os.makedirs(os.path.join(dst, "signals"))
    os.makedirs(os.path.join(dst, "rollouts"))
    shutil.copy2(os.path.join(src, "launch.json"), os.path.join(dst, "launch.json"))
    os.makedirs(os.path.join(a.root, "logs"), exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    log = open(os.path.join(a.root, "logs", f"offline_stage--{stamp}.log"), "w")

    def log_line(msg: str) -> None:
        log.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")
        log.flush()

    log_line(
        f"source={src} root={a.root} name={name} selected={len(sel)} skip_accepted={a.skip_accepted} "
        f"only_failed={a.only_failed} max_attempts={a.max_attempts} since={a.attempts_since!r}"
    )
    n_sig = n_rec = n_skip = 0
    for tid, v in sorted(sel.items()):
        task_dir = os.path.join(a.root, "evolution", "tasks", tid)
        sig = f"{tid}--g{v['group']}.json"
        data = json.load(open(os.path.join(src, "signals", sig)))
        # A signal answers the revision its attempts ran on (rev 0 for a first
        # rewrite, rev 1 when the attempts were on r1), so it is superseded only
        # by a revision newer than that one.
        newest = max(
            (
                int(d[1:])
                for d in (os.listdir(task_dir) if os.path.isdir(task_dir) else [])
                if re.fullmatch(r"r\d+", d)
            ),
            default=0,
        )
        if a.skip_accepted and newest > data.get("rev", 0):
            n_skip += 1
            log_line(f"task={tid} skipped: r{newest} is newer than the signal's rev {data.get('rev', 0)}")
            continue
        if a.only_failed:
            # Only rewrites that answered this signal's revision count: a chain
            # hop's signal at rev N follows an accepted rewrite of rev N-1.
            rws = [
                p
                for p in sorted(
                    glob.glob(os.path.join(task_dir, "rewrites", "*", "rewrite.json"))
                )
                if int(json.load(open(p)).get("input_rev") or 0)
                == int(data.get("rev") or 0)
            ]
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
                    f"task={tid} skipped: {len(tried)} attempts since {a.attempts_since} (newest {st})"
                )
                continue
        # A harder signal (all attempts solved) or an easier one (none solved):
        # the loop picks the job from the signal, and simplify runs only when
        # SWE_EVOLVE_SIMPLIFY is on.
        assert data["task"] == tid and data["direction"] in ("harder", "easier"), (
            tid,
            data,
        )
        for rel in data["attempts"]:
            s, d = os.path.join(src, rel), os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            os.link(s, d)
            n_rec += 1
        # The loop resolves the attempts against runs/<signal's run>/, so the
        # signal names the staged copy, not the run it was cut from.
        data["run"] = name
        with open(os.path.join(dst, "signals", sig), "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        n_sig += 1
        log_line(
            f"signal={name}/{tid}--g{v['group']} solved={data['solved']}/{data['total']} "
            f"attempts={len(data['attempts'])} staged"
        )
    # Reference-only groups a bench set froze beside its signals (evolve_bench
    # freeze --reference-first-group): staged into the same run, so
    # EVOLVE_PEER_CONTEXT picks its in-band peers from the signal's run.
    n_ref = 0
    ref_list = os.path.join(src, "reference.json")
    if n_sig and os.path.exists(ref_list):
        for rel in json.load(open(ref_list)):
            s, d = os.path.join(src, rel), os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            os.link(s, d)
            n_ref += 1
        log_line(f"reference records={n_ref} staged")
    log_line(f"done signals={n_sig} records={n_rec} reference={n_ref} skipped={n_skip}")
    print(
        f"staged {n_sig} signals, {n_rec} rollout records into {dst} (skipped {n_skip}); log {log.name}"
    )


if __name__ == "__main__":
    main()
