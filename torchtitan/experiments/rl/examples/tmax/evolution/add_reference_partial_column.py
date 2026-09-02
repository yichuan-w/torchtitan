#!/usr/bin/env python3
"""Mark the tasks whose reference solution admits it only does part of the job.

Those solutions carry a `# Partial:` comment and cannot score 1 by construction,
so they land in `reward_verdict == "fail"` for a reason that is not a defect —
the instruction still describes the whole job, and an agent can do more than the
reference does. Without a column saying so they are indistinguishable from
solutions that ran and were simply wrong, and the card's own advice (filter on
`pass`) silently discards them.

A note in the card only reaches whoever read the card. A column reaches
everyone.

Writes a new parquet beside the old one, then swaps, keeping a dated backup.
"""
from __future__ import annotations

import collections
import pathlib
import shutil
import tarfile
import time

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parent
TAR = ROOT / "data/seed-dataset-clean/data/tasks-00000.tar"
PARQUET = ROOT / "data/seed-dataset-clean/metadata/tasks.parquet"


def partial_task_ids() -> set[str]:
    ids: set[str] = set()
    with tarfile.open(TAR, "r|*") as tf:
        for member in tf:
            if not member.isfile() or "solve.sh" not in member.name:
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            if "# Partial:" in fh.read().decode("utf-8", "replace"):
                ids.add(member.name.split("/")[1])
    return ids


def main() -> None:
    partial = partial_task_ids()
    print(f"solutions marked '# Partial:': {len(partial)}")

    table = pq.read_table(PARQUET)
    if "reference_partial" in table.column_names:
        print("column already present")
        return

    id_col = next(c for c in ("task_id", "id", "name") if c in table.column_names)
    ids = table.column(id_col).to_pylist()
    flags = [i in partial for i in ids]
    print(f"matched in the parquet: {sum(flags)} of {len(partial)}")

    verdicts = table.column("reward_verdict").to_pylist()
    breakdown = collections.Counter(v for v, f in zip(verdicts, flags) if f)
    print(f"their reward_verdict: {dict(breakdown)}")

    table = table.append_column(
        "reference_partial", pa.array(flags, type=pa.bool_())
    )

    backup = PARQUET.with_suffix(f".parquet.bak-{time.strftime('%Y%m%d')}")
    shutil.copy2(PARQUET, backup)
    tmp = PARQUET.with_suffix(".parquet.new")
    pq.write_table(table, tmp)
    tmp.replace(PARQUET)
    print(f"column written; previous file kept at {backup.name}")


if __name__ == "__main__":
    main()
