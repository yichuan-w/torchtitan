# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Per-task decisions read off a task's attempt index (issue #306).

The index is what ``evolve_ondella._index_attempts`` returns: one line per
attempt with ``run``, ``group``, ``rev``, ``reward`` and ``infra_failed``.
"""

from __future__ import annotations

import json
import os

ORIGINAL_SUFFIX = "~orig"


def keep_original() -> bool:
    """EVOLVE_KEEP_ORIGINAL=1: a task's first fold leaves a frozen copy of its
    original row in the mix (see frozen_original). Default off."""
    return os.environ.get("EVOLVE_KEEP_ORIGINAL", "0") == "1"


def original_id(task: str) -> str:
    return task + ORIGINAL_SUFFIX


def frozen_original(line: str) -> tuple[str, str]:
    """The pinned copy of a task's original row, as ``(instance_id, line)``.

    The copy gets its own id, so the trainer serves it beside the revision the
    loop keeps rewriting under the task's id, and ``evolve_frozen``, so its
    groups raise no rewrite signal; its records land under its own id, which
    keeps the task's attempt history to the rewritten line."""
    row = json.loads(line)
    iid = original_id(row["metadata"]["instance_id"])
    row["metadata"]["instance_id"] = iid
    row["metadata"]["evolve_frozen"] = True
    if "label" in row:
        row["label"] = iid
    return iid, json.dumps(row, ensure_ascii=False)


def training_groups(index: list[dict]) -> list[dict]:
    """Scored training groups, one dict each: ``rev``, ``run``, ``group``,
    ``solved``, ``total``. Validation groups (negative group id) and
    infra-failed attempts are left out. Ordered by rev, then run, then group."""
    groups: dict[tuple, list[dict]] = {}
    for e in index:
        if e.get("infra_failed") or e.get("group") is None or e.get("rev") is None:
            continue
        if int(e["group"]) < 0:
            continue
        groups.setdefault((int(e["rev"]), str(e["run"]), int(e["group"])), []).append(e)
    return [
        {
            "rev": rev,
            "run": run,
            "group": group,
            "solved": sum(1 for a in attempts if (a.get("reward") or 0) >= 1.0),
            "total": len(attempts),
        }
        for (rev, run, group), attempts in sorted(groups.items())
    ]


def last_mixed_rev(index: list[dict]) -> dict | None:
    """The latest revision that produced a mixed group (0 < solved < total),
    as that group's ``{rev, run, group, solved, total}``; None if no revision
    has. Revisions only count up, so the highest such rev is the latest."""
    mixed = [g for g in training_groups(index) if 0 < g["solved"] < g["total"]]
    return mixed[-1] if mixed else None


def retire_after_misses() -> int:
    """EVOLVE_RETIRE_AFTER_MISSES: consecutive first-group misses after which a
    task goes back to its last mixed revision and stops being rewritten
    (#306 item 3). 0, the default, never retires."""
    n = int(os.environ.get("EVOLVE_RETIRE_AFTER_MISSES", "0"))
    if n < 0:
        raise ValueError("EVOLVE_RETIRE_AFTER_MISSES must be >= 0")
    return n


def first_group_misses(index: list[dict], current_rev: int) -> int:
    """How many rewritten revisions in a row, ending at ``current_rev``, had a
    first training group that was not mixed. A revision with no group yet ends
    the run, and so does r0, which no rewrite produced."""
    first: dict[int, dict] = {}
    for g in training_groups(index):
        first.setdefault(g["rev"], g)
    misses, rev = 0, current_rev
    while rev >= 1 and rev in first and not 0 < first[rev]["solved"] < first[rev]["total"]:
        misses += 1
        rev -= 1
    return misses


def revert_target(index: list[dict], current_rev: int, needed: int) -> int | None:
    """The revision to go back to once ``needed`` revisions in a row missed on
    their first group: the latest earlier one that produced a mixed group, or
    the original. None while the rule does not apply, including when the
    current revision has produced a mixed group since its first."""
    if needed <= 0 or first_group_misses(index, current_rev) < needed:
        return None
    groups = training_groups(index)
    if any(g["rev"] == current_rev and 0 < g["solved"] < g["total"] for g in groups):
        return None
    mixed = last_mixed_rev([e for e in index if e.get("rev") is not None and int(e["rev"]) < current_rev])
    return mixed["rev"] if mixed else 0


def reverted_at(lineage: list[dict], rev: int) -> int | None:
    """The revision ``rev`` is a copy of, when a retire made it; else None. A
    task whose current revision is one is paused: nothing rewrites it again."""
    for e in lineage:
        if e.get("event") == "fold" and e.get("to_rev") == rev and "reverted_to" in e:
            return int(e["reverted_to"])
    return None


def swap_back_enabled() -> bool:
    """EVOLVE_SWAP_BACK=1: once the policy solves a whole group of an easier
    revision, the task's evolving row leaves the mix and its pinned original
    stays (#306 item 4). Needs EVOLVE_KEEP_ORIGINAL. Default off."""
    return os.environ.get("EVOLVE_SWAP_BACK", "0") == "1"


def produced_by(lineage: list[dict], rev: int) -> str | None:
    """The job of the rewrite whose fold made ``rev`` (``easier``, ``harder``,
    ``revert``); None for r0 or an unrecorded fold. Read off the rewrite
    directory's name, ``<stamp>--<job>``."""
    for e in lineage:
        if e.get("event") == "fold" and e.get("to_rev") == rev and e.get("rewrite"):
            return str(e["rewrite"]).rsplit("--", 1)[-1]
    return None


def swapped_back(lineage: list[dict]) -> bool:
    return any(e.get("event") == "swap_back" for e in lineage)
