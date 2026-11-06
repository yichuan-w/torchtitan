I've been working on a small Lisp interpreter called MiniLisp. It's supposed to be a simple embedded scripting language for a larger project, but I can't get it to build or run correctly, and it's missing some features I need.

The source code is in `/app/src/` with headers in `/app/include/`. There's a Makefile at `/app/`. The binary should end up as `/app/lisp`.

When I try to build it, the compiler complains about various things. And even when I've hacked around the build errors, the interpreter gives wrong results for basic operations. I also never got around to implementing `let` for local variable bindings or `cond` for multi-way conditionals — those are stubbed out but don't work.

Can you fix all the issues, implement `let` and `cond`, and get the binary `/app/lisp` working? It should correctly evaluate Lisp expressions - at a minimum:
- Integer arithmetic: `(+ 1 2)` should give `3`, `(* 3 4)` should give `12`, etc.
- Negative numbers (e.g., `(+ -5 10)` should give `5`)
- Variables: `(define x 10)` then `(+ x 5)` should give `15`
- Functions: `((lambda (x) (* x x)) 5)` should give `25`
- Conditionals: `(if (> 5 3) 100 200)` should give `100`
- List operations: `(car (cons 42 99))` should give `42`
- Closures: `(define make-adder (lambda (x) (lambda (y) (+ x y))))` then `((make-adder 10) 7)` should give `17`
- Local bindings with `let`: `(let ((x 10) (y 20)) (+ x y))` should give `30`
- Multi-way conditionals with `cond`: `(cond ((> 3 5) 100) ((= 2 2) 200) (t 300))` should give `200`
- Recursion: `(define fact (lambda (n) (if (< n 2) 1 (* n (fact (- n 1))))))` then `(fact 5)` should give `120`

The interpreter reads expressions from stdin (REPL mode) or from a file given as the first argument. Each expression is evaluated and the result is printed on stdout.

Note: the `let` form should work like `(let ((var1 val1) (var2 val2) ...) body)` — it creates new bindings and evaluates body in the extended environment. The `cond` form should work like `(cond (test1 expr1) (test2 expr2) ...)` — it evaluates each test in order and returns the result of the first expression whose test is non-nil. The symbol `t` should be treated as truth.

Also, I was planning to add quasiquotation (backquote) support. The backquote `` ` `` should work like quote but allow unquoting with `,`. For example, `` `(+ ,x 3) `` with `x` bound to `5` should produce the list `(+ 5 3)`. The reader should parse backquote and comma, and the evaluator should expand them into `cons`/`list`/`append` calls. This is partially started in the reader (the backquote and comma characters are recognized) but the expansion logic in the evaluator isn't implemented — it currently treats backquote like regular quote.
