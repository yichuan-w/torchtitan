#!/usr/bin/env python3
"""Apply the diagnosed repairs to decayed task packages, one edit at a time.

Each entry names the exact text it replaces and fails loudly if that text is not
found, so a package that has drifted since the diagnosis is skipped rather than
half-edited. Written as a file rather than typed inline because these patches
carry shell inside Dockerfiles inside Python -- quoting them through ssh mangled
them once already, and a script is the thing that can be re-run.

Backups live in archive/task-backups-20260901; --check diffs against them.

Not here, and deliberately: tw_157216, whose verifier queries the public Stellar
testnet. Moving it onto a chain inside its own image is the right shape and two
of the three obstacles are now measured rather than guessed at.

The first is settled. Installing the SDK with pip --break-system-packages stops
the base image's supervisor from starting anything, and the network never comes
up; into a virtualenv instead, Horizon funds accounts ten seconds after boot.

The second is not, and it is where the attempt stops. Started with `--local`,
the network never closes a ledger: core_latest_ledger sits at 1 -- the genesis
ledger -- and history_latest_ledger at 0, unchanged across three minutes, on the
pinned build and on :latest alike. Friendbot answers 200 because it accepts the
request; the account never appears because the transaction is never confirmed.

Why it cannot close is now known, and it is not a timing problem. The core in
this image runs with MANUAL_CLOSE, so it will not close on its own; and driving
it by hand is refused:

    GET http://localhost:11626/manualclose
    {"exception": "Issuing a manual ledger close requires
                   NODE_IS_VALIDATOR to be set to true."}

So both routes are shut: automatic close is disabled by MANUAL_CLOSE, and
manual close is rejected because the node is not a validator. Whoever picks this
up starts there -- why quickstart's local mode brings core up in that
combination -- rather than repeating the measurements above. The docs say a
local network closes a ledger every second, which this does not do.

So the task keeps talking to the public testnet, where it passes today. What is
actually wrong with it is written down rather than half-fixed: every rollout
writes an account and two transactions into a shared public ledger, and its two
fixed destination addresses exist only because earlier rollouts funded them --
so an agent that skips funding them passes now and would start failing after a
testnet reset, which is difficulty drift rather than an outage and much harder
to notice. The assets from the attempt are under scripts/assets/tw_157216_*.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path("/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/tw-extract/tasks")
ASSETS = Path(__file__).resolve().parent / "assets"

# CentOS 7 is EOL and mirror.centos.org is retired. 52 of the corpus's 54
# CentOS 7 tasks already carry these two lines; these are the ones it missed.
_VAULT = (
    "# CentOS 7 is EOL and mirror.centos.org is retired; point yum at the vault\n"
    "# so this image can still resolve packages.\n"
    "RUN sed -i 's/mirrorlist/#mirrorlist/g' /etc/yum.repos.d/CentOS-*.repo && \\\n"
    "    sed -i 's|#baseurl=http://mirror.centos.org|"
    "baseurl=http://vault.centos.org|g' /etc/yum.repos.d/CentOS-*.repo\n"
)

# Files a repair adds rather than edits: {task: {dest rel path: asset name}}.
NEW_FILES: dict[str, dict[str, str]] = {
    "tw_582696": {"environment/sntp_server.py": "tw_582696_sntp_server.py"},
    "tw_693888": {"environment/setup_disk.sh": "tw_693888_setup_disk.sh"},
    "tw_627786": {"environment/setup_disk.sh": "tw_627786_setup_disk.sh"},
}

# Files a repair removes: {task: [relative paths]}.
DELETE_FILES: dict[str, list[str]] = {
    # The PID-burning entrypoint. Nothing references it once the Dockerfile
    # stops building it, and leaving it would read as still in use.
    "tw_10981": ["environment/startup.go"],
}
BACKUP = Path("/scratch/gpfs/TRIDAO/al9080/terminal-rl/archive/task-backups-20260901")

# task -> [(relative path, old text, new text, why)]
PATCHES: dict[str, list[tuple[str, str, str, str]]] = {
    "tw_158378": [
        (
            "environment/Dockerfile",
            "RUN go install github.com/ldez/prm@latest",
            "# Pinned, not @latest. Every v2 tag of prm is +incompatible, so Go's\n"
            "# @latest skips the whole v2 line and resolves to v1.10.0 from 2018 --\n"
            "# a different tool without the subcommands this task uses. It no\n"
            "# longer builds at all, which the image cache has been hiding.\n"
            "RUN go install github.com/ldez/prm@v2.4.0+incompatible",
            "@latest stopped meaning the tool this task was recorded against",
        ),
        (
            "tests/test_state.py",
            "REPO_DIR = '/root/traefik'",
            "REPO_DIR = '/root/traefik'\n\n__REFLOG_TESTS__",
            "the grader must see that the work happened, not only that it was undone",
        ),
    ],
    "tw_262693": [
        (
            "environment/Dockerfile",
            '  --advertise-address=127.0.0.1 \\',
            "  # Not 127.0.0.1: kube-apiserver v1.37.0 turned \"advertise address may\n"
            "  # not be in the loopback range\" from a reconciler warning into a fatal\n"
            "  # start error, so the server exits and nothing listens on 6443.\n"
            "  --advertise-address=\"$(hostname -i | tr ' ' '\\n' "
            "| grep -v '^127\\.' | head -1)\" \\",
            "v1.37.0 made a loopback advertise address fatal",
        ),
        (
            "environment/Dockerfile",
            '    K8S_VERSION="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"; \\',
            "    # Pinned rather than stable.txt: this image tracked whatever upstream\n"
            "    # shipped, and on 2026-08-26 stable rolled to v1.37.0 and the task\n"
            "    # stopped building a working cluster. An image that rebuilds into a\n"
            "    # different Kubernetes is not the task that was validated.\n"
            '    K8S_VERSION="v1.36.4"; \\',
            "stop tracking whatever upstream ships",
        ),
    ],
    "tw_132666": [
        (
            "solution/solve.sh",
            "apt-get install -y google-cloud-sdk",
            "# google-cloud-cli: upstream renamed the package and removed the\n"
            "# google-cloud-sdk transitional entry from its apt repository, so the old\n"
            "# name has no installation candidate at all.\n"
            "apt-get install -y google-cloud-cli",
            "upstream removed the old package name",
        ),
        (
            "tests/test_state.py",
            '    result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", '
            '"google-cloud-sdk"], capture_output=True, text=True)\n'
            '    assert result.returncode == 0, "google-cloud-sdk package is not '
            'installed."\n'
            '    assert "install ok installed" in result.stdout, "google-cloud-sdk '
            'package is not fully installed."',
            "    # Either name counts: upstream renamed the package to\n"
            "    # google-cloud-cli and withdrew the google-cloud-sdk transitional\n"
            "    # entry, so a correct solution installs the new name and dpkg knows\n"
            "    # nothing about the old one. The repository file this task asks for\n"
            "    # is still called google-cloud-sdk.list; that name is the task's,\n"
            "    # not upstream's, and is unaffected.\n"
            "    for pkg in (\"google-cloud-cli\", \"google-cloud-sdk\"):\n"
            "        result = subprocess.run([\"dpkg-query\", \"-W\", "
            "\"-f=${Status}\", pkg],\n"
            "                                capture_output=True, text=True)\n"
            "        if result.returncode == 0 and \"install ok installed\" in "
            "result.stdout:\n"
            "            return\n"
            "    assert False, \"neither google-cloud-cli nor google-cloud-sdk is "
            "installed.\"",
            "the verifier must accept the name that can actually be installed",
        ),
        (
            "instruction.md",
            "The `google-cloud-sdk` package must be installed",
            "The `google-cloud-cli` package (formerly named `google-cloud-sdk`) "
            "must be installed",
            "the instruction named a package that no longer exists",
        ),
        (
            "instruction.md.bak-canary",
            "The `google-cloud-sdk` package must be installed",
            "The `google-cloud-cli` package (formerly named `google-cloud-sdk`) "
            "must be installed",
            "keep the canary original in step, or strip_canary revives the old name",
        ),
    ],
    "tw_228601": [
        (
            "environment/Dockerfile",
            "RUN KUBECTL_VERSION=$(curl -sSL https://dl.k8s.io/release/stable.txt) && \\",
            "# Pinned, not stable.txt. An image that rebuilds into whatever Kubernetes\n# upstream ships today is not the environment this task was validated in,\n# and the drift is not hypothetical: these images already hold v1.37.0\n# against the v1.36.3 that stable.txt resolved to on 2026-08-13. It has not\n# broken this task because it starts no apiserver; tw_262693 did, and\n# v1.37.0 made a loopback advertise address fatal there.\n"
            'RUN KUBECTL_VERSION="v1.36.3" && \\',
            "an unpinned upstream reference rebuilds into a different environment",
        ),
    ],
    "tw_266088": [
        (
            "environment/Dockerfile",
            '        "https://dl.k8s.io/release/$(curl -L -s '
            'https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \\',
            "# Pinned, not stable.txt. An image that rebuilds into whatever Kubernetes\n# upstream ships today is not the environment this task was validated in,\n# and the drift is not hypothetical: these images already hold v1.37.0\n# against the v1.36.3 that stable.txt resolved to on 2026-08-13. It has not\n# broken this task because it starts no apiserver; tw_262693 did, and\n# v1.37.0 made a loopback advertise address fatal there.\n"
            '        "https://dl.k8s.io/release/v1.36.3/bin/linux/amd64/kubectl" \\',
            "an unpinned upstream reference rebuilds into a different environment",
        ),
    ],
    "tw_608928": [
        (
            "environment/Dockerfile",
            'RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s '
            'https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \\',
            "# Pinned, not stable.txt. An image that rebuilds into whatever Kubernetes\n# upstream ships today is not the environment this task was validated in,\n# and the drift is not hypothetical: these images already hold v1.37.0\n# against the v1.36.3 that stable.txt resolved to on 2026-08-13. It has not\n# broken this task because it starts no apiserver; tw_262693 did, and\n# v1.37.0 made a loopback advertise address fatal there.\n"
            'RUN curl -LO "https://dl.k8s.io/release/v1.36.3/bin/linux/amd64/kubectl" \\',
            "an unpinned upstream reference rebuilds into a different environment",
        ),
    ],
    "tw_311818": [
        (
            "environment/Dockerfile",
            "RUN curl -fsSL https://get.pulumi.com | sh",
            "# Pinned. Pulumi is what this task is about -- it runs `pulumi up` and\n"
            "# reads a stack output -- so an unpinned installer is the float that\n"
            "# matters here; the kubectl below is decorative. 3.257.0 is what the\n"
            "# installer served on 2026-08-13, against 3.260.0 today.\n"
            "RUN curl -fsSL https://get.pulumi.com | sh -s -- --version 3.257.0",
            "the task's own tool was installed from an unpinned script",
        ),
        (
            "environment/Dockerfile",
            'RUN curl -LO "https://dl.k8s.io/release/$(curl -fsSL '
            'https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \\',
            "# Pinned, not stable.txt. An image that rebuilds into whatever Kubernetes\n# upstream ships today is not the environment this task was validated in,\n# and the drift is not hypothetical: these images already hold v1.37.0\n# against the v1.36.3 that stable.txt resolved to on 2026-08-13. It has not\n# broken this task because it starts no apiserver; tw_262693 did, and\n# v1.37.0 made a loopback advertise address fatal there.\n"
            'RUN curl -LO "https://dl.k8s.io/release/v1.36.3/bin/linux/amd64/kubectl" \\',
            "an unpinned upstream reference rebuilds into a different environment",
        ),
    ],
    "tw_137314": [
        (
            "environment/Dockerfile",
            "RUN COMPOSE_VERSION=$(curl -fsSL "
            "https://api.github.com/repos/docker/compose/releases/latest \\\n"
            "        | grep '\"tag_name\"' | cut -d'\"' -f4) && \\",
            "# Pinned. v5.4.0 is what releases/latest served on 2026-08-13; today it\n"
            "# serves v5.5.0.\n"
            'RUN COMPOSE_VERSION="v5.4.0" && \\',
            "an unpinned upstream reference rebuilds into a different environment",
        ),
        (
            "solution/solve.sh",
            'DOCKER_COMPOSE_VERSION=$(curl -fsSL '
            'https://api.github.com/repos/docker/compose/releases/latest '
            '| grep \'"tag_name"\' | cut -d\'"\' -f4)',
            "# Pinned here as well as in the Dockerfile, and this is the one that\n"
            "# decides the reward: the reference solution reinstalls compose at solve\n"
            "# time and the graded /app/result.txt carries whatever THIS line\n"
            "# installed. Pinning only the image would leave the task drifting while\n"
            "# looking pinned.\n"
            'DOCKER_COMPOSE_VERSION="v5.4.0"',
            "the graded output came from an unpinned install at solve time",
        ),
    ],
    "tw_197833": [
        (
            "environment/Dockerfile",
            "    && RADARE2_DEB=$(curl -s "
            "https://api.github.com/repos/radareorg/radare2/releases/latest \\\n"
            "        | jq -r '.assets[] | select(.name | "
            "test(\"^radare2_[0-9].*_amd64\\\\.deb$\")) | .browser_download_url' \\\n"
            "        | head -1) \\",
            "    # Pinned to the URL rather than resolved from releases/latest. The\n"
            "    # verifier greps radare2's console output for `Vtable Found at`,\n"
            "    # `Type Descriptor` and `Class Hierarchy Descriptor`, so a release\n"
            "    # that rewords any of the three fails the task without anything\n"
            "    # about the task changing. 6.2.0 is both the version validated on\n"
            "    # 2026-08-13 and the current one, so nothing moves today; the pin is\n"
            "    # what keeps it that way.\n"
            "    && RADARE2_DEB=\"https://github.com/radareorg/radare2/releases/"
            "download/6.2.0/radare2_6.2.0_amd64.deb\" \\",
            "a verifier that greps tool output is broken by any release that rewords it",
        ),
    ],
    "tw_740537": [
        (
            "environment/Dockerfile",
            "RUN VERSION=$(curl -s "
            "https://api.github.com/repos/refaktor/rye/releases/latest \\\n"
            "        | grep '\"tag_name\"' | sed -E 's/.*\"tag_name\": *\"([^\"]+)\".*/\\1/') && \\",
            "# Pinned. v0.2.58 is what releases/latest served on 2026-08-13; today it\n"
            "# serves v0.2.60.\n"
            'RUN VERSION="v0.2.58" && \\',
            "an unpinned upstream reference rebuilds into a different environment",
        ),
    ],
    "tw_676108": [
        (
            "environment/Dockerfile",
            "COPY --from=composer:latest /usr/bin/composer /usr/bin/composer",
            "# Pinned. 2.10.1 is the tag that :latest pointed at on 2026-08-13.\n"
            "# This pins the composer binary and not the dependency tree: the build\n"
            "# below runs `composer update` with no committed composer.lock, so the\n"
            "# PHP dev tools re-resolve on every rebuild and solve.sh runs phpcs,\n"
            "# phpunit and phpstan under `set -e`. Freezing that needs a lock file\n"
            "# committed into the task, which is a change to what the task ships\n"
            "# rather than a pin. `FROM php:8.3-cli` floats within 8.3.x too.\n"
            "COPY --from=composer:2.10.1 /usr/bin/composer /usr/bin/composer",
            "the composer binary floated with :latest",
        ),
    ],
    "tw_284594": [
        (
            "solution/solve.sh",
            "/usr/share/openvswitch/scripts/ovs-ctl start\n",
            "# Not ovs-ctl start: it insists on inserting the openvswitch kernel\n"
            "# module, and a Daytona sandbox sits in a nested user namespace where\n"
            "# init_module returns EPERM however full the capability set looks, so\n"
            "# ovs-ctl exits 1 and `set -e` aborts the rest of the solution.\n"
            "# Reproduced on four fresh sandboxes, kernel 6.8.0-85-generic, lsmod\n"
            "# empty on all of them.\n"
            "#\n"
            "# The daemons are started directly instead. Nothing the verifier\n"
            "# checks needs a datapath: it asserts the four processes are running,\n"
            "# the encap external-ids, the NB connection, and the script. The\n"
            "# instruction asks for the services to be running and never names a\n"
            "# startup command, so this is the same task.\n"
            "mkdir -p /var/run/openvswitch /etc/openvswitch /var/run/ovn "
            "/var/log/openvswitch\n"
            "[ -f /etc/openvswitch/conf.db ] || ovsdb-tool create "
            "/etc/openvswitch/conf.db /usr/share/openvswitch/vswitch.ovsschema\n"
            "ovsdb-server --detach --pidfile "
            "--remote=punix:/var/run/openvswitch/db.sock "
            "--remote=db:Open_vSwitch,Open_vSwitch,manager_options --log-file\n"
            "ovs-vsctl --no-wait init\n"
            "ovs-vswitchd --detach --pidfile --log-file\n",
            "ovs-ctl start cannot load a kernel module in an unprivileged sandbox",
        ),
    ],
    "tw_627786": [
        (
            "environment/Dockerfile",
            "# Create a sparse 10 GB disk image to serve as the virtual disk\n"
            "RUN truncate -s 10G /disk.img",
            "# The disk image is created at container start, not in a build layer.\n"
            "# A layer materialises it: the sparse file goes into the layer tar as\n"
            "# 10 GiB of zeros and is written out in full, which measured 10818 MB\n"
            "# against Daytona's 10 GiB ceiling. Created at start it stays sparse,\n"
            "# and costs only the blocks fdisk writes. Same repair as this task's\n"
            "# near-twin tw_693888, which asks for the same layout on a 20G image.\n"
            "COPY setup_disk.sh /opt/setup_disk.sh\n"
            "RUN chmod +x /opt/setup_disk.sh\n"
            'ENTRYPOINT ["/opt/setup_disk.sh"]',
            "a 10 GiB build layer put the task over the per-sandbox disk ceiling",
        ),
    ],
    "tw_693888": [
        (
            "environment/Dockerfile",
            "# Pre-create a 20G sparse disk image that solve.sh will attach as "
            "/dev/sdb\nRUN truncate -s 20G /disk.img",
            "# The disk image is created at container start, not in a build layer.\n"
            "# A layer materialises it: the sparse file goes into the layer tar as\n"
            "# 20 GiB of zeros and is written out in full, which measured 21556 MB\n"
            "# of a sandbox against Daytona's 10 GiB ceiling -- the task could not\n"
            "# run at any sizing. Created at start it stays sparse, and costs only\n"
            "# the blocks fdisk writes.\n"
            "COPY setup_disk.sh /opt/setup_disk.sh\n"
            "RUN chmod +x /opt/setup_disk.sh\n"
            'ENTRYPOINT ["/opt/setup_disk.sh"]',
            "a 20 GiB build layer put the task over the per-sandbox disk ceiling",
        ),
    ],
    "tw_582696": [
        (
            "environment/Dockerfile",
            "WORKDIR /app",
            "WORKDIR /app\n\n"
            "# The task's time server now lives in the image. Nothing leaves this\n"
            "# sandbox on UDP except port 53, so the public server the task named\n"
            "# was never reachable and the verifier fell back to its own clock --\n"
            "# which paid full marks for `date`. This one serves a deliberately\n"
            "# skewed clock, so only a client that really speaks SNTP can pass.\n"
            "COPY sntp_server.py /opt/sntp_server.py\n"
            'ENTRYPOINT ["python3", "/opt/sntp_server.py"]',
            "serve the time locally instead of depending on a host we cannot reach",
        ),
        (
            "instruction.md",
            "Query the NTP server at `time.nplindia.org` (port 123)",
            "Query the NTP server at `localhost` (port 123)",
            "the server the instruction named is unreachable from the sandbox",
        ),
        (
            "instruction.md.bak-canary",
            "Query the NTP server at `time.nplindia.org` (port 123)",
            "Query the NTP server at `localhost` (port 123)",
            "keep the canary original in step with the instruction",
        ),
        (
            "tests/test_state.py",
            """    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(5.0)
    data = b'\\x1b' + 47 * b'\\0'
    try:
        client.sendto(data, ("time.nplindia.org", 123))
        resp, _ = client.recvfrom(48)
        ans = struct.unpack("!I", resp[32:36])[0] - 2208988800
        expected_dt = datetime.datetime.fromtimestamp(ans)
    except Exception:
        # Fallback to system time if the NTP server is unreachable during the test
        expected_dt = datetime.datetime.now()""",
            """    # No fallback to the system clock. The old one made this task free: the
    # sandbox blocks outbound UDP, the query always failed, and the check
    # collapsed to "is the file within 300s of now", which `date` satisfies.
    # The image now serves the time on localhost with a fixed offset, so the
    # expected value is computable here and reading the local clock is wrong
    # by SKEW.
    SKEW = 1234567  # must match environment/sntp_server.py
    expected_dt = datetime.datetime.fromtimestamp(time.time() + SKEW)""",
            "the fallback made a full-marks answer out of reading the local clock",
        ),
        (
            "solution/solve.sh",
            'client.sendto(data.encode(), ("time.nplindia.org", 123))',
            'client.sendto(data.encode(), ("localhost", 123))',
            "the reference solution has to ask the server the task now names",
        ),
        (
            "tests/test_state.py",
            "import socket\nimport struct\nimport datetime",
            "import datetime\nimport time",
            "socket and struct are unused once the verifier stops dialling out",
        ),
    ],
    "tw_10981": [
        (
            "environment/Dockerfile",
            "COPY startup.go /tmp/startup.go\n"
            "RUN go build -o /usr/local/bin/startup /tmp/startup.go && \\\n"
            "    chmod 755 /usr/local/bin/startup",
            "# The process this task reads about, started by name rather than by\n"
            "# number. The image used to build a Go program that drove\n"
            "# /proc/sys/kernel/ns_last_pid so the process would land on PID 1179.\n"
            "# That read is frozen in this sandbox, so the loop never terminated: it\n"
            "# forked ~600 times a second for the whole rollout, never started the\n"
            "# process, and scored 0 while occupying a core. How the kernel hands\n"
            "# out PIDs was never what the task was about.\n"
            "#\n"
            "# The entrypoint is written here rather than COPYed because Daytona\n"
            "# keys its snapshot cache on this Dockerfile's text: a change to a\n"
            "# COPYed script alone does not rebuild the image, so the two would\n"
            "# drift silently. The guard matters -- this entrypoint runs twice,\n"
            "# once as Daytona's PID 1 and once from the harness, and two\n"
            "# target-proc processes make the verifier's lookup ambiguous.\n"
            "RUN cp /bin/sleep /usr/local/bin/target-proc\n"
            "RUN printf '%s\\n' \\\n"
            "    '#!/bin/sh' \\\n"
            "    'if mkdir /run/target-proc.lock 2>/dev/null; then' \\\n"
            "    '    setsid /usr/local/bin/target-proc 86400 </dev/null "
            ">/dev/null 2>&1 &' \\\n"
            "    'fi' \\\n"
            "    'exec \"$@\"' \\\n"
            "    > /usr/local/bin/entrypoint.sh && \\\n"
            "    chmod 755 /usr/local/bin/entrypoint.sh",
            "the PID-forcing entrypoint never terminated here",
        ),
        (
            "environment/Dockerfile",
            '# ENTRYPOINT runs the PID-setup binary which bumps the PID counter to 1178\n'
            '# and starts a background sleep at PID 1179, then execs the CMD argument.\n'
            'ENTRYPOINT ["/usr/local/bin/startup"]',
            '# ENTRYPOINT starts the target process in the background, then execs CMD.\n'
            'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]',
            "point the entrypoint at the replacement",
        ),
        (
            "instruction.md",
            "the environment variables of the process with PID **1179**",
            "the environment variables of the long-running background process "
            "whose command is `/usr/local/bin/target-proc`",
            "the PID was an artefact of a 2014 recording, not part of the task",
        ),
        (
            "instruction.md.bak-canary",
            "the environment variables of the process with PID **1179**",
            "the environment variables of the long-running background process "
            "whose command is `/usr/local/bin/target-proc`",
            "keep the canary original in step with the instruction",
        ),
        (
            "solution/solve.sh",
            "sudo procfs 1179/environ | jq '.' > /app/result.txt",
            "sudo procfs \"$(pgrep -x target-proc)/environ\" | jq '.' "
            "> /app/result.txt",
            "ask for the process by name",
        ),
        (
            "tests/test_state.py",
            "def test_result_matches_proc_environ():\n"
            "    try:\n"
            '        with open("/proc/1179/environ", "rb") as f:\n'
            "            env_data = f.read()\n"
            "    except FileNotFoundError:\n"
            '        assert False, "Process 1179 does not exist or '
            '/proc/1179/environ is missing"\n'
            "    except PermissionError:\n"
            '        assert False, "Permission denied reading /proc/1179/environ"',
            "__TARGET_PID_HELPER__\n\n"
            "def test_result_matches_proc_environ():\n"
            "    pid = _target_pid()\n"
            "    try:\n"
            '        with open(f"/proc/{pid}/environ", "rb") as f:\n'
            "            env_data = f.read()\n"
            "    except FileNotFoundError:\n"
            '        assert False, f"/proc/{pid}/environ is missing"\n'
            "    except PermissionError:\n"
            '        assert False, f"Permission denied reading /proc/{pid}/environ"',
            "resolve the target by command instead of by a hard-coded PID",
        ),
        (
            "environment/docker-compose.yaml",
            'test: ["CMD", "sh", "-c", "test -r /proc/1179/environ"]',
            'test: ["CMD", "sh", "-c", "pgrep -x target-proc > /dev/null"]',
            "the healthcheck named the same vanished PID",
        ),
    ],
    "tw_299387": [
        (
            "environment/Dockerfile",
            "&& apt-get update && apt-get install -y google-cloud-sdk \\",
            "# google-cloud-cli: upstream renamed the package and withdrew the\n"
            "    # google-cloud-sdk transitional entry, so this build fails the moment\n"
            "    # the image cache misses. The /usr/lib/google-cloud-sdk path patched\n"
            "    # further down is unaffected -- the new package installs there too.\n"
            "    && apt-get update && apt-get install -y google-cloud-cli \\",
            "this build breaks on the next cache miss, not on the next run",
        ),
    ],
    "tw_130196": [
        (
            "environment/Dockerfile",
            "FROM centos:7 AS centos7_stage\n",
            "FROM centos:7 AS centos7_stage\n\n" + _VAULT,
            "CentOS 7 packages are only in the vault now",
        ),
    ],
    "tw_494637": [
        (
            "environment/Dockerfile",
            "FROM centos:7\n",
            "FROM centos:7\n\n" + _VAULT,
            "CentOS 7 packages are only in the vault now",
        ),
    ],
    "tw_17818": [
        (
            "solution/solve.sh",
            "fossil clone https://core.tcl.tk/tcl .repo",
            "# core.tcl-lang.org, not core.tcl.tk: the whole tcl.tk domain now\n"
            "# NXDOMAINs at the .tk registry -- the project moved years ago and let\n"
            "# the old name lapse. Nothing here tests where the source came from;\n"
            "# the verifier checks the build and the installed tclsh.\n"
            "fossil clone https://core.tcl-lang.org/tcl .repo",
            "the host in this command no longer resolves",
        ),
        (
            "instruction.md",
            "from `https://core.tcl.tk/tcl` into",
            "from `https://core.tcl-lang.org/tcl` into",
            "the instruction named a host that no longer exists",
        ),
        (
            "instruction.md.bak-canary",
            "from `https://core.tcl.tk/tcl` into",
            "from `https://core.tcl-lang.org/tcl` into",
            "keep the canary original in step with the instruction",
        ),
    ],
}


def apply_one(task: str, rel: str, old: str, new: str, why: str, write: bool) -> bool:
    path = ROOT / task / rel
    if not path.exists():
        print(f"  {task}/{rel}: MISSING")
        return False
    s = path.read_text()
    if new.strip().splitlines()[-1] in s and old not in s:
        print(f"  {task}/{rel}: already patched ({why})")
        return True
    if old not in s:
        print(f"  {task}/{rel}: ANCHOR NOT FOUND -- skipped ({why})")
        return False
    # A patch may splice in a helper kept as an asset, so the code that goes
    # into the task is syntax-checked on its own rather than living as an
    # escaped string three quoting levels deep.
    if "__REFLOG_TESTS__" in new:
        new = new.replace(
            "__REFLOG_TESTS__",
            (ASSETS / "tw_158378_reflog_tests.py").read_text().rstrip(),
        )
    if "__TARGET_PID_HELPER__" in new:
        new = new.replace(
            "__TARGET_PID_HELPER__",
            (ASSETS / "tw_10981_target_pid.py").read_text().rstrip(),
        )
    if write:
        path.write_text(s.replace(old, new, 1))
    print(f"  {task}/{rel}: {'patched' if write else 'would patch'} -- {why}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="*", default=sorted(PATCHES))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true", help="diff against the backup")
    a = ap.parse_args()
    tasks = a.tasks or sorted(PATCHES)
    for t in tasks:
        print(f"== {t}")
        if a.check:
            subprocess.run(["diff", "-ru", str(BACKUP / t), str(ROOT / t)])
            continue
        for rel in DELETE_FILES.get(t, []):
            victim = ROOT / t / rel
            if not victim.exists():
                print(f"  {t}/{rel}: already gone")
            elif a.apply:
                victim.unlink()
                print(f"  {t}/{rel}: removed")
            else:
                print(f"  {t}/{rel}: would remove")
        for rel, asset in NEW_FILES.get(t, {}).items():
            dest = ROOT / t / rel
            src = ASSETS / asset
            if dest.exists() and dest.read_text() == src.read_text():
                print(f"  {t}/{rel}: already in place")
            elif a.apply:
                dest.write_text(src.read_text())
                print(f"  {t}/{rel}: added from assets/{asset}")
            else:
                print(f"  {t}/{rel}: would add from assets/{asset}")
        for rel, old, new, why in PATCHES.get(t, []):
            apply_one(t, rel, old, new, why, a.apply)
    if not a.apply and not a.check:
        print("dry run -- pass --apply to write")


if __name__ == "__main__":
    main()
