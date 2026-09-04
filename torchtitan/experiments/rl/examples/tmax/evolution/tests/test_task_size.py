"""The one-rung size rule: what it counts and where it draws the lines."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import task_size as ts

SOL = "#!/bin/bash\n# setup\nset -e\n\ncd /app\npython3 gen.py > out.txt\n"
VER = "import os\nassert os.path.exists('/app/out.txt')\nif bad:\n    raise AssertionError('x')\n"


def test_counts_non_comment_lines_and_asserts() -> None:
    assert ts.solution_lines(SOL) == 3          # the shebang is a comment too
    assert ts.verifier_asserts(VER) == 2
    assert ts.verifier_asserts("#!/bin/bash\ntest -f x || exit 1\n[ -s y ] || exit 1\n", "shell") == 0
    assert ts.size_of(SOL, VER) == {"solution_lines": 3, "verifier_asserts": 2}


def test_one_rung_is_a_band_above_the_seed() -> None:
    seed = {"solution_lines": 9, "verifier_asserts": 6}
    assert ts.violations(seed, {"solution_lines": 13, "verifier_asserts": 8}) == []
    assert ts.violations(seed, {"solution_lines": 17, "verifier_asserts": 11}) == []
    too_small = ts.violations(seed, {"solution_lines": 10, "verifier_asserts": 6})
    assert len(too_small) == 1 and "at least 3 more" in too_small[0]
    too_big = ts.violations(seed, {"solution_lines": 30, "verifier_asserts": 8})
    assert len(too_big) == 1 and "at most 8 more" in too_big[0]
    asserts = ts.violations(seed, {"solution_lines": 14, "verifier_asserts": 20})
    assert len(asserts) == 1 and "at most 5 more" in asserts[0]


def test_a_large_seed_keeps_its_own_band() -> None:
    # A 17-line seed that scored 16/16 proves the policy handles 17; one rung
    # above it is 20 to 25, not "under 20".
    seed = {"solution_lines": 17, "verifier_asserts": 12}
    assert ts.violations(seed, {"solution_lines": 24, "verifier_asserts": 15}) == []
    assert ts.violations(seed, {"solution_lines": 19, "verifier_asserts": 12}) != []


def test_size_of_package_reads_the_files(tmp_path) -> None:
    (tmp_path / "solution").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "solution" / "solve.sh").write_text(SOL)
    (tmp_path / "tests" / "test_state.py").write_text(VER)
    assert ts.size_of_package(tmp_path, "tests/test_state.py") == {
        "solution_lines": 3, "verifier_asserts": 2}
