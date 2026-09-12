# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


EVOLUTION = (
    Path(__file__).resolve().parents[2]
    / "torchtitan/experiments/rl/examples/tmax/evolution"
)
with patch.object(sys, "path", [str(EVOLUTION), *sys.path]):
    spec = importlib.util.spec_from_file_location(
        "seed_verifier_audit", EVOLUTION / "seed_verifier_audit.py"
    )
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    prepare_spec = importlib.util.spec_from_file_location(
        "prepare_seed_verifier_cases", EVOLUTION / "prepare_seed_verifier_cases.py"
    )
    prepare = importlib.util.module_from_spec(prepare_spec)
    prepare_spec.loader.exec_module(prepare)


class SeedVerifierAuditTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "stage": "daytona_oracle",
            "solve_exit": 0,
            "execution_harness": "terminus",
            "terminal": {"submitted": True},
            "reward": 0,
            "grading": {"exit_code": 0},
        }

    def test_completed_negative_control_is_evidence(self):
        self.assertTrue(audit.accepted(self.result, 0))
        self.assertFalse(audit.accepted(self.result, 1))

    def test_failed_execution_cannot_certify_rejection(self):
        for field, value in (
            ("solve_exit", 127),
            ("stage", "environment_error"),
            ("terminal", {"submitted": False}),
            ("execution_harness", "bash"),
        ):
            with self.subTest(field=field):
                self.assertFalse(audit.accepted({**self.result, field: value}, 0))

    def test_nonfinite_or_missing_reward_is_not_evidence(self):
        for value in (None, float("nan"), float("inf")):
            self.assertFalse(audit.accepted({**self.result, "reward": value}, 0))

    def test_positive_requires_full_reward(self):
        self.assertTrue(audit.accepted({**self.result, "reward": 1}, 1))
        self.assertFalse(audit.accepted({**self.result, "reward": 0.5}, 1))

    def test_grader_execution_failure_is_not_a_rejection_match(self):
        for grading in ({}, {"exit_code": 124}, {"exit_code": 127}, {"exit_code": 137}):
            with self.subTest(grading=grading):
                self.assertFalse(audit.accepted({**self.result, "grading": grading}, 0))
        without_grading = dict(self.result)
        del without_grading["grading"]
        self.assertFalse(audit.accepted(without_grading, 0))

    def test_assertion_exit_remains_a_preliminary_match_for_review(self):
        result = {
            **self.result,
            "grading": {"exit_code": 1, "output_tail": "AssertionError: wrong value"},
        }
        self.assertTrue(audit.accepted(result, 0))

    def test_completion_marker_must_be_an_observed_output_line(self):
        marker = "Q1_PROBE_COMPLETE"
        for output in ("", f"printf '{marker}'\n", f"prefix {marker}\n"):
            with self.subTest(output=output):
                self.assertFalse(
                    audit.accepted(
                        {**self.result, "solve_stdout": output},
                        0,
                        completion_marker=marker,
                    )
                )
        self.assertTrue(
            audit.accepted(
                {**self.result, "solve_stdout": f"{marker}\r\n"},
                0,
                completion_marker=marker,
            )
        )

    def test_required_observation_is_checked_without_exception_heuristics(self):
        result = {**self.result, "solve_stdout": "FileNotFoundError: intended input\n"}
        self.assertTrue(
            audit.accepted(result, 0, required_observation="intended input")
        )
        self.assertFalse(
            audit.accepted(result, 0, required_observation="mutation completed")
        )

    def test_preparation_preserves_declared_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ("original", "patched"):
                (root / version / "tests").mkdir(parents=True)
                (root / version / "tests/test.sh").write_text(version)
                (root / version / "solution").mkdir()
                (root / version / "solution/solve.sh").write_text("# fixture only\n")
            script = root / "original/solution/solve.sh"
            probe = {
                "name": "reference",
                "kind": "valid",
                "script": str(script),
                "sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                "expected_original_reward": 1,
                "expected_patched_reward": 1,
                "completion_marker": "DONE",
                "required_observation": "changed input",
            }
            manifest = {
                "tasks": [
                    {
                        "task_id": "synthetic",
                        "status": "draft_grader_repair",
                        "original_package": str(root / "original"),
                        "patched_package": str(root / "patched"),
                        "changed_files": ["tests/test.sh"],
                        "probes": [probe],
                        "requirement_excerpt": "Synthetic fixture",
                        "bug": "Synthetic fixture",
                    }
                ]
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "baseline.jsonl").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "instance_id": "synthetic",
                            "tmax": {"test_sh": "original"},
                        }
                    }
                )
                + "\n"
            )
            with patch.object(
                sys,
                "argv",
                [
                    "prepare",
                    "--manifest",
                    str(root / "manifest.json"),
                    "--baseline",
                    str(root / "baseline.jsonl"),
                    "--output",
                    str(root / "cases.json"),
                    "--label",
                    "synthetic",
                ],
            ):
                prepare.main()
            cases = json.loads((root / "cases.json").read_text())["cases"]
            self.assertEqual(len(cases), 2)
            for case in cases:
                self.assertEqual(case["completion_marker"], "DONE")
                self.assertEqual(case["required_observation"], "changed input")

    def test_resume_preserves_original_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            audit.save_identical(path, "original\n")
            audit.save_identical(path, "original\n")
            with self.assertRaises(ValueError):
                audit.save_identical(path, "changed\n")
            self.assertEqual(path.read_text(), "original\n")


if __name__ == "__main__":
    unittest.main()
