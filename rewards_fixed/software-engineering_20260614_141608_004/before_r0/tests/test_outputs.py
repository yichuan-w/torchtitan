import subprocess
import os


def run_svm(asm_file):
    """Run the svm binary on an assembly file, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["./svm", asm_file],
        capture_output=True,
        text=True,
        timeout=10,
        cwd="/app"
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def test_arith():
    """Test arithmetic operations: 3+7=10, 3-7=-4, 3*7=21, 3/7=0, 3%7=3."""
    rc, stdout, stderr = run_svm("test_progs/test_arith.asm")
    assert rc == 0, f"svm exited with {rc}: {stderr}"
    lines = stdout.split('\n')
    assert len(lines) == 5, f"Expected 5 lines of output, got {len(lines)}: {stdout}"
    assert lines[0] == "10", f"Expected 10 (3+7), got '{lines[0]}'"
    assert lines[1] == "-4", f"Expected -4 (3-7), got '{lines[1]}'"
    assert lines[2] == "21", f"Expected 21 (3*7), got '{lines[2]}'"
    assert lines[3] == "0", f"Expected 0 (3/7), got '{lines[3]}'"
    assert lines[4] == "3", f"Expected 3 (3%7), got '{lines[4]}'"


def test_compare():
    """Test comparison operations: 5 vs 3."""
    rc, stdout, stderr = run_svm("test_progs/test_compare.asm")
    assert rc == 0, f"svm exited with {rc}: {stderr}"
    lines = stdout.split('\n')
    assert len(lines) == 6, f"Expected 6 lines of output, got {len(lines)}: {stdout}"
    assert lines[0] == "0", f"Expected 0 (5==3), got '{lines[0]}'"
    assert lines[1] == "1", f"Expected 1 (5!=3), got '{lines[1]}'"
    assert lines[2] == "0", f"Expected 0 (5<3), got '{lines[2]}'"
    assert lines[3] == "1", f"Expected 1 (5>3), got '{lines[3]}'"
    assert lines[4] == "0", f"Expected 0 (5<=3), got '{lines[4]}'"
    assert lines[5] == "1", f"Expected 1 (5>=3), got '{lines[5]}'"


def test_branch():
    """Test branching: if 1>0 print 100 else 200."""
    rc, stdout, stderr = run_svm("test_progs/test_branch.asm")
    assert rc == 0, f"svm exited with {rc}: {stderr}"
    lines = stdout.split('\n')
    assert len(lines) == 1, f"Expected 1 line of output, got {len(lines)}: {stdout}"
    assert lines[0] == "100", f"Expected 100, got '{lines[0]}'"


def test_loop():
    """Test loop: sum 1..5 = 15."""
    rc, stdout, stderr = run_svm("test_progs/test_loop.asm")
    assert rc == 0, f"svm exited with {rc}: {stderr}"
    lines = stdout.split('\n')
    assert len(lines) == 1, f"Expected 1 line of output, got {len(lines)}: {stdout}"
    assert lines[0] == "15", f"Expected 15 (sum 1..5), got '{lines[0]}'"


def test_call():
    """Test function call: square(7) = 49."""
    rc, stdout, stderr = run_svm("test_progs/test_call.asm")
    assert rc == 0, f"svm exited with {rc}: {stderr}"
    lines = stdout.split('\n')
    assert len(lines) == 1, f"Expected 1 line of output, got {len(lines)}: {stdout}"
    assert lines[0] == "49", f"Expected 49 (7^2), got '{lines[0]}'"


def test_complex():
    """Test complex program: sum of squares 1..5 = 55."""
    rc, stdout, stderr = run_svm("test_progs/test_complex.asm")
    assert rc == 0, f"svm exited with {rc}: {stderr}"
    lines = stdout.split('\n')
    assert len(lines) == 1, f"Expected 1 line of output, got {len(lines)}: {stdout}"
    assert lines[0] == "55", f"Expected 55 (sum i*i for i=1..5), got '{lines[0]}'"