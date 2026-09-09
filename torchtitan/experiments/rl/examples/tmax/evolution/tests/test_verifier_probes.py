import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("verifier_probes", Path(__file__).parents[1] / "verifier_probes.py")
probes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probes)


class SemanticProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pkg = Path(self.temp.name)
        directory = self.pkg / "run" / "verifier-probes"
        directory.mkdir(parents=True)
        (directory / "contract.json").write_text(json.dumps({"cases": [dict(
            requirement="Use audited count for both filtering and weight",
            wrong_behavior="Use raw count for weight", expected_failure="Edge weight differs"), dict(
            requirement="Only verified audit records qualify",
            wrong_behavior="Use pending audit record", expected_failure="Selected papers differ")]}))
        (directory / "correct.sh").write_text("write_correct_graph")
        (directory / "wrong-1.sh").write_text("write_wrong_weight_graph")
        (directory / "wrong-2.sh").write_text("write_pending_audit_graph")

    def execute(self, failure_phase=None, failure_code=0):
        calls = []
        def command(args, **kwargs):
            calls.append(args[1:])
            if args[1] == "exec" and args[2].startswith("if ["):
                return subprocess.CompletedProcess(args, 0, '{"tests": []}', "")
            index = sum(not (call[0] == "exec" and call[1].startswith("if [")) for call in calls)
            code = 1 if index > 3 and args[1] == "grade" else 0
            if index in (failure_phase if isinstance(failure_phase, tuple) else (failure_phase,)):
                code = failure_code
            return subprocess.CompletedProcess(args, code, "probe output", "")
        with patch.object(probes.subprocess, "run", side_effect=command):
            probes.verify_probes(self.pkg, {}, 60)
        return calls

    def test_fresh_environments_and_daytona_only_execution(self):
        calls = self.execute()
        self.assertEqual([call[0] for call in calls], ["reset", "exec", "grade", "exec"] * 3 + ["down"])
        self.assertEqual(calls[1][1], "write_correct_graph")
        self.assertEqual(calls[5][1], "write_wrong_weight_graph")
        self.assertEqual(calls[9][1], "write_pending_audit_graph")
        records = [json.loads(line) for line in (self.pkg / "run/verifier-probe-results.jsonl").read_text().splitlines()]
        self.assertTrue(any(row.get("status") == "passed" for row in records))
        self.assertEqual(sum(row.get("phase") == "grade_details" and row.get("status") == "finished"
                             for row in records), 3)

    def test_wrong_solution_passing_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-1/grade"):
            self.execute(6, 0)
        records = [json.loads(line) for line in (self.pkg / "run/verifier-probe-results.jsonl").read_text().splitlines()]
        self.assertTrue(any(row.get("case") == "wrong-2" and row.get("phase") == "grade"
                            and row.get("status") == "finished" for row in records))
        self.assertFalse(any(row.get("status") == "passed" for row in records))

    def test_later_wrong_solution_passing_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-2/grade"):
            self.execute(9, 0)

    def test_multiple_semantic_misses_are_reported_together(self):
        with self.assertRaises(RuntimeError) as error:
            self.execute((6, 9), 0)
        self.assertIn("wrong-1/grade", str(error.exception))
        self.assertIn("wrong-2/grade", str(error.exception))
        records = [json.loads(line) for line in (self.pkg / "run/verifier-probe-results.jsonl").read_text().splitlines()]
        self.assertEqual(len(next(row["errors"] for row in records if row.get("status") == "failed")), 2)
        self.assertEqual(records[-1]["phase"], "down")

    def test_wrong_solution_crash_is_not_a_semantic_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-1/setup"):
            self.execute(5, 1)

    def test_grading_infrastructure_error_is_not_a_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "wrong-1/grade"):
            self.execute(6, 2)
        records = [json.loads(line) for line in (self.pkg / "run/verifier-probe-results.jsonl").read_text().splitlines()]
        self.assertFalse(any(row.get("case") == "wrong-2" for row in records))
        self.assertEqual(records[-1]["phase"], "down")

    def test_correct_solution_rejection_fails_gate(self):
        with self.assertRaisesRegex(RuntimeError, "correct/grade"):
            self.execute(3, 1)


if __name__ == "__main__":
    unittest.main()
