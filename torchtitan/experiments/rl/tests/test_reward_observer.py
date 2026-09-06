# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""CPU-only tests; this file can also run directly without training imports."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

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
    def test_pairs_require_same_task_hash_and_different_epoch(self):
        result = observer.summarize(
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
        self.assertEqual(result["paired_first"]["tasks"], 1)
        self.assertEqual(result["paired_first"]["accuracy"], 0.5)
        self.assertEqual(result["paired_latest"]["accuracy"], 0.75)

    def test_rewritten_task_keeps_original_history_but_leaves_paired_cohort(self):
        result = observer.summarize(
            [row(), row(epoch=1, group=10), row(rev=1, group=20)], {"a"}
        )
        self.assertEqual(result["seed_only"]["groups"], 2)
        self.assertEqual(result["never_rewritten"]["groups"], 0)
        self.assertIsNone(result["paired_latest"]["accuracy"])

    def test_aggregation_weights_attempts_and_preserves_nonbinary_reward(self):
        first = row(scored=1, solved=1)
        first["reward_sum"] = 0.25
        result = observer.aggregate([first, row(group=1, scored=3, solved=0)])
        self.assertEqual(result["accuracy"], 0.25)
        self.assertEqual(result["reward"], 0.0625)

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
