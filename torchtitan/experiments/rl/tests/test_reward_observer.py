# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""CPU-only tests; this file can also run directly without training imports."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "examples/tmax/evolution/observe_rewards.py"
spec = importlib.util.spec_from_file_location("observe_rewards", SCRIPT)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def row(task="a", rev=0, epoch=0, group=0, scored=4, solved=2, revision="same"):
    return dict(
        task=task,
        rev=rev,
        epoch=epoch,
        group=group,
        scored=scored,
        solved=solved,
        reward_sum=solved,
        infra=0,
        n=scored,
        sample_revision=revision,
        policy_at_claim=group,
    )


class RewardObserverTest(unittest.TestCase):
    def test_publishes_three_charts_without_html_or_per_task_metrics(self):
        entries = []

        def table(**kwargs):
            return kwargs

        table.MAX_ARTIFACT_ROWS = 200000
        wb = SimpleNamespace(log=entries.append, url="test")
        rows = [row(), row(epoch=1, group=10, solved=3)]
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                sys.modules,
                {
                    "wandb": SimpleNamespace(
                        Table=table,
                        plot_table=lambda *args, **kwargs: (args, kwargs),
                    )
                },
            ):
                charts = json.loads(SCRIPT.with_name("reward_charts.json").read_text())
                for value in charts.values():
                    value["id"] = "test/chart"
                observer.publish(
                    wb,
                    {
                        "unchanged": observer.observe_lineage.unchanged(rows, set()),
                        "comparisons": [],
                        "timeline": [],
                    },
                    Path(directory),
                    charts,
                )
            self.assertEqual(
                set(entries[0]),
                {
                    "Unchanged task accuracy",
                    "Rewrite accuracy",
                    "Task timeline",
                },
            )

    def test_pairs_require_same_task_hash_and_different_epoch(self):
        result = observer.observe_lineage.unchanged(
            [
                row(),
                row(epoch=1, group=10, solved=3),
                row(task="b", group=1),
                row(task="b", epoch=1, group=11, revision="changed"),
                row(task="c", group=2),
                row(task="c", group=12),
            ],
            set(),
        )
        self.assertEqual(len(result["cohort"]), 1)
        self.assertEqual([p["accuracy"] for p in result["points"]], [0.5, 0.75])

    def test_rewritten_task_keeps_original_history_but_leaves_paired_cohort(self):
        result = observer.observe_lineage.unchanged(
            [row(), row(epoch=1, group=10), row(rev=1, group=20)], {"a"}
        )
        self.assertEqual(result["cohort"], [])
        self.assertTrue(all(p["accuracy"] is None for p in result["points"]))

    def test_third_epoch_recomputes_all_points_using_intersection(self):
        rows = [
            row(),
            row(epoch=1, group=2, solved=4),
            row(task="b", group=1, solved=0),
            row(task="b", epoch=1, group=3, solved=0),
            row(epoch=2, group=4, solved=3),
        ]
        result = observer.observe_lineage.unchanged(rows, set())
        self.assertEqual([p["tasks"] for p in result["points"]], [1, 1, 1])
        self.assertEqual([p["accuracy"] for p in result["points"]], [0.5, 1.0, 0.75])

    def test_trace_path_in_prompt_or_listing_does_not_prove_body_read(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            trace = base / "sessions/one--agent/codex/sessions/trace.jsonl"
            trace.parent.mkdir(parents=True)
            records = [
                {
                    "type": "response_item",
                    "payload": {"type": "message", "content": "read traces/a.jsonl"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": "a",
                        "arguments": "head -n1 traces/a.jsonl",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "a",
                        "output": '{"reward":1,"turns":4}',
                    },
                },
            ]
            trace.write_text("".join(json.dumps(r) + "\n" for r in records))
            cache = base / "cache"
            cache.mkdir()
            result = observer.observe_lineage.trace_evidence(base, cache)
            self.assertEqual(
                result["status"], "Trace command observed; body unconfirmed"
            )
            records[-1]["payload"]["output"] = '{"turn":1,"raw":"pwd"}'
            trace.write_text("".join(json.dumps(r) + "\n" for r in records))
            result = observer.observe_lineage.trace_evidence(base, cache)
            self.assertEqual(result["status"], "Trace body in tool output")
            self.assertEqual(result["causal_use"], "Not established by tool access")

    def test_aggregation_weights_attempts_and_preserves_nonbinary_reward(self):
        first = row(scored=1, solved=1)
        first["reward_sum"] = 0.25
        result = observer.observe_lineage.unchanged(
            [first, row(group=1, scored=3, solved=0)], set()
        )
        self.assertEqual(result["points"][0]["accuracy"], 0.25)

    def test_timeline_uses_actual_revision_and_training_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = observer.layout.Root(Path(directory))
            run = root.run("test")
            output = root.path / "observer"
            output.mkdir()
            records = []
            for group, epoch, stamp in [
                (0, 0, "2026-01-01T00:00:00Z"),
                (1, 1, "2026-01-01T00:02:00Z"),
            ]:
                base = dict(
                    group_id=group,
                    task_id="a",
                    dataset_epoch=epoch,
                    timestamp=stamp,
                    sample_revision=f"hash{group}",
                )
                records.extend(
                    [
                        dict(base, event="admitted"),
                        dict(base, event="finalized"),
                        dict(base, event="trained", train_step=10 + group),
                    ]
                )
            samples = run.trainer / "training_lineage/samples.jsonl"
            samples.parent.mkdir(parents=True)
            samples.write_text(
                "".join(
                    json.dumps({"sample_revision": f"hash{r}", "input": {"rev": r}})
                    + "\n"
                    for r in [0, 1]
                )
            )
            rows = [
                row(revision="hash0"),
                row(rev=1, group=1, epoch=1, revision="hash1", solved=1),
            ]
            folds = [
                {
                    "task": "a",
                    "stamp": "20260101-000100Z",
                    "from_rev": 0,
                    "to_rev": 1,
                    "rewrite": "rewrites/one",
                }
            ]
            result = observer.observe_lineage.build(
                root, run, rows, folds, records, output
            )
            self.assertEqual(result["comparisons"][0]["after_step"], 11)
            self.assertEqual(result["comparisons"][0]["before"], 0.5)
            trained = [e for e in result["timeline"] if e["event"] == "trained"]
            self.assertEqual(
                [(e["revision"], e["step"]) for e in trained], [(0, 10), (1, 11)]
            )
            result = observer.observe_lineage.build(
                root, run, rows[:1], folds, records, output
            )
            self.assertEqual(
                result["comparisons"], []
            )  # Never invent an after=0 point.

    def test_partial_append_is_deferred_but_corruption_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text('{"ok":1}\n{"unfinished"')
            self.assertEqual(observer.read_events(path), [{"ok": 1}])
            path.write_text("not-json\n")
            with self.assertRaises(ValueError):
                observer.read_events(path)

    def test_collect_excludes_infra_and_reloads_cached_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = observer.layout.Root(Path(directory))
            run = root.run("test")
            output = Path(directory) / "observer"
            output.mkdir()
            events = run.trainer / "training_lineage/events.jsonl"
            events.parent.mkdir(parents=True)
            common = dict(
                group_id=0,
                task_id="a",
                occurrence_id="one",
                sample_revision="hash",
                dataset_epoch=0,
                generator_policy_version=7,
            )
            records = [
                dict(common, event="claimed"),
                dict(common, event="finalized", num_rollouts=3),
            ]
            events.write_text("".join(json.dumps(e) + "\n" for e in records))
            task = run.rollouts / "a"
            task.mkdir(parents=True)
            for index, (reward, infra) in enumerate(
                [(1, False), (0, True), (None, False)]
            ):
                (task / f"g0-r{index}.jsonl").write_text(
                    json.dumps(
                        dict(
                            task="a", rev=0, group=0, reward=reward, infra_failed=infra
                        )
                    )
                    + "\n"
                )
            rows, _ = observer.collect(root, run, output)
            self.assertEqual(
                (rows[0]["scored"], rows[0]["solved"], rows[0]["infra"]), (1, 1, 1)
            )
            self.assertEqual(rows[0]["policy_at_claim"], 7)
            (task / "g0-r0.jsonl").unlink()
            cached, _ = observer.collect(root, run, output)
            self.assertEqual(cached, rows)

    def test_source_url_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            run = observer.layout.Root(Path(directory)).run("test")
            run.path.mkdir(parents=True)
            run.stdout_log.write_text(
                "wandb: View run at https://wandb.ai/team/project/runs/abcd1234\n"
            )
            self.assertEqual(observer.source_wandb(run), "team/project/abcd1234")


if __name__ == "__main__":
    unittest.main()
