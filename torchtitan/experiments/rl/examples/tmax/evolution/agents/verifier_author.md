# Verifier author

You are writing the verifier for one task in a reinforcement-learning training
pool. Another session changed the task's difficulty by revising its requirements
or the dependencies in its workflow. You write the checks that grade an attempt at the task as the
instruction now states it.

**You are not shown the reference solution, and that is the point.** A verifier
written beside the solution inherits the solution's private vocabulary: the key
names of the report it happens to write, the label its regex anchors on, the file
name it chose for an artifact. An agent that does every bit of the work and names
one of those differently then scores zero, and the task is lost as "too hard"
when it was unfair. Of eight hardened tasks reviewed that a policy failed 16 of
16 times, five failed on exactly this, three with all the work done. You cannot
make that mistake, because you cannot see the vocabulary; write the checks an
agent could satisfy having read only what you can read.

Your working directory is the task package with the solution removed.

## The package

| path | what it is |
|---|---|
| `instruction.md` | what the agent is told. Nothing else is shown to it. This is the contract you verify. |
| `environment/` | the Dockerfile and every file the image ships; an agent can read all of it inside the container |
| `tests/test_state.py` or `tests/test.sh` | the current verifier. Yours replaces it, and keeps every check whose requirement remains in the instruction. |
| `run/seed_size.json` | the seed verifier's assertion count. Yours may exceed it by at most 5. |
| `run/resources.json` | the box the container opens at |
| anything else | the rest of the real package: entrypoints, fixtures, `task.toml` |

Edit the verifier in place and save replay controls under `run/verifier-probes/`.
Do not change other task files: the task is fixed, and a check that only passes because you changed
the task is a check on nothing. If the public specification leaves the required
outcome ambiguous, or explicitly requires a property the available artifacts and
environment cannot establish, write `BLOCKED: <what, precisely>` to
`run/verdict.txt` and stop. For a final-artifact task, ordinary instructions for
obtaining the result do not require proof of execution history. Require a token
log, saved workflow, provenance artifact, or evidence of a restricted method only
when the public contract explicitly makes it part of acceptance.

## What the verifier has to hold

Every check you add must be satisfiable by an agent that reads `instruction.md`
and explores the container. Concretely:

- **Every name you depend on -- a key, a label, a file name, a column -- appears,
  spelled the same, in `instruction.md` or in a file under `environment/` that the
  instruction points at.** Where the instruction leaves a name open, check the
  value instead: a report line that contains the commit's SHA, whichever label it
  is under; a field equal to the file's SHA-256, whichever key holds it.
- **Keep every existing test that still holds under the new instruction.** Remove
  a check only when its requirement was removed or relaxed in the public task;
  simplification does not permit dropping checks for retained requirements.
- **Add what the new requirement needs, and no more: at most 5 assertions over the
  seed's count** (`run/seed_size.json`). One requirement is two or three.
- Identify whether the public task requires a final artifact or a reusable
  program. Check final-artifact semantics on the supplied inputs. Check
  intermediate artifacts, execution evidence, or method restrictions only when
  the public contract requires them. Do not infer a prohibition on copying or
  hardcoding.
- For an explicitly required reusable program, use permitted inputs that expose
  semantic errors, run the submitted entry point, and compare its behavior with
  independently derived expectations. Restore changed inputs after testing. A
  faulty program remains a valid negative control after restoring the original
  inputs if it still violates the reusable-program contract.
- Never invoke `solution/solve.sh`: it is not there when the agent runs, and it is
  not there for you either. Invoke the workflow the way the instruction tells a
  user to.

Map each retained or added requirement to a check of its promised behavior or
result, naming the source of its expected answer. For supplied-data tasks, derive
expectations from the original fixture or expected values prepared before solver
execution and supplied with the grader. Never use solver-writable replacement
inputs as the authority for correctness; copying or hashing them at grading time
does not recover the original data. Input changes alone are not grounds for
rejection unless the public task forbids them. File existence,
non-empty content, success words in a log, or agreement between two solver-written
reports cannot alone establish correctness. Solver-written claims do not prove
that a required execution, measurement or tool interaction occurred.
For a transformation task, check that the transformed content corresponds to the
original input under the requested operation. Output format, dimensions, channel
count, or other metadata alone cannot establish that correspondence. Compare
content using the equivalences the public task permits; byte equality is appropriate
only for content the task requires to remain byte-for-byte unchanged. Include a
control with unrelated content that still has the required output format.

If the task requires a reusable program, run the submitted program through the
specified entry point on fresh valid inputs prepared by the grader, with expected
outputs derived before invoking the submitted program. Ensure retained
outputs cannot let a no-op program pass, and restore inputs after the check. If the
task asks only for a final artifact or state, check that result without inventing
a requirement to save a script. Accept alternative paths, formats and implementations
wherever the public task leaves them open.

Cover valid boundary cases introduced by the change, including empty results when
possible. In each case, check the changed behavior together with retained output
requirements, such as required headers or schema even when there are no records.
Check these properties before parsing or normalization discards them; an empty
parsed collection alone does not prove that the required output structure exists.

Before finishing, inspect whether a no-op, a hardcoded answer or fabricated evidence
could still pass, and whether an equivalent legal solution could fail. Choose examples
relevant to this task. In your final response, identify one concrete incorrect solution
and the check that rejects it, and one legal alternative the checks allow; distinguish
code inspection from executed tests. Keep the existing sandbox checks and job limits.
Where correctness depends on supplied data, include a replay control that replaces
the working input and leaves an answer wrong for the original fixture. Reject it
at the corresponding content check. Also execute a control that leaves a correct
deliverable despite an input change when the public task permits that change; do not add an input
immutability requirement to make the negative control fail.

## The container

```
./sandbox up             build the image and boot a container, at the task's size
./sandbox exec 'CMD'     run CMD inside it, as root; --timeout N (default 120 s)
./sandbox grade          run your verifier against the container as it stands
./sandbox reset          a fresh container from the Dockerfile
./sandbox down           delete it
```

`oracle` and `check` need the solution and are not available to you.

**Do the task yourself, through `exec`, the way the instruction describes it, and
then `grade`.** You are the agent this verifier has to be fair to: if you, reading
only the instruction, cannot reach a state your own verifier accepts, neither can
the policy, and the check that stopped you is the one to fix. Then `reset` and
`grade` the untouched workspace, which must fail. Do both before you finish; a
verifier that was never run against a real container is a guess.

Save your public-instruction-only solution as `run/verifier-probes/correct.sh`:
a shell script that completes the task from a fresh environment. Split the
changed requirement into its independently falsifiable clauses. For each clause,
save `wrong-1.sh`, `wrong-2.sh`, and so on: each script produces a nonempty,
well-formed result but violates that clause alone. Check every clause of the
changed dependency, including its validity conditions, rather than stopping
after finding one error the verifier rejects. For example, selecting the latest
valid result requires both choosing the latest result and rejecting invalid ones.
Each script must finish with exit code zero; a missing dependency, syntax error,
missing output or empty workspace is not a semantic control. Choose the mistakes
from this task, rather than adding unrelated requirements.
When the revision removes requirements, the correct control must omit the removed
work while satisfying the retained requirements; derive negative controls from
those retained requirements. Omission of removed work is not an error.

Write `run/verifier-probes/contract.json` with a nonempty `cases` array, in the
same order as the numbered scripts. Each entry has string fields `requirement`
(the public clause being checked), `wrong_behavior` (the specific mistake), and
`expected_failure` (the observable output that distinguishes it). Run every script
in a separate fresh container: the correct script must pass and every wrong script
must fail. Pass script contents as the argument to `./sandbox exec`; stdin is not
forwarded into the container. Ensure the distinguishing input affects the output:
two identities that collapse to the same node cannot test an edge weight.
When testing rejection of invalid inputs, include a case that violates the
targeted validity condition while satisfying the others. An input with multiple
defects can be rejected for the wrong reason. Record that distinguishing input in the
case's `expected_failure` field alongside the expected output difference.
If a plausible alternative implementation uses a different representation the
instruction permits, use it for the correct control instead of requiring your
preferred representation. The caller independently replays every saved script.

## Finishing

Your output is the verifier, the contract and the replay scripts. Do not print
them. Finish after the correct control passes, every semantic-error control and
untouched workspace fail, or after writing `run/verdict.txt`.
