#!/usr/bin/env python3
"""How many shipped tasks have a reference solution that admits it is partial.

The dataset card cites 61, a number that came from a collaborator's analysis
rather than from anything measured here. It matters because those tasks cannot
score 1 by construction — the reference deliberately does part of the job — so
they sit in `fail` for a reason that is not a defect, and there is no column
telling them apart.

Streams the task tar, reads each `solution/solve.sh`, and crosses the hits with
`reward_verdict` from the parquet.
"""
from __future__ import annotations

import collections
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent
TAR = ROOT / "data/seed-dataset-clean/data/tasks-00000.tar"
PARQUET = ROOT / "data/seed-dataset-clean/metadata/tasks.parquet"


def main() -> None:
    partial: set[str] = set()
    seen = 0
    with tarfile.open(TAR, "r|*") as tf:
        for member in tf:
            if not member.isfile() or "solve.sh" not in member.name:
                continue
            seen += 1
            fh = tf.extractfile(member)
            if fh is None:
                continue
            body = fh.read().decode("utf-8", "replace")
            if "# Partial:" in body:
                # task id is the top-level directory of the member path
                partial.add(member.name.split("/")[1])

    print(f"solve.sh read: {seen}")
    print(f"marked '# Partial:': {len(partial)}")

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("(no pyarrow — skipping the verdict cross-reference)")
        return

    table = pq.read_table(PARQUET)
    cols = table.column_names
    id_col = next((c for c in ("task_id", "id", "name") if c in cols), None)
    if id_col is None or "reward_verdict" not in cols:
        print(f"(columns are {cols[:8]}… — cannot cross-reference)")
        return

    ids = table.column(id_col).to_pylist()
    verdicts = table.column("reward_verdict").to_pylist()
    counts = collections.Counter(
        v for i, v in zip(ids, verdicts) if i in partial
    )
    print(f"their reward_verdict: {dict(counts)}")
    matched = sum(counts.values())
    print(f"matched in the parquet: {matched} of {len(partial)}")


if __name__ == "__main__":
    main()
