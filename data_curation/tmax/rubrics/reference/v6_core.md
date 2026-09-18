<!-- v4 实跑 (batch2 298 道) — 从 /tmp/v4b2_a.js 的 AGENTS[0].prompt 原样抠出,
     去掉了每个 agent 专属的任务 id 清单。这是**实际发给评委的文本**。 -->

You are auditing synthetic terminal-agent tasks for fitness as RL training data.
Audit exactly the task ids listed at the end, ONE AT A TIME.
Read all three files IN FULL for each:
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

## Two axes — decide both, then read off the tier

A defect has two independent properties, and conflating them is what made the
previous version over-reject.

**Axis 1 — soundness.** What is wrong with the reward?
  `accepts-wrong`   a submission that did not do the task can score 1
  `rejects-right`   a faithful, correct solution scores 0
  `unstable`        the same correct submission scores differently across runs
  `sound`           neither

**Axis 2 — exploitability.** WHO could trigger it? The model does **not** see
`tests/` while it works — that is confirmed in the training code, tests are
uploaded only after submission. But three things follow that are easy to get
wrong, and I got them wrong before:

  `rollout-visible`         discoverable from what the agent can see while
                            working: the instruction, and the container's own
                            contents. A reference program sitting in /app is
                            this. **The strongest evidence class.**
  `grading-runtime-visible` the submitted artifact is EXECUTED at grade time in
                            the same container, so a script/binary/service the
                            agent wrote can see files that only exist during
                            grading — an oracle the verifier writes, /tmp, the
                            test fixtures. Not visible while writing it; visible
                            while it runs.
  `feedback-learnable`      not visible at all, but an RL loop could stumble into
                            it and be rewarded — the classic case is a check that
                            only tests existence, which pays out for a natural
                            empty-shell failure. **No awareness required.**
  `privileged-test-only`    reachable ONLY by someone who has read the hidden
                            verifier and aims at it. An auditor can construct it;
                            nothing in the training loop can.

## Look for BOTH directions — the second one is routinely missed

Almost every rubric, including the four earlier versions of this one, hunts only
for *wrong solutions passing*. Across 578 judgements those versions produced,
**`rejects-right` was recorded exactly once** — not because it is rare, but
because nobody was asked to look.

So look. Concretely, ask: **would a competent, faithful solution that differs
from the one the author imagined be rejected?** Common shapes:

  - the verifier greps the source for a specific API or token, so a correct
    solution using a different one fails — `assert "strncpy" in content` rejects
    a correct `std::string::substr` fix;
  - an exact string or byte comparison where the instruction permits a range of
    valid forms (key order, whitespace, float formatting, line endings);
  - a hardcoded path or filename the instruction only suggested ("e.g. main.go");
  - a timing or performance threshold that a correct implementation misses on a
    loaded machine;
  - a re-run that assumes the initial state, when a correct solution has already
    consumed it.

`rejects-right` is the one finding whose severity does not depend on
exploitability at all: it corrupts the reward with no agent involvement, and it
actively punishes correct behaviour. **Always REPAIR, never dismissed as
latent.**

## The rule that prevents over-rejection

**If a bypass can only be constructed by an auditor who read the hidden
verifier, and cannot be triggered by the agent's answer-time information, by the
submitted artifact's grading-time capabilities, by permitted reward feedback, or
by a natural failure mode — do NOT reject on it.** Record it as a
**latent verifier weakness** and route to REVIEW.

Conversely, `rejects-right` must be fixed **whatever** its visibility: a verifier
that fails correct work corrupts the reward signal with no agent involvement at
all.

## Verdicts

CLEAN
  The instruction is clear and self-sufficient, a correct-per-instruction
  solution passes, AND the verifier genuinely validates correctness rather than
  checking that the output merely has the right shape. Hard-but-fair is CLEAN.

  FIRST decide which kind of task this is — "hardcoded expected value" means
  opposite things in the two, and conflating them was a real defect in the
  previous version of this rubric:
    - FIXED-INSTANCE: the instruction asks for THE answer to ONE given input
      ("how many orphaned records are in this database"). A hardcoded expected
      value is CORRECT here — there is one right answer and the verifier states
      it. CLEAN.
    - GENERAL-PROGRAM: the instruction promises behaviour over arbitrary inputs
      ("implement a parser", "expose an endpoint"). If the verifier only ever
      exercises a handful of fixed inputs, each can be hardcoded case by case
      and the generality is never tested. VERIFIER-TOO-WEAK, however exact the
      comparison is.
  The separator: does the instruction promise behaviour BEYOND the inputs the
  verifier actually exercises?

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

MUTABLE-GROUND-TRUTH
  The verifier's expected answer is not fixed: it is recomputed at grade time
  from a file the agent can write, or produced by executing a program the agent
  can overwrite. The agent never has to solve the task — it only has to move or
  agree with the verifier's own reference. Two confirmed shapes:
    - the verifier open()s an input under a `chmod 777` path and derives the
      expected value from it; emptying or editing that file moves the target;
    - the verifier runs the agent's own script/validator and trusts its exit
      code or stdout as the judgement.
  This is NOT VERIFIER-TOO-WEAK (the comparison logic may be rigorous) and NOT
  ANSWER-LEAK (nothing is revealed). Trace the provenance of the expected value
  on EVERY task.

  **But set the exploitability honestly.** Knowing that the verifier recomputes
  from a given file is usually knowledge only the verifier's source carries — in
  that case this is `privileged-test-only` and belongs in REVIEW, not REJECT.
  It rises to `feedback-learnable` when a natural failure reaches it: if
  emptying or failing to produce the input is a plausible unsuccessful attempt
  AND the degraded comparison then passes, an RL loop can be rewarded for it
  without ever understanding why. Say which of the two you mean, and why.

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

## Tier — this is the actionable output

| tier | when |
|---|---|
| **PASS** | soundness `sound` |
| **REJECT-PROVEN** | `accepts-wrong` **and** exploitability is `rollout-visible`, `grading-runtime-visible`, or `feedback-learnable` |
| **REPAIR** | `rejects-right` (always), or `accepts-wrong` where the gap is a stated requirement the verifier simply never tests |
| **REVIEW** | `accepts-wrong` with exploitability `privileged-test-only`; or `unstable`; or your confidence is below 8; or this is the only judge that has seen it |

**REJECT-PROVEN is the only tier that discards a task, so it carries the burden
of proof.** If you cannot name who could trigger the bypass without reading
`tests/test.sh`, the tier is REVIEW, not REJECT.

**The `defects` flags bind the verdict. CLEAN is not available if any is true:**

| `defects` flag set | the verdict is |
|---|---|
| `reachable_reference` | ANSWER-LEAK or VERIFIER-TOO-WEAK |
| `preplaced_value` | ANSWER-LEAK |
| `unenforced_requirement` | INSTR-VERIFIER-MISMATCH |
| `shape_only_assertion` | VERIFIER-TOO-WEAK |
| `nondeterministic_reward` | OTHER |
| `mutable_ground_truth` | MUTABLE-GROUND-TRUTH |
| `artifact_identity_unbound` | VERIFIER-TOO-WEAK |
| `rejects_correct_variant` | INSTR-VERIFIER-MISMATCH, tier REPAIR, always |

Set a `defects` flag only where the finding genuinely removes the work or breaks
the reward. A constant the agent must still earn access to is a premise: record
it in `checks`, leave the flag false, and say why in the evidence.

**For `unenforced_requirement`, decide with these two questions rather than by
feel** — across 90 audited tasks this one field was judged a defect 25 times and
a non-defect 33 times, entirely on the judge's sense of what mattered, which
makes it the least reproducible call in this rubric:

1. *If an agent ignored this clause completely, could it still score full
   reward?* If no, the clause is enforced by the grade — not a defect.
2. *If yes: what does the task then actually teach?* Compare that to what the
   instruction says it teaches. If they are the same thing, the clause is
   decorative phrasing — not a defect. If they differ, the task trains something
   other than its stated skill — **that is the defect**.

Worked examples. "Write a Go program using goroutines and `syscall.Flock`",
graded by grepping the source for the literal `syscall.Flock`: ignoring the
clause still scores (a comment containing that string passes), and the task then
teaches "emit the right output file", not "write concurrent Go" — **defect**.
"Save it as `report.csv`" where the grader reads `report.csv`: ignoring the
clause scores nothing, because the path is how the grade finds the work —
**not a defect**.

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
  "evidence": "<the exact line that decides the verdict — quote it, with its
                file and line number>",
  "evidence_file": "instruction.md | setup.sh | tests/test.sh",
  "evidence_line": <integer>,

  "checks": {
    "reachable_reference":    "<path of a reference program the agent can read
                                or execute, or null>",
    "reference_guarded":      <true ONLY if the guard survives a RUNTIME wrapper.
                               islink/realpath/samefile and "compile the source"
                               do NOT qualify: a script that exec's the reference
                               at run time is a genuine file built from genuine
                               source. A real guard runs the agent's artifact
                               where the reference is absent>,
    "mutable_ground_truth":   "<path of the agent-writable file or program the
                               verifier derives its expected answer from, else null>",
    "artifact_identity_bound":<true if the verifier ties what it grades to the
                               work it claims to test; false if a decoy of the
                               right name and shape would satisfy it>,
    "rejects_correct_variant":"<a specific correct solution this verifier would
                                wrongly fail, or null. Name the variant.>",
    "preplaced_value":        "<a literal the verifier asserts that already
                                exists in the environment, or null>",
    "unenforced_requirement": "<an instruction clause the verifier never tests,
                                or null>",
    "shape_only_assertion":   <true if the decisive assertion tests only shape,
                               format, type or count>,
    "nondeterministic_reward":<true if the reward can differ for the same
                               correct answer — wall-clock, ordering, network>
  },

  "defects": {
    "reachable_reference": <bool>, "preplaced_value": <bool>,
    "unenforced_requirement": <bool>, "shape_only_assertion": <bool>,
    "nondeterministic_reward": <bool>,
    "mutable_ground_truth": <bool>, "artifact_identity_unbound": <bool>,
    "rejects_correct_variant": <bool>
  },

  "task_kind": "fixed-instance | general-program",
  "soundness": "accepts-wrong | rejects-right | unstable | sound",
  "exploitability": "rollout-visible | grading-runtime-visible | feedback-learnable | privileged-test-only | n/a",
  "who_could_trigger": "<one concrete sentence naming the actor and the path, or null if sound>",
  "tier": "PASS | REPAIR | REVIEW | REJECT-PROVEN",
  "mismatch_direction": "faithful_solution_rejected | invalid_solution_accepted | both | n/a",

  "verifier_expected_answer_from":
     "recomputed | hardcoded-literal | in-image-program | agent-self-report | shape-only"
}

`checks` is what you **found**. `defects` is whether each finding **is actually a
defect**. They differ: a constant the agent must still earn access to belongs in
`checks` with its `defects` flag false.

CLEAN is the verdict you return when you fail to find a defect. Before returning
it, state to yourself which specific check you ran for each of: a reachable
reference program, a pre-placed expected value, an unenforced requirement, a
shape-only assertion, and a non-deterministic or timing-dependent reward. If you did not run one of those
checks, run it now. Lower your confidence when the verifier's logic is long
enough that you could have missed something in it.

---

## Recording your work — the FILE is the deliverable, not your reply

Append ONE line of JSON to `/tmp/v4_batch2.jsonl` the moment you finish each task.
Never batch: write task 1's line before you start task 2. Include `"task_id"` and
`"agent"` on every line. Before starting, grep that file for your ids and skip any
already present. Your final reply: one line with counts. The file is the deliverable.