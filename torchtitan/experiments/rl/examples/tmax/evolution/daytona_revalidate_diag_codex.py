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
import os
import sys
import time
from pathlib import Path

# torchtitan is imported normally; PYTHONPATH picks the checkout, the same way
# the training launchers do it. This used to prepend a default checkout to
# sys.path, which made the choice invisible and, worse, made it win over the
# caller's: a caller that set its own path saw this module's default silently
# take precedence at import time, and then verified against a harness nobody
# was going to run. Failing to import is the honest outcome when nothing is
# configured, so that is what happens.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack_to_dataset as pack  # noqa: E402

import shlex  # noqa: E402

try:
    from torchtitan.experiments.rl.harness.agents.claude_code import (  # noqa: E402
        boot_agent_sandbox,
    )
except ModuleNotFoundError as exc:  # pragma: no cover -- configuration, not logic
    raise SystemExit(
        "cannot import torchtitan: set PYTHONPATH to a torchtitan checkout, "
        "e.g. PYTHONPATH=$HOME/torchtitan-yichuan (what the training run uses) "
        f"or a fresh clone of the canonical branch. ({exc})"
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


async def _start_entrypoint(sb, command: str, *, workdir: str) -> None:
    """Start the image ENTRYPOINT detached, as PID 1 would (rollouter mirror)."""
    await sb.exec(
        f"cd {shlex.quote(workdir)} 2>/dev/null || cd /; "
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
            cmd = f"cd {workdir} && bash /solution/solve.sh"
        else:
            cmd = f"cd {workdir} && {shortcut}"
        code, out, err = await sb.exec(cmd, check=False, timeout=solve_timeout)
        log(f"run exit={code}")
        reward = await grade_tmax(sb, tmax, workdir=workdir)
        ctrf = await sb.read_file("/logs/verifier/ctrf.json", user="root")
    tail = (out + "\n" + err)[-400:]
    if shortcut is None:
        return {"ok": reward >= 1.0, "stage": "daytona_oracle",
                "reward": reward, "solve_exit": code, "tail": tail, "ctrf": ctrf}
    return {"ok": True, "stage": "daytona_shortcut",
            "passed": reward >= 1.0, "reward": reward, "tail": tail}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", help="task package dir")
    ap.add_argument("--shortcut", help="probe this cheat command instead of "
                                       "running the reference solution")
    ap.add_argument("--solve-timeout", type=int, default=900)
    args = ap.parse_args()
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
