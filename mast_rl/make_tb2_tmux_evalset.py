"""Bake tmux into every TB2.1 eval row by giving each a Dockerfile.

The TB2 images ship without tmux, so harbor's Terminus-2 installs it at runtime
(`apt-get update && apt-get install -y tmux`, 120 s exec timeout). With ~445
sandboxes starting at once the mirrors slow down and the install times out
("Failed to start tmux session"), scoring the trial 0 before turn 0.

Each row gets `FROM <image>` (or its existing env-patch Dockerfile) plus a RUN
that does what harbor would do at runtime. Daytona builds and caches the
Dockerfile server-side, so the install is paid once per task, not per sandbox.
The apt lists are deliberately kept: harbor's runtime install leaves them in
place, and an agent's bare `apt-get install` must keep working as before.

    python mast_rl/make_tb2_tmux_evalset.py SRC.jsonl DST.jsonl
"""

import json
import sys

TMUX_BLOCK = (
    "# tmux for the Terminus-2 agent, installed the way harbor does it at runtime\n"
    "RUN if ! command -v tmux >/dev/null 2>&1; then "
    "if command -v apt-get >/dev/null 2>&1; then "
    "DEBIAN_FRONTEND=noninteractive apt-get update && "
    "DEBIAN_FRONTEND=noninteractive apt-get install -y tmux; "
    "elif command -v apk >/dev/null 2>&1; then apk add --no-cache tmux; "
    "elif command -v dnf >/dev/null 2>&1; then dnf install -y tmux; "
    "elif command -v yum >/dev/null 2>&1; then yum install -y tmux; "
    "else echo 'no supported package manager for tmux' >&2; exit 1; fi; "
    "fi && tmux -V\n"
)


def main(src: str, dst: str) -> None:
    with open(src) as f_in, open(dst, "w") as f_out:
        for line in f_in:
            row = json.loads(line)
            md = row["metadata"]
            base = md.get("dockerfile") or f"FROM {md['image']}\n"
            md["dockerfile"] = base.rstrip("\n") + "\n" + TMUX_BLOCK
            f_out.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
