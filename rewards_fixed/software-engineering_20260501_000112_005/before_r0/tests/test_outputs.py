import subprocess
import os
import time


def test_parallel_build_succeeds():
    """Test that parallel build works from clean state."""
    # First clean any existing build artifacts
    result = subprocess.run(
        ["make", "clean"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=60
    )
    
    # Run parallel build
    result = subprocess.run(
        ["make", "-j4"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=120
    )
    
    assert result.returncode == 0, f"Parallel build failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    
    # Verify the executable was created
    assert os.path.exists("/app/parser"), "Parser executable not found after build"


def test_parser_executable_works():
    """Test that the parser can parse the test input file."""
    # Ensure the executable exists
    assert os.path.exists("/app/parser"), "Parser executable not found"
    
    result = subprocess.run(
        ["./parser", "test_input.dat"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=30
    )
    
    assert result.returncode == 0, f"Parser failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "Parse successful" in result.stdout, f"Parser did not report success:\n{result.stdout}"
    assert "OBJECT:" in result.stdout, f"Parser did not produce expected AST output:\n{result.stdout}"


def test_parallel_build_is_deterministic():
    """Test that parallel builds are deterministic (no race conditions)."""
    # Clean and build multiple times to verify determinism
    for i in range(3):
        result = subprocess.run(
            ["make", "clean"],
            cwd="/app",
            capture_output=True,
            text=True,
            timeout=60
        )
        
        result = subprocess.run(
            ["make", "-j4"],
            cwd="/app",
            capture_output=True,
            text=True,
            timeout=120
        )
        
        assert result.returncode == 0, f"Parallel build attempt {i+1} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"


def test_incremental_rebuild():
    """Test that incremental rebuilds work after touching a header."""
    # First ensure a clean build
    subprocess.run(["make", "clean"], cwd="/app", capture_output=True, timeout=60)
    subprocess.run(["make", "-j4"], cwd="/app", capture_output=True, timeout=120)
    
    # Touch a header file
    subprocess.run(["touch", "include/config.h"], cwd="/app", capture_output=True, timeout=10)
    
    # Rebuild should recompile dependent files
    result = subprocess.run(
        ["make", "-j4"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=120
    )
    
    assert result.returncode == 0, f"Incremental rebuild failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    
    # The executable should still work
    result = subprocess.run(
        ["./parser", "test_input.dat"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=30
    )
    
    assert result.returncode == 0, f"Parser failed after incremental rebuild:\nstdout: {result.stdout}\nstderr: {result.stderr}"


def test_generated_header_dependency():
    """Test that the build properly handles generated headers (parser.h)."""
    # Clean build
    subprocess.run(["make", "clean"], cwd="/app", capture_output=True, timeout=60)
    
    # Build once
    result = subprocess.run(
        ["make", "-j4"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=120
    )
    assert result.returncode == 0, f"First build failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    
    # Remove generated parser.h
    parser_h = "/app/src/parser.h"
    if os.path.exists(parser_h):
        os.remove(parser_h)
    
    # Rebuild should regenerate parser.h and rebuild dependent objects
    result = subprocess.run(
        ["make", "-j4"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=120
    )
    
    assert result.returncode == 0, f"Rebuild after removing parser.h failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert os.path.exists(parser_h), "parser.h was not regenerated"
