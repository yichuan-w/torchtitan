"""Submit a multi-host, multi-generator Search-R1 RL job to MAST from Python.

Ported from ``fbsource//fbcode/pytorch/torchtitan/fb/mast_rl/launcher.py``.

Parses the forwarded training config (a ``MastRLConfig`` from
``mast_rl.config_registry`` via ``--module mast_rl``), extracts its ``launcher``
field, and submits via ``MastLauncher.submit``. The job's node count is inferred
from the config's trainer/generator parallelism + ``num_generators``.
"""

from __future__ import annotations

import argparse
import os

# ``mast`` resolves to ``mast_rl/mast.py`` because Python adds this script's
# directory to ``sys.path[0]``.
from mast import launcher_config
from mast_job_lifecycle import write_job_name_file

from torchtitan.config import ConfigManager


def _mast_url(job_name: str) -> str:
    return f"https://www.internalfb.com/intern/mast/job/{job_name}"


def _strip_main_py(args: list[str]) -> list[str]:
    """Drop a leading ``mast_rl/main.py`` so the rest is the config argv."""
    return args[1:] if args and args[0].endswith(".py") else args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "script_args",
        nargs=argparse.REMAINDER,
        help=(
            "the controller command + config, forwarded to MAST (typically "
            "``mast_rl/main.py --module mast_rl --config ... [--num_generators N] "
            "[--launcher.<field>=...]``)"
        ),
    )
    args = parser.parse_args()
    if args.script_args and args.script_args[0] == "--":
        args.script_args = args.script_args[1:]
    assert args.script_args, "must provide a script and its args after `--`"

    cfg = ConfigManager().parse_args(_strip_main_py(list(args.script_args)))
    lc = launcher_config(cfg)

    handle = lc.build().submit(cfg, tuple(args.script_args))
    print(f"submitted: {handle}")
    print(_mast_url(handle.rsplit("/", 1)[-1]))
    if output_path := os.environ.get("MAST_JOB_NAME_FILE"):
        job_name = write_job_name_file(output_path, handle)
        print(f"recorded MAST job identity: {job_name}")


if __name__ == "__main__":
    main()
