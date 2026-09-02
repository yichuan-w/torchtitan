#!/usr/bin/env python3
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
from torchtitan.experiments.rl.examples.tmax.grading import (  # noqa: E402
    grade_tmax,
    seed_workspace,
)


class _Root:
    """Force every sandbox operation to run as root (tmax tasks touch system
    paths). Local mirror of the rollouter's _RootSandbox so this probe does not
    import the rollouter module (which drags the training stack in)."""

    def __init__(self, inner):
        self._inner = inner

    async def exec(self, cmd, *, check=False, timeout=None, **kw):
        kw.pop("user", None)
        return await self._inner.exec(cmd, user="root", check=check,
                                      timeout=timeout, **kw)

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
        check=False, timeout=120)
    await asyncio.sleep(3)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


async def probe(pkg: Path, shortcut: str | None, solve_timeout: int) -> dict:
    row = pack.to_row(str(pkg))
    md = row["metadata"]
    tmax = md["tmax"]
    workdir = md.get("workdir") or "/workspace"
    sol_dir = pkg / "solution"
    if shortcut is None and not (sol_dir / "solve.sh").exists():
        return {"ok": False, "stage": "no_solution",
                "why": "package ships no solution/solve.sh"}

    log(f"boot sandbox for {md['instance_id']} "
        f"({'shortcut probe' if shortcut else 'oracle'})")
    async with boot_agent_sandbox(
        md.get("image") or "",
        dockerfile=md.get("dockerfile") or None,
        build_context=md.get("build_context") or None,
        install_claude=False,
        disk_gb=md.get("daytona_disk_gb"),
    ) as sandbox:
        sb = _Root(sandbox)
        if md.get("entrypoint"):
            await _start_entrypoint(sb, md["entrypoint"], workdir=workdir)
        await seed_workspace(sb, tmax)
        if shortcut is None:
            for f in sorted(sol_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(sol_dir)
                    await sb.write_file(
                        f"/solution/{rel}", f.read_text(errors="replace"))
            cmd = "bash /solution/solve.sh"
        else:
            cmd = shortcut
        code, out, err = await sb.exec(cmd, check=False, timeout=solve_timeout)
        log(f"run exit={code}")
        reward = await grade_tmax(sb, tmax, workdir=workdir)
    tail = (out + "\n" + err)[-400:]
    if shortcut is None:
        return {"ok": reward >= 1.0, "stage": "daytona_oracle",
                "reward": reward, "solve_exit": code, "tail": tail}
    return {"ok": True, "stage": "daytona_shortcut",
            "passed": reward >= 1.0, "reward": reward, "tail": tail}


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
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 -- provenance is best effort
        pass
    dirty = ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        n = len([x for x in out.splitlines() if x.strip()])
        dirty = f", {n} uncommitted" if n else ""
    except Exception:  # noqa: BLE001
        pass
    return f"{root} [{head or 'no git'}{dirty}]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", help="task package dir")
    ap.add_argument("--shortcut", help="probe this cheat command instead of "
                                       "running the reference solution")
    ap.add_argument("--solve-timeout", type=int, default=900)
    args = ap.parse_args()
    log(f"harness: {_harness_provenance()}")
    try:
        verdict = asyncio.run(
            probe(Path(args.package), args.shortcut, args.solve_timeout))
    except ValueError as e:
        # The package itself did not check out -- a missing harness file, a
        # malformed layout. Reporting it as a platform error is what made the
        # evolution agent read "the sandbox is broken" and abandon a session it
        # could have fixed, and it is the retry the caller must NOT spend, since
        # nothing about the package changes between attempts.
        verdict = {"ok": False, "stage": "package_error",
                   "why": f"{type(e).__name__}: {e}"[:300]}
    except Exception as e:  # noqa: BLE001 -- the caller needs a verdict line
        verdict = {"ok": False, "stage": "daytona_error",
                   "why": f"{type(e).__name__}: {e}"[:300]}
    print(json.dumps(verdict), flush=True)
    sys.exit(0 if verdict.get("ok") else 1)


if __name__ == "__main__":
    main()
