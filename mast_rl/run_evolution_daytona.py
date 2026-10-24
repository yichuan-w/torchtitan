#!/usr/bin/env python3
"""Run evolution through Daytona with local recorded-action safeguards."""

from __future__ import annotations

import os
import runpy
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVOLUTION_CLI = (
    REPO_ROOT / "torchtitan/experiments/rl/examples/tmax/evolution/evolve_ondella.py"
)
EVOLUTION_DIR = EVOLUTION_CLI.parent


def _codex_env_with_local_state(
    original_codex_env,
    local_state_root: Path,
    trl_base: Path,
    work_root: Path | None,
    session,
):
    session_path = session.path.resolve()
    if work_root is not None and session_path.is_relative_to(work_root.resolve()):
        return original_codex_env(session)
    try:
        relative = session_path.relative_to(trl_base.resolve())
    except ValueError as error:
        raise RuntimeError(
            f"Codex session {session.path} is outside TRL_BASE {trl_base} "
            f"and EVOLVE_WORK_ROOT {work_root}"
        ) from error
    local_home = local_state_root / relative / "codex"
    shared_home = session.codex_home
    if shared_home.is_symlink():
        if shared_home.resolve() != local_home.resolve():
            raise RuntimeError(
                f"Codex home link {shared_home} points outside its local state"
            )
    else:
        local_home.mkdir(parents=True, mode=0o700, exist_ok=True)
        local_home.chmod(0o700)
        if shared_home.is_dir():
            for child in shared_home.iterdir():
                target = local_home / child.name
                if target.exists() or target.is_symlink():
                    raise RuntimeError(f"Codex local state already contains {target}")
                shutil.move(str(child), str(target))
            shared_home.rmdir()
        elif shared_home.exists():
            raise RuntimeError(f"Codex home is not a directory: {shared_home}")
        shared_home.symlink_to(local_home, target_is_directory=True)
    return original_codex_env(session)


def main() -> None:
    if os.environ.get("TT_SANDBOX_BACKEND") != "daytona":
        raise SystemExit("TT_SANDBOX_BACKEND=daytona is required")

    system_which = shutil.which

    def which_without_local_docker(command: str, *args, **kwargs):
        if command == "docker":
            return None
        return system_which(command, *args, **kwargs)

    # feedback_loop prefers any usable local Docker-compatible daemon over
    # Daytona. This devvm exposes rootless Podman as docker, but this run's
    # validation backend is deliberately Daytona. Hide only that executable
    # for this process; all other command discovery is unchanged.
    shutil.which = which_without_local_docker

    # Keep Codex's private state on the local filesystem. OilFS is the shared
    # source of truth for the evolution root, but it does not implement one of
    # the filesystem operations used by Codex's in-process app server. Each
    # session still exposes its private home through a symlink under sessions/.
    sys.path.insert(0, str(EVOLUTION_DIR))
    import evolve_codex

    original_codex_env = evolve_codex._codex_env
    local_state_root = Path(
        os.environ.get(
            "EVOLVE_CODEX_LOCAL_STATE_ROOT",
            str(
                Path(os.environ.get("TMAX_LOCAL_ROOT") or Path.home() / "rl")
                / ".tmax_codex_state/default"
            ),
        )
    )
    trl_base = Path(os.environ["TRL_BASE"])
    configured_work_root = os.environ.get("EVOLVE_WORK_ROOT", "").strip()
    work_root = Path(configured_work_root) if configured_work_root else None

    def codex_env_with_local_state(session):
        return _codex_env_with_local_state(
            original_codex_env,
            local_state_root,
            trl_base,
            work_root,
            session,
        )

    evolve_codex._codex_env = codex_env_with_local_state

    # Normalize malformed recorded-action forms before upstream validates a
    # rewritten package. This remains useful for any non-agentic repair path.
    import feedback_loop
    from recorded_action_guard import install
    from recorded_solution import parse_actions

    install(feedback_loop, parse_actions)
    # --ladder <job> ...: the overfit bench's ladder rewriter instead of the loop.
    entry = EVOLUTION_CLI
    if sys.argv[1:2] == ["--ladder"]:
        entry = EVOLUTION_DIR.parent / "overfit_bench/ladder_rewriter.py"
        del sys.argv[1]
    sys.argv[0] = str(entry)
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
