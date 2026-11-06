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

cat <<'INDEPENDENT_TESTS' > /tmp/test_scale_transform_independent.py
import numpy as np

from fvcore.transforms import ScaleTransform


def test_scale_transform_independent_expected_values():
    float_image = np.arange(10 * 20 * 3, dtype=np.float32).reshape(10, 20, 3)
    float_result = ScaleTransform(10, 20, 20, 20, "nearest").apply_image(
        float_image
    )
    float_expected = np.repeat(float_image, 2, axis=0)
    assert isinstance(float_result, np.ndarray) and float_result.shape == (20, 20, 3)
    assert np.array_equal(float_result, float_expected)

    uint8_image = np.array([[0, 1], [2, 3]], dtype=np.uint8)[:, :, None]
    uint8_result = ScaleTransform(2, 2, 3, 3, "bilinear").apply_image(uint8_image)
    uint8_expected = np.array(
        [[0, 0, 1], [1, 2, 2], [2, 2, 3]], dtype=np.uint8
    )[:, :, None]
    assert isinstance(uint8_result, np.ndarray) and uint8_result.shape == (3, 3, 1)
    assert np.array_equal(uint8_result, uint8_expected)

    segmentation = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    segmentation_result = ScaleTransform(2, 3, 4, 6, "bilinear").apply_segmentation(
        segmentation
    )
    segmentation_expected = np.repeat(np.repeat(segmentation, 2, axis=0), 2, axis=1)
    assert (
        isinstance(segmentation_result, np.ndarray)
        and segmentation_result.shape == (4, 6)
        and np.array_equal(segmentation_result, segmentation_expected)
    )
INDEPENDENT_TESTS

if [ -f /opt/miniconda3/bin/activate ]; then
    source /opt/miniconda3/bin/activate
    conda activate testbed
fi
NOCOV=()
if pytest --help 2>/dev/null | grep -q -- "--no-cov"; then
    NOCOV=(--no-cov)
fi

# Restore the repository's own test files from HEAD before judging: this
# grader runs inside the container the agent worked in, where a test
# edited to pass would otherwise be a passing test. SWE-smith's evaluation
# does the same (git checkout -- <F2P and P2P files>).
(cd /testbed && git checkout -q -- io_tests/test_file_io.py tests/param_scheduler/test_scheduler_composite.py tests/param_scheduler/test_scheduler_constant.py tests/param_scheduler/test_scheduler_cosine.py tests/param_scheduler/test_scheduler_exponential.py tests/param_scheduler/test_scheduler_linear.py tests/param_scheduler/test_scheduler_multi_step.py tests/param_scheduler/test_scheduler_polynomial.py tests/param_scheduler/test_scheduler_step.py tests/param_scheduler/test_scheduler_step_with_fixed_gamma.py tests/test_activation_count.py tests/test_checkpoint.py tests/test_common.py tests/test_flop_count.py tests/test_giou_loss.py tests/test_jit_model_analysis.py tests/test_layers_squeeze_excitation.py tests/test_param_count.py tests/test_precise_bn.py tests/test_print_model_statistics.py tests/test_smooth_l1_loss.py tests/test_transform.py tests/test_transform_util.py tests/test_weight_init.py 2>/dev/null) || true

if python /tmp/swe_smith_tests.py && pytest "${NOCOV[@]}" --disable-warnings --color=no --tb=short --verbose /tmp/test_scale_transform_independent.py; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
