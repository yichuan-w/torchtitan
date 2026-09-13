# Independent semantic controls

Design replay controls for the task described by `instruction.md` and the files
available in its environment. The grading tests and reference solution are not
provided. Work inside this package; use the container to investigate the public
task and test your implementations. The file `tests/test.sh` is a placeholder,
so `grade`, `check`, and `oracle` cannot validate your work.
Use only this package as task evidence. Do not search or read sibling tasks,
prior experiment outputs, campaign files, or other sessions to recover withheld
graders, solutions, or examples. Public tool documentation remains available.

Save a complete correct implementation as `run/verifier-probes/correct.sh`.
Use that implementation to test choices the public task leaves open. Where
permitted, use different internal names or equivalent data representations
instead of relying on conventional defaults; preserve every explicitly required
name, type, ordering rule, and interface. In a top-level `correct` object in
`run/verifier-probes/contract.json`, record the choices exercised and why they satisfy the public
requirements. If no such choice is available, record that and use the required
representation. Verify the positive deliverable against the public requirements
before presenting it to the withheld grader.
For a transformation task that permits alternative representations, exercise a
choice in the transformed content itself, such as a lossless change in numeric encoding or
serialization, and verify that the represented result is unchanged. Changing
only report labels or paths does not cover this choice. Use the public task to
identify fixed properties; an encoder's defaults do not make unspecified
properties mandatory. Record the equivalence check in the `correct` object.
Include a legal nondefault container or serialization supported by the task's
required tool when available; changing numeric precision alone does not exercise
container parsing. Verify the decoded content and record the chosen variant.
Include a transformation error that preserves the required format and metadata
while changing the represented content. For every field the task defines
numerically, including parsed numeric fields, pair a correct equivalent encoding
with a small wrong-value deviation in that same encoding. Preserve any explicitly
required type; where it is open, JSON `1.0` can represent the same number as `1`.
Changing whitespace or key order does not exercise numeric equality. Check that
the correct value is unchanged and that the wrong value exceeds the error allowed
by the public task and permitted encodings. Record both values, the deviation,
and the allowance in the contract.
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
