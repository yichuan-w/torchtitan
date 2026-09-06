#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Revalidate a task package on Daytona -- the platform the training runs on.

Structural retunes (a verifier tightened, a stage cut, the k/k "harder"
direction) need their environment rebuilt and their reference solution re-run
before they can be trusted into the mix. On hosts with docker that is
feedback_loop's local build path; on della there is no docker, and this is the
replacement: boot the package's environment exactly the way training rollouts
do (same harness, same dockerfile+build_context path, same grading contract),
run the reference solution, grade with the real verifier, and report reward.

Two probes, one sandbox each (a shortcut must see a FRESH environment, not one
the solution already ran in):

  daytona_revalidate.py <package_dir>                    # oracle: solve.sh -> reward
  daytona_revalidate.py <package_dir> --shortcut "CMD"   # cheat probe: CMD -> reward

The last stdout line is a JSON verdict; everything else is progress logging.
Requires the training venv (torchtitan + daytona SDK) and the Daytona env file
sourced -- feedback_loop wraps both.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

# This file lives inside the torchtitan tree it verifies against, so the harness
# it imports is the one in that tree, and nothing has to be configured for that
# to be true.
#
# It used to live in another repository and prepend a checkout to sys.path,
# defaulting to a separate clone. That made the choice invisible and, worse,
# made it win: the insert runs at import time, so a caller that set its own path
# still got this module's default, and the verdict then described a harness
# nobody was going to run. It happened: a healthy task scored 0 against a clone
# missing the fix that stopped read_file truncating through an exec.
#
# PYTHONPATH still wins if it is set, because that is how Python already works
# and the training launchers use it. What is gone is a default pointing
# somewhere else.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _repo_root() -> Path | None:
    """The checkout this file sits in, found by looking for the package.

    Counting parent directories would be shorter and would break silently the
    next time this file moves: it would resolve to some other directory that
    happens to exist, and the import would fall through to whatever else is on
    the path. Looking for torchtitan/__init__.py either finds the tree or does
    not.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "torchtitan" / "__init__.py").exists():
            return parent
    return None


_TREE = _repo_root()
if _TREE is not None and str(_TREE) not in sys.path:
    sys.path.append(str(_TREE))

import pack_to_dataset as pack  # noqa: E402


try:
    from torchtitan.experiments.rl.harness.agents.claude_code import (  # noqa: E402
        boot_agent_sandbox,
    )
except ModuleNotFoundError as exc:  # pragma: no cover -- configuration, not logic
    raise SystemExit(
        "cannot import torchtitan. This file expects to sit inside a torchtitan "
        f"checkout and found {_TREE if _TREE else 'none above it'}; if it was "
        "copied out of the repository, set PYTHONPATH to a checkout instead. "
        f"({exc})"
    ) from exc
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.grading import (  # noqa: E402
    grade_tmax,
    seed_workspace,
)
from torchtitan.experiments.rl.examples.tmax.integrity_baseline import (  # noqa: E402
    capture_baseline,
    protected_cmds_of,
    protected_entries_of,
    protected_paths_of,
)


class _Root:
    """Force every sandbox operation to run as root (tmax tasks touch system
    paths). Local mirror of the rollouter's _RootSandbox so this probe does not
    import the rollouter module (which drags the training stack in)."""

    def __init__(self, inner):
        self._inner = inner

    async def exec(self, cmd, *, check=False, timeout=None, **kw):
        kw.pop("user", None)
        return await self._inner.exec(
            cmd, user="root", check=check, timeout=timeout, **kw
        )

    async def write_file(self, dest, content, **kw):
        kw.pop("user", None)
        return await self._inner.write_file(dest, content, user="root", **kw)

    async def read_file(self, path, **kw):
        kw.pop("user", None)
        return await self._inner.read_file(path, user="root", **kw)


async def _start_entrypoint(sb, command: str, *, workdir: str) -> None:  # noqa: ARG001
    # workdir is unused and stays in the signature on purpose: this function
    # mirrors rollouter._start_entrypoint, and the two drop the parameter
    # together or not at all. Dropping it here alone is the divergence this
    # whole change is removing.
    """Start the image ENTRYPOINT detached, as PID 1 would (rollouter mirror)."""
    # No cd: Docker starts ENTRYPOINT in the image's own WORKDIR, and the
    # container already puts every exec there. Measured on four tasks -- a plain
    # exec and a tmux pane land in the same directory every time, and it is the
    # image's, not the one data prep guessed.
    await sb.exec(
        f"setsid nohup {command} > /tmp/entrypoint.log 2>&1 < /dev/null &",
        check=False,
        timeout=120,
    )
    await asyncio.sleep(3)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# A box at the platform ceiling cannot truncate a reading; a box below it can,
# which is what the `oom_kill` / disk-exhausted / timeout fields beside a
# measurement report.
from derive_sizing import CEILING  # noqa: E402

# oom_kill separates a kernel kill from a deadline; memory.peak beside
# memory.max shows a near miss as well as a hit; cpu.stat's usage_usec over the
# solve's wall time is the mean cores the reference solution drew. The same
# read as verify_provisioning.py takes for the seed campaign, so a task sized
# here and a seed sized there rest on the same counters.
CGROUP_READ = (
    "cat /sys/fs/cgroup/memory.events 2>/dev/null | tr '\\n' ' '; echo '|'; "
    "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max "
    "2>/dev/null | tr '\\n' ' '; echo '|'; "
    "awk '/usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null"
)


async def measure(sb, solve_secs: float, tail: str = "") -> dict:
    """What the container's counters say the run just finished cost.

    Read before grading, which starts processes of its own and would fold their
    memory into a peak meant to describe the solution. `df` of `/` is the
    sandbox disk: its size is the quota, so `df_used_mb` is the occupancy the
    quota has to hold, image included.
    """
    _, ev, _ = await sb.exec(CGROUP_READ, check=False, timeout=60)
    parts = (ev or "").split("|")
    toks = parts[0].split()
    kv = dict(zip(toks[::2], toks[1::2]))
    nums = [int(x) for x in (parts[1] if len(parts) > 1 else "").split() if x.isdigit()]
    usec = next(
        (int(x) for x in (parts[2] if len(parts) > 2 else "").split() if x.isdigit()),
        None,
    )
    _, dfout, _ = await sb.exec(
        "df -B1 --output=size,used / | tail -1", check=False, timeout=60
    )
    dparts = (dfout or "").split()
    size_mb = used_mb = None
    if len(dparts) == 2 and all(x.isdigit() for x in dparts):
        size_mb = round(int(dparts[0]) / 1048576, 1)
        used_mb = round(int(dparts[1]) / 1048576, 1)
    return {
        "solve_secs": round(solve_secs, 1),
        "cpu_seconds": round(usec / 1e6, 1) if usec else None,
        "cpu_mean_cores": (
            round(usec / 1e6 / solve_secs, 2) if usec and solve_secs > 0 else None
        ),
        "oom_kill": int(kv.get("oom_kill", -1)),
        "mem_peak_mb": round(nums[0] / 1048576, 1) if nums else None,
        "mem_max_mb": round(nums[1] / 1048576, 1) if len(nums) > 1 else None,
        "df_size_mb": size_mb,
        "df_used_mb": used_mb,
        "disk_exhausted": "no space left" in (tail or "").lower(),
    }


def starved(measured: dict, solve_exit: int | None, solve_timeout: int) -> str:
    """Why a failed run cannot be trusted as a measurement, or "" if it can.

    A run the box cut short read the box, not the task: the kernel killed it
    (oom_kill), the disk filled (ENOSPC in the output), or it ran out of solve
    budget on the cores it had (exit 124 from the harness's own `timeout`
    wrapper, or a wall time at the budget). Any of those on a box below the
    ceiling means "measure again at the ceiling", not "the task is broken".
    """
    if measured.get("oom_kill", 0) > 0:
        return "memory"
    if measured.get("disk_exhausted"):
        return "disk"
    if solve_exit == 124 or (measured.get("solve_secs") or 0) >= solve_timeout:
        return "time"
    return ""


async def probe(
    pkg: Path,
    shortcut: str | None,
    solve_timeout: int,
    resources: dict | None = None,
    require_paths: list[str] | None = None,
    pretest: tuple[str, str] | None = None,
) -> dict:
    # `pretest` is the row's pin hook; the adapter puts it on the grading
    # payload beside this package's own environment identity, so grade_tmax
    # below runs it, or skips it, exactly as a training rollout would.
    row = pack.to_row(str(pkg), pretest=pretest)
    md = row["metadata"]
    tmax = md["tmax"]
    workdir = md.get("workdir") or "/workspace"
    hook = None
    n_paths, n_cmds = len(protected_paths_of(tmax)), len(protected_cmds_of(tmax))
    if n_paths or n_cmds:
        # A row with protected entries grades by the integrity baseline (taken
        # below, right before the run) and never consults the pin hook.
        hook = {"mode": "baseline", "paths": n_paths, "cmds": n_cmds}
        log(f"integrity baseline: {n_paths} paths, {n_cmds} cmds")
    elif pretest:
        stamped = tmax.get("pretest_env_identity") or ""
        episode = tmax.get("pretest_episode_env_identity") or ""
        hook = {
            "mode": "stamp",
            "stamped": stamped,
            "episode": episode,
            "runs": bool(stamped and episode and stamped == episode),
        }
        log(
            f"pin hook: stamped={stamped or '?'} episode={episode or '?'} -> "
            f"{'runs before the verifier' if hook['runs'] else 'skipped: environment moved'}"
        )
    sol_dir = pkg / "solution"
    if shortcut is None and not (sol_dir / "solve.sh").exists():
        return {
            "ok": False,
            "stage": "no_solution",
            "why": "package ships no solution/solve.sh",
        }

    # The box is the caller's to size: the loop passes the size the row will be
    # provisioned at, so an oracle pass here is a pass where training will run
    # it. A key left None falls to the harness default (TT_DAYTONA_*), which is
    # what a row declaring nothing gets in training too -- provided this process
    # carries the trainer's env, which evolve_ondella resolves and logs.
    box = {
        **{"cpu": None, "mem_gb": None, "disk_gb": md.get("daytona_disk_gb")},
        **{k: v for k, v in (resources or {}).items() if v is not None},
    }
    log(
        f"boot sandbox for {md['instance_id']} "
        f"({'shortcut probe' if shortcut else 'oracle'}) "
        f"box=cpu:{box['cpu']} mem_gb:{box['mem_gb']} disk_gb:{box['disk_gb']}"
    )
    async with boot_agent_sandbox(
        md.get("image") or "",
        dockerfile=md.get("dockerfile") or None,
        build_context=md.get("build_context") or None,
        install_claude=False,
        cpu=box["cpu"],
        memory=box["mem_gb"],
        disk_gb=box["disk_gb"],
    ) as sandbox:
        sb = _Root(sandbox)
        if md.get("entrypoint"):
            await _start_entrypoint(sb, md["entrypoint"], workdir=workdir)
        await seed_workspace(sb, tmax)
        # Which of the paths the caller asks about the untouched workspace
        # already has. A path the verifier requires that is here before anything
        # runs is a precondition the agent inherits; one that is not, and that
        # nothing the agent can read names, is something it cannot know to
        # create. Asked before the solution runs, which would create them.
        missing: list[str] = []
        if require_paths:
            script = "; ".join(
                f"test -e {shlex.quote(p)} && echo p{i} || echo m{i}"
                for i, p in enumerate(require_paths)
            )
            _, pout, _ = await sb.exec(script, check=False, timeout=60)
            hits = {ln.strip() for ln in (pout or "").splitlines()}
            missing = [p for i, p in enumerate(require_paths) if f"m{i}" in hits]
        if shortcut is None:
            for f in sorted(sol_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(sol_dir)
                    await sb.write_file(
                        f"/solution/{rel}", f.read_text(errors="replace")
                    )
            cmd = "bash /solution/solve.sh"
        else:
            cmd = shortcut
        # INTEGRITY BASELINE: the state the run starts from -- solution/ in,
        # nothing run yet -- for the oracle and the shortcut probe alike; the
        # verifier re-digests and a difference grades 0. None without entries.
        baseline = await capture_baseline(sb, tmax, workdir=workdir, timeout=120)
        t0 = time.time()
        code, out, err = await sb.exec(cmd, check=False, timeout=solve_timeout)
        log(f"run exit={code}")
        measured = await measure(sb, time.time() - t0, tail=(out or "") + (err or ""))
        reward = await grade_tmax(sb, tmax, workdir=workdir, baseline_digests=baseline)
    tail = (out + "\n" + err)[-400:]
    if shortcut is None:
        why = starved(measured, code, solve_timeout) if reward < 1.0 else ""
        return {
            "ok": reward >= 1.0,
            "stage": "daytona_oracle",
            "reward": reward,
            "solve_exit": code,
            "tail": tail,
            "resources": box,
            "measured": measured,
            "pretest": hook,
            "paths_checked": list(require_paths or []),
            "paths_missing": missing,
            **(
                {
                    "starved": why,
                    "why": f"reference solution ran out of {why} " f"in box {box}",
                }
                if why
                else {}
            ),
        }
    return {
        "ok": True,
        "stage": "daytona_shortcut",
        "passed": reward >= 1.0,
        "reward": reward,
        "tail": tail,
        "resources": box,
        "pretest": hook,
        "paths_checked": list(require_paths or []),
        "paths_missing": missing,
    }


def _harness_provenance() -> str:
    """Which torchtitan produced this verdict.

    A verdict describes the harness it ran through, and the checkouts disagree:
    the training one runs 25 commits behind the canonical branch today, missing
    the fix that stopped read_file truncating through an exec. Verifying against
    it scored a healthy task 0 and no line in the output said which code that
    verdict came from.
    """
    import torchtitan

    root = Path(torchtitan.__file__).resolve().parent.parent
    head = ""
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "log", "--oneline", "-1"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 -- provenance is best effort
        pass
    dirty = ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        n = len([x for x in out.splitlines() if x.strip()])
        dirty = f", {n} uncommitted" if n else ""
    except Exception:  # noqa: BLE001
        pass
    return f"{root} [{head or 'no git'}{dirty}]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", help="task package dir")
    ap.add_argument(
        "--shortcut",
        help="probe this cheat command instead of " "running the reference solution",
    )
    ap.add_argument("--solve-timeout", type=int, default=900)
    ap.add_argument("--cpu", type=int, help="box size; unset = harness default")
    ap.add_argument("--mem-gb", type=int)
    ap.add_argument("--disk-gb", type=int)
    ap.add_argument(
        "--require-path",
        action="append",
        default=[],
        help="report whether this path exists in the untouched "
        "workspace (repeatable)",
    )
    ap.add_argument(
        "--pretest-file",
        type=Path,
        help="the row's pin hook (a rewrite's pretest.json); graded "
        "with, as training does, before the verifier runs",
    )
    args = ap.parse_args()
    log(f"harness: {_harness_provenance()}")
    try:
        verdict = asyncio.run(
            probe(
                Path(args.package),
                args.shortcut,
                args.solve_timeout,
                resources={
                    "cpu": args.cpu,
                    "mem_gb": args.mem_gb,
                    "disk_gb": args.disk_gb,
                },
                require_paths=args.require_path,
                pretest=(
                    layout.read_pretest(args.pretest_file)
                    if args.pretest_file
                    else None
                ),
            )
        )
    except ValueError as e:
        # The package itself did not check out -- a missing harness file, a
        # malformed layout. Reporting it as a platform error is what made the
        # evolution agent read "the sandbox is broken" and abandon a session it
        # could have fixed, and it is the retry the caller must NOT spend, since
        # nothing about the package changes between attempts.
        verdict = {
            "ok": False,
            "stage": "package_error",
            "why": f"{type(e).__name__}: {e}"[:300],
        }
    except Exception as e:  # noqa: BLE001 -- the caller needs a verdict line
        verdict = {
            "ok": False,
            "stage": "daytona_error",
            "why": f"{type(e).__name__}: {e}"[:300],
        }
    print(json.dumps(verdict), flush=True)
    sys.exit(0 if verdict.get("ok") else 1)


if __name__ == "__main__":
    main()
