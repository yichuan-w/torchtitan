#!/usr/bin/env python3
"""Replace individual task packages inside the published dataset tar.

Rebuilding the whole shard to ship a handful of repairs re-derives every other
task from whatever the build inputs happen to be today, which is a much larger
change than the one intended and much harder to review. This swaps only the
named tasks and copies the rest through byte for byte, then reports what moved
so the diff can be checked before the upload.

  patch_dataset_tar.py --tar tasks-00000.tar --tasks-dir <pool> \
      --ids tw_10981 tw_17818 ... --out tasks-00000.new.tar
"""
from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", required=True)
    ap.add_argument("--tasks-dir", required=True,
                    help="directory holding the current <task_id>/ packages")
    ap.add_argument("--ids", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    pool = Path(a.tasks_dir)
    wanted = set(a.ids)
    missing = [t for t in wanted if not (pool / t).is_dir()]
    if missing:
        raise SystemExit(f"no package directory for: {missing}")

    old_files: dict[str, bytes] = {}
    with tarfile.open(a.tar) as src, tarfile.open(a.out, "w") as dst:
        seen = set()
        for member in src:
            parts = member.name.split("/")
            tid = parts[1] if len(parts) > 2 and parts[0] == "tasks" else None
            if tid in wanted:
                # Held for the comparison below, then written from the pool.
                if member.isfile():
                    f = src.extractfile(member)
                    old_files[member.name] = f.read() if f else b""
                seen.add(tid)
                continue
            dst.addfile(member, src.extractfile(member) if member.isfile() else None)

        absent = sorted(wanted - seen)
        if absent:
            print(f"  not in the tar, will be added: {absent}")

        new_files: dict[str, bytes] = {}
        for tid in sorted(wanted):
            for path in sorted((pool / tid).rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(pool / tid)
                # The working copies have the canary header stripped out of
                # instruction.md so it does not leak into training text; the
                # published dataset ships that header, and the whole point of a
                # canary is that it travels with the data. instruction.md.bak-canary
                # is the version that still carries it, so that is what goes into
                # the shard, under the real name, and the .bak file itself is not
                # shipped as a second copy. Copying the working file through
                # instead would silently delete the marker from every task
                # touched. Other files keep their canary line in both places and
                # copy through unchanged.
                if rel.name.endswith(".bak-canary"):
                    continue
                # Build artefacts the working copy accumulates from running the
                # tests on the host. They are not part of the task and would
                # ship a compiled copy of the verifier alongside its source.
                if "__pycache__" in rel.parts or rel.suffix in (".pyc", ".pyo"):
                    continue
                if rel.name == "instruction.md":
                    canaried = path.with_name("instruction.md.bak-canary")
                    if canaried.exists():
                        path = canaried
                arc = f"tasks/{tid}/{rel}"
                data = path.read_bytes()
                new_files[arc] = data
                info = tarfile.TarInfo(arc)
                info.size = len(data)
                info.mode = 0o755 if path.suffix == ".sh" else 0o644
                dst.addfile(info, io.BytesIO(data))

    unmarked = [p for p, d in new_files.items()
                if p.endswith("/instruction.md")
                and b"harbor-canary" not in d]
    if unmarked:
        raise SystemExit(
            "refusing to write: these instruction.md would ship without the "
            f"canary header: {unmarked}")

    added = sorted(set(new_files) - set(old_files))
    removed = sorted(set(old_files) - set(new_files))
    changed = sorted(p for p in set(old_files) & set(new_files)
                     if old_files[p] != new_files[p])
    print(f"tasks patched: {len(wanted)}")
    print(f"  files added:   {len(added)}")
    for p in added:
        print(f"    + {p}")
    print(f"  files removed: {len(removed)}")
    for p in removed:
        print(f"    - {p}")
    print(f"  files changed: {len(changed)}")
    for p in changed:
        print(f"    ~ {p}  {digest(old_files[p])} -> {digest(new_files[p])}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
