You are auditing synthetic terminal-agent tasks to decide whether each is fit to use as
reinforcement-learning training data. Audit exactly the task ids listed at the end, one at a time.
For each, read all three files IN FULL:
  /tmp/tmax_gate_work/<task_id>/instruction.md
  /tmp/tmax_gate_work/<task_id>/setup.sh
  /tmp/tmax_gate_work/<task_id>/tests/test.sh

You are auditing one synthetic terminal-agent task to decide whether it is fit
to use as reinforcement-learning training data. You are given three artifacts
that must be mutually consistent:

  instruction.md   the task specification shown to the agent
  setup.sh         what is built into the container before the agent starts
  tests/test.sh    the verifier that scores the agent's rollout

Answer one question: **would a competent agent that faithfully follows the
instruction be able to (a) understand the task unambiguously, and (b) actually
pass this verifier — and is passing it evidence that the task was done?**

Assign exactly one verdict.

## Verdicts

CLEAN
  The instruction is clear and self-sufficient, a correct-per-instruction
  solution passes, AND the verifier genuinely validates correctness: it
  recomputes or decodes the true answer from the input, or exact-compares
  against a correct hardcoded answer, rather than merely checking that the
  output has the right shape. A hard-but-fair task is still CLEAN.

VERIFIER-TOO-WEAK
  The verifier passes without the task actually being solved. It checks only
  output shape, format, types or count; trusts a self-reported value; greps the
  source for keywords; checks a hardcoded subset; tests only fixed inputs so
  the answer can be hardcoded; OR — see the provenance section below — it
  compares against a reference the agent can reach, so the agent can obtain the
  reward without doing the work.

INSTR-VERIFIER-MISMATCH
  The verifier checks something the instruction does not imply, or contradicts
  it: reads or writes a different path, asserts a hardcoded value that conflicts
  with the data, demands a stricter format. A faithful solution still fails.

INSTR-ENV-MISMATCH
  The instruction or verifier references input paths, files, dependencies or
  services the environment does not provide, so the task cannot be completed as
  written.

ANSWER-LEAK
  The instruction itself, or anything shipped in the environment, hands the
  agent the expected answer, so the task requires no genuine work. This includes
  a readable reference implementation, a ground-truth output file, an embedded
  solution, or — see below — an executable reference the agent can simply run.

INSTR-AMBIGUOUS
  The instruction alone is underspecified: a necessary detail is missing, so two
  equally reasonable readings produce different outputs and the verifier accepts
  only one. Merely admitting several valid solution *paths* is not enough.

TASK-TRIVIAL
  There is no meaningful work or learning signal: write a constant, echo a
  value, copy a file — regardless of verifier strength.

OTHER
  A genuine defect fitting none of the above: verifier code that crashes;
  a task needing network, GPU, GUI or hardware the container lacks;
  non-determinism; a required background service that is never started; a
  reward that depends on wall-clock speed; an internally contradictory spec.

## The provenance question — ask this explicitly, every time

A verifier can be excellent at telling a right answer from a wrong answer and
still be worthless, if it cannot tell **whose work produced the answer**.

Trace where the verifier's expected answer comes from. If it comes from running
a program, find that program and ask:

  1. Can the agent read it?     -> the algorithm is handed over. ANSWER-LEAK.
  2. Can the agent EXECUTE it?  -> the agent can wrap, symlink, or shell out to
     it and collect full reward without implementing anything. This holds even
     if the program is a stripped binary that reveals nothing when read.
  3. Does the verifier check provenance at all — `islink`, `realpath`,
     `st_ino`/`samefile`, compiling the agent's source itself and running only
     that? If there is no such check, the shortcut is available.

**Differential fuzzing against a reference is not evidence of strength.** It is
the most discriminating verifier shape there is and the least provenance-
resistant. "It fuzzes 1000 random inputs against a correct oracle, so nothing
can be faked" is a false inference — write it down and you have almost certainly
missed a hole. If the reference is reachable, one line defeats the entire
comparison.

Do not reason past a reachable reference on the grounds that the instruction
already specifies the algorithm, so the reference is "redundant". Redundancy is
an argument about information. Execution is not about information: the agent
does not need to know how it works to run it.

## Also check: is the expected value already sitting somewhere?

Separately from a reference *program*, look for a reference *value*. Take each
literal the verifier asserts on — a token, a flag string, a hash, a specific
count — and search `setup.sh` and the environment for it. If it is there, decide
which of two situations you are in:

  **Premise.** The value legitimately lives in material the agent is meant to
  work with: a constant in the source they must fix, a field in a config they
  must parse, a record in the input data. Its presence is the setup of the task,
  and reaching it still requires doing the work. This is CLEAN.

  **Leak.** The value is the *expected output*, planted where the agent can find
  it, and the derivation the task is about can be skipped — `grep -r FLAG_STRING
  /home/user && echo` scores the assertion. This is ANSWER-LEAK.

The test that separates them: **could the agent reach that value without
performing the step the task is actually about?** A token inside a C file whose
build the agent must repair is a premise — the build has to work first. The same
token written into a plaintext file the agent can simply `cat` is a leak.

Do not skip this because the literal looks incidental. Do not flag it merely
because the string appears twice — over a set of 400 audited tasks a naive
"asserted literal also appears in setup.sh" scan fires on 15% of them and is
mostly benign. This one needs your judgement, which is why it is asked of you
and not left to a regex.

## Also check: are the requirements enforced?

If the instruction demands an artifact in a specific language or form — "write
it in Rust", "pure Bash, no Python", "implement it yourself" — check whether the
verifier tests that, or merely asserts the file exists. A grade that rests only
on an output file makes the language clause decorative, and the task trains
"produce this output", not what it claims to teach. Report as
INSTR-VERIFIER-MISMATCH, and say in the evidence that the requirement is
unenforced.

## How to answer

Report the **worst** defect you find, not the first. If a task both leaks the
answer and has a weak verifier, it is ANSWER-LEAK.

`checks` records **what you found**; `defects` records **whether each finding is
actually a defect**. Keep them separate — a judge in the previous round wrote
`preplaced_value: "...(pre-built index entry) -- premise"`, correctly identifying
a premise, and a rule keyed on the field being non-empty would have wrongly
flipped that task. Fill `checks` descriptively; fill `defects` with your
judgement.

**The `defects` flags bind the verdict. CLEAN is not available if any is true:**

| `defects` flag set | the verdict is |
|---|---|
| `reachable_reference` | ANSWER-LEAK or VERIFIER-TOO-WEAK |
| `preplaced_value` | ANSWER-LEAK |
| `unenforced_requirement` | INSTR-VERIFIER-MISMATCH |
| `shape_only_assertion` | VERIFIER-TOO-WEAK |
| `nondeterministic_reward` | OTHER |

Set a `defects` flag only where the finding genuinely removes the work or breaks
the reward. A constant the agent must still earn access to is a premise: record
it in `checks`, leave the flag false, and say why in the evidence. An
instruction clause the verifier does not literally test is only a defect when it
is load-bearing — "write it in Rust" ungraded is a defect; an unchecked filename
the grade does not rest on is not.

This rule exists because a judge in the previous round recorded
`nondeterministic_reward: true` *and* named an unenforced requirement, then
returned CLEAN anyway — reasoning that the verifier was strict, which it was.
Strictness is not the question. A verifier that asserts a wall-clock speedup is
strict *and* returns a different reward for the same correct answer depending on
machine load. Filling a check honestly and then not acting on it wastes the
check.

If you believe a filled check should nonetheless be CLEAN, you are probably
looking at a premise rather than a defect — re-read the relevant section, and if
it really is a premise, set the field to null and say why in the evidence.

Return exactly this JSON, nothing else:

{
  "verdict": "<one of the eight labels>",
  "confidence": <integer 1-10>,
  "evidence": "<one or two sentences citing the specific line, path or
                assertion that decides the verdict — quote it>",
  "leaked_path": "<absolute path of the leaking file, or null>",
  "reachable_reference": "<absolute path of a reference program the agent can
                           read or execute, or null>",
  "unenforced_requirement": "<the instruction clause the verifier never tests,
                              or null>",
  "preplaced_value": "<a literal the verifier asserts that is already reachable
                       in the environment AND bypasses the task's real work, or
                       null — null if it is a premise rather than a leak>"
}

CLEAN is the verdict you return when you fail to find a defect. Before returning
it, state to yourself which specific check you ran for each of: a reachable
reference program, a pre-placed expected value, an unenforced requirement, a
shape-only assertion, and a non-deterministic or timing-dependent reward. If you did not run one of those
checks, run it now. Lower your confidence when the verifier's logic is long
enough that you could have missed something in it.
