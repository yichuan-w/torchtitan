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
