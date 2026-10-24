#!/usr/bin/env python3
"""Append restart-aware offline W&B history to a live mirror."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from wandb.proto import wandb_internal_pb2
from wandb.sdk.internal.datastore import DataStore
from wandb.sdk.internal.internal_api import Api

import wandb

CURSOR_KEY = "recovery/upload_cursor"


def _history_rows(path: Path) -> list[dict]:
    # Manifold can expose an active run file before W&B has written its LevelDB
    # log header. Treat that short-lived state as an empty history; the autosync
    # loop will retry after the next interval.
    if path.stat().st_size < 7:
        return []

    datastore = DataStore()
    datastore.open_for_scan(str(path))
    rows = []
    while True:
        try:
            raw = datastore.scan_data()
        except AssertionError:
            if datastore.in_last_block():
                break
            raise
        if raw is None:
            break

        record = wandb_internal_pb2.Record()
        record.ParseFromString(raw)
        if record.WhichOneof("record_type") != "history":
            continue

        row = {}
        for item in record.history.item:
            if item.key:
                key = item.key
            elif len(item.nested_key) == 1:
                key = item.nested_key[0]
            else:
                key = ".".join(item.nested_key)
            row[key] = json.loads(item.value_json)
        if "_step" in row:
            rows.append(_collapse_tables(row, path.parent))
    return rows


def _collapse_tables(row: dict, segment_dir: Path) -> dict:
    """W&B stores a logged Table as nested fields (``key._type``, ``key.path``,
    ...) naming a JSON under the run's ``files/``. Replaying the fragments logs
    junk scalars and loses the table, so fold them back into one reference."""
    bases = [
        key[: -len("._type")]
        for key, value in row.items()
        if key.endswith("._type") and value == "table-file"
    ]
    for base in bases:
        prefix = f"{base}."
        row[base] = {
            key[len(prefix) :]: row.pop(key)
            for key in [key for key in row if key.startswith(prefix)]
        }
    for value in row.values():
        if isinstance(value, dict) and value.get("_type") == "table-file":
            value["_segment_dir"] = str(segment_dir)
    return row


def _load_table(ref: dict, manifold_prefix: str | None):
    """The Table a collapsed reference names, fetched from Manifold when the
    autosync loop downloaded only the history file; None if unavailable."""
    segment_dir = Path(ref["_segment_dir"])
    local = segment_dir / "files" / ref["path"]
    if not local.exists() and manifold_prefix:
        local.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "manifold",
                "get",
                f"{manifold_prefix}/{segment_dir.name}/files/{ref['path']}",
                str(local),
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
    try:
        data = json.loads(local.read_text())
        return wandb.Table(columns=data["columns"], data=data["data"])
    except (OSError, ValueError, KeyError) as error:
        print(f"history mirror: table {ref['path']} unavailable ({error}); skipped")
        return None


def _resolve_tables(payload: dict, manifold_prefix: str | None) -> dict:
    resolved = {}
    for key, value in payload.items():
        if isinstance(value, dict) and value.get("_type") == "table-file":
            table = _load_table(value, manifold_prefix)
            if table is not None:
                resolved[key] = table
        else:
            resolved[key] = value
    return resolved


def _as_charts(payload: dict, panels: dict) -> dict:
    """Log a custom chart's table as the chart again (``wandb.plot_table``
    writes ``<key>_table`` and registers the panel), not as a bare table."""
    charted = {}
    for key, value in payload.items():
        panel = panels.get(key[: -len("_table")]) if key.endswith("_table") else None
        if panel is not None and isinstance(value, wandb.Table):
            config = panel["panel_config"]
            charted[key[: -len("_table")]] = wandb.plot_table(
                config["panelDefId"],
                value,
                config.get("fieldSettings") or {},
                config.get("stringSettings") or {},
            )
        else:
            charted[key] = value
    return charted


def _panels_and_summary(paths: list[Path]) -> tuple[dict, dict]:
    """Custom chart panels (config ``_wandb.visualize``) and the latest summary
    value of every key: history replay carries neither."""
    panels: dict = {}
    summary: dict = {}
    for path in paths:
        if path.stat().st_size < 7:
            continue
        datastore = DataStore()
        datastore.open_for_scan(str(path))
        while True:
            try:
                raw = datastore.scan_data()
            except AssertionError:
                if datastore.in_last_block():
                    break
                raise
            if raw is None:
                break
            record = wandb_internal_pb2.Record()
            record.ParseFromString(raw)
            kind = record.WhichOneof("record_type")
            if kind == "config":
                for item in record.config.update:
                    nested = list(item.nested_key) or [item.key]
                    value = json.loads(item.value_json)
                    if nested[:2] == ["_wandb", "visualize"] and len(nested) == 3:
                        panels[nested[2]] = value
                    elif nested == ["_wandb"] and isinstance(value, dict):
                        panels.update(value.get("visualize") or {})
            elif kind == "summary":
                for item in record.summary.update:
                    key = item.key or ".".join(item.nested_key)
                    summary[key] = json.loads(item.value_json)
    return panels, summary


def _stitch_history(paths: list[Path]) -> list[dict]:
    """Merge chronological restart segments, with later segments taking priority."""
    rows_by_step: dict[int, dict] = {}
    for path in paths:
        for row in _history_rows(path):
            step = int(row["_step"])
            merged = rows_by_step.setdefault(step, {"_step": step})
            merged.update(row)
            merged["_step"] = step
    return [rows_by_step[step] for step in sorted(rows_by_step)]


def _last_uploaded_step(status: dict) -> int:
    """Return the last remote history step, or -1 for an empty run."""
    # New online runs can expose a synthetic step-0 tail without history rows.
    # The line count distinguishes that placeholder from an uploaded record.
    if int(status.get("historyLineCount") or 0) == 0:
        return -1
    tail = status.get("historyTail") or []
    if isinstance(tail, str):
        try:
            tail = json.loads(tail)
        except json.JSONDecodeError:
            tail = []
    if isinstance(tail, dict):
        tail = [tail]

    steps = []
    for item in tail:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                continue
        if isinstance(item, dict) and "_step" in item:
            steps.append(int(item["_step"]))
    if steps:
        return max(steps)
    raise RuntimeError("W&B mirror has history but no parseable _step in historyTail")


def _rows_after_step(rows: list[dict], last_uploaded_step: int) -> list[dict]:
    if not rows:
        if last_uploaded_step >= 0:
            raise RuntimeError(
                "W&B mirror has history but the offline history is empty"
            )
        return []

    last_local_step = int(rows[-1]["_step"])
    if last_uploaded_step > last_local_step:
        raise RuntimeError(
            f"W&B mirror is at step {last_uploaded_step}, but stitched offline "
            f"history ends at step {last_local_step}"
        )
    return [row for row in rows if int(row["_step"]) > last_uploaded_step]


def _cursor(raw: object, run_id: str) -> dict:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != 1
        or raw.get("run_id") != run_id
    ):
        raise ValueError("invalid recovery cursor or cursor belongs to another W&B run")
    if not isinstance(raw.get("source_counts"), dict) or not isinstance(
        raw.get("remote_step"), int
    ):
        raise ValueError("invalid recovery cursor fields")
    if any(
        not isinstance(count, int) or count < 0
        for count in raw["source_counts"].values()
    ):
        raise ValueError("invalid per-segment history cursor")
    return raw


def _resume_cursor(status: dict, path: Path, run_id: str) -> tuple[dict, int, bool]:
    """Use remote acknowledgement plus a durable local cursor after a sync crash.

    The cursor travels in every history row and W&B's summary, so a client crash
    after upload but before the local write cannot duplicate already-synced rows.
    """
    last_step = _last_uploaded_step(status)
    candidates = []
    summary = status.get("summaryMetrics") or {}
    if isinstance(summary, str):
        summary = json.loads(summary)
    if CURSOR_KEY in summary:
        candidates.append(_cursor(summary[CURSOR_KEY], run_id))
    tail = status.get("historyTail") or []
    if isinstance(tail, str):
        tail = json.loads(tail)
    if isinstance(tail, dict):
        tail = [tail]
    for row in tail:
        if isinstance(row, str):
            row = json.loads(row)
        if CURSOR_KEY in row:
            candidates.append(_cursor(row[CURSOR_KEY], run_id))
    if path.exists():
        candidates.append(_cursor(json.loads(path.read_text()), run_id))
    if not candidates:
        if last_step >= 0:
            raise ValueError(
                "restart-aware mirroring requires an empty run or an existing recovery cursor"
            )
        return (
            {
                "schema_version": 1,
                "run_id": run_id,
                "source_counts": {},
                "remote_step": -1,
            },
            last_step,
            False,
        )
    latest = max(candidates, key=lambda candidate: candidate["remote_step"])
    # W&B can expose its summary or a completed local upload before historyTail
    # catches up. Wait for acknowledgement rather than resend or drop new rows.
    return latest, last_step, latest["remote_step"] > last_step


def _restart_rows(
    paths: list[Path], cursor: dict, last_uploaded_step: int
) -> tuple[list[dict], dict]:
    """Append each source record once, even after training-step rollback.

    Per-segment offsets also permit a delayed upload from an older attempt to be
    backfilled after newer attempts have already appeared in the dashboard.
    """
    counts = dict(cursor["source_counts"])
    state = {**cursor, "source_counts": counts}
    delivery_step = last_uploaded_step
    missing = []
    for path in paths:
        segment = f"{path.parent.name}/{path.name}"
        for row_index, row in enumerate(_history_rows(path)):
            if row_index < counts.get(segment, 0):
                continue
            delivery_step += 1
            counts[segment] = row_index + 1
            state["remote_step"] = delivery_step
            payload = {key: value for key, value in row.items() if key != "_step"}
            payload.update(
                {
                    "_step": delivery_step,
                    "training/step": row.get("train/policy_version", row["_step"]),
                    "recovery/source_step": row["_step"],
                    "recovery/source_segment": segment,
                    "recovery/source_history_row": row_index,
                    CURSOR_KEY: json.dumps(state, sort_keys=True),
                }
            )
            missing.append(payload)
    return missing, state


def _write_cursor(path: Path, cursor: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(cursor, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "wandb_files",
        type=Path,
        nargs="+",
        help="offline run files in chronological restart order",
    )
    parser.add_argument("--entity", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--restart-aware", action="store_true")
    parser.add_argument("--cursor-file", type=Path)
    parser.add_argument(
        "--manifold-prefix",
        help="Manifold path holding the offline-run-* segments, to fetch logged tables",
    )
    args = parser.parse_args()

    if args.restart_aware and args.cursor_file is None:
        parser.error("--restart-aware requires --cursor-file")
    status = Api().run_resume_status(args.entity, args.project, args.run_id)
    if status is None:
        raise RuntimeError(f"W&B mirror run {args.run_id!r} does not exist")
    last_uploaded_step = _last_uploaded_step(status)
    cursor = None
    if args.restart_aware:
        cursor, last_uploaded_step, awaiting_ack = _resume_cursor(
            status, args.cursor_file, args.run_id
        )
        if awaiting_ack:
            print("history mirror: waiting for remote history acknowledgement")
            return
        missing, cursor = _restart_rows(args.wandb_files, cursor, last_uploaded_step)
    else:
        missing = _rows_after_step(
            _stitch_history(args.wandb_files), last_uploaded_step
        )
    if not missing:
        print(f"history mirror: up to date at step {last_uploaded_step}")
        return

    settings = wandb.Settings(
        console="off",
        x_disable_meta=True,
        x_disable_stats=True,
        x_update_finish_state=False,
    )
    run = wandb.init(
        entity=args.entity,
        project=args.project,
        id=args.run_id,
        resume="must",
        settings=settings,
    )
    if args.restart_aware:
        # Validation first: W&B resolves a key against the globs in definition
        # order, and "*" would otherwise claim validation/* for training/step.
        run.define_metric("validation/policy_version")
        run.define_metric(
            "validation/*", step_metric="validation/policy_version", step_sync=False
        )
        run.define_metric("training/step")
        run.define_metric("*", step_metric="training/step", step_sync=False)
    panels, summary = _panels_and_summary(args.wandb_files)
    for row in missing:
        step = int(row["_step"])
        payload = {key: value for key, value in row.items() if key != "_step"}
        run.log(
            _as_charts(_resolve_tables(payload, args.manifold_prefix), panels),
            step=step,
        )
    history_keys = {key for row in _stitch_history(args.wandb_files) for key in row}
    for key, value in summary.items():
        # Keys only ever written to the summary (e.g. a link), never logged.
        if (
            key.startswith("_")
            or key == CURSOR_KEY
            or key in history_keys
            or key.rsplit(".", 1)[0] in history_keys  # a logged table's fields
        ):
            continue
        if isinstance(value, dict) and "_type" in value:
            continue
        run.summary[key] = value
    run.finish()
    if cursor is not None:
        _write_cursor(args.cursor_file, cursor)
    print(
        f"history mirror: appended {len(missing)} row(s), "
        f"now at delivery step {int(missing[-1]['_step'])}"
    )


if __name__ == "__main__":
    main()
