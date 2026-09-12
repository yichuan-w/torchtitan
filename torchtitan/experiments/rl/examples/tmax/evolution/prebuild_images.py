# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build prepared task images in Daytona and publish immutable registry references.

Run build and verify on the training host. Push reads a repository-scoped registry
bearer token from stdin; a long-lived registry credential is never needed there.
Each task has its own output directory. Completed stages can be resumed without
overwriting their inputs or results.
"""

import argparse
import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import data_release as release


def image_key(row):
    md = row["metadata"]
    return hashlib.sha256(
        release.encoded(
            {k: md.get(k) for k in ("image", "dockerfile", "build_context")}
        )
    ).hexdigest()


def log(out, event, **fields):
    item = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with (out / "progress.jsonl").open("a", buffering=1) as stream:
        stream.write(json.dumps(item) + "\n")
    print(json.dumps(item), flush=True)


def execute(sb, command, out, name, timeout=60):
    log(out, "start", stage=name)
    result = sb.process.exec(command, timeout=timeout)
    (out / (name + ".log")).write_text(result.result or "")
    log(out, "end", stage=name, exit_code=result.exit_code)
    if result.exit_code:
        raise RuntimeError(f"{name} failed; see {out / (name + '.log')}")
    return result.result


def build(source, task, out, repository, *, prepared=None):
    from daytona import CreateSandboxFromImageParams, Daytona, Image, Resources

    if prepared is None:
        manifest = release.verify(source)
        rows = [
            json.loads(line) for line in (source / "mix.jsonl").read_text().splitlines()
        ]
        row = next(row for row in rows if row["metadata"]["instance_id"] == task)
    else:
        manifest, row = prepared
    tag = repository + ":" + image_key(row)
    inputs = {
        "release_sha256": manifest["release_sha256"],
        "row": row,
        "tag": tag,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True
        ).strip(),
    }
    out.mkdir(parents=True, exist_ok=True)
    if (out / "input.json").exists():
        if json.loads((out / "input.json").read_text()) != inputs:
            raise ValueError("existing build belongs to different inputs")
        if (out / "built.json").exists():
            log(out, "resume", stage="build")
            return
        raise ValueError("incomplete build: inspect it and use a new attempt directory")
    release.write_json(out / "input.json", inputs)
    client = Daytona()
    log(out, "start", stage="builder", task=task)
    sb = client.create(
        CreateSandboxFromImageParams(
            image=Image.base("docker:27-dind").run_commands(
                "apk add --no-cache python3"
            ),
            resources=Resources(cpu=2, memory=4, disk=10),
            ephemeral=True,
            auto_stop_interval=30,
            ttl_minutes=120,
            labels={"purpose": "prebuilt-data-image", "input": image_key(row)},
        ),
        timeout=1200,
    )
    release.write_json(out / "builder.json", {"id": sb.id})
    md = row["metadata"]
    try:
        execute(
            sb, "nohup dockerd > /tmp/dockerd.log 2>&1 < /dev/null &", out, "daemon"
        )
        execute(
            sb,
            "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && exit 0; "
            "sleep 1; done; cat /tmp/dockerd.log; exit 1",
            out,
            "daemon-ready",
        )
        execute(sb, "mkdir -p /tmp/task-build", out, "context")
        for name, content in (md.get("build_context") or {}).items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or path.name == "Dockerfile":
                raise ValueError(f"invalid context path: {name}")
            parent = "/tmp/task-build/" + str(path.parent)
            execute(sb, "mkdir -p " + shlex.quote(parent), out, "context-directory")
            sb.fs.upload_file(
                base64.b64decode(content, validate=True), "/tmp/task-build/" + name
            )
        dockerfile = md.get("dockerfile") or "FROM " + md["image"] + "\n"
        sb.fs.upload_file(dockerfile.encode(), "/tmp/task-build/Dockerfile")
        command = "docker build -t " + shlex.quote(tag) + " /tmp/task-build"
        execute(sb, command, out, "build", timeout=900)
        details = execute(
            sb, "docker image inspect " + shlex.quote(tag), out, "inspect"
        )
        release.write_json(
            out / "built.json", {"id": sb.id, "image": json.loads(details)[0]}
        )
    except BaseException:
        sb.delete(timeout=120, wait=True)
        raise


PUSH_SCRIPT = """import http.client,json,socket,sys
class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.connect('/var/run/docker.sock')
connection=Connection('localhost',timeout=900)
connection.request('POST',sys.argv[2],headers={'X-Registry-Auth':sys.argv[1]})
response=connection.getresponse()
assert response.status==200,response.status
failed=False
for line in response:
    item=json.loads(line)
    if item.get('error'): failed=True
    print(json.dumps(item),flush=True)
sys.exit(1 if failed else 0)
"""


def push(out, token):
    from daytona import Daytona

    if (out / "publication.json").exists():
        log(out, "resume", stage="push")
        return
    inputs = json.loads((out / "input.json").read_text())
    built = json.loads((out / "built.json").read_text())
    client = Daytona()
    sb = client.get(built["id"])
    repository, tag = inputs["tag"].rsplit(":", 1)
    auth = base64.urlsafe_b64encode(
        json.dumps(
            {"serveraddress": repository.split("/")[0], "registrytoken": token}
        ).encode()
    ).decode()
    sb.fs.upload_file(PUSH_SCRIPT.encode(), "/tmp/push-image.py")
    command = shlex.join(
        ["python3", "/tmp/push-image.py", auth, f"/images/{repository}/push?tag={tag}"]
    )
    execute(sb, command, out, "push", timeout=900)
    raw = execute(
        sb,
        "docker image inspect --format '{{json .RepoDigests}}' "
        + shlex.quote(inputs["tag"]),
        out,
        "digest",
    )
    refs = [ref for ref in json.loads(raw) if ref.startswith(repository + "@sha256:")]
    if len(refs) != 1 or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", refs[0]):
        raise ValueError("registry did not return one immutable image reference")
    release.write_json(out / "publication.json", {"image": refs[0], "input": inputs})
    sb.delete(timeout=120, wait=True)
    release.write_json(out / "builder-deleted.json", {"id": sb.id})


def verify(out):
    from daytona import CreateSandboxFromImageParams, Daytona, Resources

    if (out / "verification.json").exists():
        log(out, "resume", stage="verify")
        return
    publication = json.loads((out / "publication.json").read_text())
    md = publication["input"]["row"]["metadata"]
    client = Daytona()
    log(out, "start", stage="fresh-boot", image=publication["image"])
    sb = client.create(
        CreateSandboxFromImageParams(
            image=publication["image"],
            resources=Resources(
                cpu=md["daytona_cpu"],
                memory=md["daytona_mem_gb"],
                disk=md["daytona_disk_gb"],
            ),
            ephemeral=True,
            auto_stop_interval=5,
            ttl_minutes=15,
        ),
        timeout=600,
    )
    release.write_json(out / "verifier-sandbox.json", {"id": sb.id})
    try:
        output = execute(
            sb,
            "tmux -V && tmux -L release-check new-session -d -s check && tmux -L release-check kill-server",
            out,
            "tmux",
        )
        release.write_json(
            out / "verification.json",
            {
                **publication,
                "tmux": output,
                "exit_code": 0,
                "scope": "fresh_digest_boot_and_tmux",
            },
        )
    finally:
        sb.delete(timeout=120, wait=True)
        release.write_json(out / "verifier-deleted.json", {"id": sb.id})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "push", "verify"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--repository")
    args = parser.parse_args()
    if args.action == "build":
        if not all((args.release, args.task, args.repository)):
            parser.error("build requires --release, --task and --repository")
        build(args.release, args.task, args.out, args.repository)
    elif args.action == "push":
        token = sys.stdin.read().strip()
        if not token:
            parser.error("push requires a scoped registry bearer token on stdin")
        push(args.out, token)
    else:
        verify(args.out)


if __name__ == "__main__":
    main()
