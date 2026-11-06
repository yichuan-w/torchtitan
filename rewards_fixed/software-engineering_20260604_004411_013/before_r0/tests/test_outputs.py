import subprocess
import os
import sys

LISP_BIN = "/app/lisp"

def run_lisp(expr):
    """Run a Lisp expression through the interpreter and return stdout."""
    result = subprocess.run(
        [LISP_BIN],
        input=expr + "\n",
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()

def run_lisp_file(filepath):
    """Run a Lisp file through the interpreter and return stdout."""
    result = subprocess.run(
        [LISP_BIN, filepath],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()

def test_binary_exists():
    """Verify the lisp binary exists and is executable."""
    assert os.path.exists(LISP_BIN), f"Binary not found: {LISP_BIN}"
    assert os.access(LISP_BIN, os.X_OK), f"Binary not executable: {LISP_BIN}"

def test_simple_addition():
    """Test (+ 1 2) => 3"""
    output = run_lisp("(+ 1 2)")
    assert "3" in output, f"Expected 3 in output, got: {output}"

def test_nested_arithmetic():
    """Test (+ (* 2 3) 4) => 10"""
    output = run_lisp("(+ (* 2 3) 4)")
    assert "10" in output, f"Expected 10 in output, got: {output}"

def test_subtraction():
    """Test (- 10 3 2) => 5"""
    output = run_lisp("(- 10 3 2)")
    assert "5" in output, f"Expected 5 in output, got: {output}"

def test_division():
    """Test (/ 12 3) => 4"""
    output = run_lisp("(/ 12 3)")
    assert "4" in output, f"Expected 4 in output, got: {output}"

def test_negative_numbers():
    """Test (+ -5 8) => 3"""
    output = run_lisp("(+ -5 8)")
    assert "3" in output, f"Expected 3 in output, got: {output}"

def test_variable_definition():
    """Test (define x 10) (+ x 5) => 15"""
    # Run both expressions in one process via a test file
    test_file = "/tmp/test_var.lisp"
    with open(test_file, "w") as f:
        f.write("(define x 10)\n(+ x 5)\n")
    output = run_lisp_file(test_file)
    assert "15" in output, f"Expected 15 in output, got: {output}"

def test_conditional_true():
    """Test (if (> 5 3) 100 200) => 100"""
    output = run_lisp("(if (> 5 3) 100 200)")
    assert "100" in output, f"Expected 100 in output, got: {output}"

def test_conditional_false():
    """Test (if (< 5 3) 100 200) => 200"""
    output = run_lisp("(if (< 5 3) 100 200)")
    assert "200" in output, f"Expected 200 in output, got: {output}"

def test_cons_car_cdr():
    """Test (car (cons 42 99)) => 42"""
    output = run_lisp("(car (cons 42 99))")
    assert "42" in output, f"Expected 42 in output, got: {output}"

def test_lambda_application():
    """Test ((lambda (x) (* x x)) 5) => 25"""
    output = run_lisp("((lambda (x) (* x x)) 5)")
    assert "25" in output, f"Expected 25 in output, got: {output}"

def test_nested_function():
    """Test ((lambda (x) ((lambda (y) (+ x y)) 3)) 7) => 10"""
    output = run_lisp("((lambda (x) ((lambda (y) (+ x y)) 3)) 7)")
    assert "10" in output, f"Expected 10 in output, got: {output}"

def test_list_creation():
    """Test (car (cdr (list 1 2 3))) => 2"""
    output = run_lisp("(car (cdr (list 1 2 3)))")
    assert "2" in output, f"Expected 2 in output, got: {output}"

def test_equality():
    """Test (= 5 5) => t"""
    output = run_lisp("(= 5 5)")
    assert "t" in output, f"Expected t in output, got: {output}"

def test_recursive_function():
    """Test a recursive factorial function."""
    test_file = "/tmp/test_fact.lisp"
    with open(test_file, "w") as f:
        f.write("(define fact (lambda (n) (if (< n 2) 1 (* n (fact (- n 1))))))\n")
        f.write("(fact 5)\n")
    output = run_lisp_file(test_file)
    assert "120" in output, f"Expected 120 in output, got: {output}"

def test_closure():
    """Test that closures capture their defining environment."""
    test_file = "/tmp/test_closure.lisp"
    with open(test_file, "w") as f:
        f.write("(define make-adder (lambda (x) (lambda (y) (+ x y))))\n")
        f.write("((make-adder 10) 7)\n")
    output = run_lisp_file(test_file)
    assert "17" in output, f"Expected 17 in output, got: {output}"

def test_let_binding():
    """Test (let ((x 10) (y 20)) (+ x y)) => 30"""
    test_file = "/tmp/test_let.lisp"
    with open(test_file, "w") as f:
        f.write("(let ((x 10) (y 20)) (+ x y))\n")
    output = run_lisp_file(test_file)
    assert "30" in output, f"Expected 30 in output, got: {output}"

def test_cond():
    """Test cond with multiple clauses."""
    test_file = "/tmp/test_cond.lisp"
    with open(test_file, "w") as f:
        f.write("(cond ((> 3 5) 100) ((= 2 2) 200) (t 300))\n")
    output = run_lisp_file(test_file)
    assert "200" in output, f"Expected 200 in output, got: {output}"

def test_let_shadowing():
    """Test that let properly shadows outer bindings."""
    test_file = "/tmp/test_let_shadow.lisp"
    with open(test_file, "w") as f:
        f.write("(define x 100)\n")
        f.write("(let ((x 5) (y x)) (+ x y))\n")
    output = run_lisp_file(test_file)
    # y should get the outer x (100) in parallel let, x is 5
    assert "105" in output, f"Expected 105 in output, got: {output}"

def test_set_bang():
    """Test that set! can modify existing bindings."""
    test_file = "/tmp/test_setbang.lisp"
    with open(test_file, "w") as f:
        f.write("(define x 10)\n")
        f.write("(set! x 42)\n")
        f.write("x\n")
    output = run_lisp_file(test_file)
    assert "42" in output, f"Expected 42 in output, got: {output}"

def test_deep_recursion():
    """Test moderately deep recursion for factorial of 10."""
    test_file = "/tmp/test_deep.lisp"
    with open(test_file, "w") as f:
        f.write("(define fact (lambda (n) (if (< n 2) 1 (* n (fact (- n 1))))))\n")
        f.write("(fact 10)\n")
    output = run_lisp_file(test_file)
    assert "3628800" in output, f"Expected 3628800 in output, got: {output}"

def test_quasiquote():
    """Test quasiquotation: `(+ ,5 3) should produce the list (+ 5 3)."""
    output = run_lisp("`(+ ,5 3)")
    # Should produce the list (+ 5 3), printed as (+ 5 3)
    assert "(+ 5 3)" in output or "(+ 5 3)" in output.replace(" ", ""), \
        f"Expected (+ 5 3) in output, got: {output}"
