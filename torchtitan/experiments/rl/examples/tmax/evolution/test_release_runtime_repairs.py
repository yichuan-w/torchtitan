# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Checks that publication rejects incomplete or corrupted oracle evidence."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from release_runtime_repairs import digest, read_result, require_pass


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "task_id": "example",
            "ok": True,
            "reward": 1,
            "solve_exit": 0,
            "execution_harness": "terminus",
            "terminal": {"submitted": True, "finish_reason": "submit", "turns": 3},
            "measured": {"oom_kill": 0, "disk_exhausted": False},
            "resources": {"cpu": 2, "mem_gb": 1, "disk_gb": 2},
            "run": {
                "revision": "tested",
                "resource_defaults": {"cpu": 2, "mem_gb": 4, "disk_gb": 6},
            },
            "input": {"row": {"metadata": {"daytona_mem_gb": 1, "daytona_disk_gb": 2}}},
        }

    def test_valid_explicit_resources(self):
        require_pass(self.result)

    def test_reward_one_does_not_hide_resource_or_reference_failure(self):
        for key, value in (("disk_exhausted", True), ("oom_kill", 1)):
            result = copy.deepcopy(self.result)
            result["measured"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_pass(result)
        self.result["solve_exit"] = 2
        with self.assertRaises(ValueError):
            require_pass(self.result)

    def test_unsubmitted_or_missing_trace_is_not_accepted(self):
        self.result["terminal"]["submitted"] = False
        with self.assertRaises(ValueError):
            require_pass(self.result)
        self.result["terminal"]["submitted"] = True
        self.result["pane_error"] = "download failed"
        with self.assertRaises(ValueError):
            require_pass(self.result)

    def test_wrong_resource_allocation_is_not_accepted(self):
        self.result["resources"]["disk_gb"] = 1
        with self.assertRaises(ValueError):
            require_pass(self.result)

    def test_input_tampering_and_wrong_revision_are_rejected(self):
        payload = (
            json.dumps(self.result["input"], ensure_ascii=False, sort_keys=True) + "\n"
        ).encode()
        self.result["input_sha256"] = digest(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(self.result))
            read_result(path, "example", "tested")
            with self.assertRaises(ValueError):
                read_result(path, "example", "untested")
            self.result["input"]["row"]["metadata"]["daytona_disk_gb"] = 6
            path.write_text(json.dumps(self.result))
            with self.assertRaises(ValueError):
                read_result(path, "example", "tested")


if __name__ == "__main__":
    unittest.main()
