# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Bind a frozen data release to verified images, retaining pre-test provenance."""

import argparse
import concurrent.futures
import copy
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import data_release as release
import prebuild_images as images


def selected(row, entry, batch, source_sha):
    key = images.image_key(row)
    if entry["status"] != "verified":
        raise ValueError(f"{key}: image is not verified")
    out = Path(entry.get("attempt_dir", batch / "tasks" / key))
    if entry.get("verification"):
        out = Path(entry["verification"]).parent
    verification = json.loads((out / "verification.json").read_text())
    if (
        verification["exit_code"] != 0
        or verification["input"]["row"] != row
        or verification["input"]["release_sha256"] != source_sha
        or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", verification["image"])
    ):
        raise ValueError(f"{key}: verification does not match frozen input")
    evidence = {"verification_sha256": release.digest(out / "verification.json")}
    if (out / "owner-repair.json").exists():
        semantic = json.loads((out / "semantic-validation.json").read_text())
        if not all(
            semantic[p]["verdict"]["ok"] and semantic[p]["verdict"]["reward"] == reward
            for p, reward in [("oracle", 1), ("null", 0)]
        ):
            raise ValueError(f"{key}: owner repair lacks oracle/null acceptance")
        evidence["owner_semantic_sha256"] = release.digest(
            out / "semantic-validation.json"
        )
    return verification["image"], evidence


def check_hook(row, image, out):
    from daytona import CreateSandboxFromImageParams, Resources

    md = row["metadata"]
    tm = md.get("tmax", {})
    script = tm.get("pre_test_sh")
    if not script:
        return None
    stamped, episode = tm.get("pretest_env_identity"), tm.get(
        "pretest_episode_env_identity"
    )
    if not stamped or stamped != episode:
        raise ValueError(
            f"{md['instance_id']}: original pre-test identity does not match"
        )
    expected = {
        "image": image,
        "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "stamped_identity": stamped,
        "source_episode_identity": episode,
    }
    result_path = out / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if (
            any(result.get(k) != v for k, v in expected.items())
            or result.get("exit_code") != 0
        ):
            raise ValueError("pre-test checkpoint differs from input")
        if not (out / "deleted.json").exists():
            raise ValueError("pre-test checkpoint has no confirmed deletion")
        return result
    attempt = out / f"attempt-{time.time_ns()}"
    attempt.mkdir(parents=True)
    images.log(attempt, "pretest-start", task=md["instance_id"], **expected)
    sb = images.create_sandbox(
        images.get_client(),
        CreateSandboxFromImageParams(
            image=image,
            os_user="root",
            resources=Resources(
                cpu=md["daytona_cpu"],
                memory=md["daytona_mem_gb"],
                disk=md["daytona_disk_gb"],
            ),
            ephemeral=True,
            auto_stop_interval=5,
            ttl_minutes=15,
        ),
        attempt,
        timeout=600,
    )
    release.write_json(attempt / "sandbox.json", {"id": sb.id})
    try:
        result = sb.process.exec(
            "bash -lc " + shlex.quote(script), cwd=md.get("workdir"), timeout=120
        )
        (attempt / "pretest.log").write_text(result.result or "")
        evidence = {**expected, "exit_code": result.exit_code}
        release.write_json(attempt / "result.json", evidence)
        if result.exit_code != 0:
            raise ValueError(f"{md['instance_id']}: original pre-test failed on digest")
    finally:
        images.delete_confirmed(sb, attempt, "deleted.json")
    shutil.copyfile(attempt / "deleted.json", out / "deleted.json")
    release.write_json(result_path, evidence)
    images.log(attempt, "pretest-end", exit_code=0)
    return evidence


def bind_row(row, image, evidence, hook):
    result = copy.deepcopy(row)
    md = result["metadata"]
    provenance = {k: md.pop(k, None) for k in ("image", "dockerfile", "build_context")}
    md["image"] = image
    # Keep the original pin identities: fresh execution proves those pins on
    # this digest. Deriving a new stamp from the digest would defeat the guard.
    md["prebuilt_provenance"] = {
        "source_build": provenance,
        **evidence,
        "pretest": hook,
    }
    return result


def build(source, batch, out, workers=1000):
    manifest = release.verify(source)
    rows = list(map(json.loads, (source / "mix.jsonl").read_text().splitlines()))
    ledger = json.loads((batch / "budget.json").read_text())
    if set(ledger) != {images.image_key(row) for row in rows}:
        raise ValueError("batch membership differs from frozen release")
    inputs = [
        (
            row,
            *selected(
                row, ledger[images.image_key(row)], batch, manifest["release_sha256"]
            ),
        )
        for row in rows
    ]
    out.mkdir(parents=True, exist_ok=True)
    images.log(
        out,
        "release-start",
        rows=len(rows),
        workers=workers,
        source_sha256=manifest["release_sha256"],
    )

    def prepare(item):
        row, image, evidence = item
        key = images.image_key(row)
        try:
            hook = check_hook(row, image, out / "hooks" / key)
            return bind_row(row, image, evidence, hook)
        except Exception as error:
            images.log(
                out,
                "release-row-failed",
                task=row["metadata"]["instance_id"],
                error_type=type(error).__name__,
            )
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        bound = list(pool.map(prepare, inputs))
    stage = out / "stage"
    if stage.exists():
        raise ValueError(
            "release stage already exists; retain it and use a new output directory"
        )
    shutil.copytree(source, stage)
    (stage / "mix.jsonl").write_bytes(b"".join(release.encoded(row) for row in bound))
    info = {k: v for k, v in manifest.items() if k not in ("files", "release_sha256")}
    info.update(
        source_release_sha256=manifest["release_sha256"],
        image_reproducibility="verified immutable OCI digests",
        prebuilt_code_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True
        ).strip(),
        pretest_verified=sum(
            bool(r["metadata"]["prebuilt_provenance"]["pretest"]) for r in bound
        ),
    )
    release.write_json(stage / "mix.manifest.json", info)
    final = {
        **info,
        "files": {
            str(p.relative_to(stage)): release.digest(p)
            for p in sorted(stage.rglob("*"))
            if p.is_file() and p != stage / "manifest.json"
        },
    }
    sha = hashlib.sha256(release.encoded(final)).hexdigest()
    release.write_json(stage / "manifest.json", {**final, "release_sha256": sha})
    release.verify(stage)
    destination = out / "releases" / sha
    destination.parent.mkdir(exist_ok=True)
    stage.rename(destination)
    release.write_json(
        out / "result.json",
        {
            "release_sha256": sha,
            "path": str(destination),
            "rows": len(bound),
            "pretest_verified": info["pretest_verified"],
        },
    )
    images.log(out, "release-complete", release_sha256=sha)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1000)
    args = parser.parse_args()
    build(args.source, args.batch, args.out, args.workers)
