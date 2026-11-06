import os
import subprocess
import tempfile
from pathlib import Path


def compile_asm():
    """Compile /app/asm.c into /app/asm."""
    asm_src = Path("/app/asm.c")
    if not asm_src.exists():
        raise AssertionError("/app/asm.c does not exist")
    result = subprocess.run(
        ["gcc", "-static", "-O2", "-o", "/app/asm", "/app/asm.c", "-lm"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"Failed to compile /app/asm.c: {result.stderr}")
    return True


def assemble_and_run(assembly_source, expected_output):
    """Assemble the given source, run through VM, and compare output."""
    compile_asm()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.asm', delete=False) as f:
        asm_path = f.name
        f.write(assembly_source)

    try:
        result = subprocess.run(
            ["/app/asm", asm_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"Assembler failed (exit {result.returncode}): {result.stderr.decode()}\n"
                f"Source:\n{assembly_source}"
            )
        bytecode = result.stdout
        if not bytecode:
            raise AssertionError("Assembler produced empty output")

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.bin', delete=False) as f:
            bin_path = f.name
            f.write(bytecode)

        try:
            result = subprocess.run(
                ["/app/vm", bin_path],
                capture_output=True, timeout=30,
            )
            output = result.stdout.decode()
            assert output == expected_output, (
                f"Output mismatch.\n"
                f"Expected: {repr(expected_output)}\n"
                f"Got:      {repr(output)}\n"
                f"VM stderr: {result.stderr.decode()}"
            )
        finally:
            os.unlink(bin_path)
    finally:
        os.unlink(asm_path)


def test_asm_exists():
    """Verify /app/asm.c exists and compiles."""
    assert Path("/app/asm.c").exists(), "/app/asm.c does not exist"
    compile_asm()
    assert Path("/app/asm").exists(), "/app/asm binary not created"


def test_push_print():
    assemble_and_run(
        "PUSH 42\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "42\n"
    )


def test_arithmetic():
    # (10 + 5) * 3 - 8 / 2 = 41
    assemble_and_run(
        "PUSH 10\nPUSH 5\nADD\nPUSH 3\nMUL\n"
        "PUSH 8\nPUSH 2\nDIV\nSUB\nPRINT\n"
        "PUSH 10\nPRCH\nHALT\n",
        "41\n"
    )


def test_mod():
    assemble_and_run(
        "PUSH 100\nPUSH 7\nMOD\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 100\nPUSH 3\nMOD\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "2 1\n"
    )


def test_comparisons():
    assemble_and_run(
        "PUSH 5\nPUSH 5\nEQ\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 5\nPUSH 3\nEQ\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 3\nPUSH 5\nLT\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 5\nPUSH 3\nLT\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 5\nPUSH 3\nGT\nPRINT\nPUSH 10\nPRCH\n"
        "HALT\n",
        "1 0 1 0 1\n"
    )


def test_dup():
    assemble_and_run(
        "PUSH 42\nDUP\nPRINT\nPUSH 32\nPRCH\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "42 42\n"
    )


def test_swap():
    assemble_and_run(
        "PUSH 10\nPUSH 20\nSWAP\nPRINT\nPUSH 32\nPRCH\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "10 20\n"
    )


def test_pop():
    assemble_and_run(
        "PUSH 1\nPUSH 2\nPOP\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "1\n"
    )


def test_countdown_loop():
    assemble_and_run(
        "PUSH 5\nloop:\nDUP\nPRINT\nPUSH 10\nPRCH\n"
        "PUSH 1\nSUB\nDUP\nPUSH 0\nGT\nJNZ loop\n"
        "POP\nHALT\n",
        "5\n4\n3\n2\n1\n"
    )


def test_squares_loop():
    assemble_and_run(
        "PUSH 1\nloop:\nDUP\nDUP\nMUL\nPRINT\nPUSH 10\nPRCH\n"
        "PUSH 1\nADD\nDUP\nPUSH 6\nLT\nJNZ loop\n"
        "POP\nHALT\n",
        "1\n4\n9\n16\n25\n"
    )


def test_jmp():
    assemble_and_run(
        "PUSH 1\nPRINT\nPUSH 32\nPRCH\nJMP skip\n"
        "PUSH 999\nPRINT\nPUSH 32\nPRCH\n"
        "skip:\nPUSH 2\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "1 2\n"
    )


def test_jz_zero():
    assemble_and_run(
        "PUSH 1\nPRINT\nPUSH 32\nPRCH\nPUSH 0\nJZ skip\n"
        "PUSH 99\nPRINT\nPUSH 32\nPRCH\n"
        "skip:\nPUSH 2\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "1 2\n"
    )


def test_jz_nonzero():
    assemble_and_run(
        "PUSH 1\nPRINT\nPUSH 32\nPRCH\nPUSH 5\nJZ skip\n"
        "PUSH 99\nPRINT\nPUSH 32\nPRCH\n"
        "skip:\nPUSH 2\nPRINT\nPUSH 10\nPRCH\nHALT\n",
        "1 99 2\n"
    )


def test_hello():
    assemble_and_run(
        "PUSH 72\nPRCH\nPUSH 101\nPRCH\nPUSH 108\nPRCH\nPUSH 108\nPRCH\n"
        "PUSH 111\nPRCH\nPUSH 10\nPRCH\nHALT\n",
        "Hello\n"
    )


def test_fibonacci():
    assemble_and_run(
        "PUSH 0\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 1\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 1\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 2\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 3\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 5\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 8\nPRINT\nPUSH 32\nPRCH\n"
        "PUSH 13\nPRINT\nPUSH 10\nPRCH\n"
        "HALT\n",
        "0 1 1 2 3 5 8 13\n"
    )


def test_comments_and_blanks():
    assemble_and_run(
        "; header comment\n\n"
        "PUSH 42  ; inline comment\n"
        "PRINT\n"
        "; more comments\n\n"
        "PUSH 10\nPRCH\n"
        "HALT\n",
        "42\n"
    )
