import hashlib
import os
import subprocess

import pytest


PARSER_HEADER_SHA256 = "effe703bd880fcc41b9e192682b9093710bba030cb6e798789f5e68ab5a5a841"
PARSER_LIBRARY_SHA256 = "54dbe4396f29eeb3e4e94827a7b3d0fa29be5e3add5f7ba32e8bcd5c540e687c"


def compile_reader():
    subprocess.run(
        ["make", "-C", "/app", "clean"], capture_output=True, text=True, timeout=30
    )
    result = subprocess.run(
        ["make", "-C", "/app"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        pytest.fail(
            f"Failed to compile reader:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    executable = "/app/reader"
    assert os.path.exists(executable), "Reader executable was not created"
    return executable


def file_sha256(path):
    with open(path, "rb") as input_file:
        return hashlib.file_digest(input_file, "sha256").hexdigest()


def test_parser_dependencies_unchanged():
    assert (
        file_sha256("/app/parser.h") == PARSER_HEADER_SHA256
        and file_sha256("/app/libparser.so") == PARSER_LIBRARY_SHA256
    ), "parser.h and the compiled libparser.so must remain unchanged"


def test_malformed_file_does_not_crash():
    executable = compile_reader()
    result = subprocess.run(
        [executable, "/app/malformed.dat"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode >= 0, (
        f"Reader crashed on malformed.dat (killed by signal {-result.returncode}):\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_no_valgrind_heap_errors():
    executable = compile_reader()
    result = subprocess.run(
        [
            "valgrind",
            "--leak-check=full",
            "--error-exitcode=99",
            "--errors-for-leak-kinds=all",
            executable,
            "/app/malformed.dat",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode >= 0 and "ERROR SUMMARY: 0 errors" in result.stderr, (
        "Reader crashed or Valgrind detected memory errors:\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_both_files_process_in_sequence():
    executable = compile_reader()
    malformed_result = subprocess.run(
        [executable, "/app/malformed.dat"], capture_output=True, text=True, timeout=60
    )
    assert malformed_result.returncode >= 0, (
        f"Reader crashed on malformed.dat (signal {-malformed_result.returncode}):\n"
        f"STDOUT: {malformed_result.stdout}\nSTDERR: {malformed_result.stderr}"
    )

    normal_result = subprocess.run(
        [executable, "/app/normal.dat"], capture_output=True, text=True, timeout=30
    )
    assert normal_result.returncode == 0, (
        f"Reader failed on normal.dat after malformed.dat (exit {normal_result.returncode}):\n"
        f"STDOUT: {normal_result.stdout}\nSTDERR: {normal_result.stderr}"
    )
    assert "Dataset: 3 items" in normal_result.stdout, (
        f"Expected 3 items. Got:\nSTDOUT: {normal_result.stdout}"
    )
