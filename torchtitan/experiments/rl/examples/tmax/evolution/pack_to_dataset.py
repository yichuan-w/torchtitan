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

Usage:
  pack_to_dataset.py --evolved data/feedback-r1b --ids results/feedback_r1b_clean_ids.txt \\
      [--base rts_train.jsonl] --out rts_train_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
import os

_TT_CANDIDATES = [
    os.environ.get("TRL_TT", ""),
    os.path.expanduser("~/torchtitan"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "torchtitan"),
]


def _rts_module():
    """Import the training side's own row adapter module.

    This module used to carry a hand-written mirror of prepare_rts_data._to_row.
    The mirror drifted: it dropped ``entrypoint`` (tasks whose environment is a
    service became unsolvable once folded) and never applied the agent-runtime
    injection (tmux -- required by the Terminus agent). Delegating is the fix;
    a silent fallback to a local copy would just re-grow the drift, so a host
    without the torchtitan checkout fails loudly instead.
    """
    import importlib.util
    import sys

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

    # Load the two adapter modules straight from their files, registered under
    # their real dotted names. A dotted name already in sys.modules short-
    # circuits the import machinery, so prepare_rts_data's own
    # `from ...prepare_tmax_data import _REWARD_PATH` resolves without running
    # torchtitan/experiments/rl/__init__.py -- which imports torch, absent from
    # the plain python the evolution loop runs under. Both files are stdlib-only.
    base = "torchtitan.experiments.rl.examples.tmax"
    d = os.path.join(root, *base.split("."))
    for leaf in ("prepare_tmax_data", "prepare_rts_data"):
        dotted = f"{base}.{leaf}"
        if dotted in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(
            dotted, os.path.join(d, leaf + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[dotted] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{base}.prepare_rts_data"]


def _rts_to_row():
    return _rts_module()._to_row


def fixture_ceiling() -> int:
    """How many bytes a package's COPY sources, and separately its tests/
    fixtures, may add up to before the adapter refuses it. One number, owned by
    prepare_rts_data; read from there so nothing here can drift from it."""
    return _rts_module()._MAX_CONTEXT_BYTES


def to_row(task_dir: str, *, task_id: str | None = None,
           inject_agent_runtime: bool = True,
           pretest: tuple[str, str] | None = None) -> dict:
    """One package -> one data_path row, exactly as prepare_rts_data builds it
    (entrypoint, oracle_commands, tmux runtime injection included), so a folded
    row is indistinguishable from a freshly prepared one. A revision directory
    is named ``r<N>``, so the loop passes ``task_id``; a corpus directory is
    named after its task and needs nothing. ``pretest`` is the row's pin hook,
    ``(pre_test_sh, pretest_env_identity)`` as the mix row or the dataset
    carries it; None or an empty script adds nothing to the row."""
    row, reason = _rts_to_row()(task_dir, task_id=task_id,
                                inject_agent_runtime=inject_agent_runtime,
                                pretest=pretest)
    if row is None:
        raise ValueError(f"{task_dir}: {reason}")
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
