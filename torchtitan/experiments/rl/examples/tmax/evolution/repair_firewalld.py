#!/usr/bin/env python3
"""Stage the firewalld seed's container instructions without changing its verifier."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

INSTRUCTION = """Install firewalld and configure the following permanent rules in its
`public` zone: open TCP port `8092`, allow the `http` and `https` services,
and add a rich rule accepting TCP port `80` traffic from `172.16.238.14`.

This container does not run systemd. Configure the persistent rules with
`firewall-offline-cmd`; do not start the firewalld daemon or reload the live
firewall. Service activation is outside this task. The installed firewalld
package and its service unit must remain available.

The permanent configuration in `/etc/firewalld/zones/public.xml` must contain
port `8092/tcp` exactly once, services `http` and `https`, and the rich rule
`rule family="ipv4" source address="172.16.238.14" port port="80" protocol="tcp" accept`.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output / "tw_311787"
    shutil.copytree(args.tasks_dir / target.name, target)
    instruction = target / "instruction.md"
    old = instruction.read_text()
    marker = old.find("# BENCHMARK DATA")
    canary = old[marker:] if marker >= 0 else ""
    instruction.write_text(INSTRUCTION + canary)
    print(target)


if __name__ == "__main__":
    main()
