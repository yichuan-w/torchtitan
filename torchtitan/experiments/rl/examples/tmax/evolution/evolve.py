#!/usr/bin/env python3
"""Load and save task packages for the evolution agent and its validators."""

from __future__ import annotations

from pathlib import Path

from recorded_solution import ACTION_PATH, parse_actions, solution_path


FILES = {
    "instruction": "instruction.md",
    "dockerfile": "environment/Dockerfile",
    "solve_sh": "solution/solve.sh",
    "test_state_py": "tests/test_state.py",
}

VERIFIER_CANDIDATES = ("tests/test_state.py", "tests/test.sh")
ROLE_PATCHES = {"solve_sh": "solution/fix.patch", "test_state_py": "tests/test.patch"}


def _verifier_rel(task: dict) -> str:
    return task.get("_verifier_rel", FILES["test_state_py"])


def file_map(task: dict) -> dict[str, str]:
    """Return the actual paths of the four task roles for this package."""
    return {
        **FILES,
        "test_state_py": _verifier_rel(task),
        "solve_sh": task.get("_solution_rel", FILES["solve_sh"]),
    }


def load(task_dir: str | Path) -> dict:
    """Read the task roles and record which verifier and solution paths exist."""
    directory = Path(task_dir)
    verifier = next(
        (candidate for candidate in VERIFIER_CANDIDATES if (directory / candidate).exists()),
        FILES["test_state_py"],
    )
    solution = solution_path(directory)
    paths = {**FILES, "test_state_py": verifier, "solve_sh": solution}
    task = {
        key: (directory / path).read_text(errors="replace")
        for key, path in paths.items()
    }
    task["_verifier_rel"] = verifier
    task["_solution_rel"] = solution
    if solution == ACTION_PATH:
        parse_actions(task["solve_sh"])
    task["_role_files"] = {
        key: path for key, path in ROLE_PATCHES.items() if (directory / path).is_file()
    }
    task["_src_dir"] = str(directory)
    return task


def save(task: dict, task_dir: str | Path) -> Path:
    """Write the task roles to a package directory."""
    directory = Path(task_dir)
    for key, path in file_map(task).items():
        output = directory / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(task[key])
    return directory
