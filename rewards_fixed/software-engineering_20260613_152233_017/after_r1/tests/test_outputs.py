import os
import hashlib
import subprocess
import pytest


CALC_DIR = "/app/calc"
CALC_BIN = os.path.join(CALC_DIR, "calc")
CALC_SRC = os.path.join(CALC_DIR, "calc.c")
MAKEFILE = os.path.join(CALC_DIR, "Makefile")


def run_calc(input_lines):
    """Run the calculator binary with given input lines, return (stdout, stderr, returncode)."""
    input_str = "\n".join(input_lines) + "\n"
    result = subprocess.run(
        [CALC_BIN],
        input=input_str,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


@pytest.fixture(scope="module", autouse=True)
def build_calc():
    """Build the calculator before running tests."""
    assert os.path.isfile(CALC_SRC), f"Missing {CALC_SRC}"
    assert os.path.isfile(MAKEFILE), f"Missing {MAKEFILE}"
    result = subprocess.run(
        ["make", "-C", CALC_DIR],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Make failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert os.path.isfile(CALC_BIN), f"Binary not found at {CALC_BIN}"
    assert os.access(CALC_BIN, os.X_OK), f"Binary not executable: {CALC_BIN}"


# --- Basic arithmetic ---

def test_simple_addition():
    out, err, rc = run_calc(["(+ 1 2)"])
    assert rc == 0
    assert out == "3", f"Expected '3', got '{out}'"


def test_nested_arithmetic():
    out, err, rc = run_calc(["(* (+ 1 2) 3)"])
    assert rc == 0
    assert out == "9", f"Expected '9', got '{out}'"


def test_subtraction_multi_arg():
    out, err, rc = run_calc(["(- 10 3 2)"])
    assert rc == 0
    assert out == "5", f"Expected '5', got '{out}'"


def test_multiplication():
    out, err, rc = run_calc(["(* 2 3 4)"])
    assert rc == 0
    assert out == "24", f"Expected '24', got '{out}'"


def test_division():
    out, err, rc = run_calc(["(/ 10 2)"])
    assert rc == 0
    assert out == "5", f"Expected '5', got '{out}'"


def test_division_truncation():
    out, err, rc = run_calc(["(/ 10 3)"])
    assert rc == 0
    assert out == "3", f"Expected '3', got '{out}'"


def test_negative_numbers():
    out, err, rc = run_calc(["(- 0 5)", "(+ -3 7)"])
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "-5"
    assert lines[1] == "4"


def test_deeply_nested():
    out, err, rc = run_calc(["(* (+ 1 2) (- 10 4) (/ 8 2))"])
    assert rc == 0
    assert out == "72", f"Expected '72', got '{out}'"


def test_zero_arg_plus():
    out, err, rc = run_calc(["(+)"])
    assert rc == 0
    assert out == "0"


def test_zero_arg_multiply():
    out, err, rc = run_calc(["(*)"])
    assert rc == 0
    assert out == "1"


# --- Comparisons ---

def test_equal_comparison():
    out, err, rc = run_calc(["(= 5 5)", "(= 5 6)"])
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "1"
    assert lines[1] == "0"


def test_less_than():
    out, err, rc = run_calc(["(< 3 5)", "(< 5 3)"])
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "1"
    assert lines[1] == "0"


def test_greater_than():
    out, err, rc = run_calc(["(> 5 3)", "(> 3 5)"])
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "1"
    assert lines[1] == "0"


# --- Conditionals ---

def test_if_true_branch():
    out, err, rc = run_calc(["(if 1 10 20)"])
    assert rc == 0
    assert out == "10"


def test_if_false_branch():
    out, err, rc = run_calc(["(if 0 10 20)"])
    assert rc == 0
    assert out == "20"


def test_if_short_circuit():
    """The branch NOT taken must not be evaluated. If the else branch
    were evaluated, (/ 1 0) would cause a division-by-zero error."""
    out, err, rc = run_calc(["(if 1 42 (/ 1 0))"])
    assert rc == 0, f"Short-circuit failed: else branch was evaluated. stderr: {err}"
    assert out == "42"


def test_if_with_comparison():
    out, err, rc = run_calc(["(if (> 5 3) 100 200)"])
    assert rc == 0
    assert out == "100"


# --- Cond ---

def test_cond_first_match():
    out, err, rc = run_calc(["(cond ((< 3 1) 100) ((= 5 5) 200) (else 300))"])
    assert rc == 0
    assert out == "200"


def test_cond_else_fallback():
    out, err, rc = run_calc(["(cond ((< 5 1) 100) ((= 3 7) 200) (else 999))"])
    assert rc == 0
    assert out == "999"


def test_cond_no_match_error():
    out, err, rc = run_calc(["(cond ((< 5 1) 100) ((= 3 7) 200))"])
    assert rc != 0, f"Expected error for cond with no match, got rc={rc}"


def test_cond_evaluates_only_first_match():
    """If the first matching branch's expression would error if evaluated
    in the second branch, the second branch must not be evaluated."""
    out, err, rc = run_calc(["(cond ((= 1 1) 42) ((= 2 2) (/ 1 0)) (else 999))"])
    assert rc == 0, f"cond evaluated a subsequent branch. stderr: {err}"
    assert out == "42"


# --- Variables ---

def test_define_and_use_variable():
    out, err, rc = run_calc(["(define x 5)", "(+ x 3)"])
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "5"
    assert lines[1] == "8"


def test_variable_redefinition():
    out, err, rc = run_calc(["(define x 5)", "(define x 10)", "x"])
    assert rc == 0
    lines = out.split("\n")
    assert lines[2] == "10"


def test_undefined_variable():
    out, err, rc = run_calc(["undefined_var"])
    assert rc != 0
    assert "error" in err.lower() or "Error" in err


# --- Lambda ---

def test_lambda_value_prints_zero():
    out, err, rc = run_calc(["(lambda (x) (+ x 1))"])
    assert rc == 0
    assert out == "0"


def test_simple_lambda_call():
    out, err, rc = run_calc(["((lambda (x) (+ x 1)) 5)"])
    assert rc == 0
    assert out == "6"


def test_lambda_multi_arg():
    out, err, rc = run_calc(["((lambda (x y) (+ x y)) 3 4)"])
    assert rc == 0
    assert out == "7"


def test_lambda_multi_body():
    """Lambda with multiple body expressions returns the last."""
    out, err, rc = run_calc(["((lambda (x) (define y (* x 2)) (+ y 1)) 5)"])
    assert rc == 0
    assert out == "11"


def test_define_named_function():
    session = [
        "(define add1 (lambda (n) (+ n 1)))",
        "(add1 10)",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "0"
    assert lines[1] == "11"


# --- Closure / lexical scoping ---

def test_closure_captures_environment():
    session = [
        "(define make-adder (lambda (x) (lambda (y) (+ x y))))",
        "(define add5 (make-adder 5))",
        "(add5 3)",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[2] == "8"


def test_closure_independence():
    session = [
        "(define make-adder (lambda (x) (lambda (y) (+ x y))))",
        "(define add3 (make-adder 3))",
        "(define add7 (make-adder 7))",
        "(+ (add3 10) (add7 10))",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[3] == "30"


# --- Recursive function definition ---

def test_recursive_factorial():
    session = [
        "(define (fact n) (if (= n 0) 1 (* n (fact (- n 1)))))",
        "(fact 5)",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "0", f"Expected '0' for define, got '{lines[0]}'"
    assert lines[1] == "120", f"Expected '120', got '{lines[1]}'"


def test_mutual_recursion():
    """Test that function definitions with (define (name args) ...) work
    for mutual recursion via a helper."""
    session = [
        "(define (is-even n) (if (= n 0) 1 (is-odd (- n 1))))",
        "(define (is-odd n) (if (= n 0) 0 (is-even (- n 1))))",
        "(is-even 4)",
        "(is-odd 4)",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[2] == "1", f"Expected '1' for (is-even 4), got '{lines[2]}'"
    assert lines[3] == "0", f"Expected '0' for (is-odd 4), got '{lines[3]}'"


# --- Counter / mutation via closure ---

def test_counter_closure():
    """Test that a closure can maintain mutable state via define in the
    captured environment."""
    session = [
        "(define (make-counter start)",
        "  (lambda ()",
        "    (define result start)",
        "    (define start (+ start 1))",
        "    result))",
        "(define c (make-counter 10))",
        "(c)",
        "(c)",
        "(c)",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    # Line 0: define make-counter -> 0
    # Line 1: define c -> 0
    # Line 2: (c) -> 10
    # Line 3: (c) -> 11
    # Line 4: (c) -> 12
    assert lines[0] == "0", f"Line 0: expected '0', got '{lines[0]}'"
    assert lines[1] == "0", f"Line 1: expected '0', got '{lines[1]}'"
    assert lines[2] == "10", f"Line 2: expected '10', got '{lines[2]}'"
    assert lines[3] == "11", f"Line 3: expected '11', got '{lines[3]}'"
    assert lines[4] == "12", f"Line 4: expected '12', got '{lines[4]}'"


# --- Let ---

def test_simple_let():
    out, err, rc = run_calc(["(let ((x 3) (y 4)) (* x y))"])
    assert rc == 0
    assert out == "12"


def test_let_shadows_global():
    session = [
        "(define x 10)",
        "(let ((x 5)) x)",
        "x",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[1] == "5"
    assert lines[2] == "10"


def test_let_multi_body():
    """Let with multiple body expressions returns the last."""
    out, err, rc = run_calc(["(let ((a 2) (b 3)) (define x (* a b)) (+ x 1))"])
    assert rc == 0
    assert out == "7"


# --- Begin ---

def test_begin_returns_last():
    out, err, rc = run_calc(["(begin (+ 1 2) (* 3 4) (- 10 5))"])
    assert rc == 0
    assert out == "5"


def test_begin_with_define():
    session = [
        "(begin (define x 10) (define y 20) (+ x y))",
        "x",
    ]
    out, err, rc = run_calc(session)
    assert rc == 0
    lines = out.split("\n")
    assert lines[0] == "30"
    assert lines[1] == "10"


# --- Error handling ---

def test_division_by_zero():
    out, err, rc = run_calc(["(/ 10 0)"])
    assert rc != 0


def test_unmatched_parens():
    out, err, rc = run_calc(["(+ 1 2"])
    assert rc != 0


def test_empty_input():
    out, err, rc = run_calc([""])
    assert rc == 0
    assert out == ""


def test_too_few_args_comparison():
    out, err, rc = run_calc(["(= 5)"])
    assert rc != 0


def test_lambda_wrong_arg_count():
    session = [
        "(define f (lambda (x y) (+ x y)))",
        "(f 1)",
    ]
    out, err, rc = run_calc(session)
    assert rc != 0


# --- Makefile ---

def test_makefile_uses_wall_wextra():
    assert os.path.isfile(MAKEFILE)
    with open(MAKEFILE) as f:
        content = f.read()
    assert "-Wall" in content, "Makefile should use -Wall flag"
    assert "-Wextra" in content, "Makefile should use -Wextra flag"
    assert "-std=c99" in content, "Makefile should use -std=c99 flag"


def test_prepared_calc_foundation_is_retained():
    with open(CALC_SRC, "rb") as f:
        content = f.read()
    prefix = content[:5536]
    main_start = content.find(b"int main(void)")
    suffix = content[main_start:] if main_start >= 0 else b""
    assert (hashlib.sha256(prefix).hexdigest(), len(suffix), hashlib.sha256(suffix).hexdigest()) == (
        "3b8d00d199d6332cd835c9083c756b6d89b3287bd79be1bd4512653b2675598f",
        1259,
        "11f6bf9ff05340bc8cd98e145aafeb9f91d25184b764932468d5d2770cda6169",
    )


def test_prepared_makefile_is_retained():
    with open(MAKEFILE, "rb") as f:
        content = f.read()
    assert (len(content), hashlib.sha256(content).hexdigest()) == (
        101,
        "3d29dbd62d56deef38684d85d946a3d7fff8ff17f56bff1e97332f71cc657ac2",
    )
