# The audit prompt

The instrument used to judge whether a synthetic terminal-agent task is fit to
train on — and the corrected version, after an adversarial review found what the
first one missed.

Reproduced in full so results here can be checked, and reused. If you are
auditing an environment pool of your own, **start from v2 below**; v1 is
included only because the difference between them is the finding.

---

## What went wrong with v1

v1 implemented the RIVER rubric (arXiv 2608.22631, App. C) faithfully. It still
passed 12 tasks whose reward can be collected with `ln -s`.

The tasks it missed all share a shape: the verifier computes the expected answer
by **running a reference program that is left in the container**, then compares
the agent's output against it, with nothing checking that the agent's deliverable
is its own work.

Here is what the judge wrote about them, in its own words:

> *"so nothing can be faked"* · *"so only true black-box equivalence passes"* ·
> *"so only a true Welford rewrite matches"* · *"the /opt oracle source is out of
> scope and redundant with the spec"*

The judge was asked whether the verifier can tell a **right answer from a wrong
answer**. It answered correctly: differential fuzzing over a thousand random
inputs is about as discriminating as a verifier gets. Nobody asked whether the
verifier can tell **the agent's work from the reference's work**. It cannot, the
moment the reference is reachable.

Two structural reasons v1 could not catch this:

1. **`VERIFIER-TOO-WEAK` is defined by enumeration** — shape checks, self-reported
   values, keyword greps, hardcoded subsets, fixed inputs. A differential fuzz
   verifier fails *every* listed criterion, i.e. scores maximally strong. The
   list has no entry for "the reference implementation is invocable", so the more
   carefully a judge applies the rubric, the more confidently it says CLEAN.
2. **`ANSWER-LEAK` is defined around *reading*** — a file that "reveals",
   "embeds", "lists" the answer. A reference that is compiled and stripped
   reveals nothing and still hands over the answer when executed.

The fix is not more information. The judge already saw `setup.sh` and named the
oracle out loud. The fix is a question it was never asked.

---

## v2 — the prompt

Give the judge three files verbatim: `instruction.md`, `setup.sh`, and
`tests/test.sh`. Do not summarise them.

````
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
    "reference_guarded":      <true if the verifier checks islink/realpath/
                               st_ino/samefile, or compiles the agent's source>,
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
    "nondeterministic_reward": <bool>
  },

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
````

---

## How to run it

Two things matter as much as the prompt.

**Two independent judges, and take the worst reading.** In this build the
second judge disagreed with **21%** of the first judge's CLEAN verdicts
(65 of 310). Worse, 12 tasks were called `ANSWER-LEAK` by one pass and shipped
on another pass's CLEAN — the rubric caught them and the *ship rule* threw the
catch away. A task is fit only if **no** pass found a defect. One judge is not
an audit; it is an opinion, and its CLEAN is the least durable thing it says.

**Judge with the artifacts, verify with behaviour.** A rubric audit is a
reading, and readings miss things this one demonstrably missed. Pair it with
tests that cannot be talked around:

- *empty submission* — grade an untouched container; reward must be 0
- *solve gate* — a strong agent must be able to pass it at all
- *mutation* — corrupt the answer's content while preserving its shape, re-grade,
  and require reward 0 (see PROCESS.md §7b, including this test's own blind spots)
- *provenance* — for any task with a reachable reference, try the shortcut:
  symlink the deliverable to the reference and grade. If that scores 1, the task
  is broken no matter what any judge said.

The last one is the check that would have caught all 12 leaks in this dataset
without any judge at all.
