#!/usr/bin/env python3
"""A task package as one training row.

TMaxDataset reads a jsonl whose rows are {prompt, label, metadata:{instance_id,
rev, dockerfile, workdir, problem_statement, tmax:{test_sh, fixtures,
reward_path}, build_context}}. ``to_row`` converts one package directory (a
corpus task or an accepted revision ``tasks/<task>/r<N>/``) to that row,
mirroring prepare_rts_data._to_row, so a folded row is indistinguishable from
a freshly prepared one. The loop replaces the row in the mix and publishes a
new version through layout.MixDir; the CLI below builds a whole file from
packages and, given a base, replaces rows in it.

A package carries no pin hook: that lives on the row (``metadata.tmax``), so
the caller hands it in as ``pretest`` and the adapter re-derives this
package's environment identity beside it -- the seed's stamp travels, and a
Dockerfile the rewrite changed shows up as a mismatch that grading skips.

The row's PROTECTED lists (``metadata.tmax.protected_paths`` / ``protected_cmds``,
the integrity baseline grading holds an episode to) come in the same way, as
``protected`` -- or from the package itself: ``tests/protected_paths.json``
({"paths": [...], "cmds": [...]}) is the authoring agent's way to set a
variant's lists, read on the host at pack time and overriding what the caller
passed. tests/ never reaches the solving agent's sandbox before grading, so the
lists are not readable from inside an episode. The keys land on the row through
integrity_baseline.tmax_protected_fields, the one place that decides their shape.

Usage:
  pack_to_dataset.py --evolved data/feedback-r1b --ids results/feedback_r1b_clean_ids.txt \\
      [--base rts_train.jsonl] --out rts_train_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_TT_CANDIDATES = [
    os.environ.get("TRL_TT", ""),
    os.path.expanduser("~/torchtitan"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "torchtitan"),
]


def _checkout_root() -> str:
    # TRL_TT is read here, not at import: the loop's launcher exports it
    # before this module loads, but a caller that sets it afterwards (a test,
    # a tool run by hand) has to be honoured too.
    candidates = [os.environ.get("TRL_TT", ""), *_TT_CANDIDATES[1:]]
    root = next((c for c in candidates if c and os.path.isdir(
        os.path.join(c, "torchtitan"))), None)
    if root is None:
        raise ModuleNotFoundError(
            "no torchtitan checkout found (set TRL_TT); refusing to fold with "
            "a local approximation of prepare_rts_data")
    return root


def _tmax_modules(*leaves: str):
    """Load stdlib-only modules of the tmax example straight from their files,
    registered under their real dotted names, in the order given. A dotted name
    already in sys.modules short-circuits the import machinery, so a module's
    own `from ...prepare_tmax_data import _REWARD_PATH` resolves without running
    torchtitan/experiments/rl/__init__.py -- which imports torch, absent from
    the plain python the evolution loop runs under."""
    import importlib.util
    import sys

    base = "torchtitan.experiments.rl.examples.tmax"
    d = os.path.join(_checkout_root(), *base.split("."))
    for leaf in leaves:
        dotted = f"{base}.{leaf}"
        if dotted in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(
            dotted, os.path.join(d, leaf + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[dotted] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{base}.{leaves[-1]}"]


def _rts_module():
    """Import the training side's own row adapter module.

    This module used to carry a hand-written mirror of prepare_rts_data._to_row.
    The mirror drifted: it dropped ``entrypoint`` (tasks whose environment is a
    service became unsolvable once folded) and never applied the agent-runtime
    injection (tmux -- required by the Terminus agent). Delegating is the fix;
    a silent fallback to a local copy would just re-grow the drift, so a host
    without the torchtitan checkout fails loudly instead.
    """
    return _tmax_modules("prepare_tmax_data", "prepare_rts_data")


def _ib_module():
    """integrity_baseline: the shape of the protected keys, from the one module
    that also digests them at rollout and grading."""
    return _tmax_modules("integrity_baseline")


PROTECTED_FILE = os.path.join("tests", "protected_paths.json")


@dataclass
class Protected:
    """A row's protected lists as the loop carries them: off the reaudit
    parquet's cells, off a mix row being folded, or off a package's own
    tests/protected_paths.json. Iterated as lists, never joined and re-split."""
    paths: list = field(default_factory=list)
    cmds: list = field(default_factory=list)

    @classmethod
    def from_cells(cls, paths_cell, cmds_cell) -> "Protected | None":
        """From the parquet's two JSON-list cells (prepare_tmax_reaudit_data's
        rules: a blank cell or an absent column is nothing; anything else must be
        a JSON list of non-empty strings). None when both are blank, so the keys
        stay absent from the row."""
        paths = _json_list(paths_cell, "protected_paths")
        cmds = _json_list(cmds_cell, "protected_cmds")
        return cls(paths, cmds) if (paths or cmds) else None

    @classmethod
    def from_tmax(cls, tmax: dict) -> "Protected | None":
        """From a row that already carries the keys (the mix row a rewrite
        descends from), or None."""
        paths = list(tmax.get("protected_paths") or [])
        cmds = list(tmax.get("protected_cmds") or [])
        return cls(paths, cmds) if (paths or cmds) else None

    @classmethod
    def from_pretest_file(cls, path) -> "Protected | None":
        """From the snapshot the loop writes beside a rewrite (and copies under
        the package's run/ for the sandbox tool): layout.write_pretest's
        ``protected_paths`` / ``protected_cmds`` keys. None without a file or
        without lists -- the same tolerance as layout.read_pretest."""
        lists = _tmax_modules("layout").read_protected_lists(Path(path))
        if not lists:
            return None
        return cls(list(lists.get("protected_paths") or []),
                   list(lists.get("protected_cmds") or []))


def effective_protected(inherited: "Protected | None", package_dir: str) -> "Protected | None":
    """THE resolver every build of a variant's row goes through: the package's
    own tests/protected_paths.json when it ships one (the authoring agent's
    lists override), else the lists the variant INHERITS from the row it
    descends from. Every caller -- the loop's probe, the agent's sandbox tool
    (boot, oracle, grade), the fold, the mix builder -- hands to_row the same
    inherited lists, so a variant is validated and folded with one and the same
    baseline. Shipped packages carry no file, so inheritance is the real path."""
    own = package_protected(package_dir)
    return own if own is not None else inherited


def _json_list(cell, column: str) -> list:
    if cell is None or not str(cell).strip():
        return []
    try:
        got = json.loads(cell)
    except ValueError:
        raise ValueError(f"{column} is not JSON") from None
    if not isinstance(got, list) or not all(isinstance(p, str) and p.strip() for p in got):
        raise ValueError(f"{column} must be a JSON list of non-empty strings")
    return got


def package_protected(task_dir: str) -> Protected | None:
    """The package's own lists, tests/protected_paths.json: an object with only
    "paths" and "cmds", each a list of non-empty strings (either may be
    missing). Read on the host at pack time; it OVERRIDES what the caller
    passes, and a file with two empty lists clears them. None when the file is
    absent. Malformed -> ValueError naming the file."""
    p = os.path.join(task_dir, PROTECTED_FILE)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            got = json.loads(f.read())
    except (OSError, ValueError) as e:
        raise ValueError(f"{PROTECTED_FILE}: not JSON ({e})") from None
    if not isinstance(got, dict) or set(got) - {"paths", "cmds"}:
        raise ValueError(f'{PROTECTED_FILE}: must be an object with only "paths" and "cmds"')
    for key in ("paths", "cmds"):
        v = got.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise ValueError(f"{PROTECTED_FILE}: {key} must be a list of non-empty strings")
    return Protected(list(got.get("paths", [])), list(got.get("cmds", [])))


def _rts_to_row():
    return _rts_module()._to_row


def fixture_ceiling() -> int:
    """How many bytes a package's COPY sources, and separately its tests/
    fixtures, may add up to before the adapter refuses it. One number, owned by
    prepare_rts_data; read from there so nothing here can drift from it."""
    return _rts_module()._MAX_CONTEXT_BYTES


def to_row(task_dir: str, *, task_id: str | None = None,
           inject_agent_runtime: bool = True,
           pretest: tuple[str, str] | None = None,
           protected: Protected | None = None) -> dict:
    """One package -> one data_path row, exactly as prepare_rts_data builds it
    (entrypoint, oracle_commands, tmux runtime injection included), so a folded
    row is indistinguishable from a freshly prepared one. A revision directory
    is named ``r<N>``, so the loop passes ``task_id``; a corpus directory is
    named after its task and needs nothing. ``pretest`` is the row's pin hook,
    ``(pre_test_sh, pretest_env_identity)`` as the mix row or the dataset
    carries it; None or an empty script adds nothing to the row. ``protected``
    is the row's protected lists the same way; the package's own
    tests/protected_paths.json, when present, replaces it (see
    ``package_protected``). Malformed lists refuse the package by id."""
    row, reason = _rts_to_row()(task_dir, task_id=task_id,
                                inject_agent_runtime=inject_agent_runtime,
                                pretest=pretest)
    if row is None:
        raise ValueError(f"{task_dir}: {reason}")
    ident = task_id or os.path.basename(task_dir.rstrip("/"))
    try:
        lists = effective_protected(protected, task_dir)
        if lists is not None:
            ib = _ib_module()
            try:
                fields = ib.tmax_protected_fields(lists.paths, lists.cmds)
            except ib.IntegrityHarnessError as e:
                raise ValueError(str(e)) from None
            # prepare_rts_data builds metadata.tmax as a plain dict; the keys
            # go beside test_sh / fixtures, absent when both lists are empty.
            row["metadata"]["tmax"].update(fields)
    except ValueError as e:
        raise ValueError(f"{ident}: protected lists: {e}") from None
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evolved", required=True)
    ap.add_argument("--ids", help="only these task ids (a clean-ids file)")
    ap.add_argument("--base", help="base data_path jsonl to fold into (optional)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows, order = {}, []
    if args.base:
        for line in open(args.base):
            if line.strip():
                iid = json.loads(line)["metadata"]["instance_id"]
                rows[iid] = line if line.endswith("\n") else line + "\n"
                order.append(iid)

    keep = {i.strip() for i in open(args.ids)} if args.ids else None
    replaced = added = 0
    for d in sorted(os.listdir(args.evolved)):
        td = os.path.join(args.evolved, d)
        if not os.path.isdir(td) or not os.path.exists(os.path.join(td, "instruction.md")):
            continue
        if keep and d not in keep:
            continue
        row = to_row(td)
        iid = row["label"]
        if iid in rows:
            replaced += 1
        else:
            added += 1
            order.append(iid)
        rows[iid] = json.dumps(row) + "\n"

    with open(args.out, "w") as f:
        for iid in order:
            f.write(rows[iid])
    print(f"folded: {replaced} replaced, {added} added -> {args.out} ({len(order)} rows)")


if __name__ == "__main__":
    main()
