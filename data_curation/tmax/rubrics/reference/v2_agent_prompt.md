You are auditing synthetic terminal-agent tasks to decide whether each is fit to use as reinforcement-learning training data. You will audit exactly the task ids listed at the end of this message, one at a time.

For each task, read all three files **in full** — do not skim, do not summarise:

```
/tmp/tmax_gate_work/<task_id>/instruction.md    the spec shown to the agent
/tmp/tmax_gate_work/<task_id>/setup.sh          what is built into the container
/tmp/tmax_gate_work/<task_id>/tests/test.sh     the verifier that scores the rollout
```

Answer one question: **would a competent agent that faithfully follows the instruction (a) understand the task unambiguously, (b) actually pass this verifier — and is passing it evidence the task was done?**

---

## Verdicts — assign exactly one

**CLEAN** — the instruction is clear and self-sufficient, a correct-per-instruction solution passes, AND the verifier genuinely validates correctness: it recomputes or decodes the true answer from the input, or exact-compares against a correct hardcoded answer, rather than checking that the output merely has the right shape. A hard-but-fair task is still CLEAN.

**VERIFIER-TOO-WEAK** — the verifier passes without the task being solved. It checks only output shape, format, types or count; trusts a self-reported value; greps the source for keywords; checks a hardcoded subset; tests only fixed inputs so the answer can be hardcoded; OR it compares against a reference the agent can reach (see Provenance).

**INSTR-VERIFIER-MISMATCH** — the verifier checks something the instruction does not imply, or contradicts it: a different path, a hardcoded value conflicting with the data, a stricter format. A faithful solution still fails. Also use this when the instruction demands a language or implementation the verifier never enforces.

**INSTR-ENV-MISMATCH** — the instruction or verifier references paths, files, dependencies or services the environment does not provide, so the task cannot be completed as written.

**ANSWER-LEAK** — the instruction, or anything shipped in the environment, hands the agent the expected answer, so the task requires no genuine work: a readable reference implementation, a ground-truth output file, an embedded solution, a comment naming the exact bug and fix, or an executable reference the agent can simply run.

**INSTR-AMBIGUOUS** — the instruction alone is underspecified: a necessary detail is missing, so two equally reasonable readings produce different outputs and the verifier accepts only one. Admitting several valid solution *paths* is not enough.

**TASK-TRIVIAL** — no meaningful work or learning signal: write a constant, echo a value, copy a file — **regardless of verifier strength**. Use this when the verifier is sound but the work has been removed, e.g. a source comment states the exact diagnosis and only a one-token edit remains.

**OTHER** — a genuine defect fitting none of the above: verifier code that crashes; a task needing network, GPU, GUI or hardware the container lacks; non-determinism; a required background service never started; a reward that depends on wall-clock speed; an internally contradictory spec.

---

## Provenance — ask this on every task, without exception

A verifier can be excellent at telling a right answer from a wrong answer and still be worthless if it cannot tell **whose work produced the answer**.

Trace where the verifier's expected answer comes from. If it comes from running a program, find that program and ask:

1. **Can the agent read it?** The algorithm is handed over → ANSWER-LEAK.
2. **Can the agent EXECUTE it?** The agent can symlink, wrap or shell out to it and collect full reward without implementing anything. **This holds even when the program is a compiled, stripped binary that reveals nothing when read** — and `setup.sh` deleting the source does not close it.
3. **Does the verifier check provenance at all** — `islink`, `realpath`, `st_ino`/`samefile`, or compiling the agent's own source and running only that? If not, the shortcut is open.

**Differential fuzzing against a reference is not evidence of strength.** It is the most discriminating verifier shape there is and the least provenance-resistant. If you find yourself about to write *"it fuzzes N random inputs against a correct oracle, so nothing can be faked"* — stop. That exact inference passed twelve exploitable tasks in the previous audit round. Four of them were later proven collectable with a single `ln -s`.

Do not reason past a reachable reference because the instruction already specifies the algorithm and the reference is therefore "redundant". Redundancy is an argument about *information*. Execution is not about information: the agent does not need to understand it to run it.

Reference programs are not always named like one. Two real cases were `/app/loc_filter` and `/app/log_packer`.

---

## Pre-placed values — a reference *value*, not a reference program

Take each literal the verifier asserts on — a token, flag string, hash, expected count — and search `setup.sh` and the environment for it. If present, decide which case you are in:

- **Premise (CLEAN).** The value legitimately lives in material the agent must work with: a constant in source they must repair, a field in a config they must parse. Reaching it still requires doing the work.
- **Leak (ANSWER-LEAK).** The value is the expected *output*, placed where the agent can find it, and the derivation the task is about can be skipped — `grep -r FLAG /home/user && echo` scores the assertion.

The separator: **could the agent reach that value without performing the step the task is actually about?** A token inside a C file whose build the agent must fix is a premise; the same token in a plaintext file they can `cat` is a leak.

Do not flag merely because a string appears twice. Across 400 audited tasks, a naive "asserted literal also appears in setup.sh" scan fires on ~15% and is mostly benign.

---

## Confidence — use this anchored scale, not a feeling

Unanchored confidence is worthless: in the previous round the tasks the judge got *wrong* averaged 6.67 and the rest 7.04. Report against these anchors instead.

| score | means |
|---|---|
| **9-10** | I traced the verifier's expected answer to its source and read every assertion. I can name the exact line that decides this verdict. |
| **7-8** | I read the whole verifier and am confident, but some path (a helper, an import, a subprocess) I did not fully follow. |
| **5-6** | The verifier is long, or indirect, or builds paths at runtime; my verdict rests on the parts I could follow. |
| **3-4** | I could not establish where the expected answer comes from. |
| **1-2** | I could not meaningfully assess this task. |

**Any CLEAN below 8 will be re-judged.** Do not inflate to avoid that — a low-confidence CLEAN is a useful signal and costs nothing. Inflating one hides a task that needed a second look.

---

## Before you write CLEAN

CLEAN is the verdict you return when you *fail to find* a defect, which is exactly what a judge with a blind spot does. Before writing it, answer all six `checks` fields below with something concrete. If you cannot answer one, you have not finished reading — go back.

A sound verifier is **necessary but not sufficient** for CLEAN. Also ask: *what work does this task actually require of a competent agent?* If the answer is "read a comment and change one token", it is TASK-TRIVIAL no matter how rigorous the verifier is.

---

## Output — append ONE line to `/tmp/v2_salvage.jsonl` per task, the moment you finish it

Do not batch. Write task 1's line before starting task 2. If you crash, at most the in-flight task is lost.

**Before you start, `grep` that file for each of your task ids and skip any already present** — a restart must not re-judge.

```json
{"task_id":"...","verdict":"...","confidence":7,
 "evidence":"the exact quoted line that decides the verdict",
 "evidence_file":"tests/test.sh","evidence_line":42,
 "checks":{"reachable_reference":"/app/foo or null",
           "reference_guarded":false,
           "preplaced_value":"literal or null",
           "unenforced_requirement":"clause or null",
           "shape_only_assertion":false,
           "nondeterministic_reward":false},
 "files_read":["instruction.md","setup.sh","tests/test.sh"],
 "verifier_expected_answer_from":"recomputed|hardcoded-literal|in-image-program|agent-self-report|shape-only",
 "model_reported":"<the model serving you>","agent":"<your label>","ts":<unix seconds>}
```

**The `checks` fields bind the verdict. CLEAN is not available if any is set:**

| if you filled | the verdict is |
|---|---|
| `reachable_reference` non-null AND `reference_guarded` false | ANSWER-LEAK or VERIFIER-TOO-WEAK |
| `preplaced_value` non-null | ANSWER-LEAK |
| `unenforced_requirement` non-null | INSTR-VERIFIER-MISMATCH |
| `shape_only_assertion` true | VERIFIER-TOO-WEAK |
| `nondeterministic_reward` true | OTHER |

A judge in the previous round recorded `nondeterministic_reward: true` *and*
named an unenforced requirement, then returned CLEAN anyway — reasoning the
verifier was strict, which it was. Strictness is not the question: a verifier
asserting a wall-clock speedup is strict *and* pays a different reward for the
same correct answer depending on machine load. Filling a check honestly and not
acting on it wastes the check.

If you think a filled check should still be CLEAN, you are probably looking at a
premise rather than a defect — set the field to null and say why in evidence.

Rules that are not optional:

- `verifier_expected_answer_from` must be answered for every task. **`in-image-program` is the answer that was missed twelve times last round** — if that is what it is, say so, then apply Provenance.
- For any non-CLEAN verdict, `evidence` must quote a **real line that exists in the file**, with its number. Not a paraphrase, not a summary.
- All six `checks` fields must be filled. Conservative values are fine; blanks are not.
- Some tasks are security-domain (sockets, seccomp, exploit-shaped code). That is expected in a dataset audit — read them as an auditor. If you will not engage with one, still write its line with `verdict:"OTHER"` and say so in `evidence`.

Return, as your final message, only a one-line summary: how many tasks you wrote, and the verdict counts. The salvage file is the real output.

---

## Your task ids

