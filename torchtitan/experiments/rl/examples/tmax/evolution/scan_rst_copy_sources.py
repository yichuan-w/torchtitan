#!/usr/bin/env python3
"""Independently verify Yichuan's claim that ~6,699 RST tasks need external
build context: count tasks whose Dockerfile COPY/ADD sources are absent from
their own released task package, and show what the missing sources look like.
"""
import collections
import re
import tarfile
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent / "data" / "rst-tasks"

t = pq.read_table(ROOT / "metadata" / "tasks.parquet",
                  columns=["task_id", "dockerfile", "member_prefix"])

# member_prefix -> set of paths relative to the task's environment/ dir
env_files: dict[str, set] = collections.defaultdict(set)
for i in range(8):
    with tarfile.open(ROOT / "data" / f"tasks-{i:05d}.tar") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            pre = "/".join(parts[:2])  # tasks/rts_task_x
            rel = "/".join(parts[2:])
            if rel.startswith("environment/"):
                env_files[pre].add(rel.removeprefix("environment/"))

missing_tasks, samples = set(), []
for tid, df, pre in zip(t["task_id"].to_pylist(), t["dockerfile"].to_pylist(),
                        t["member_prefix"].to_pylist()):
    own = env_files.get(pre, set())
    for line in (df or "").splitlines():
        m = re.match(r"^(COPY|ADD)\s+(?!--from)(.+)", line.strip(), re.I)
        if not m:
            continue
        for src in m.group(2).split()[:-1]:
            if src.startswith(("--", "http")):
                continue
            base = src.lstrip("./").rstrip("/*")
            if base and not any(f == base or f.startswith(base + "/") for f in own):
                missing_tasks.add(tid)
                if len(samples) < 8:
                    samples.append((tid, src))
                break
        if tid in missing_tasks:
            break

print(f"RST tasks with COPY/ADD source missing from their own package: "
      f"{len(missing_tasks)} / {t.num_rows}")
print("sample missing sources:")
for tid, src in samples:
    print(f"  {tid}: {src}")
