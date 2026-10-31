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
