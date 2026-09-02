#!/usr/bin/env python3
"""Gather the tasks that cleared every gate into one package, ready to train on.

A synthesis run leaves its output spread across the round directories of whatever
prompt version produced it, mixed in with everything that failed a gate. What is
wanted downstream is one archive of the tasks that survived, in the same layout
the seed corpus uses, so the same tooling reads both — `solve_eval.py` can
measure the rewrites the way it measured the seeds, and a trainer can point at
one file.

Only `accepted` is collected. `too_easy` and `too_hard` are excluded on purpose:
they are complete, valid tasks that carry no gradient, and shipping them would
put back exactly the saturation the rewrite exists to remove.

Usage:
  collect_accepted.py --runs 'results/synth_v5_p*.jsonl' --tasks data/synth-v5 \\
      --out data/accepted --tar data/accepted/tasks-00000.tar
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import shutil
import tarfile
from pathlib import Path

KEEP = {"accepted", "usable"}
REQUIRED = ("instruction.md", "environment/Dockerfile", "solution/solve.sh",
            "tests/test_state.py", "tests/test.sh")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", action="append", required=True,
                    help="glob over results jsonl; repeatable across versions")
    ap.add_argument("--tasks", action="append", required=True,
                    help="matching task directory for each --runs glob")
    ap.add_argument("--verified",
                    help="a solve_eval results file; keep only tasks it "
                         "re-measured inside the usable band")
    ap.add_argument("--out", default="data/accepted")
    ap.add_argument("--tar", default="data/accepted/tasks-00000.tar")
    args = ap.parse_args()
    if len(args.runs) != len(args.tasks):
        raise SystemExit("--runs and --tasks must pair up")

    # The loop's difficulty gate samples k=4 once, and re-measuring five
    # accepted tasks at k=5 moved two of them out of the band — one to 0.0 and
    # one to 1.0. A single estimate of difficulty is noisy, so the gate is
    # treated as a cheap filter and an independent pass decides what ships.
    band = None
    if args.verified:
        band = set()
        for line in Path(args.verified).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            p = r.get("pass_at_k")
            if p is not None and 0 < p < 1:
                band.add(r["task_id"])

    out = Path(args.out)
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    kept, skipped = [], collections.Counter()

    for run_glob, task_dir in zip(args.runs, args.tasks):
        rows = []
        for path in sorted(glob.glob(run_glob)):
            rows += [json.loads(l) for l in Path(path).read_text().splitlines()
                     if l.strip()]
        for r in rows:
            if r.get("status") not in KEEP:
                skipped[r.get("status")] += 1
                continue
            tid = r.get("task_id")
            src = next(iter(Path(task_dir).glob(f"round_*/{tid}")), None)
            if src is None:
                skipped["package missing on disk"] += 1
                continue
            if band is not None and tid not in band:
                skipped["re-measured outside the usable band"] += 1
                continue
            missing = [f for f in REQUIRED if not (src / f).exists()]
            if missing:
                # A record can say accepted while the package on disk is short a
                # file, and a task that cannot be rebuilt is not a task.
                skipped[f"incomplete: {missing[0]}"] += 1
                continue
            dest = out / "tasks" / tid
            if dest.exists():
                # Already collected by an earlier run of this command. Counting
                # it as a skip empties `kept`, and the manifest is written from
                # `kept` — so a second run used to overwrite a good ids file with
                # an empty one and leave the tasks on disk unreferenced.
                kept.append({"task_id": tid, "seed_id": r.get("seed_id"),
                             "operator": r.get("operator"),
                             "family": r.get("family"),
                             "pass_at_k": r.get("pass_at_k"),
                             "rewards": r.get("rewards")})
                continue
            shutil.copytree(src, dest)
            kept.append({"task_id": tid, "seed_id": r.get("seed_id"),
                         "operator": r.get("operator"), "family": r.get("family"),
                         "pass_at_k": r.get("pass_at_k"),
                         "rewards": r.get("rewards")})

    (out / "accepted.jsonl").write_text(
        "".join(json.dumps(k) + "\n" for k in kept))
    (out / "accepted_ids.txt").write_text(
        "".join(k["task_id"] + "\n" for k in kept))

    tar_path = Path(args.tar)
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "w") as tf:
        for pkg in sorted((out / "tasks").iterdir()):
            tf.add(pkg, arcname=f"tasks/{pkg.name}")

    print(f"kept {len(kept)} accepted tasks -> {tar_path}")
    fams = collections.Counter(k["family"] for k in kept if k["family"])
    for f, n in fams.most_common():
        print(f"  {f:34s} {n}")
    print("\nnot collected:")
    for k, n in skipped.most_common(12):
        print(f"  {str(k):34s} {n}")


if __name__ == "__main__":
    main()
