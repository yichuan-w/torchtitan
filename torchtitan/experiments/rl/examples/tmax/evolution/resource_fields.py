#!/usr/bin/env python3
"""Per-task resource fields for the seed dataset.

A consumer that runs these tasks has to size a sandbox for each one, and the
size it picks decides how many run concurrently: a cloud tier that caps total
sandbox memory at 10GiB fits four 2GiB tasks but only two that defaulted to
4GiB. The information needed to size them correctly is already in the tasks —
Terminal-Bench's task.toml declares `cpus` and `memory` — it just never
surfaced as a column, so every consumer either re-parses the TOML blob or
falls back to a runner default.

Three fields are exposed:

  req_cpus, req_memory_mb  declared by the task. Ground truth, null when the
                           task omits them (214 omit memory, 34 omit cpus).
  est_disk_mb              *estimated*, not declared: no task states a disk
                           requirement. Derived from the base image's
                           compressed size, an expansion factor for unpacking,
                           the task's own files, and headroom for whatever the
                           build installs on top. Treat it as a floor with
                           slack, not a measurement.

Usage as a script: resolve and cache base-image sizes, then report coverage.
  uv run python scripts/resource_fields.py [--refresh]
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "results" / "base_image_sizes.json"

# A registry reports compressed layer bytes; unpacked layers run roughly 2-3x
# that. 3 keeps the estimate on the safe side of "the pull fails at 95%".
UNPACK_FACTOR = 3
# What `RUN apt-get install ...` on top of the base image costs. Most tasks
# install a handful of packages; 1GiB covers that without inflating every row.
BUILD_HEADROOM_MB = 1024

MEM_RE = re.compile(r"^\s*memory\s*=\s*[\"']?([0-9.]+)\s*([KMGT]?)i?[Bb]?[\"']?", re.M)
CPU_RE = re.compile(r"^\s*cpus\s*=\s*[\"']?([0-9.]+)", re.M)
FROM_RE = re.compile(r"^\s*FROM\s+(\S+)", re.M | re.I)

_UNIT_MB = {"": 1 / (1024 * 1024), "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}


def parse_memory_mb(task_toml: str) -> int | None:
    m = MEM_RE.search(task_toml)
    if not m:
        return None
    return round(float(m.group(1)) * _UNIT_MB[m.group(2).upper()])


def parse_cpus(task_toml: str) -> int | None:
    m = CPU_RE.search(task_toml)
    return round(float(m.group(1))) if m else None


def parse_base_image(dockerfile: str) -> str | None:
    """First FROM, with build-stage aliases resolved away.

    A multi-stage Dockerfile's later stages may build FROM an earlier stage;
    only the first FROM names something a registry can size.
    """
    found = FROM_RE.findall(dockerfile)
    return found[0] if found else None


def _hub_size_mb(image: str) -> int | None:
    """Compressed size from Docker Hub, or None if it can't be resolved."""
    ref = image.split("@")[0]
    repo, _, tag = ref.partition(":")
    tag = tag or "latest"
    if repo.count("/") == 0:
        repo = f"library/{repo}"
    elif repo.count("/") > 1 or "." in repo.split("/")[0]:
        return None  # not Docker Hub (ghcr.io, quay.io, private registries)
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    size = data.get("full_size")
    return round(size / (1024 * 1024)) if size else None


def load_base_sizes(images: list[str], refresh: bool = False) -> dict[str, int | None]:
    """Resolve each image once, cached on disk so rebuilds cost no requests."""
    cache: dict[str, int | None] = {}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text())
    missing = [i for i in dict.fromkeys(images) if i not in cache]
    for n, image in enumerate(missing, 1):
        cache[image] = _hub_size_mb(image)
        if n % 20 == 0:
            print(f"  resolved {n}/{len(missing)}")
    if missing:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return cache


def est_disk_mb(base_size_mb: int | None, task_bytes: int) -> int | None:
    if base_size_mb is None:
        return None
    return round(base_size_mb * UNPACK_FACTOR
                 + task_bytes / (1024 * 1024) + BUILD_HEADROOM_MB)


def main() -> None:
    import pyarrow.parquet as pq

    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="re-query every image instead of using the cache")
    ap.add_argument("--dataset", default="data/seed-dataset")
    ap.add_argument("--write", action="store_true",
                    help="add the columns to the dataset's parquet")
    args = ap.parse_args()

    t = pq.read_table(ROOT / args.dataset / "metadata" / "tasks.parquet")
    tomls = t["task_toml"].to_pylist()
    dockerfiles = t["dockerfile"].to_pylist()
    images = [parse_base_image(d or "") or "" for d in dockerfiles]
    print(f"{t.num_rows} tasks, {len(set(images))} distinct base images")

    sizes = load_base_sizes([i for i in images if i], refresh=args.refresh)
    resolved = sum(1 for i in images if sizes.get(i) is not None)
    mem = [parse_memory_mb(x or "") for x in tomls]
    cpu = [parse_cpus(x or "") for x in tomls]
    print(f"req_memory_mb: {sum(v is not None for v in mem)}/{len(mem)} declared")
    print(f"req_cpus:      {sum(v is not None for v in cpu)}/{len(cpu)} declared")
    print(f"base image sized: {resolved}/{len(images)}")
    if not args.write:
        return

    import pyarrow as pa
    import shutil

    tb = t["uncompressed_bytes"].to_pylist()
    disk = [est_disk_mb(sizes.get(i), b or 0) for i, b in zip(images, tb)]
    out = ROOT / args.dataset / "metadata" / "tasks.parquet"
    shutil.copy2(out, out.with_suffix(".parquet.bak"))
    for name, values, typ in (("req_cpus", cpu, pa.int32()),
                              ("req_memory_mb", mem, pa.int32()),
                              ("base_image", images, pa.string()),
                              ("est_disk_mb", disk, pa.int32())):
        if name in t.column_names:
            t = t.drop_columns([name])
        t = t.append_column(name, pa.array(values, type=typ))
    pq.write_table(t, out)
    print(f"wrote {out} (+4 columns, backup at {out.name}.bak)")


if __name__ == "__main__":
    main()
