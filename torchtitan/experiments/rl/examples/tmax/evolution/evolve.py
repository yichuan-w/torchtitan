#!/usr/bin/env python3
"""Evolve one task, or make one task easier — as functions, not a batch run.

`synth_loop.py` is a fleet: it takes a seed list, deals them across workers, and
writes a jsonl. That is the wrong shape for a trainer that has one task in hand
and wants it changed now — an RL run that finds a task nobody in the batch solved
should be able to call something and get an easier version back, without
launching a run or parsing a results file.

Two functions, both taking and returning the same dict:

    task = {"instruction": str, "dockerfile": str,
            "solve_sh": str, "test_state_py": str}

    evolve(task)   harder, by one operator, the way a synthesis round does
    simplify(task) easier, aimed at a target solve rate

Neither builds an image or runs anything: they rewrite files and hand them back.
Verifying the result is the caller's, and `synth_loop.run_one` is what does that
when the batch path is wanted.

    from evolve import evolve, simplify
    harder = evolve(task)
    easier = simplify(task, solved=0, attempts=8,
                      trajectory="$ ls /app\\n$ cat run.sh\\n... DONE")

From the shell:
    evolve.py --task-dir data/synth/round_1/syn_tw_123 --mode simplify \\
        --solved 0 --attempts 8 --out data/eased/syn_tw_123
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import synth_client as llm  # noqa: E402
from recorded_solution import ACTION_PATH, parse_actions, solution_path  # noqa: E402

FILES = {"instruction": "instruction.md",
         "dockerfile": "environment/Dockerfile",
         "solve_sh": "solution/solve.sh",
         "test_state_py": "tests/test_state.py"}

# TW packages verify through tests/test_state.py; SWE (Turing Labs) packages
# through tests/test.sh. Otherwise the layout is identical, so only the verifier
# key's path moves. It is detected per package at load(), carried on the task as
# `_verifier_rel`, and used wherever the verifier file is read or written back —
# so a task made easier keeps writing its hint to the file it actually has, not
# to a test_state.py an SWE package never carried.
VERIFIER_CANDIDATES = ("tests/test_state.py", "tests/test.sh")

# Where a package keeps the content of a role when the mapped file is only a
# wrapper. A SWE-Rebench package's solution/solve.sh applies solution/fix.patch
# and its tests/test.sh applies tests/test.patch and runs the graded node ids;
# both scripts are the same in every task of that corpus apart from a commit
# and a list of ids, so anything measuring the role has to read the patch.
# Read on the task as `_role_files`; absent for a package that carries neither.
ROLE_PATCHES = {"solve_sh": "solution/fix.patch", "test_state_py": "tests/test.patch"}


def _verifier_rel(task: dict) -> str:
    return task.get("_verifier_rel", FILES["test_state_py"])


def file_map(task: dict) -> dict[str, str]:
    """FILES for this specific task, with the verifier key's path resolved to
    whichever verifier the package on disk actually carries."""
    return {
        **FILES,
        "test_state_py": _verifier_rel(task),
        "solve_sh": task.get("_solution_rel", FILES["solve_sh"]),
    }


def _to_files(task: dict) -> dict[str, str]:
    return {path: task[key] for key, path in file_map(task).items()}


def _from_files(task: dict, files: dict[str, str]) -> dict:
    return {**task, **{key: files.get(path, task[key])
                       for key, path in file_map(task).items()}}


def load(task_dir: str | Path) -> dict:
    """Read a task package off disk into the dict these functions take.

    The verifier file differs by corpus (TW: tests/test_state.py, SWE:
    tests/test.sh). Whichever the package carries is read into the test_state_py
    key and its path recorded on the task, so writeback lands where it came
    from. A package with neither falls back to the TW path and fails loudly on
    the read, which is the right outcome for a malformed package.
    """
    d = Path(task_dir)
    vrel = next(
        (c for c in VERIFIER_CANDIDATES if (d / c).exists()), FILES["test_state_py"]
    )
    srel = solution_path(d)
    fm = {**FILES, "test_state_py": vrel, "solve_sh": srel}
    task = {key: (d / path).read_text(errors="replace") for key, path in fm.items()}
    task["_verifier_rel"] = vrel
    task["_solution_rel"] = srel
    if srel == ACTION_PATH:
        parse_actions(task["solve_sh"])
    task["_role_files"] = {
        key: rel for key, rel in ROLE_PATCHES.items() if (d / rel).is_file()
    }
    # Where it came from. Only four files round-trip through this dict, but a
    # package is usually more than four -- entrypoints, fixtures, helper
    # modules, the harness's own tests/test.sh -- and anything that has to work
    # on a copy of the package needs those too. See evolve_codex._lay_out.
    task["_src_dir"] = str(d)
    return task


def save(task: dict, task_dir: str | Path) -> Path:
    """Write the dict back out as a task package, provenance included.

    The operator a task was evolved along is written beside it, because that is
    what the diversity terms read back later. Without it a pool is just files
    and the next round's family balance is blind — see `history_from_pool`.
    """
    d = Path(task_dir)
    for key, path in file_map(task).items():
        out = d / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(task[key])
    if task.get("_operator"):
        (d / ".provenance.json").write_text(json.dumps(
            {"operator": task["_operator"], "family": task.get("_family"),
             "parent": task.get("_parent", seed_id_of(task))},
            ensure_ascii=False))
    return d


def seed_id_of(task: dict) -> str:
    return task.get("_seed_id", "unknown")


def history_from_pool(task_dirs) -> tuple[dict, dict]:
    """Reconstruct (used_ops, used_fams) from a live pool of tasks.

    What the diversity terms D(f) and P(o) need is not a log of past calls — it
    is the pool's current composition. In a batch run the two happen to be
    equal, so a counter that advances per call is enough; when the trainer
    evolves one task at a time, the pool is the state, and the trainer already
    holds it. Each task carries the operator it was made with (save writes it),
    so the distribution is read back off disk rather than tracked in parallel —
    which also means it survives a restart: rescan the pool and the counts are
    exactly what they were.
    """
    used_ops: dict[str, int] = {}
    used_fams: dict[str, int] = {}
    for d in task_dirs:
        prov = Path(d) / ".provenance.json"
        if not prov.exists():
            continue
        p = json.loads(prov.read_text())
        if p.get("operator"):
            used_ops[p["operator"]] = used_ops.get(p["operator"], 0) + 1
        if p.get("family"):
            used_fams[p["family"]] = used_fams.get(p["family"], 0) + 1
    return used_ops, used_fams


def evolve(task: dict, seed_id: str = "task", operator: str | None = None,
           used_ops: dict | None = None, used_fams: dict | None = None) -> dict:
    """One synthesis round on a single task: harder, along one operator.

    Picks the operator the same way a batch round does — local fit against the
    operator's own card, damped for family balance and for repeats — unless one
    is named. Raises `synth_client.Blocked` when the model reads the task and
    says no operator on the shortlist fits it.

    `used_ops`/`used_fams` are how the single-task path keeps the diversity that
    a batch run gets from a shared counter. Pass them and the family-balance and
    repeat-penalty terms work exactly as they do in batch; omit them and both
    terms go flat, so a run of independent evolve() calls piles onto whichever
    operator each task fits best. Derive them from the pool with
    `history_from_pool` rather than tracking a counter by hand — the pool is the
    real state, the counter was only ever its stand-in.
    """
    seed = {"task_id": seed_id, "instruction": task["instruction"],
            "dockerfile": task["dockerfile"], "solution": task["solve_sh"],
            "solution_rel": file_map(task)["solve_sh"],
            "env_files": {}}
    if operator:
        fam = next(f for f, ops_ in llm.ops.OPERATORS.items() if operator in ops_)
        definition = llm.ops.OPERATORS[fam][operator]
    else:
        fam, operator, definition = llm.pick_operator(
            seed, used_ops or {}, used_fams or {}, random.Random(0))
    _, files = llm.synthesize(seed, fam, operator, definition)
    return {**_from_files(task, files), "_operator": operator, "_family": fam,
            "_seed_id": seed_id}


# How much guidance a simplification is allowed to write into the instruction.
# This is the knob between "made easier" and "gave the answer away": a hint
# specific enough to unblock the agent is also specific enough to leak the
# solution, so the level is a deliberate choice, not a default to drift on. It
# also decides whether a transcript is needed at all — only the specific level
# reads one, which is the flag for "we don't need to look at the trace".
HINT_LEVELS = {
    "none": (
        "Take difficulty off structurally only: remove a stage, relax a "
        "constraint, or state an assumption the task left implicit. Do NOT add "
        "any guidance to the instruction about how to solve it — it should ask "
        "for less, not explain more. No transcript is needed for this."),
    "vague": (
        "You may add a directional hint to the instruction — the kind of "
        "artifact to produce, where results belong, which family of tool fits — "
        "but no steps, and nothing that reveals the answer. Prefer taking "
        "difficulty off structurally; add the hint only if that is not enough."),
    "specific": (
        "You may add a concrete hint aimed at exactly where the transcript shows "
        "the agent stuck: the command it kept missing, the file it never opened, "
        "the check it did not know about. Give the smallest hint that clears that "
        "one failure, never the full method — a task the instruction walks "
        "through step by step is no longer worth training on."),
}


def simplify(task: dict, solved: int = 0, attempts: int = 8,
             trajectory: str = "", target: str = "about half",
             hint: str = "vague") -> dict:
    """Make one task easier, aimed at a solve rate rather than at "easier".

    `solved` and `attempts` are what a solver actually managed, and they are the
    point: "make this easier" has no stopping condition, "0 of 8 should become
    about half" does.

    `hint` decides how much the simplification may write into the instruction,
    and with it whether a transcript is needed:

      none      difficulty comes off structurally; the instruction gains no
                guidance. No transcript used.
      vague     a directional hint is allowed — what kind of thing to produce,
                not how. Transcript optional.
      specific  a hint aimed at the exact point the agent got stuck, drawn from
                the transcript. Needs one; without a transcript it falls back to
                vague, and the returned task's `_hint` says which level actually
                applied.

    The verifier is left alone regardless: a task made easier by weakening what
    it checks is a task worth less, and the instruction is where difficulty comes
    off first.
    """
    if hint not in HINT_LEVELS:
        raise ValueError(f"hint must be one of {list(HINT_LEVELS)}")
    # The specific level is the only one that reads the transcript, so without
    # one it has nothing to aim at and is exactly the vague level.
    effective = "vague" if hint == "specific" and not trajectory else hint

    finding = (f"too_hard: an agent solved this {solved} of {attempts} attempts. "
               f"The target is {target}.\n\n{HINT_LEVELS[effective]}")
    if effective != "none":
        # A hint that names the verifier is a leak, not a hint: for an SWE task
        # the failing test IS the answer, so "run pytest tests/test_x.py::t" hands
        # it over. The pipeline's audit rejects such a task, so the retune is
        # wasted unless the prohibition is stated up front. Point at the behavior
        # to fix, never at the check that grades it.
        finding += (
            "\n\nHard rule for the hint: never name the verifier. No test file "
            "paths (tests/...), no test or function names, no pytest or test.sh "
            "command to run. Describe the behavior to fix or where to look in the "
            "source -- naming the test that grades it gives the answer away and "
            "the task is rejected.")
    if trajectory and effective == "specific":
        # Only the specific level consumes the transcript. Collection is
        # lossless; trimming happens here, at consumption, where the caller still
        # holds the full traces. The cap matches REPAIR_CONTEXT: several full
        # attempts fit, bounded only so one pathological transcript cannot crowd
        # the files out of the prompt.
        finding += f"\n\nWhat the agent did:\n{trajectory[:llm.REPAIR_CONTEXT]}"
    files = llm._repair(llm.RETUNE, {}, _to_files(task), "{}", finding=finding)
    out = _from_files(task, files)
    out["_hint"] = effective
    return out


def repair_oracle(task: dict, observed: str, exit_code: int = 1) -> dict:
    """Repair a synthesized task whose reference solution failed the real run.

    `synthesize` already runs a blind oracle-repair pass, but it reads the files
    only -- it has never seen the task execute. The failure that matters shows up
    later, when the package is actually built and solve.sh is run against the
    verifier, and that run's output is information the blind pass never had.
    Feeding it back is the difference between reconciling two files by reading
    and fixing the check that demonstrably failed.

    Returns the task with whatever files the repair rewrote; on any failure it
    returns the task unchanged, so the caller's own revalidation stays the gate.
    """
    files = llm._repair(llm.ORACLE_REPAIR_OBSERVED, {}, _to_files(task), "{}",
                        observed=(observed or "")[-llm.REPAIR_CONTEXT:],
                        exit_code=exit_code)
    out = _from_files(task, files)
    out["_repaired"] = "oracle_observed"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", required=True)
    ap.add_argument("--mode", choices=("evolve", "simplify"), default="evolve")
    ap.add_argument("--operator", help="evolve only; default is to choose one")
    ap.add_argument("--solved", type=int, default=0)
    ap.add_argument("--attempts", type=int, default=8)
    ap.add_argument("--trajectory-file")
    ap.add_argument("--hint", choices=tuple(HINT_LEVELS), default="vague",
                    help="simplify only: how much guidance to add to the "
                         "instruction. 'specific' reads the transcript; "
                         "'none'/'vague' do not")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    task = load(args.task_dir)
    if args.mode == "evolve":
        out = evolve(task, seed_id=Path(args.task_dir).name,
                     operator=args.operator)
    else:
        traj = (Path(args.trajectory_file).read_text(errors="replace")
                if args.trajectory_file else "")
        out = simplify(task, solved=args.solved, attempts=args.attempts,
                       trajectory=traj, hint=args.hint)
    save(out, args.out)
    changed = [p for k, p in file_map(out).items() if out[k] != task[k]]
    print(json.dumps({"out": args.out, "operator": out.get("_operator"),
                      "changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
