# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Resolve a moving HF ref once and keep a preparation's inputs together."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

HF_REPO = "Fzz1/Tmax-Tasks-Clean"
HF_PARQUET = "splits/reaudit.parquet"
HF_TAR = "data/tasks-reaudit-00000.tar"
HF_PEAKS = "splits/reaudit_full.parquet"


class RefuseError(RuntimeError):
    """An input or snapshot invariant failed before publication."""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_record(path: str | Path) -> dict:
    path = Path(path)
    return {
        "path": str(path.absolute()),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def fetch_snapshot(*, revision: str, token: str | None, cache_dir: str | None) -> dict:
    """All three downloads use the resolved commit, even if main moves mid-fetch.

    Hub LFS digests are read from that commit's metadata, never from source-code
    constants. Non-LFS files still get a recorded SHA256; package validation is
    performed by the preparer independently of these transport checks.
    """
    from huggingface_hub import hf_hub_download, HfApi

    info = HfApi(token=token).dataset_info(
        HF_REPO, revision=revision, files_metadata=True
    )
    if not isinstance(info.sha, str) or not re.fullmatch(r"[0-9a-f]{40}", info.sha):
        raise RefuseError("Hub did not resolve the dataset to a commit")
    entries = {s.rfilename: s for s in info.siblings}
    files = {}
    for name in (HF_PARQUET, HF_TAR, HF_PEAKS):
        if name not in entries:
            raise RefuseError(f"{info.sha} lacks {name}")
        path = hf_hub_download(
            HF_REPO,
            name,
            repo_type="dataset",
            revision=info.sha,
            token=token,
            cache_dir=cache_dir,
        )
        record = file_record(path)
        lfs = entries[name].lfs
        expected = (
            (lfs.get("sha256") if isinstance(lfs, dict) else lfs.sha256)
            if lfs
            else None
        )
        if expected is not None and record["sha256"] != expected:
            raise RefuseError(
                f"{name}: downloaded bytes differ from Hub digest at {info.sha}"
            )
        files[name] = record
    return {
        "repo": HF_REPO,
        "requested_revision": revision,
        "revision": info.sha,
        "files": files,
    }


def validate_peaks(path: str, task_ids: set[str]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    needed = {
        "task_id": pa.types.is_string,
        "peak_ram_mb": None,
        "peak_disk_mb": None,
    }
    for name in (
        "peak_ram_mb_censored",
        "peak_ram_is_measurement",
        "peak_disk_is_measurement",
    ):
        if name in table.column_names:
            needed[name] = pa.types.is_boolean
    for name, predicate in needed.items():
        if name not in table.column_names:
            raise RefuseError(f"peaks file lacks {name}")
        kind = table.schema.field(name).type
        if name == "task_id":
            valid = pa.types.is_string(kind) or pa.types.is_large_string(kind)
        elif predicate is None:
            valid = (
                pa.types.is_integer(kind)
                or pa.types.is_floating(kind)
                or pa.types.is_null(kind)
            )
        else:
            valid = predicate(kind) or pa.types.is_null(kind)
        if not valid:
            raise RefuseError(f"peaks column {name} has incompatible type {kind}")
    ids = table.column("task_id").to_pylist()
    if len(ids) != len(set(ids)) or set(ids) != task_ids:
        raise RefuseError("peaks and split must have identical unique task IDs")


def publish_sources(snapshot: dict, tasks: str, source_dir: str) -> dict[str, str]:
    """Publish a fresh version directory; never overwrite an experiment's sources.

    ``tasks`` is the full package tree already checked and extracted by prepare().
    The usual corpus names are nested under the revision so new_root's symlinks
    resolve to this version, not to a mutable 'latest' directory.
    """
    revision = snapshot["revision"]
    if revision is None:
        raise RefuseError("--source-dir requires a Hub-resolved revision")
    root = Path(source_dir).absolute()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / revision
    if dest.exists():
        raise RefuseError(
            f"source snapshot already exists: {dest}; reuse it without overwriting"
        )
    sources = {
        name: str(dest / "data/sources" / name)
        for name in ("tmax-clean", "tmax-extract")
    }
    with tempfile.TemporaryDirectory(prefix=".reaudit-", dir=root) as tmp:
        stage = Path(tmp)
        for name, record in snapshot["files"].items():
            target = stage / "data/sources/tmax-clean" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(record["path"], target)
            if sha256_file(target) != record["sha256"]:
                raise RefuseError(f"source bytes changed while copying {name}")
        shutil.copytree(tasks, stage / "data/sources/tmax-extract/tasks")
        (stage / "snapshot.json").write_text(
            json.dumps({**snapshot, "sources": sources}, indent=2) + "\n"
        )
        stage.rename(dest)
    return sources
