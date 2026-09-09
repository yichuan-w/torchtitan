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
does not demonstrate a semantic error. A successful setup must exit zero;
propagate setup errors instead of masking them with `exit 0`.

Write `run/verifier-probes/contract.json` with a nonempty `cases` array, ordered
like the wrong scripts. Each case has string fields `requirement` (the relevant
public clause), `wrong_behavior` (the specific error), and `expected_failure`
(the witness input, correct output, and wrong output). The saved scripts must
run inside a fresh container. Save container commands directly in each script:
the caller passes its entire contents to `./sandbox exec`. Do not put sandbox
commands, host paths, or an outer launcher in a saved script. Each script must
be self-contained: sibling control files are not copied into the container.
To validate a saved script from this workspace, run `./sandbox reset`, then
`./sandbox exec "$(cat run/verifier-probes/correct.sh)"` (or the wrong script).
Stdin is not forwarded. Execute student scripts only in that container.
Edit only files under `run/verifier-probes/`, except for an ambiguity verdict.

If the public specification is ambiguous enough that the two outputs cannot be
distinguished, write the precise ambiguity to `run/verdict.txt` and finish.
Otherwise finish after the correct implementation and all witness demonstrations
have run. The caller will replay the controls against the withheld grader.
