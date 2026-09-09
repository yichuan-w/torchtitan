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
        (directory / "contract.json").write_text(json.dumps(dict(
            requirement="Use audited count for both filtering and weight",
            wrong_behavior="Use raw count for weight", expected_failure="Edge weight differs")))
        (directory / "correct.sh").write_text("write_correct_graph")
        (directory / "wrong.sh").write_text("write_wrong_weight_graph")

    def execute(self, failure_phase=None, failure_code=0):
        calls = []
        def command(args, **kwargs):
            calls.append(args[1:])
            index = len(calls)
            code = 1 if index == 6 and args[1] == "grade" else 0
            if index == failure_phase:
                code = failure_code
            return subprocess.CompletedProcess(args, code, "probe output", "")
        with patch.object(probes.subprocess, "run", side_effect=command):
            probes.verify_probes(self.pkg, {}, 60)
        return calls

    def test_fresh_environments_and_daytona_only_execution(self):
        calls = self.execute()
        self.assertEqual([call[0] for call in calls], ["reset", "exec", "grade", "reset", "exec", "grade", "down"])
        self.assertEqual(calls[1][1], "write_correct_graph")
        self.assertEqual(calls[4][1], "write_wrong_weight_graph")
        records = [json.loads(line) for line in (self.pkg / "run/verifier-probe-results.jsonl").read_text().splitlines()]
        self.assertTrue(any(row.get("status") == "passed" for row in records))

    def test_wrong_solution_passing_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "wrong/grade"):
            self.execute(6, 0)

    def test_wrong_solution_crash_is_not_a_semantic_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "wrong/setup"):
            self.execute(5, 1)

    def test_grading_infrastructure_error_is_not_a_rejection(self):
        with self.assertRaisesRegex(RuntimeError, "wrong/grade"):
            self.execute(6, 2)

    def test_correct_solution_rejection_fails_gate(self):
        with self.assertRaisesRegex(RuntimeError, "correct/grade"):
            self.execute(3, 1)


if __name__ == "__main__":
    unittest.main()
