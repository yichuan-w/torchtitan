import subprocess
import os


def test_shared_library_exists():
    """Test that the shared library was installed to /usr/local/lib"""
    lib_path = "/usr/local/lib/libnumutils.so"
    assert os.path.exists(lib_path), f"Shared library not found at {lib_path}"
    
    # Check it's actually an ELF file by reading the magic bytes
    with open(lib_path, 'rb') as f:
        magic = f.read(4)
    assert magic == b'\x7fELF', f"File is not an ELF binary: magic bytes {magic!r}"


def test_header_file_exists():
    """Test that the header file was installed to /usr/local/include"""
    header_path = "/usr/local/include/numutils.h"
    assert os.path.exists(header_path), f"Header file not found at {header_path}"
    
    # Check it contains expected declarations
    with open(header_path, 'r') as f:
        content = f.read()
    
    assert 'nu_mean' in content, "Header missing nu_mean declaration"
    assert 'nu_variance' in content, "Header missing nu_variance declaration"
    assert 'nu_matrix_create' in content, "Header missing nu_matrix_create declaration"


def test_library_is_linkable():
    """Test that the library can be linked against"""
    # Compile the test program
    result = subprocess.run(
        [
            "gcc", 
            "/app/test_numutils.c", 
            "-L/usr/local/lib", 
            "-lnumutils", 
            "-lm", 
            "-o", 
            "/app/test_numutils"
        ],
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0, \
        f"Failed to link test program: {result.stderr}"


def test_library_functions_work():
    """Test that the library functions work correctly"""
    # First compile the test program
    compile_result = subprocess.run(
        [
            "gcc", 
            "/app/test_numutils.c", 
            "-L/usr/local/lib", 
            "-lnumutils", 
            "-lm", 
            "-o", 
            "/app/test_numutils"
        ],
        capture_output=True,
        text=True
    )
    
    assert compile_result.returncode == 0, \
        f"Failed to compile test program: {compile_result.stderr}"
    
    # Run the test program with LD_LIBRARY_PATH set
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = "/usr/local/lib"
    
    result = subprocess.run(
        ["/app/test_numutils"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )
    
    # Check all tests passed
    assert result.returncode == 0, \
        f"Test program failed with return code {result.returncode}.\n" \
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    
    assert "Failed: 0" in result.stdout, \
        f"Some tests failed in the test program:\n{result.stdout}"


def test_library_version():
    """Test that the library reports correct version"""
    # Create a small test program
    test_code = '''
#include <stdio.h>
#include "numutils.h"

int main() {
    printf("%s\\n", nu_version());
    return 0;
}
'''
    with open("/tmp/version_test.c", "w") as f:
        f.write(test_code)
    
    # Compile and run
    compile_result = subprocess.run(
        [
            "gcc",
            "/tmp/version_test.c",
            "-I/usr/local/include",
            "-L/usr/local/lib",
            "-lnumutils",
            "-lm",
            "-o",
            "/tmp/version_test"
        ],
        capture_output=True,
        text=True
    )
    
    assert compile_result.returncode == 0, \
        f"Failed to compile version test: {compile_result.stderr}"
    
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = "/usr/local/lib"
    
    result = subprocess.run(
        ["/tmp/version_test"],
        capture_output=True,
        text=True,
        env=env
    )
    
    assert "1.2.3" in result.stdout, \
        f"Library reported wrong version: {result.stdout}"


def test_source_not_modified_to_cheat():
    """Verify the test program wasn't gutted to bypass library testing"""
    
    # Check test file exists and wasn't modified
    assert os.path.exists("/app/test_numutils.c"), "Test file is missing"
    
    # Verify test_numutils.c contains expected tests (hasn't been gutted)
    with open("/app/test_numutils.c", 'r') as f:
        content = f.read()
    
    expected_tests = [
        "nu_mean",
        "nu_variance",
        "nu_matrix_create",
        "nu_root_bisection",
        "nu_integrate_trapezoid",
        "nu_correlation"
    ]
    
    for test in expected_tests:
        assert test in content, \
            f"Test file appears to have been modified - missing reference to {test}"
    
    # The test_numutils binary should exist and be runnable
    # This only happens after successful compilation which requires the library
    assert os.path.exists("/app/test_numutils"), \
        "test_numutils binary not found - library not successfully built and linked"
    
    # Run the test to verify library actually works
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = "/usr/local/lib"
    
    result = subprocess.run(
        ["/app/test_numutils"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )
    
    assert result.returncode == 0, \
        f"test_numutils failed - library not fully functional:\n{result.stdout}"
    assert "Failed: 0" in result.stdout, \
        f"test_numutils reported failures:\n{result.stdout}"
