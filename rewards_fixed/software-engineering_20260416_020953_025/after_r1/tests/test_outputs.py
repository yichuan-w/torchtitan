import subprocess
import os
import re


ORIGINAL_MAKEFILE = b'''# Makefile for binparse library
CC = gcc
CFLAGS = -Wall -Wextra -O2 -g
LDFLAGS = 

SRCS = binparse.c binparse_test.c
OBJS = $(SRCS:.c=.o)
TARGET = binparse_test

.PHONY: all clean

all: $(TARGET)

$(TARGET): binparse.c binparse_test.c binparse.h
\t$(CC) $(CFLAGS) -o $@ binparse.c binparse_test.c $(LDFLAGS)

clean:
\trm -f $(TARGET) $(OBJS)

# Debug build target (for comparison)
debug: CFLAGS = -Wall -Wextra -O0 -g
debug: clean all
'''

ORIGINAL_TEST_SOURCE = b'''/*
 * binparse_test.c - Test program for binparse library
 */

#include "binparse.h"
#include <stdio.h>
#include <stdlib.h>

static const char *error_str(int err) {
    switch (err) {
        case PARSE_OK:      return "OK";
        case PARSE_ERR_IO:  return "IO error";
        case PARSE_ERR_MAGIC: return "Invalid magic/version";
        case PARSE_ERR_CSUM: return "Checksum mismatch";
        case PARSE_ERR_SIZE: return "Invalid size";
        default:            return "Unknown error";
    }
}

int main(int argc, char *argv[])
{
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file1.bin> [file2.bin ...]\\n", argv[0]);
        return 1;
    }
    
    int all_ok = 1;
    
    for (int i = 1; i < argc; i++) {
        printf("Parsing %s... ", argv[i]);
        fflush(stdout);
        
        int result = binparse_file(argv[i]);
        
        if (result == PARSE_OK) {
            printf("OK\\n");
        } else {
            printf("FAILED: %s\\n", error_str(result));
            all_ok = 0;
        }
    }
    
    if (all_ok) {
        printf("\\nAll files parsed successfully.\\n");
        return 0;
    } else {
        printf("\\nSome files failed to parse.\\n");
        return 1;
    }
}
'''


def test_protected_files_unchanged():
    """The public task forbids changing the Makefile or test code."""
    with open("/app/libbinparse/Makefile", "rb") as makefile:
        actual_makefile = makefile.read()
    with open("/app/libbinparse/binparse_test.c", "rb") as test_source:
        actual_test_source = test_source.read()

    assert (actual_makefile, actual_test_source) == (
        ORIGINAL_MAKEFILE,
        ORIGINAL_TEST_SOURCE,
    ), "Do not modify the Makefile or test code"


def test_parse_all_files_with_o2():
    """
    Test that binparse_test successfully parses all test files when compiled with -O2.
    This tests both functionality AND the constraint that optimizations must remain enabled.
    """
    # Clean and build with -O2 (default Makefile)
    subprocess.run(["make", "clean"], cwd="/app/libbinparse", capture_output=True)
    result = subprocess.run(
        ["make"],
        cwd="/app/libbinparse",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Build failed: {result.stderr}"
    
    # Run on all test files
    test_files = [
        "/app/testdata/sample1.bin",
        "/app/testdata/sample2.bin",
        "/app/testdata/sample3.bin",
        "/app/testdata/sample4.bin",
        "/app/testdata/sample5.bin",
    ]
    
    result = subprocess.run(
        ["/app/libbinparse/binparse_test"] + test_files,
        capture_output=True,
        text=True,
        timeout=30
    )
    
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    
    # Verify the Makefile used -O2 (not just modified to use -O0)
    with open("/app/libbinparse/Makefile", 'r') as f:
        makefile = f.read()
    
    cflags_match = re.search(r'CFLAGS\s*=\s*(.+)', makefile)
    assert cflags_match, "Could not find CFLAGS in Makefile"
    cflags = cflags_match.group(1)
    
    assert "-O2" in cflags, f"-O2 must be in CFLAGS (fix should not disable optimizations): {cflags}"
    assert "-O0" not in cflags, f"-O0 should not be in CFLAGS (fix should not disable optimizations): {cflags}"
    
    # Verify all files parsed successfully
    assert result.returncode == 0, f"Test binary failed with return code {result.returncode}: {result.stderr}"
    assert "All files parsed successfully" in result.stdout, "Not all files parsed successfully"


def test_no_undefined_behavior():
    """
    Test that the fixed code runs without undefined behavior under sanitizers.
    This ensures the fix addresses the root cause, not just the symptom.
    """
    # Build with undefined behavior and address sanitizers
    result = subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-O2", "-g", 
         "-fsanitize=undefined,address",
         "-o", "binparse_test_ubsan", 
         "binparse.c", "binparse_test.c"],
        cwd="/app/libbinparse",
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0, f"UBSan build failed: {result.stderr}"
    
    # Run with sanitizer on all test files
    test_files = [
        "/app/testdata/sample1.bin",
        "/app/testdata/sample2.bin",
        "/app/testdata/sample3.bin",
        "/app/testdata/sample4.bin",
        "/app/testdata/sample5.bin",
    ]
    
    result = subprocess.run(
        ["/app/libbinparse/binparse_test_ubsan"] + test_files,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "UBSAN_OPTIONS": "halt_on_error=1:abort_on_error=1"}
    )
    
    print("UBSan STDOUT:", result.stdout)
    print("UBSan STDERR:", result.stderr)
    
    # Check for UBSan runtime errors
    assert "runtime error" not in result.stderr.lower(), \
        f"Undefined behavior detected: {result.stderr}"
    assert result.returncode == 0, f"Test failed under sanitizer: {result.stderr}"
    assert "All files parsed successfully" in result.stdout, "Not all files parsed under sanitizer"


def test_handles_misaligned_data():
    """
    Test that the library correctly handles data at arbitrary offsets.
    This specifically tests the alignment bug fix.
    """
    # Build with -O2 and -fsanitize=undefined to catch any remaining alignment issues
    result = subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-O2", "-g", 
         "-fsanitize=undefined",
         "-o", "binparse_test_align", 
         "binparse.c", "binparse_test.c"],
        cwd="/app/libbinparse",
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0, f"Build failed: {result.stderr}"
    
    # sample2.bin has records with odd-sized data that creates misalignment
    result = subprocess.run(
        ["/app/libbinparse/binparse_test_align", 
         "/app/testdata/sample2.bin"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "UBSAN_OPTIONS": "halt_on_error=1"}
    )
    
    print("Alignment test STDOUT:", result.stdout)
    print("Alignment test STDERR:", result.stderr)
    
    # Should not report alignment-related UB
    stderr_lower = result.stderr.lower()
    assert "alignment" not in stderr_lower or "runtime error" not in stderr_lower, \
        f"Alignment-related undefined behavior: {result.stderr}"
    assert result.returncode == 0, f"Failed on misaligned data test: {result.stderr}"
