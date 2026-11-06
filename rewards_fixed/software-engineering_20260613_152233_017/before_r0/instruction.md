I need a small LISP interpreter written in C. It should evaluate S-expressions with arithmetic, conditionals, first-class functions with lexical scoping, and recursion.

Put the source at `/app/calc/calc.c`. It reads S-expressions from stdin, one per line, evaluates them, and prints the result to stdout as a decimal integer followed by a newline.

Here's what it needs to support:

**Literals:** integers like `42` or `-10`.

**Arithmetic:** `(+ a b c...)`, `(- a b c...)`, `(* a b c...)`, `(/ a b)`. All take variable numbers of arguments. `+` and `*` with zero arguments return 0 and 1 respectively. `-` and `/` need at least one argument. Division by zero should print `Error` to stderr and exit with code 1.

**Comparisons:** `(= a b)`, `(< a b)`, `(> a b)`. Return `1` for true, `0` for false. All take exactly two arguments.

**Conditional:** `(if cond then else)`. Evaluate `cond`. If non-zero, evaluate and return `then`. Otherwise evaluate and return `else`. The branch NOT taken must NOT be evaluated.

**Multi-way conditional:** `(cond (test1 expr1) (test2 expr2) ... (else exprN))`. Evaluate tests in order. When a test is non-zero, evaluate and return the corresponding expr. `else` always matches. If no test matches, print `Error` to stderr and exit with code 1.

**Variables:** `(define name value)` binds a name to a value and returns the value.

**Function definitions:** `(define (name params...) body1 body2...)` is syntactic sugar for `(define name (lambda (params...) body1 body2...))`. The function name is in scope inside the body, so recursion works.

**Lambda:** `(lambda (params...) body1 body2...)` creates an anonymous function with lexical scoping. When called, it evaluates each body expression in order and returns the value of the last one.

**Function application:** `(func arg1 arg2...)` where `func` evaluates to a function. Arguments are evaluated left-to-right before the function is called. The number of arguments must match the number of parameters.

**Let:** `(let ((var1 val1) (var2 val2) ...) body1 body2...)` introduces local bindings and evaluates body expressions in order, returning the last.

**Begin:** `(begin expr1 expr2 ...)` evaluates expressions in order and returns the value of the last.

When printing a function value, print `0`. Referring to an undefined name should print `Error` to stderr and exit with code 1.

Example:
```
(define (fact n) (if (= n 0) 1 (* n (fact (- n 1)))))
0
(fact 5)
120
(define (make-counter start)
  (lambda ()
    (define result start)
    (define start (+ start 1))
    result))
0
(define c (make-counter 10))
0
(c)
10
(c)
11
(cond ((< 3 1) 100) ((= 5 5) 200) (else 300))
200
```

Any malformed expression should print `Error` to stderr and exit with code 1. This includes unmatched parentheses, unknown operators, wrong number of arguments for comparisons, undefined names, or anything that can't be parsed.

Write a Makefile at `/app/calc/Makefile` that compiles `calc.c` into a binary at `/app/calc/calc`. The default target should build it. Use `gcc` with `-Wall -Wextra` and the `-std=c99` flag.