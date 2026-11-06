import os
import subprocess
import time


APP = "/app"


def run(command, timeout=120):
    return subprocess.run(
        command,
        cwd=APP,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def require_success(result, context):
    assert result.returncode == 0, (
        f"{context}:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_parallel_build_succeeds():
    """Test that parallel build works from clean state."""
    run(["make", "clean"], timeout=60)
    result = run(["make", "-j4"])

    require_success(result, "Parallel build failed")
    assert os.path.exists("/app/parser"), "Parser executable not found after build"


def test_parser_executable_works():
    """Test that the parser can parse the test input file."""
    assert os.path.exists("/app/parser"), "Parser executable not found"

    result = run(["./parser", "test_input.dat"], timeout=30)

    require_success(result, "Parser failed")
    assert "Parse successful" in result.stdout, (
        f"Parser did not report success:\n{result.stdout}"
    )
    assert "OBJECT:" in result.stdout, (
        f"Parser did not produce expected AST output:\n{result.stdout}"
    )


def test_parallel_build_is_deterministic():
    """Test repeated clean parallel builds for race conditions."""
    for attempt in range(3):
        run(["make", "clean"], timeout=60)
        result = run(["make", "-j4"])
        require_success(result, f"Parallel build attempt {attempt + 1} failed")


def test_incremental_header_rebuild():
    """A meaningful header edit must affect the incrementally rebuilt parser."""
    config_path = "/app/include/config.h"
    original = open(config_path, "rb").read()

    run(["make", "clean"], timeout=60)
    require_success(run(["make", "-j4"]), "Initial build failed")

    try:
        with open(config_path, "ab") as config:
            config.write(b"\n#undef VERSION_PATCH\n#define VERSION_PATCH 73\n")
        os.utime(config_path, ns=(time.time_ns(), time.time_ns() + 2_000_000_000))

        require_success(run(["make", "-j4"]), "Incremental header rebuild failed")
        result = run(["./parser", "test_input.dat"], timeout=30)
        require_success(result, "Parser failed after incremental header rebuild")
        assert "Parser v1.0.73" in result.stdout, (
            "Editing include/config.h did not update the parser executable:\n"
            f"{result.stdout}"
        )
    finally:
        with open(config_path, "wb") as config:
            config.write(original)
        os.utime(config_path, None)
        run(["make", "-j4"])


def test_incremental_parser_regeneration():
    """Editing the bison source must regenerate and rebuild the parser."""
    grammar_path = "/app/src/parser.y"
    original = open(grammar_path, "rb").read()
    marker = b"VERIFIER_PARSER_Y_REGENERATED"

    run(["make", "clean"], timeout=60)
    require_success(run(["make", "-j4"]), "Initial build failed")

    try:
        with open(grammar_path, "wb") as grammar:
            grammar.write(original)
            grammar.write(
                b'\n__attribute__((constructor)) static void verifier_marker(void) '
                b'{ puts("' + marker + b'"); }\n'
            )
        os.utime(grammar_path, ns=(time.time_ns(), time.time_ns() + 2_000_000_000))

        require_success(run(["make", "-j4"]), "Incremental parser regeneration failed")
        result = run(["./parser", "test_input.dat"], timeout=30)
        require_success(result, "Parser failed after parser.y regeneration")
        assert marker.decode() in result.stdout, (
            "Editing src/parser.y did not update the generated parser:\n"
            f"{result.stdout}"
        )
    finally:
        with open(grammar_path, "wb") as grammar:
            grammar.write(original)
        os.utime(grammar_path, None)
        run(["make", "-j4"])
