# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Repair the Docker Machine seed into a new package, preserving its source."""

import argparse
import json
import shutil
from pathlib import Path


START = r"""#!/bin/bash
set -euo pipefail
mkdir -p /run/sshd /root/.ssh /root/.docker/machine/machines/myvm1
ssh-keygen -A
if [ ! -f /root/.ssh/myvm1 ]; then
    ssh-keygen -q -t rsa -b 3072 -m PEM -N '' -f /root/.ssh/myvm1
    cat /root/.ssh/myvm1.pub >> /root/.ssh/authorized_keys
fi
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
/usr/sbin/sshd -p 2222 -o ListenAddress=127.0.0.1 -o PasswordAuthentication=no \
    -o PermitRootLogin=prohibit-password -o PubkeyAcceptedAlgorithms=+ssh-rsa
dockerd > /var/log/dockerd.log 2>&1 &
for attempt in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 1
done
docker info >/dev/null
docker swarm init --advertise-addr eth0
cat > /root/.docker/machine/machines/myvm1/config.json <<'JSON'
{"ConfigVersion":3,"Name":"myvm1","DriverName":"generic","Driver":{"MachineName":"myvm1","IPAddress":"127.0.0.1","SSHUser":"root","SSHPort":2222,"SSHKeyPath":"/root/.ssh/myvm1","StorePath":"/root/.docker/machine","EnginePort":2376},"HostOptions":{"EngineOptions":{},"SwarmOptions":{},"AuthOptions":{}}}
JSON
docker-machine ssh myvm1 'docker info >/dev/null'
touch /run/deployment-ready
wait
"""

REMOTE_TEST = """def test_remote_deployment():
    import json
    import time

    def remote(command):
        result = subprocess.run(
            ["docker-machine", "ssh", "myvm1", command],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    status = subprocess.run(
        ["docker-machine", "status", "myvm1"],
        capture_output=True, text=True, timeout=30,
    )
    assert status.returncode == 0 and status.stdout.strip() == "Running", (
        status.stdout + status.stderr
    )
    remote("test -d /root/data && test -f /root/docker-compose-WITH-REDIS.yml")
    expected = {"web": 5, "visualizer": 1, "redis": 1}
    names = ["getstartedlab_" + name for name in expected]
    services = json.loads(remote("docker service inspect " + " ".join(names)))
    assert len(services) == 3
    for service in services:
        short = service["Spec"]["Name"].removeprefix("getstartedlab_")
        assert service["Spec"]["Mode"]["Replicated"]["Replicas"] == expected[short]
    deadline = time.monotonic() + 120
    while True:
        running = {}
        for name in names:
            ids = remote("docker service ps --filter desired-state=running -q " + name).split()
            tasks = json.loads(remote("docker inspect " + " ".join(ids))) if ids else []
            running[name] = sum(task["Status"]["State"] == "running" for task in tasks)
        if running == {"getstartedlab_" + k: v for k, v in expected.items()}:
            break
        assert time.monotonic() < deadline, f"Services did not converge: {running}"
        time.sleep(2)
"""


def repair(source: Path, output: Path) -> None:
    """Write a separately versioned task with a real local deployment target."""
    if output.exists():
        raise FileExistsError(output)
    shutil.copytree(source, output)
    env = output / "environment"
    dockerfile = (env / "Dockerfile").read_text()
    dockerfile = dockerfile.replace(
        "    openssh-client \\\n",
        "    openssh-client \\\n    openssh-server \\\n    tmux \\\n    python3-pip \\\n",
    )
    dockerfile = dockerfile.replace(
        'CMD ["/bin/bash"]',
        "RUN pip3 install --no-cache-dir pytest==8.4.1 PyYAML==6.0.2 pytest-json-ctrf==0.3.5\n"
        "COPY start-services.sh /usr/local/bin/start-services.sh\n"
        "RUN chmod +x /usr/local/bin/start-services.sh\n"
        'ENTRYPOINT ["/usr/local/bin/start-services.sh"]',
    )
    (env / "Dockerfile").write_text(dockerfile)
    (env / "start-services.sh").write_text(START)
    for name in ("instruction.md", "instruction.md.bak-canary"):
        instruction = output / name
        if instruction.exists():
            instruction.write_text(
                instruction.read_text().replace("6379:6739", "6379:6379")
                + "\nThe environment provides `myvm1` as a local SSH deployment target with a "
                "running single-node Docker Swarm. Its home directory is `/root`. "
                "Wait for `docker-machine ssh myvm1 'docker info'` to succeed before deployment.\n"
            )
    solution = output / "solution" / "solve.sh"
    solution.write_text(
        solution.read_text()
        .replace("6379:6739", "6379:6379")
        .replace(" || true", "")
        .replace(
            "set -e\n",
            "set -e\ntimeout 120 bash -c 'until test -f /run/deployment-ready; do sleep 1; done'\n",
        )
    )
    tests = output / "tests" / "test_state.py"
    content = tests.read_text().split("def test_remote_deployment():", 1)[0]
    content = content.replace(
        '("6379:6739" in p or "6379:6379" in p)', '"6379:6379" in p'
    )
    content = content.replace('in ["6739", "6379"]', '== "6379"').replace(
        "6379:6739", "6379:6379"
    )
    tests.write_text(content + REMOTE_TEST)
    test_sh = output / "tests" / "test.sh"
    canary = test_sh.read_text().split("#!/bin/bash", 1)[0]
    test_sh.write_text(
        canary
        + """#!/bin/bash
set -u
mkdir -p /logs/verifier
if python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
"""
    )
    task = output / "task.toml"
    task.write_text(
        task.read_text()
        .replace("cpus = 1", "cpus = 2")
        .replace('memory = "2G"', 'memory = "4G"')
    )
    print(json.dumps({"source": str(source), "output": str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repair(args.source, args.output)
