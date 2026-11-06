#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier

cat <<'SWESMITH_TESTS' > /tmp/swe_smith_tests.py
import json
import subprocess
import sys

tests = json.loads('["parsimonious/tests/test_nodes.py::SimpleTests::test_match_shortcut", "parsimonious/tests/test_nodes.py::SimpleTests::test_visitation_exception", "parsimonious/tests/test_nodes.py::SpecialCasesTests::test_generic_visit_NotImplementedError_named_node", "parsimonious/tests/test_nodes.py::SpecialCasesTests::test_generic_visit_NotImplementedError_unnamed_node"]')
if not tests:
    print("No tests configured for this task.", file=sys.stderr)
    sys.exit(1)

try:
    import pytest_cov  # noqa: F401
    NOCOV = ["--no-cov"]
except ImportError:
    NOCOV = []

base_cmd = [
    "pytest",
    *NOCOV,
    "--disable-warnings",
    "--color=no",
    "--tb=short",
    "--verbose",
]

# Avoid hitting OS argv limits on rows with thousands of selected tests.
chunk_size = 200
exit_code = 0
for start in range(0, len(tests), chunk_size):
    chunk = tests[start : start + chunk_size]
    print(f"Running tests {start + 1}-{start + len(chunk)} of {len(tests)}", flush=True)
    result = subprocess.run(base_cmd + chunk, cwd="/testbed")
    if result.returncode != 0:
        exit_code = result.returncode
        break
if exit_code == 0:
    from parsimonious.grammar import Grammar
    assert Grammar('foo = ~"a*"+').parse("") is not None
sys.exit(exit_code)
SWESMITH_TESTS

if [ -f /opt/miniconda3/bin/activate ]; then
    source /opt/miniconda3/bin/activate
    conda activate testbed
fi

# Restore the repository's own test files from HEAD before judging: this
# grader runs inside the container the agent worked in, where a test
# edited to pass would otherwise be a passing test. SWE-smith's evaluation
# does the same (git checkout -- <F2P and P2P files>).
(cd /testbed && git checkout -q -- parsimonious/tests/test_benchmarks.py parsimonious/tests/test_expressions.py parsimonious/tests/test_grammar.py parsimonious/tests/test_nodes.py 2>/dev/null) || true

if python /tmp/swe_smith_tests.py; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
