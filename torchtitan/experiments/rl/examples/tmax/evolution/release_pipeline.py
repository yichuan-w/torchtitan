# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Prepare, build, validate and publish a data release from a project config.

Run the controller on the credential-owning machine. The remote worker only
receives Daytona settings and short-lived, repository-scoped registry tokens.
Restart the same command to reuse completed stages and per-image evidence.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import shlex
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import data_release as release
import prebuild_batch
import prebuild_images as images
import prebuilt_release


def frozen(path, value):
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"inputs changed: {path}; use a new output directory")
    else:
        release.write_json(path, value)


def worker(config, out):
    out.mkdir(parents=True, exist_ok=True)
    with (out / "pipeline.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen(out / "inputs.json", config)
        images.log(out, "phase-start", phase="prepare")
        if config.get("prepared_source"):
            source = Path(config["prepared_source"])
            manifest = release.verify(source)
            if manifest["release_sha256"] != config["prepared_sha256"]:
                raise ValueError("prepared release hash differs from config")
            if json.loads((source / "config.json").read_text()) != config["sources"]:
                raise ValueError("prepared release sources differ from config")
        else:
            source = release.build(config["sources"], out / "prepared")
        release.write_json(out / "prepared.json", {"path": str(source)})
        images.log(out, "phase-end", phase="prepare")
        batch = Path(config.get("batch_dir", out / "images"))
        images.log(out, "phase-start", phase="images", workers=config["workers"])
        summary = (
            json.loads((batch / "summary.json").read_text())
            if (batch / "summary.json").exists()
            else {}
        )
        completed = summary.get("counts", {})
        if not (
            completed.get("verified")
            and completed.get("failed") == 0
            and completed.get("running") == 0
            and summary.get("unattempted") == 0
        ):
            prebuild_batch.run(
                SimpleNamespace(
                    release=source,
                    out=batch,
                    repository=config["image_repository"],
                    token_file=out / "registry.token",
                    workers=config["workers"],
                    budget=None,
                    retry_failed=True,
                    task=None,
                    limit=None,
                )
            )
        ledger = json.loads((batch / "budget.json").read_text())
        if any(entry["status"] != "verified" for entry in ledger.values()):
            raise RuntimeError("image failures remain; rerun to retry failed attempts")
        images.log(out, "phase-end", phase="images")
        images.log(out, "phase-start", phase="bind")
        bound = Path(config.get("digest_dir", out / "digest"))
        result_file = bound / "result.json"
        if result_file.exists():
            result = json.loads(result_file.read_text())
            destination = Path(result["path"])
            manifest = release.verify(destination)
            if (
                manifest["source_release_sha256"]
                != release.verify(source)["release_sha256"]
            ):
                raise ValueError("digest checkpoint belongs to a different source")
        else:
            destination = prebuilt_release.build(
                source, batch, bound, config["workers"]
            )
            result = json.loads(result_file.read_text())
        release.write_json(out / "result.json", result)
        if (bound / "publication.json").exists():
            publication = json.loads((bound / "publication.json").read_text())
            if publication["release_sha256"] != result["release_sha256"]:
                raise ValueError("publication receipt does not match digest release")
            release.write_json(out / "publication.json", publication)
        images.log(
            out, "phase-end", phase="bind", release_sha256=result["release_sha256"]
        )


def remote(config, script):
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            config["ssh_host"],
            "bash -s",
        ],
        input=script,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        # Remote failures can include a command carrying the scoped token.
        raise RuntimeError(f"remote command failed with exit {result.returncode}")
    return result.stdout


def scoped_token(config):
    repository = config["image_repository"].removeprefix("ghcr.io/")
    if repository == config["image_repository"]:
        raise ValueError("the registry token issuer supports ghcr.io repositories")
    secret = Path(config["github_token_file"]).read_text().strip()
    credentials = base64.b64encode(
        (config["github_user"] + ":" + secret).encode()
    ).decode()
    url = "https://ghcr.io/token?" + urllib.parse.urlencode(
        {"service": "ghcr.io", "scope": f"repository:{repository}:pull,push"}
    )
    request = urllib.request.Request(
        url, headers={"Authorization": "Basic " + credentials}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["token"]


def remote_config(config):
    return {
        key: config[key]
        for key in (
            "sources",
            "image_repository",
            "workers",
            "prepared_source",
            "prepared_sha256",
            "batch_dir",
            "digest_dir",
        )
        if key in config
    }


def controller(config, out):
    out.mkdir(parents=True, exist_ok=True)
    frozen(out / "inputs.json", config)
    if (out / "publication.json").exists():
        images.log(
            out,
            "pipeline-complete",
            **json.loads((out / "publication.json").read_text()),
        )
        return
    root = config["remote_out"]
    checkout = config["remote_checkout"]
    unit = "data-release-" + hashlib.sha256(root.encode()).hexdigest()[:16]
    effective = remote_config(config)
    env = {}
    for line in Path(config["daytona_env_file"]).read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.startswith("DAYTONA_"):
            env[key] = shlex.split(value, comments=True)[0]
    if not env.get("DAYTONA_API_KEY"):
        raise ValueError("project Daytona credentials are missing")
    images.log(
        out,
        "pipeline-start",
        config_sha256=hashlib.sha256(release.encoded(config)).hexdigest(),
    )
    code = remote(
        config, "git -C " + shlex.quote(checkout) + " rev-parse HEAD\n"
    ).strip()
    if code != config["code_commit"]:
        raise ValueError("remote checkout is not the configured commit")
    if remote(
        config,
        "git -C "
        + shlex.quote(checkout)
        + " status --porcelain --untracked-files=no\n",
    ).strip():
        raise ValueError("remote checkout has uncommitted changes")
    remote(config, "umask 077\nmkdir -p " + shlex.quote(root) + "\n")

    def refresh():
        token = scoped_token(config)
        path = root + "/registry.token"
        remote(
            config,
            "umask 077\nprintf %s "
            + shlex.quote(token)
            + " > "
            + shlex.quote(path + ".partial")
            + "\nmv "
            + shlex.quote(path + ".partial")
            + " "
            + shlex.quote(path)
            + "\n",
        )

    done = remote(
        config,
        "test ! -f "
        + shlex.quote(root + "/result.json")
        + " || cat "
        + shlex.quote(root + "/result.json")
        + "\n",
    ).strip()
    if not done:
        refresh()
        state = remote(
            config, "systemctl --user is-active " + unit + ".service || true\n"
        ).strip()
        if state != "active":
            command = [
                config["remote_python"],
                "torchtitan/experiments/rl/examples/tmax/evolution/release_pipeline.py",
                "--worker",
                "--config",
                root + "/config.json",
                "--out",
                root,
            ]
            launch = [
                "systemd-run",
                "--user",
                "--collect",
                "--unit=" + unit,
                "-p",
                "LimitNOFILE=5120",
                "--working-directory=" + checkout,
                "-p",
                "StandardOutput=append:" + root + "/worker.log",
                "-p",
                "StandardError=inherit",
                "--setenv=PYTHONPATH=" + checkout,
                "--setenv=PYTHONUNBUFFERED=1",
            ]
            launch += ["--setenv=" + key for key in env]
            script = "set -eu\numask 077\n"
            script += (
                "\n".join(
                    "export " + key + "=" + shlex.quote(value)
                    for key, value in env.items()
                )
                + "\n"
            )
            script += (
                "printf %s "
                + shlex.quote(json.dumps(effective))
                + " > "
                + shlex.quote(root + "/config.json")
                + "\n"
            )
            remote(config, script + shlex.join(launch + command) + "\n")
        refreshed = time.monotonic()
        while True:
            state = remote(
                config, "systemctl --user is-active " + unit + ".service || true\n"
            ).strip()
            images.log(out, "worker-state", state=state, log=root + "/worker.log")
            if state != "active":
                break
            if time.monotonic() - refreshed >= 120:
                refresh()
                refreshed = time.monotonic()
            time.sleep(30)
        done = remote(
            config, "cat " + shlex.quote(root + "/result.json") + "\n"
        ).strip()
    result = json.loads(done)
    previous = remote(
        config,
        "test ! -f "
        + shlex.quote(root + "/publication.json")
        + " || cat "
        + shlex.quote(root + "/publication.json")
        + "\n",
    ).strip()
    local = out / "releases" / result["release_sha256"]
    local.mkdir(parents=True, exist_ok=True)
    images.log(
        out, "phase-start", phase="publish", release_sha256=result["release_sha256"]
    )
    subprocess.run(
        [
            "rsync",
            "-a",
            "--",
            config["ssh_host"] + ":" + result["path"] + "/",
            str(local) + "/",
        ],
        check=True,
    )
    release.verify(local)
    token = Path(config["hf_token_file"]).read_text().strip()
    if previous:
        from huggingface_hub import hf_hub_download

        publication = json.loads(previous)
        if (
            publication["repo"] != config["publish_repo"]
            or publication["release_sha256"] != result["release_sha256"]
        ):
            raise ValueError("existing publication belongs to different inputs")
        archive = hf_hub_download(
            publication["repo"],
            "releases/" + publication["release_sha256"] + "/release.tar",
            repo_type="dataset",
            revision=publication["revision"],
            token=token,
        )
        if release.digest(archive) != publication["archive_sha256"]:
            raise ValueError("existing published archive hash differs")
    else:
        publication = release.publish(local, config["publish_repo"], token)
    release.write_json(out / "publication.json", publication)
    remote(
        config,
        "umask 077\nprintf %s "
        + shlex.quote(json.dumps(publication))
        + " > "
        + shlex.quote(root + "/publication.json")
        + "\nrm -f "
        + shlex.quote(root + "/registry.token")
        + "\n",
    )
    images.log(out, "pipeline-complete", **publication)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text())
    settings.setdefault("workers", 1000)
    if args.worker:
        worker(settings, args.out)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / "controller.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            controller(settings, args.out)
