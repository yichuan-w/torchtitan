#!/usr/bin/env python3
"""Gather every accepted task from every prompt version into one package.

The versions are not interchangeable — the pipeline changed under them, and a
task from v3 was gated by a solver that stopped after three turns while a v19 one
was gated by a solver told to check its work. Rather than keep only the newest,
this ships all of them and says which is which, so a consumer can filter on
provenance instead of taking our word for which era was good.

Each row of the manifest carries:

  version        the prompt version that produced it
  gate_pass_at_k what the loop measured at k=4, once
  verified       what an independent k=5 run measured, where one was done
  in_band        whether that independent run put it in (0,1)

`in_band` is the column to filter on. `verified` being null means nobody has
re-measured it, which is not the same as it being bad.

Usage: consolidate_all.py --out data/all-accepted
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import shutil
import tarfile
from pathlib import Path

REQUIRED = ("instruction.md", "environment/Dockerfile", "solution/solve.sh",
            "tests/test_state.py", "tests/test.sh")


def load_verified() -> dict[str, float]:
    """Every independent re-measurement found, latest per task."""
    seen: dict[str, tuple[float, float]] = {}
    for path in glob.glob("results/solve_accepted*.jsonl") + \
            glob.glob("baseline-v*/solve_accepted*.jsonl"):
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if not r.get("graded"):
                continue
            t, when = r["task_id"], r.get("t_start", 0)
            if t not in seen or when >= seen[t][1]:
                seen[t] = (r["pass_at_k"], when)
    return {t: v for t, (v, _) in seen.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/all-accepted")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    verified = load_verified()

    rows, skipped = [], collections.Counter()
    for results in sorted(glob.glob("baseline-v*/synth_*_p*.jsonl")
                          + glob.glob("results/synth_*_p*.jsonl")):
        root = Path(results).parent
        m = re.search(r"synth_(v[\w]+)_p\d+\.jsonl$", results)
        version = m.group(1) if m else "unknown"
        # The packages sit beside the records: baseline-vN/tasks for archived
        # runs, data/synth-vN for the live one.
        candidates = [root / "tasks", root / f"synth-{version}",
                      Path("data") / f"synth-{version}"]
        for line in Path(results).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("status") != "accepted":
                continue
            tid = r.get("task_id")
            src = next((p for c in candidates
                        for p in c.glob(f"round_*/{tid}")), None)
            if src is None:
                skipped["package not on disk"] += 1
                continue
            if [f for f in REQUIRED if not (src / f).exists()]:
                skipped["incomplete package"] += 1
                continue
            dest = out / "tasks" / tid
            if not dest.exists():
                shutil.copytree(src, dest)
            v = verified.get(tid)
            rows.append({
                "task_id": tid, "version": version, "seed_id": r.get("seed_id"),
                "operator": r.get("operator"), "family": r.get("family"),
                "gate_pass_at_k": r.get("pass_at_k"),
                "retuned_from": r.get("retuned_from"),
                "verified": v,
                "in_band": None if v is None else bool(0 < v < 1),
            })

    seen_ids, unique = set(), []
    for row in rows:
        if row["task_id"] not in seen_ids:
            seen_ids.add(row["task_id"])
            unique.append(row)

    (out / "manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in unique))
    (out / "all_ids.txt").write_text(
        "".join(r["task_id"] + "\n" for r in unique))
    (out / "in_band_ids.txt").write_text(
        "".join(r["task_id"] + "\n" for r in unique if r["in_band"]))

    with tarfile.open(out / "tasks-00000.tar", "w") as tf:
        for pkg in sorted((out / "tasks").iterdir()):
            tf.add(pkg, arcname=f"tasks/{pkg.name}")

    ver = [r for r in unique if r["verified"] is not None]
    band = [r for r in ver if r["in_band"]]
    print(f"{len(unique)} accepted tasks -> {out}/tasks-00000.tar")
    print(f"  independently re-measured: {len(ver)}")
    print(f"  confirmed in the usable band: {len(band)}"
          + (f"  ({100 * len(band) / len(ver):.0f}% of those measured)"
             if ver else ""))
    print(f"  not yet re-measured: {len(unique) - len(ver)}")
    print("\nby version:")
    for v, n in collections.Counter(r["version"] for r in unique).most_common():
        print(f"  {v:10s} {n}")
    print("\nby family:")
    for f, n in collections.Counter(r["family"] for r in unique
                                    if r["family"]).most_common():
        print(f"  {f:34s} {n}")
    if skipped:
        print("\nnot collected:", dict(skipped))


if __name__ == "__main__":
    main()
