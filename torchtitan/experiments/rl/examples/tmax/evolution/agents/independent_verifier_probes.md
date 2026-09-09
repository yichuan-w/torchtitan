# Independent semantic controls

Design replay controls for the task described by `instruction.md` and the files
available in its environment. The grading tests and reference solution are not
provided. Work inside this package; use the container to investigate the public
task and test your implementations. The file `tests/test.sh` is a placeholder,
so `grade`, `check`, and `oracle` cannot validate your work.

Save a complete correct implementation as `run/verifier-probes/correct.sh`.
Then save `wrong-1.sh`, `wrong-2.sh`, and so on. Each wrong implementation should
make a specific semantic mistake while otherwise completing the task. Derive
these mistakes from the public requirements, including independently falsifiable
validity conditions and decisions needed when inputs change. A constant answer
alone does not cover errors in implementations that actually process their inputs.

For each wrong implementation, construct a concrete input that exposes its
mistake while satisfying the other input conditions. Change inputs only where
the public task requires handling those changes. Run that input against your
correct and wrong implementations in the container. Check the two observable
outputs against the public specification. Include the changed-input witness in
the wrong script: demonstrate and print its incorrect output, restore the
original input, and rerun the wrong implementation so the caller can grade it
on its own tests. A script that crashes or never produces the requested artifact
does not demonstrate a semantic error. All saved scripts must exit zero.

Write `run/verifier-probes/contract.json` with a nonempty `cases` array, ordered
like the wrong scripts. Each case has string fields `requirement` (the relevant
public clause), `wrong_behavior` (the specific error), and `expected_failure`
(the witness input, correct output, and wrong output). The saved scripts must
run from a fresh environment. Use `./sandbox reset` and pass script contents as
the argument to `./sandbox exec`; stdin is not forwarded. Execute student scripts
only in that container. Edit only files under `run/verifier-probes/`.

If the public specification is ambiguous enough that the two outputs cannot be
distinguished, write the precise ambiguity to `run/verdict.txt` and finish.
Otherwise finish after the correct implementation and all witness demonstrations
have run. The caller will replay the controls against the withheld grader.
