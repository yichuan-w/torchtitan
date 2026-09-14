#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build, publish and retrieve a data release from fixed upstream commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pack_to_dataset as pack


def encoded(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def extract(archive, target):
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        for member in bundle:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"unsafe archive member: {member.name}")
            dest = target / path
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, dest.open("wb") as output:
                    shutil.copyfileobj(source, output)
                dest.chmod(member.mode & 0o777)


def check_config(config):
    if set(config) != {"sources", "seed"} or not isinstance(config["seed"], int):
        raise ValueError("configuration requires sources and integer seed")
    names = set()
    for source in config["sources"]:
        required = {
            "name",
            "repo",
            "revision",
            "adapter",
            "metadata",
            "archives",
            "count",
        }
        if set(source) != required:
            raise ValueError(f"source keys must be {sorted(required)}")
        if not re.fullmatch(r"[a-z0-9_-]+", source["name"]) or source["name"] in names:
            raise ValueError("source names must be unique path components")
        names.add(source["name"])
        if not re.fullmatch(r"[0-9a-f]{40}", source["revision"]):
            raise ValueError(
                "upstream revision must be a full commit SHA, not a branch"
            )
        if source["adapter"] not in {"swe_peaks", "tmax_reaudit"}:
            raise ValueError("supported adapters: swe_peaks, tmax_reaudit")
        count = source["count"]
        if count is not None and (type(count) is not int or count <= 0):
            raise ValueError("count must be a positive integer or null for all rows")
        for filename in [source["metadata"], *source["archives"]]:
            if Path(filename).is_absolute() or ".." in Path(filename).parts:
                raise ValueError("source paths must stay inside their repository")
    if not names:
        raise ValueError("at least one source is required")


def fetch_source(source, cache, token):
    from huggingface_hub import hf_hub_download, HfApi

    info = HfApi(token=token).dataset_info(
        source["repo"], revision=source["revision"], files_metadata=True
    )
    if info.sha != source["revision"]:
        raise ValueError("upstream did not resolve to the requested commit")
    entries = {item.rfilename: item for item in info.siblings}
    files = {}
    filenames = [source["metadata"], *source["archives"]]
    cache_manifest = "metadata/cache_manifest.jsonl"
    if cache_manifest in entries:
        filenames.append(cache_manifest)
    for filename in filenames:
        local = Path(
            hf_hub_download(
                source["repo"],
                filename,
                repo_type="dataset",
                revision=info.sha,
                cache_dir=cache,
                token=token,
            )
        )
        lfs = entries[filename].lfs
        expected = (
            (lfs.get("sha256") if isinstance(lfs, dict) else lfs.sha256)
            if lfs
            else None
        )
        actual = digest(local)
        if expected and expected != actual:
            raise ValueError(f"upstream checksum mismatch: {filename}")
        files[filename] = {"path": local, "sha256": actual}
        if filename == cache_manifest:
            for line in local.read_text().splitlines():
                item = json.loads(line)
                if (
                    not item["file"].startswith("caches/")
                    or ".." in Path(item["file"]).parts
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                ):
                    raise ValueError("invalid offline cache manifest entry")
                filenames.append(item["file"])
    return files


def resources(source, record):
    sizing = pack._tmax_modules("resource_sizing")
    if source["adapter"] == "tmax_reaudit":
        import math

        result = {"daytona_cpu": int(record["req_cpus"])}
        for column, field, cap in (
            ("req_memory_mb", "daytona_mem_gb", 8),
            ("est_disk_mb", "daytona_disk_gb", 10),
        ):
            value = record[column]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"invalid published allocation: {column}")
            value = math.ceil(value / 1024)
            if value > cap:
                raise ValueError(f"published allocation exceeds platform cap: {column}")
            result[field] = value
        return result
    result = {"daytona_cpu": 1}
    for column, field, cap in (
        ("peak_ram_mb", "daytona_mem_gb", 8),
        ("peak_disk_mb", "daytona_disk_gb", 10),
    ):
        size = sizing.measured_gib(record[column], cap)
        result[field] = size if size is not None else 2
    return result


def prepare_source(source, files, stage, checkpoints, log):
    import pyarrow.parquet as pq

    home = stage / "sources" / source["name"]
    home.mkdir(parents=True, exist_ok=True)
    for filename in source["archives"]:
        extract(files[filename]["path"], home)
    metadata = pq.read_table(files[source["metadata"]]["path"]).to_pylist()
    id_key = "task_id" if metadata and "task_id" in metadata[0] else "instance_id"
    ids = [record[id_key] for record in metadata]
    if len(ids) != len(set(ids)) or any(
        not isinstance(tid, str) or Path(tid).name != tid for tid in ids
    ):
        raise ValueError("task IDs must be unique path components")
    actual_ids = {p.name for p in (home / "tasks").iterdir() if p.is_dir()}
    if actual_ids != set(ids):
        raise ValueError(f"{source['name']}: package and metadata membership differ")
    shutil.copyfile(files[source["metadata"]]["path"], home / "metadata.parquet")
    caches = {}
    cache_manifest = files.get("metadata/cache_manifest.jsonl")
    if cache_manifest:
        for line in cache_manifest["path"].read_text().splitlines():
            item = json.loads(line)
            if files[item["file"]]["sha256"] != item["sha256"]:
                raise ValueError(f"cache checksum mismatch: {item['file']}")
            caches[item["image"]] = item
            dest = home / item["file"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(files[item["file"]]["path"], dest)
    rows = []
    for record in sorted(metadata, key=lambda r: r[id_key]):
        tid = record[id_key]
        task = home / "tasks" / tid
        if record.get("prefetched_cache_required"):
            item = caches.get(record.get("image") or record.get("base_image"))
            if item is None:
                raise ValueError(f"{tid}: required offline cache is missing")
            dockerfile = task / "environment/Dockerfile"
            url = f"https://huggingface.co/datasets/{source['repo']}/resolve/{source['revision']}/{item['file']}"
            dockerfile.write_text(
                dockerfile.read_text().rstrip()
                + "\n"
                + f"ADD {url} /tmp/seed-cache.tar.gz\n"
                + f"RUN echo '{item['sha256']}  /tmp/seed-cache.tar.gz' | sha256sum -c - "
                + "&& tar -xzf /tmp/seed-cache.tar.gz -C / && rm /tmp/seed-cache.tar.gz\n"
            )
        checkpoint = checkpoints / source["name"] / (tid + ".json")
        log(source=source["name"], task=tid, status="start")
        if checkpoint.exists():
            row = json.loads(checkpoint.read_text())
            log(source=source["name"], task=tid, status="resume")
        else:
            pretest = (
                record.get("pre_test_sh") or "",
                record.get("pre_test_env_identity") or "",
            )
            protected = None
            if source["adapter"] == "tmax_reaudit":
                protected = pack.Protected.from_cells(
                    record.get("protected_paths"), record.get("protected_cmds")
                )
            row = pack.to_row(
                str(task),
                inject_agent_runtime=True,
                pretest=pretest,
                protected=protected,
            )
            md = row["metadata"]
            md.update(resources(source, record))
            md["corpus"] = source["name"]
            md["source"] = {
                "repo": source["repo"],
                "revision": source["revision"],
                "adapter": source["adapter"],
            }
            md["resource_inputs"] = {
                key: record.get(key)
                for key in (
                    "peak_ram_mb",
                    "peak_disk_mb",
                    "req_cpus",
                    "req_memory_mb",
                    "est_disk_mb",
                )
            }
            md["rev"] = 0
            if record.get("validated_test_timeout_s"):
                md["verifier_timeout_sec"] = record["validated_test_timeout_s"]
            write_json(checkpoint, row)
            log(source=source["name"], task=tid, status="complete")
        rows.append(row)
    count = source["count"]
    if count is not None:
        if count > len(rows):
            raise ValueError("requested source count exceeds available tasks")
        rows = sorted(
            rows,
            key=lambda row: hashlib.sha256(
                encoded([source["name"], row["label"], source["selection_seed"]])
            ).hexdigest(),
        )[:count]
    return rows


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    release_id = manifest.pop("release_sha256")
    if hashlib.sha256(encoded(manifest)).hexdigest() != release_id:
        raise ValueError("release manifest digest mismatch")
    expected_files = set(manifest["files"]) | {"manifest.json"}
    actual_files = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual_files != expected_files:
        raise ValueError("release file membership differs from manifest")
    for name, expected in manifest["files"].items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or digest(root / name) != expected
        ):
            raise ValueError(f"release file digest mismatch: {name}")
    return {**manifest, "release_sha256": release_id}


def build(config, out, cache=None, token=None):
    check_config(config)
    if os.environ.get("SWE_EMIT_AGENT_TIMEOUT", "0") != "0":
        raise ValueError("release preparation requires SWE_EMIT_AGENT_TIMEOUT=0")
    checkout = Path(pack._checkout_root())
    code = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=checkout,
        text=True,
    ).strip():
        raise ValueError("commit preparation code before producing a release")
    identity = {
        "config": config,
        "code_commit": code,
        "uv_lock_sha256": digest(checkout / "uv.lock"),
    }
    key = hashlib.sha256(encoded(identity)).hexdigest()
    work = out / "work" / key
    stage = work / "stage"
    stage.mkdir(parents=True, exist_ok=True)
    with (work / "build.jsonl").open("a", buffering=1) as stream:

        def log(**event):
            stream.write(
                json.dumps({"time": datetime.now(timezone.utc).isoformat(), **event})
                + "\n"
            )

        log(status="start", **identity)
        rows, provenance = [], []
        for source in config["sources"]:
            log(source=source["name"], status="fetch")
            files = fetch_source(source, cache, token)
            provenance.append(
                {
                    **source,
                    "files": {name: record["sha256"] for name, record in files.items()},
                }
            )
            rows.extend(
                prepare_source(
                    {**source, "selection_seed": config["seed"]},
                    files,
                    stage,
                    work / "checkpoints",
                    log,
                )
            )
        ids = [row["metadata"]["instance_id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("task ID collision across sources")
        rows.sort(
            key=lambda row: hashlib.sha256(
                encoded([config["seed"], row["metadata"]["corpus"], row["label"]])
            ).hexdigest()
        )
        (stage / "mix.jsonl").write_bytes(b"".join(encoded(row) for row in rows))
        write_json(stage / "config.json", config)
        inputs = {
            **identity,
            "sources": provenance,
            "rows": len(rows),
            "runtime": "terminus",
            "tmux": "required_at_image_build",
            "image_reproducibility": "upstream image tags and package repositories may change",
        }
        write_json(stage / "mix.manifest.json", inputs)
        manifest = {
            **inputs,
            "files": {
                str(p.relative_to(stage)): digest(p)
                for p in sorted(stage.rglob("*"))
                if p.is_file() and p != stage / "manifest.json"
            },
        }
        release_id = hashlib.sha256(encoded(manifest)).hexdigest()
        write_json(stage / "manifest.json", {**manifest, "release_sha256": release_id})
        verify(stage)
        destination = out / "releases" / release_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            verify(destination)
        else:
            shutil.copytree(stage, destination)
        log(status="complete", release_sha256=release_id, rows=len(rows))
        return destination


def publish(root, repo, token=None):
    from huggingface_hub import CommitOperationAdd, hf_hub_download, HfApi

    manifest = verify(root)
    release_id = manifest["release_sha256"]
    archive = root.parent / (release_id + ".tar")
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as bundle:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            member = tarfile.TarInfo(str(path.relative_to(root)))
            member.size = path.stat().st_size
            member.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            with path.open("rb") as stream:
                bundle.addfile(member, stream)
    path_in_repo = f"releases/{release_id}/release.tar"
    api = HfApi(token=token)
    info = api.dataset_info(repo)
    result = api.create_commit(
        repo,
        repo_type="dataset",
        parent_commit=info.sha,
        commit_message=f"Publish prepared data {release_id[:12]}",
        operations=[
            CommitOperationAdd(path_in_repo=path_in_repo, path_or_fileobj=str(archive)),
            CommitOperationAdd(
                path_in_repo=f"releases/{release_id}/manifest.json",
                path_or_fileobj=str(root / "manifest.json"),
            ),
        ],
    )
    downloaded = hf_hub_download(
        repo, path_in_repo, repo_type="dataset", revision=result.oid, token=token
    )
    if digest(downloaded) != digest(archive):
        raise ValueError("published archive differs from local release")
    return {
        "repo": repo,
        "revision": result.oid,
        "release_sha256": release_id,
        "archive_sha256": digest(archive),
    }


def fetch_release(repo, revision, release_id, out, token=None):
    from huggingface_hub import hf_hub_download

    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(
        r"[0-9a-f]{64}", release_id
    ):
        raise ValueError("fetch requires full publication commit and release SHA256")
    if out.exists():
        raise ValueError("fetch destination must be new")
    path = hf_hub_download(
        repo,
        f"releases/{release_id}/release.tar",
        repo_type="dataset",
        revision=revision,
        token=token,
    )
    extract(path, out)
    if verify(out)["release_sha256"] != release_id:
        raise ValueError("downloaded release is not the requested release")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--config", required=True, type=Path)
    build_parser.add_argument("--out", required=True, type=Path)
    build_parser.add_argument("--cache-dir")
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--release", required=True, type=Path)
    publish_parser.add_argument("--repo", required=True)
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("--repo", required=True)
    fetch_parser.add_argument("--revision", required=True)
    fetch_parser.add_argument("--release-sha256", required=True)
    fetch_parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    token = args.token_file.read_text().strip() if args.token_file else None
    if args.command == "build":
        result = str(
            build(json.loads(args.config.read_text()), args.out, args.cache_dir, token)
        )
    elif args.command == "publish":
        result = publish(args.release, args.repo, token)
    else:
        result = str(
            fetch_release(
                args.repo, args.revision, args.release_sha256, args.out, token
            )
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
