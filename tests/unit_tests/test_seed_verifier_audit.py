# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import importlib.util
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


class SeedVerifierAuditTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "stage": "daytona_oracle",
            "solve_exit": 0,
            "execution_harness": "terminus",
            "terminal": {"submitted": True},
            "reward": 0,
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
