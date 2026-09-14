# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "verifier_probes", Path(__file__).parents[1] / "verifier_probes.py"
)
probes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probes)


class SemanticProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pkg = Path(self.temp.name)
        directory = self.pkg / "run" / "verifier-probes"
        directory.mkdir(parents=True)
        (directory / "contract.json").write_text(
            json.dumps(
                {
                    "cases": [
                        dict(
                            requirement="Use audited count for both filtering and weight",
                            wrong_behavior="Use raw count for weight",
                            expected_failure="Edge weight differs",
                        ),
                        dict(
                            requirement="Only verified audit records qualify",
                            wrong_behavior="Use pending audit record",
                            expected_failure="Selected papers differ",
                        ),
                    ]
                }
            )
        )
        (directory / "correct.sh").write_text("write_correct_graph")
        (directory / "wrong-1.sh").write_text("write_wrong_weight_graph")
        (directory / "wrong-2.sh").write_text("write_pending_audit_graph")

    def execute(self, failure_phase=None, failure_code=0):
        calls = []

        def command(args, **kwargs):
            calls.append(args[1:])
            if args[1] == "exec" and args[2].startswith("if ["):
                return subprocess.CompletedProcess(args, 0, '{"tests": []}', "")
            index = sum(
                not (call[0] == "exec" and call[1].startswith("if [")) for call in calls
            )
            code = 1 if index > 3 and args[1] == "grade" else 0
            if index in (
                failure_phase if isinstance(failure_phase, tuple) else (failure_phase,)
            ):
                code = failure_code
            return subprocess.CompletedProcess(args, code, "probe output", "")

        with patch.object(probes.subprocess, "run", side_effect=command):
            probes.verify_probes(self.pkg, {}, 60)
        return calls

    def test_fresh_environments_and_daytona_only_execution(self):
        calls = self.execute()
        self.assertEqual(
            [call[0] for call in calls],
            ["reset", "exec", "grade", "exec"] * 3 + ["down"],
        )
        self.assertEqual(calls[1][1], "write_correct_graph")
        self.assertEqual(calls[5][1], "write_wrong_weight_graph")
        self.assertEqual(calls[9][1], "write_pending_audit_graph")
        records = [
            json.loads(line)
            for line in (self.pkg / "run/verifier-probe-results.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertTrue(any(row.get("status") == "passed" for row in records))
        self.assertEqual(
            sum(
                row.get("phase") == "grade_details" and row.get("status") == "finished"
                for row in records
            ),
            3,
        )

    def test_wrong_solution_passing_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-1/grade"):
            self.execute(6, 0)
        records = [
            json.loads(line)
            for line in (self.pkg / "run/verifier-probe-results.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertTrue(
            any(
                row.get("case") == "wrong-2"
                and row.get("phase") == "grade"
                and row.get("status") == "finished"
                for row in records
            )
        )
        self.assertFalse(any(row.get("status") == "passed" for row in records))

    def test_later_wrong_solution_passing_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-2/grade"):
            self.execute(9, 0)

    def test_multiple_semantic_misses_are_reported_together(self):
        with self.assertRaises(RuntimeError) as error:
            self.execute((6, 9), 0)
        self.assertIn("wrong-1/grade", str(error.exception))
        self.assertIn("wrong-2/grade", str(error.exception))
        records = [
            json.loads(line)
            for line in (self.pkg / "run/verifier-probe-results.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertEqual(
            len(
                next(row["errors"] for row in records if row.get("status") == "failed")
            ),
            2,
        )
        self.assertEqual(records[-1]["phase"], "down")

    def test_wrong_solution_crash_is_not_a_semantic_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-1/setup"):
            self.execute(5, 1)

    def test_grading_infrastructure_error_is_not_a_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-1/grade"):
            self.execute(6, 2)
        records = [
            json.loads(line)
            for line in (self.pkg / "run/verifier-probe-results.jsonl")
            .read_text()
            .splitlines()
        ]
        self.assertFalse(any(row.get("case") == "wrong-2" for row in records))
        self.assertEqual(records[-1]["phase"], "down")

    def test_correct_solution_rejection_fails_gate(self):
        with self.assertRaisesRegex(RuntimeError, "correct/grade"):
            self.execute(3, 1)

    def transport_commands(self, phase="grade", failures=1, mutate=False, stderr=None):
        self.calls = []
        self.current_script = None
        self.transport_failures = 0

        def command(args, **kwargs):
            self.calls.append(args[1:])
            operation = args[1]
            if operation == "exec" and not args[2].startswith("if ["):
                self.current_script = args[2]
            if (
                self.current_script == "write_pending_audit_graph"
                and operation == ("exec" if phase == "setup" else "grade")
                and not (operation == "exec" and args[2].startswith("if ["))
                and self.transport_failures < failures
            ):
                self.transport_failures += 1
                if mutate:
                    (
                        self.pkg
                        / (mutate if isinstance(mutate, str) else "instruction.md")
                    ).write_text("changed")
                return subprocess.CompletedProcess(
                    args,
                    2,
                    "",
                    stderr
                    or "sandbox error: DaytonaBadGatewayError: Failed to execute session command: \n",
                )
            code = int(
                operation == "grade" and self.current_script != "write_correct_graph"
            )
            return subprocess.CompletedProcess(args, code, "probe output", "")

        return command

    def test_transport_failure_replays_only_failed_control_from_reset(self):
        for phase in ("setup", "grade"):
            with self.subTest(phase=phase):
                log = self.pkg / "run/verifier-probe-results.jsonl"
                log.unlink(missing_ok=True)
                with patch.object(
                    probes.subprocess, "run", side_effect=self.transport_commands(phase)
                ):
                    probes.verify_probes(self.pkg, {}, 60)
                setups = [
                    call[1]
                    for call in self.calls
                    if call[0] == "exec" and not call[1].startswith("if [")
                ]
                self.assertEqual(
                    setups,
                    [
                        "write_correct_graph",
                        "write_wrong_weight_graph",
                        "write_pending_audit_graph",
                        "write_pending_audit_graph",
                    ],
                )
                self.assertEqual(sum(call[0] == "reset" for call in self.calls), 4)
                records = [json.loads(line) for line in log.read_text().splitlines()]
                retry = next(
                    row for row in records if row.get("status") == "transport_retry"
                )
                self.assertEqual(retry["case"], "wrong-2")
                self.assertEqual(retry["attempt"], 2)
                self.assertTrue(any(row.get("returncode") == 2 for row in records))
                self.assertTrue(any(row.get("status") == "passed" for row in records))
                self.assertEqual(records[-1]["phase"], "down")

    def test_second_transport_failure_aborts_without_third_attempt(self):
        with patch.object(
            probes.subprocess, "run", side_effect=self.transport_commands(failures=2)
        ):
            with self.assertRaises(probes._DaytonaTransportFailure):
                probes.verify_probes(self.pkg, {}, 60)
        self.assertEqual(sum(call[0] == "reset" for call in self.calls), 4)
        self.assertEqual(self.calls[-1], ["down"])

    def test_changed_inputs_refuse_transport_recovery(self):
        for path in ("instruction.md", "run/verifier-probes/wrong-2.sh"):
            with self.subTest(path=path):
                with patch.object(
                    probes.subprocess,
                    "run",
                    side_effect=self.transport_commands(mutate=path),
                ):
                    with self.assertRaisesRegex(RuntimeError, "inputs changed"):
                        probes.verify_probes(self.pkg, {}, 60)
                self.assertEqual(sum(call[0] == "reset" for call in self.calls), 3)

    def test_script_printing_transport_error_is_not_retried(self):
        stderr = "sandbox error: DaytonaBadGatewayError: fake\n[exit 2]\n"
        with patch.object(
            probes.subprocess,
            "run",
            side_effect=self.transport_commands("setup", stderr=stderr),
        ):
            with self.assertRaisesRegex(RuntimeError, "wrong-2/setup"):
                probes.verify_probes(self.pkg, {}, 60)
        self.assertEqual(sum(call[0] == "reset" for call in self.calls), 3)


if __name__ == "__main__":
    unittest.main()
