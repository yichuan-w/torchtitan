# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Per-validation-pass trace export: a browsable Markdown report per eval step.

One directory per validation pass, named after the policy version it scored, so a
report sits next to the checkpoint that produced it::

    <dump_dir>/<dirname>/step-<policy_version>/
        INDEX.md      # one row per trial: task, state, reward, turns, tokens
        index.json    # the same rows, machine-readable
        summary.json  # solve rate, avg@k / pass@k, task and trial counts
        traces/<task>/<task>__g<group>_s<sibling>.md

The Markdown layout mirrors the Harbor/terminal-bench readable export so the two
can be diffed by eye: a field table, then the decoded conversation.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from torchtitan.config import Configurable
from torchtitan.experiments.rl.rollout import Rollout, RolloutGroup, RolloutTurn
from torchtitan.observability import structured_logger as sl

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class EvalSummary:
    """Aggregate outcome of one validation pass over a task set."""

    policy_version: int
    num_tasks: int
    num_trials: int
    num_pass: int
    avg_at_k: float | None
    """Mean reward over every trial -- the avg@k solve rate for group_size=k."""
    pass_at_k: float | None
    """Fraction of tasks with at least one passing trial."""
    report_dir: str
    valid: bool = True


def validation_is_valid(groups, *, num_groups: int, group_size: int) -> bool:
    """A benchmark score requires every requested trial to have a measured reward."""
    return (
        len(groups) == num_groups
        and num_groups > 0
        and all(
            len(group.rollouts) == group_size
            and all(
                not rollout.diagnostics.get("infra_failed", False)
                and rollout.reward is not None
                and math.isfinite(rollout.reward)
                for rollout in group.rollouts
            )
            for group in groups
        )
    )


def _slug(text: str) -> str:
    """Filesystem-safe task/trial name."""
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in text) or "unnamed"


class ValidationTraceRecorder(Configurable):
    """Write a per-eval-step Markdown + JSON report of a validation pass.

    Example:

        recorder = ValidationTraceRecorder.Config(enable=True).build(dump_dir="outputs/rl")
        summary = recorder.record(
            policy_version=20, groups=groups, task_ids=ids, decode=tokenizer.decode
        )
        # -> outputs/rl/validation_traces/step-20/{INDEX.md,index.json,summary.json,traces/}
    """

    @dataclass(kw_only=True, slots=True)
    class Config(Configurable.Config):
        enable: bool = False
        """Write the report. Off by default; a validation pass only logs metrics."""
        dirname: str = "validation_traces"
        """Subdirectory of ``dump_dir`` holding one ``step-<N>`` report per pass."""
        max_chars_per_trace: int = 4_000_000
        """Truncate a single decoded transcript at this many characters. A 65536-token
        agent episode is ~250 KB, so this only trips on a runaway rollout."""

    def __init__(self, config: Config, *, dump_dir: str) -> None:
        self._enabled = config.enable
        self._max_chars = config.max_chars_per_trace
        self._root = Path(dump_dir) / config.dirname

    @property
    def enabled(self) -> bool:
        return self._enabled

    @sl.log_trace_span("validation_trace_record")
    def record(
        self,
        *,
        policy_version: int,
        groups: list[RolloutGroup],
        task_ids: list[str],
        decode,
        valid: bool = True,
    ) -> EvalSummary | None:
        """Write the report for one validation pass.

        Args:
            policy_version: Trainer policy version the pass scored; names the
                report directory so it lines up with ``checkpoint/step-<N>``.
            task_ids: One task id per group, in the same order as ``groups``.
            decode: ``list[int] -> str`` token decoder (the renderer's tokenizer).

        Returns:
            The pass summary, or None when the recorder is disabled.
        """
        if not self._enabled:
            return None
        step_dir = self._root / f"step-{policy_version}"
        (step_dir / "traces").mkdir(parents=True, exist_ok=True)

        rows: list[dict] = []
        for task_id, group in zip(task_ids, groups, strict=True):
            for rollout in group.rollouts:
                rows.append(
                    self._write_trace(
                        step_dir=step_dir,
                        task_id=task_id,
                        group=group,
                        rollout=rollout,
                        decode=decode,
                    )
                )

        summary = self._summarize(
            policy_version=policy_version,
            num_tasks=len(groups),
            rows=rows,
            report_dir=str(step_dir),
            valid=valid,
        )
        (step_dir / "index.json").write_text(json.dumps(rows, indent=2, sort_keys=True))
        (step_dir / "summary.json").write_text(
            json.dumps(asdict(summary), indent=2, sort_keys=True)
        )
        (step_dir / "INDEX.md").write_text(self._render_index(summary, rows))
        logger.info(
            f"validation trace report (policy_version={policy_version}): "
            f"valid={summary.valid} avg@k={summary.avg_at_k} pass@k={summary.pass_at_k} "
            f"-> {step_dir}/INDEX.md"
        )
        return summary

    def _write_trace(
        self,
        *,
        step_dir: Path,
        task_id: str,
        group: RolloutGroup,
        rollout: Rollout,
        decode,
    ) -> dict:
        """Write one trial's Markdown transcript; return its index row."""
        trial = f"{_slug(task_id)}__g{group.group_id}_s{rollout.rollout_id}"
        rel_path = f"traces/{_slug(task_id)}/{trial}.md"
        reward = rollout.reward
        state = "PASS" if reward is not None and reward > 0 else "FAIL"
        if rollout.diagnostics.get("infra_failed", False):
            state = "INFRA_FAILED"
        prompt_tokens = sum(len(turn.prompt_token_ids) for turn in rollout.turns)
        completion_tokens = sum(
            len(turn.completion_token_ids) for turn in rollout.turns
        )
        row = {
            "task": task_id,
            "trial": trial,
            "state": state,
            "reward": reward,
            "turns": len(rollout.turns),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "status": str(rollout.status),
            # Whatever the rollouter recorded per rollout (e.g. finish_reason,
            # format_errors). Flattened into the row so the index is one flat table.
            **{key: value for key, value in rollout.diagnostics.items()},
            "path": rel_path,
        }

        header = "\n".join(
            [
                f"# {task_id} / {trial}",
                "",
                "| Field | Value |",
                "|---|---|",
                f"| State | {state} |",
                f"| Reward | {reward} |",
                f"| Rollout status | {rollout.status} |",
                f"| Assistant turns | {len(rollout.turns)} |",
                f"| Prompt tokens, accumulated | {prompt_tokens} |",
                f"| Completion tokens, accumulated | {completion_tokens} |",
                f"| Reward breakdown | `{json.dumps(rollout.reward_breakdown)}` |",
                *(
                    f"| {key} | {value} |"
                    for key, value in sorted(rollout.diagnostics.items())
                ),
                "",
                "## Conversation",
                "",
            ]
        )
        body = self._decode_transcript(rollout.turns, decode)
        path = step_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{header}\n{body}\n")
        return row

    def _decode_transcript(self, turns: list[RolloutTurn], decode) -> str:
        """Render the conversation turn by turn from the per-turn token ids.

        Token-in/token-out invariant: turn N+1's prompt extends turn N's
        prompt+completion with the new environment output. So each turn contributes
        two readable pieces -- the delta its prompt added (the task on turn 1, the
        environment's reply after that) and the model's completion -- and decoding
        them separately gives one section per turn instead of a single undivided
        blob of the whole session.

        A turn whose prompt does NOT extend the running prefix is a history rewrite
        (Terminus-2 summarizing a long session, say). Concatenating only the final
        prefix would silently drop everything before the rewrite and leave a
        transcript that appears to begin mid-task, so the break gets its own marker
        and the turns on both sides are kept. Those segments are also what training
        sees as separate samples.
        """
        if not turns:
            return "(no turns)"
        # Per-turn (prompt delta, completion, rewrite?) before any truncation.
        pieces: list[tuple[list[int], list[int], bool]] = []
        prefix: list[int] = []
        for turn in turns:
            prompt = list(turn.prompt_token_ids)
            extends = prompt[: len(prefix)] == prefix
            delta = prompt[len(prefix) :] if extends else prompt
            completion = list(turn.completion_token_ids)
            pieces.append((delta, completion, not extends and bool(prefix)))
            prefix = prompt + completion

        # Spread the budget over turns so a long early turn cannot hide the ending.
        budget = max(self._max_chars // len(pieces), 1)

        def block(label: str, token_ids: list[int]) -> str:
            if not token_ids:
                return ""
            text = decode(token_ids)
            if len(text) > budget:
                text = f"{text[:budget]}\n... [{len(text) - budget} characters omitted]"
            return f"**{label}**\n\n```text\n{text}\n```\n\n"

        parts: list[str] = []
        for index, (delta, completion, rewritten) in enumerate(pieces):
            if rewritten:
                parts.append(
                    f"> **History rewritten before this turn.** Everything above is "
                    f"no longer in the model's context; turn {index + 1} starts from "
                    f"the prompt below (a fresh training sample too).\n\n"
                )
            parts.append(f"### Turn {index + 1}\n\n")
            parts.append(block("Task" if index == 0 else "Environment", delta))
            parts.append(block("Assistant", completion))
        return "".join(parts).rstrip("\n")

    @staticmethod
    def _summarize(
        *,
        policy_version: int,
        num_tasks: int,
        rows: list[dict],
        report_dir: str,
        valid: bool = True,
    ) -> EvalSummary:
        rewards = [row["reward"] for row in rows if row["reward"] is not None]
        passing_tasks = {row["task"] for row in rows if row["state"] == "PASS"}
        valid = (
            valid
            and bool(rows)
            and all(
                not row.get("infra_failed", False)
                and row["reward"] is not None
                and math.isfinite(row["reward"])
                for row in rows
            )
        )
        return EvalSummary(
            policy_version=policy_version,
            num_tasks=num_tasks,
            num_trials=len(rows),
            num_pass=sum(1 for row in rows if row["state"] == "PASS"),
            avg_at_k=statistics.fmean(rewards) if valid else None,
            pass_at_k=len(passing_tasks) / num_tasks if valid else None,
            report_dir=report_dir,
            valid=valid,
        )

    @staticmethod
    def _render_index(summary: EvalSummary, rows: list[dict]) -> str:
        lines = [
            f"# Validation traces at policy version {summary.policy_version}",
            "",
            f"Scored the policy in `checkpoint/step-{summary.policy_version}`: "
            f"{summary.num_tasks} tasks x {summary.num_trials // max(summary.num_tasks, 1)} "
            f"trials = {summary.num_trials} rollouts.",
            "",
            f"- Valid benchmark score: {summary.valid}",
            f"- avg@k (mean reward over all trials): {summary.avg_at_k}",
            f"- pass@k (tasks with >=1 pass): {summary.pass_at_k} "
            f"({len(({row['task'] for row in rows if row['state'] == 'PASS'}))}"
            f"/{summary.num_tasks})",
            "",
        ]
        if not summary.valid:
            lines += [
                "Evaluation incomplete: repair infrastructure and rerun the full "
                "task set at the same checkpoint. Raw trial rewards follow.",
                "",
            ]
        # Diagnostic keys the rollouter supplied, as their own columns. Discovered
        # from the rows rather than hardcoded, so the recorder stays rollouter-agnostic.
        fixed = {
            "task",
            "trial",
            "state",
            "reward",
            "turns",
            "prompt_tokens",
            "completion_tokens",
            "status",
            "path",
        }
        extra = sorted({key for row in rows for key in row} - fixed)

        # A per-reason breakdown of how the trials ended is the first thing to look at
        # when a solve rate moves, so surface it above the table when it is available.
        if "finish_reason" in extra:
            counts = Counter(str(row.get("finish_reason")) for row in rows)
            lines.append("Trials by finish reason:")
            lines += [
                f"- `{reason}`: {count} ({count / len(rows):.1%})"
                for reason, count in counts.most_common()
            ]
            lines.append("")

        header = [
            "Task",
            "Trial",
            "State",
            "Reward",
            "Turns",
            "Prompt tokens",
            "Completion tokens",
            *extra,
        ]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|---|---|---|---:|---:|---:|---:|" + "---|" * len(extra))
        for row in sorted(rows, key=lambda r: (r["task"], r["trial"])):
            cells = [
                row["task"],
                f"[{row['trial']}]({row['path']})",
                row["state"],
                row["reward"],
                row["turns"],
                row["prompt_tokens"],
                row["completion_tokens"],
                *(row.get(key, "-") for key in extra),
            ]
            lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
        return "\n".join(lines) + "\n"
