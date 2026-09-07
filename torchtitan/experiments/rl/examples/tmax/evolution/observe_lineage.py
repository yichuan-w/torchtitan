# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Join observer inputs for the unchanged cohort, rewrite comparison and timeline."""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import logging
import re
from pathlib import Path

import layout

LOG = logging.getLogger("observe_rewards")


def milliseconds(value: str | None) -> float | None:
    if not value:
        return None
    if re.fullmatch(r"\d{8}-\d{6}Z", value):
        return layout.parse_stamp(value) * 1000
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000


def unchanged(rows: list[dict], folded: set[str]) -> dict:
    """Recompute all epoch points over one intersection, never a moving average."""
    epochs = sorted({r["epoch"] for r in rows if r["epoch"] is not None})
    groups = {epoch: collections.defaultdict(list) for epoch in epochs}
    for row in rows:
        if (
            row["rev"] == 0
            and row["task"] not in folded
            and row["scored"]
            and row["sample_revision"]
            and row["epoch"] in groups
        ):
            groups[row["epoch"]][(row["task"], row["sample_revision"])].append(row)
    cohort = set.intersection(*(set(g) for g in groups.values())) if groups else set()
    points = []
    for epoch, members in groups.items():
        chosen = [row for key in sorted(cohort) for row in members[key]]
        scored = sum(r["scored"] for r in chosen)
        policies = [r["policy_at_claim"] for r in chosen]
        points.append(
            {
                "epoch": epoch,
                "tasks": len(cohort),
                "attempts": scored,
                "accuracy": sum(r["solved"] for r in chosen) / scored
                if scored
                else None,
                "policy_min": min(policies) if policies else None,
                "policy_max": max(policies) if policies else None,
                "coverage": "latest epoch may still be arriving"
                if epoch == epochs[-1]
                else "observed",
            }
        )
    return {"cohort": [list(key) for key in sorted(cohort)], "points": points}


def trace_evidence(rewrite: Path, cache: Path) -> dict:
    """Report observed tool output, never infer causal use from a path mention."""
    files = sorted(rewrite.glob("sessions/*--agent/codex/sessions/**/*.jsonl"))
    inputs = [
        {"path": str(p), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
        for p in files
    ]
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    target = cache / f"{key}.json"
    if target.exists():
        return json.loads(target.read_text())
    calls = {}
    evidence = []
    for path in files:
        with path.open() as stream:
            for number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    break  # The active session can still be appending this record.
                record = json.loads(line)
                payload = record.get("payload") or {}
                if record.get("type") != "response_item":
                    continue
                if payload.get("type") == "function_call":
                    arguments = payload.get("arguments", "")
                    if "traces/" in arguments and re.search(
                        r"\b(cat|head|tail|sed|jq|rg|grep|read_text|open)\b", arguments
                    ):
                        calls[payload["call_id"]] = {
                            "source": f"{path}:{number}",
                            "call": payload,
                        }
                elif payload.get("type") == "function_call_output":
                    call = calls.get(payload.get("call_id"))
                    if call:
                        output = payload.get("output") or ""
                        if not isinstance(output, str):
                            output = json.dumps(output)
                        body = bool(
                            re.search(
                                r'"(?:turn|raw|keystrokes)"\s*:|\bturn \d+:', output
                            )
                        )
                        evidence.append(
                            {**call, "output": payload, "body_observed": body}
                        )
    result = {
        "inputs": inputs,
        "evidence": evidence,
        "status": (
            "Trace body in tool output"
            if any(e["body_observed"] for e in evidence)
            else "Trace command observed; body unconfirmed"
            if evidence
            else "No trace-read evidence found"
            if files
            else "Session unavailable"
        ),
        "causal_use": "Not established by tool access",
    }
    layout.write_json_atomic(target, result)
    return result


def build(
    root,
    run,
    rows: list[dict],
    folds: list[dict],
    events: list[dict],
    output: Path,
    task_filter: str | None = None,
) -> dict:
    """Keep missing next observations missing; timestamps alone do not prove adoption."""
    cache = output / "trace-evidence"
    cache.mkdir(exist_ok=True)
    by_group = {r["group"]: r for r in rows}
    admissions = {}
    trained = {}
    epoch_starts = {}
    finalized = {
        e["group_id"]: milliseconds(e["timestamp"])
        for e in events
        if e.get("event") == "finalized"
    }
    revisions = {}
    samples = run.trainer / "training_lineage/samples.jsonl"
    if samples.exists():
        with samples.open() as stream:
            for line in stream:
                if not line.endswith("\n"):
                    break
                sample = json.loads(line)
                revisions[sample["sample_revision"]] = sample["input"].get("rev")
    timeline = []
    for event in events:
        kind = event.get("event")
        if kind not in {"admitted", "trained"}:
            continue
        group = event["group_id"]
        instant = milliseconds(event["timestamp"])
        if kind == "admitted":
            admissions[group] = event
            epoch = event["dataset_epoch"]
            epoch_starts[epoch] = min(epoch_starts.get(epoch, instant), instant)
        else:
            trained[group] = event
        if task_filter and event["task_id"] != task_filter:
            continue
        revision = revisions.get(event.get("sample_revision"))
        row = by_group.get(group, {})
        label = (
            f"Take r{revision} · epoch {event.get('dataset_epoch')}"
            if kind == "admitted"
            else f"Train r{revision} · Step {event.get('train_step')}"
        )
        timeline.append(
            {
                "task": event["task_id"],
                "event": kind,
                "time": instant,
                "label": label,
                "epoch": event.get("dataset_epoch"),
                "step": event.get("train_step"),
                "revision": revision,
                "accuracy": row.get("solved", 0) / row["scored"]
                if row.get("scored")
                else None,
                "evidence": "",
                "source": f"group {group}",
            }
        )
    for epoch, instant in epoch_starts.items():
        timeline.append(
            {
                "task": "",
                "event": "epoch",
                "time": instant,
                "label": f"Epoch {epoch} starts",
                "epoch": epoch,
                "step": None,
                "revision": None,
                "accuracy": None,
                "evidence": "",
                "source": "first admission in this epoch",
            }
        )
    comparisons = []
    rewrite_inputs = []
    for task in root.evolution.task_dirs():
        if task_filter and task.task_id != task_filter:
            continue
        for rewrite in task.rewrite_dirs():
            if not rewrite.meta.exists():
                continue
            meta = json.loads(rewrite.meta.read_text())
            if not str(meta.get("signal", "")).startswith(run.name + "/"):
                continue
            LOG.info(
                "rewrite start task=%s rewrite=%s", task.task_id, rewrite.path.name
            )
            evidence = trace_evidence(rewrite.path, cache)
            rewrite_inputs.append({"meta": meta, "trace_inputs": evidence["inputs"]})
            source = f"{task.task_id}/{rewrite.path.name}"
            for kind, field in (("rewrite", "started"), ("rewrite_end", "finished")):
                if meta.get(field):
                    timeline.append(
                        {
                            "task": task.task_id,
                            "event": kind,
                            "time": milliseconds(meta[field]),
                            "epoch": None,
                            "step": None,
                            "revision": meta.get("input_rev"),
                            "accuracy": None,
                            "label": (
                                f"Rewrite r{meta.get('input_rev')}"
                                if kind == "rewrite"
                                else f"Rewrite {meta.get('status')}"
                            ),
                            "evidence": evidence["status"],
                            "source": source,
                        }
                    )
            LOG.info(
                "rewrite done task=%s rewrite=%s trace=%s",
                task.task_id,
                rewrite.path.name,
                evidence["status"],
            )
    for fold in folds:
        instant = milliseconds(fold["stamp"])
        tid = fold["task"]
        old = [
            r
            for r in rows
            if r["task"] == tid
            and r["rev"] == fold["from_rev"]
            and r["scored"]
            and r["group"] in admissions
            and finalized.get(r["group"], float("inf")) < instant
        ]
        new = [
            r
            for r in rows
            if r["task"] == tid
            and r["rev"] == fold["to_rev"]
            and r["scored"]
            and r["group"] in admissions
            and milliseconds(admissions[r["group"]]["timestamp"]) >= instant
        ]
        before = max(old, key=lambda r: r["group"]) if old else None
        after = min(new, key=lambda r: r["group"]) if new else None
        timeline.append(
            {
                "task": tid,
                "event": "fold",
                "time": instant,
                "label": f"Fold r{fold['from_rev']} → r{fold['to_rev']}",
                "epoch": None,
                "step": None,
                "revision": fold["to_rev"],
                "accuracy": None,
                "evidence": "",
                "source": fold["rewrite"],
            }
        )
        if before and after:
            comparisons.append(
                {
                    "task": tid,
                    "from_rev": fold["from_rev"],
                    "to_rev": fold["to_rev"],
                    "before": before["solved"] / before["scored"],
                    "after": after["solved"] / after["scored"],
                    "before_policy": before["policy_at_claim"],
                    "after_policy": after["policy_at_claim"],
                    "before_n": before["scored"],
                    "after_n": after["scored"],
                    "before_group": before["group"],
                    "after_group": after["group"],
                    "after_step": trained.get(after["group"], {}).get("train_step"),
                }
            )
        else:
            LOG.info(
                "comparison skip task=%s revision=%s reason=%s",
                tid,
                fold["to_rev"],
                "old observation unavailable before fold"
                if not before
                else "new observation not ready",
            )
    return {
        "unchanged": unchanged(rows, {f["task"] for f in folds}),
        "comparisons": comparisons,
        "timeline": sorted(timeline, key=lambda e: e["time"]),
        "inputs": {"events": events, "rewrites": rewrite_inputs},
    }
