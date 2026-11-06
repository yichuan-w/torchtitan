# Hardening rationale

## Trace evidence and current strategy

All 12 current attempts passed. Their common strategy was to inspect `parsimonious/nodes.py` and the existing node tests, replace `generic_visit()` with the old `NotImplementedError`, remove the `pos + 1` offset from `NodeVisitor.match()`, and run the node or full test suite. For example, `traces/attempt-10.jsonl` changes the match line at turn 9, repairs `generic_visit()` at turns 10-18, and passes the node and full suites at turns 23-24. `traces/attempt-11.jsonl` makes the same two local edits at turns 8-9 and refines the exception text against the existing tests at turns 16-20. The lone earlier mixed-group failure, `traces/revision/mixed/g744-r5.jsonl`, overcomplicated the exception formatting and submitted despite two failing repository tests at turn 62; passing attempts preserve the exact expression rendering.

The current task therefore spares the student from debugging beyond two conspicuous changed lines in one method cluster. A full repository test run accepts those edits and exercises no zero-width `OneOrMore` match at end-of-input.

## Primary sources fetched

- https://api.github.com/repos/erikrose/parsimonious/issues/257 — upstream issue 257 says that `OneOrMore` over a zero-width member fails when the cursor is at end-of-input because `while new_pos < size` prevents the first sub-match at `new_pos == size`; its reproduction is `Grammar('foo = ~"a*"+').parse("")`, which raises `ParseError` instead of succeeding.
- https://raw.githubusercontent.com/erikrose/parsimonious/eb79639859a9697a86c0992a045174a8856b5fb0/parsimonious/expressions.py — the source at the pinned upstream commit has SHA-256 `9667cd963b4334cb9f0c7f07b31410745c942b25dfd2b8f2cbd92e4e5ab29d0e` and contains the reported `while new_pos < size` guard.
- https://api.github.com/repos/erikrose/parsimonious/commits/eb79639 — GitHub identifies the pinned artifact as commit `eb79639859a9697a86c0992a045174a8856b5fb0`, whose message is “Bump version number and update readme for release.”

## Reproduction in this environment

On the untouched task image I ran:

```
python - <<'PY'
from parsimonious import Grammar
Grammar('foo = ~"a*"+').parse("")
PY
```

It produced `ParseError: Rule 'foo' didn't match at '' (line 1, column 1).` The installed `/testbed/parsimonious/expressions.py` hashes to the same `9667cd...29d0e` as the pinned upstream file. Parsing `"a"` with the same grammar succeeds, confirming the end-of-input shape from the report.

## Proposed change and new decision

Keep the original NodeVisitor repair and additionally require the parser's one-or-more quantifier to attempt its required first match even at end-of-input, while retaining its protection against infinite repetition of zero-width matches. The environment will explicitly install the exact upstream `expressions.py` artifact at commit `eb79639859a9697a86c0992a045174a8856b5fb0`; no fixed release, patch, or fix note is shipped.

This asks the student to move from restoring two obvious NodeVisitor lines to diagnosing one real parser boundary condition in the neighboring library implementation. The `ParseError`, the `foo` rule, and the empty location give an investigating student a bounded lead, but do not dictate whether to alter the loop bound, preflight the minimum, or special-case empty input. It is one modest additional source-debugging decision using the same inspect-edit-test skills the successful traces already show.

## Obvious-fix trials

1. **The tool output's natural local repair:** restored only the two NodeVisitor behaviors used by every successful trace, then graded. Result: **reward 0**. The four inherited NodeVisitor checks passed, while `test_required_zero_width_match_at_end_of_input` failed with the same `ParseError`.
2. **The attempts' common dependency fallback:** ran `python -m pip install --force-reinstall parsimonious==0.10.0`, then graded. Result: **reward 0**. Because the checkout remains the imported package, the four NodeVisitor checks still passed and the zero-width end-of-input check still failed. Reinstalling/upgrading therefore does not announce or apply the source repair.

## Successful-attempt replays and prediction

### Replay 1: `traces/attempt-10.jsonl`

From a fresh reset, I replayed the attempt's effective edits: removed `pos + 1`, restored the original `generic_visit()` exception and exact expression rendering, and ran the full repository suite in the `testbed` environment. The suite reported `83 passed, 2 skipped`, exactly the success signal on which this attempt stopped. Grading then gave **reward 0**. The match-shortcut, visitation-exception, named-expression and unnamed-expression checks all passed; only the required zero-width end-of-input behavior failed with `ParseError`.

### Replay 2: `traces/attempt-11.jsonl`

From another fresh reset, I replayed this attempt's effective two-line repair and its final targeted command, `python -m pytest parsimonious/tests/test_nodes.py -v`. It reported `11 passed, 1 skipped`, matching the attempt's stopping criterion. Grading again gave **reward 0**: all four inherited checks passed and only the required zero-width end-of-input behavior failed with `ParseError`.

The revised task should therefore interrupt both representative successful paths only after their familiar green suite. I predict **6 of 12** for the next group: all current attempts can restore NodeVisitor, but they must notice the additional public reproduction, move to `expressions.py`, and change the quantifier guard without breaking zero-width loop termination; the traces show broad source-debugging ability but a strong tendency to stop once the existing suite is green.
