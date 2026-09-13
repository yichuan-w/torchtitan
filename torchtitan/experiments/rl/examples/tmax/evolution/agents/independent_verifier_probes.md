# Independent semantic controls

Design replay controls for the task described by `instruction.md` and the files
available in its environment. The grading tests and reference solution are not
provided. Work inside this package; use the container to investigate the public
task and test your implementations. The file `tests/test.sh` is a placeholder,
so `grade`, `check`, and `oracle` cannot validate your work.

Save a complete correct implementation as `run/verifier-probes/correct.sh`.
Use that implementation to test choices the public task leaves open. Where
permitted, use different internal names or equivalent data representations
instead of relying on conventional defaults; preserve every explicitly required
name, type, ordering rule, and interface. In a top-level `correct` object in
`run/verifier-probes/contract.json`, record the choices exercised and why they satisfy the public
requirements. If no such choice is available, record that and use the required
representation. Verify the positive deliverable against the public requirements
before presenting it to the withheld grader.
Then save `wrong-1.sh`, `wrong-2.sh`, and so on. Each wrong implementation should
make a specific semantic mistake while otherwise completing the task. Derive
these mistakes from the public requirements, including independently falsifiable
validity conditions. Identify what the public task requires the agent to leave:
a final artifact, or a program explicitly required to handle further inputs.
For a final-artifact task, each wrong script must leave an artifact that violates
the specification on the supplied inputs. Do not invent a requirement to leave
a reusable program or handle changed inputs.

For a task that explicitly requires a reusable program, construct a permitted
input that exposes each wrong program's mistake. Run that input against the
correct and wrong programs and compare their outputs with the public specification.
Include the demonstration in the wrong script, restore the original inputs,
and leave the faulty program available at the entry point the task requires.

Before a wrong script exits zero, check the deliverable it actually leaves
against the public requirement and print the expected and observed behavior
that establishes the violation. The state presented for grading must still
violate that requirement. An earlier incorrect output on changed inputs is
insufficient if restoring the inputs and rerunning leaves a valid deliverable.
If the violation cannot be established, write `BLOCKED: <reason>` to `run/verdict.txt`
and stop instead of labeling the control wrong.
A script that crashes or never produces the requested artifact
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
Before finishing, inspect your workspace changes and remove any extra files you
created outside that directory; keep the supplied package files unchanged.

If the public specification is ambiguous enough that the two outputs cannot be
distinguished, write `BLOCKED: <precise ambiguity>` to `run/verdict.txt` and finish.
Otherwise finish after the correct implementation and all witness demonstrations
have run. The caller will replay the controls against the withheld grader.
