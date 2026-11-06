#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier

cat <<'SWESMITH_TESTS' > /tmp/swe_smith_tests.py
import json
import subprocess
import sys

tests = json.loads('["tests/test_transform.py::TestTransforms::test_scale_img_transforms", "tests/test_transform.py::TestTransforms::test_scale_seg_transforms"]')
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
(cd /testbed && git checkout -q -- io_tests/test_file_io.py tests/param_scheduler/test_scheduler_composite.py tests/param_scheduler/test_scheduler_constant.py tests/param_scheduler/test_scheduler_cosine.py tests/param_scheduler/test_scheduler_exponential.py tests/param_scheduler/test_scheduler_linear.py tests/param_scheduler/test_scheduler_multi_step.py tests/param_scheduler/test_scheduler_polynomial.py tests/param_scheduler/test_scheduler_step.py tests/param_scheduler/test_scheduler_step_with_fixed_gamma.py tests/test_activation_count.py tests/test_checkpoint.py tests/test_common.py tests/test_flop_count.py tests/test_giou_loss.py tests/test_jit_model_analysis.py tests/test_layers_squeeze_excitation.py tests/test_param_count.py tests/test_precise_bn.py tests/test_print_model_statistics.py tests/test_smooth_l1_loss.py tests/test_transform.py tests/test_transform_util.py tests/test_weight_init.py 2>/dev/null) || true

if python /tmp/swe_smith_tests.py; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
