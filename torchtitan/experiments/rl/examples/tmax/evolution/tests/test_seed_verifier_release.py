# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Synthetic data tests only; no corpus solution or grader is executed."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

CODE = Path(__file__).resolve().parents[1] / "release_seed_verifier_repairs.py"
spec = importlib.util.spec_from_file_location("release_builder", CODE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline"
        self.output = self.root / "release"
        self.baseline.mkdir()
        self.packages, self.rows = {}, []
        for tid in ("tw_1", "task_2"):
            files = {
                "instruction.md": b"Produce a result.\n",
                "environment/Dockerfile": b"FROM synthetic\n",
                "tests/test.sh": b"# synthetic original test\n",
                "tests/fixture.txt": b"fixture\n",
                "solution/solve.sh": b"# synthetic reference; never execute\n",
            }
            self.packages[tid] = files
            self.rows.append(
                {
                    "prompt": "Produce a result.\n",
                    "other": ["keep"],
                    "metadata": {
                        "instance_id": tid,
                        "oracle_commands": 7,
                        "dockerfile": "FROM synthetic\n",
                        "tmax": {
                            "test_sh": files["tests/test.sh"].decode(),
                            "fixtures": {"tests/fixture.txt": "fixture\n"},
                            "pre_test_sh": "immutable hook",
                            "protected_paths": ["immutable"],
                            "pre_test_env_identity": "immutable identity",
                        },
                    },
                }
            )
        sources = {
            builder.BASELINE_JSONL: b"".join(
                (json.dumps(r) + "\n").encode() for r in self.rows
            )
        }
        for family, tid in [("terminalworld", "tw_1"), ("tmax", "task_2")]:
            sources[builder.BASELINE_ARCHIVES[family]] = builder.archive_bytes(
                self.packages, {}, [tid]
            )
        sources[builder.BASELINE_METADATA] = builder.json_bytes(
            {
                "total": 2,
                "sha256": {n: builder.digest(v) for n, v in sources.items()},
                "package_sha256": {
                    t: builder.package_hash(f) for t, f in self.packages.items()
                },
            }
        )
        builder.write_outputs(self.baseline, sources)
        for variant in ("original", "patched"):
            builder.write_outputs(self.root / variant, self.packages["tw_1"])
        (self.root / "patched/tests/test.sh").write_bytes(
            b"# synthetic repaired test\n"
        )
        self.manifest = {
            "tasks": [
                {
                    "task_id": "tw_1",
                    "original_package": str(self.root / "original"),
                    "patched_package": str(self.root / "patched"),
                    "changed_files": ["tests/test.sh"],
                }
            ]
        }
        self.acceptance = {
            "tasks": [
                {
                    "task_id": "tw_1",
                    "manifest": str(self.root / "manifest.json"),
                    "reviewed_package_sha256": builder.package_hash(
                        builder.read_package(str(self.root / "patched"))
                    ),
                    "reviewed": True,
                    "evidence": [
                        {
                            "case_id": "tw_1.patched.reference.fullhash",
                            "input_sha256": "a" * 64,
                            "result_sha256": "b" * 64,
                            "actual_reward": 1,
                            "expected_reward": 1,
                            "control_kind": "valid",
                            "private_path": "/Users/private/do-not-publish",
                        }
                    ],
                }
            ]
        }

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        (self.root / "accepted.json").write_text(json.dumps(self.acceptance))
        return builder.build_release(
            self.baseline,
            self.root / "accepted.json",
            self.output,
            "v1",
            "public/seeds",
            "a" * 40,
        )

    def test_preserves_rows_and_private_fields_and_resumes(self):
        first = self.build()
        self.assertEqual(first, self.build())
        rows = [
            json.loads(line)
            for line in (self.output / "data/seed-verifier-repaired-v1.jsonl")
            .read_text()
            .splitlines()
        ]
        expected = copy.deepcopy(self.rows)
        expected[0]["metadata"]["tmax"]["test_sh"] = "# synthetic repaired test\n"
        self.assertEqual(expected, rows)
        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"/Users/private", path.read_bytes())
        self.assertEqual(
            (self.output / "data/seed-verifier-repaired-v1.jsonl").read_bytes(),
            (
                self.output / "data/train-ready-seed-verifier-repaired-v1.jsonl"
            ).read_bytes(),
        )

    def test_quarantine_can_overlap_repair(self):
        self.acceptance["quarantined"] = [
            {
                "task_id": "tw_1",
                "reason": "Remaining public requirement has no validated check.",
            }
        ]
        result = self.build()
        self.assertEqual(
            (result["total"], result["train_ready"], result["quarantined"]), (2, 1, 1)
        )
        self.assertEqual(
            (
                self.output / "metadata/train-ready-seed-verifier-repaired-v1-ids.txt"
            ).read_text(),
            "task_2\n",
        )

    def test_rejects_undeclared_change(self):
        (self.root / "patched/solution/solve.sh").write_text("# changed reference\n")
        self.acceptance["tasks"][0]["reviewed_package_sha256"] = builder.package_hash(
            builder.read_package(str(self.root / "patched"))
        )
        with self.assertRaisesRegex(ValueError, "Declared changed_files"):
            self.build()

    def test_rejects_environment_change_even_declared(self):
        (self.root / "patched/environment/Dockerfile").write_text("FROM changed\n")
        self.acceptance["tasks"][0]["reviewed_package_sha256"] = builder.package_hash(
            builder.read_package(str(self.root / "patched"))
        )
        self.manifest["tasks"][0]["changed_files"].append("environment/Dockerfile")
        with self.assertRaisesRegex(ValueError, "environment"):
            self.build()

    def test_rejects_wrong_original(self):
        (self.root / "original/tests/fixture.txt").write_text("wrong\n")
        with self.assertRaisesRegex(ValueError, "original differs"):
            self.build()

    def test_rejects_unreviewed(self):
        self.acceptance["tasks"][0]["reviewed"] = False
        with self.assertRaisesRegex(ValueError, "not reviewed"):
            self.build()

    def test_rejects_overwrite(self):
        self.build()
        (self.root / "patched/tests/test.sh").write_text("# different repair\n")
        self.acceptance["tasks"][0]["reviewed_package_sha256"] = builder.package_hash(
            builder.read_package(str(self.root / "patched"))
        )
        with self.assertRaisesRegex(ValueError, "Existing release file differs"):
            self.build()

    def test_reference_parser_is_used_only_for_authorized_change(self):
        (self.root / "patched/solution/solve.sh").write_text("# changed reference\n")
        self.acceptance["tasks"][0]["reviewed_package_sha256"] = builder.package_hash(
            builder.read_package(str(self.root / "patched"))
        )
        self.manifest["tasks"][0]["changed_files"].append("solution/solve.sh")
        self.acceptance["tasks"][0]["allowed_reference_changes"] = ["solution/solve.sh"]
        original = builder.recount_reference
        calls = []
        builder.recount_reference = lambda source: calls.append(source) or 99
        try:
            self.build()
        finally:
            builder.recount_reference = original
        self.assertEqual(calls, ["# changed reference\n"])
        row = json.loads(
            (self.output / "data/seed-verifier-repaired-v1.jsonl")
            .read_text()
            .splitlines()[0]
        )
        self.assertEqual(row["metadata"]["oracle_commands"], 99)

    def test_refuses_changed_grader_with_identical_acceptance_into_fresh_output(self):
        self.build()
        acceptance_before = (self.root / "accepted.json").read_bytes()
        manifest_before = (self.root / "manifest.json").read_bytes()
        (self.root / "patched/tests/test.sh").write_text(
            "# unreviewed grader replacement\n"
        )
        self.output = self.root / "fresh-output"
        with self.assertRaisesRegex(ValueError, "reviewed_package_sha256"):
            self.build()
        self.assertEqual(acceptance_before, (self.root / "accepted.json").read_bytes())
        self.assertEqual(manifest_before, (self.root / "manifest.json").read_bytes())
        self.assertFalse(self.output.exists())

    def test_published_row_sidecar_must_be_explicit_and_equal(self):
        for version in ("original", "patched"):
            (self.root / version / "published-row.json").write_text(
                json.dumps(self.rows[0])
            )
        self.manifest["tasks"][0]["published_row"] = str(
            self.root / "original/published-row.json"
        )
        self.build()
        (self.root / "patched/published-row.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "sidecar"):
            self.build()

    def test_public_case_identity_and_classification_survive(self):
        evidence = self.acceptance["tasks"][0]["evidence"][0]
        evidence["case_id"] = "run-v2/tw_1--original--decimal"
        evidence["classification"] = "original_grader_type_error"
        self.build()
        metadata = json.loads(
            (self.output / "metadata/seed_verifier_repairs_v1.json").read_bytes()
        )
        self.assertEqual(
            metadata["repairs"][0]["evidence"][0]["case_id"], evidence["case_id"]
        )
        self.assertEqual(
            metadata["repairs"][0]["evidence"][0]["classification"],
            "original_grader_type_error",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
