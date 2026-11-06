# EvolveBench audit: is this rewrite a meaningful change, or did the task get worse?

You are auditing rewritten training tasks for a terminal agent. Each task
directory holds `original/` (the seed task: `instruction.md`, `environment/`,
`solution/`, `tests/`) and one or more variants `V1`, `V2`, ... Each variant is
one rewriter's accepted revision of that seed: `package/` (the whole revised
task), `diff_instruction.txt`, `diff_tests.txt`, `diff_solution.txt`,
`diff_environment.txt` (each against the seed), `agent_summary.md` (what the
rewriter said it did) and `direction.txt` (`harder`: the student solved the
seed every time; `easier`: it never did).

The student sees only `instruction.md` and the environment the Dockerfile
builds. It never sees `tests/` or `solution/`. A task is good training data
when a capable student who reads the instruction carefully can solve it, and
one who does the work wrongly or skips it cannot pass.

You do not know which rewriter produced which variant, and nothing depends on
guessing it. Judge each variant on its own against the seed.

## Labels

Give each variant exactly one label, the first that applies in this order:

1. **BROKEN**: the test machinery is wrong. A correct solution fails for a
   reason unrelated to the task (the check crashes, reads the wrong path,
   compares against a value the reference itself does not produce, depends on
   timing or ordering it does not control), or the reference solution passes
   only by doing something the instruction forbids.
2. **LEAKY**: a student can pass without doing the work: the answer or a
   bypass sits in a file the student can read (a log, a README, a cached
   output, a name), or the grader accepts an output that a trivial program
   writes (empty, `[]`, `{}`, a copied fixture). Reward-hackable is LEAKY.
3. **UNFAIR**: the verifier checks something the instruction never states,
   or the instruction is ambiguous between readings that the verifier
   treats differently. Name the check and the sentence it would need.
4. **UNSTATED-ENVIRONMENT-TRAP**: the difficulty comes from an environment
   fact the instruction contradicts or hides in a way no careful reading
   could catch (a file that silently differs from what the instruction
   describes, random bytes that change the answer).
5. **GRADER-TIGHTENING-ONLY**: the only change that adds difficulty is a
   stricter or larger verifier (more cases, tighter tolerances, exact
   formats) for the same work the seed asked for; the student's task did
   not change.
6. **WEAK**: the new step is dictated by the instruction or a README in so
   many words, so the student follows directions rather than infers
   anything. For `easier`, WEAK is a change that hands over the answer or
   the decisive step instead of removing an obstacle.
7. **EFFECTIVE**: the change asks for a new inference from a fact the
   student has to find in the environment (`harder`), or removes an
   obstacle while leaving the core skill with the student (`easier`), and
   every sentence of the instruction stays true.

## What to write

For each variant write `<task>/<V>/verdict.json`:

```json
{"label": "UNFAIR",
 "findings": [
   {"id": "f1", "class": "UNFAIR",
    "file": "package/tests/test_outputs.py", "line": 42,
    "evidence": "asserts the CSV has a trailing newline; instruction.md never says so",
    "would_fail": "a solution that writes rows with csv.writer and closes the file"}
 ],
 "notes": "one or two sentences on what the rewrite changed"}
```

Every finding names a file and line, quotes the evidence, and says which
plausible solution it would wrongly fail or wrongly pass. A label of
EFFECTIVE, WEAK or GRADER-TIGHTENING-ONLY may have no findings. Read the
seed before the variant: a defect the seed already had is not the
rewrite's; record it with `"class": "SEED"` so it is not counted.

Do not run anything that needs network access, and do not edit any file
outside the `verdict.json` files you write.
