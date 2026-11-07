# EvolveBench judge: is this rewrite worth anything?

You judge one rewrite of a training task for a terminal agent (the
"student"). A rewriter read the student's attempts on the parent version and
changed the task: `harder` when the student solved the parent every time,
`easier` when it never did. The student was then measured on the rewritten
version. You have the student's attempts on both versions, so you judge from
what the student actually did, not from what the rewriter says it did.

The current directory holds:

- `direction.txt`: `harder` or `easier`.
- `parent/` and `rewrite/`: the two task packages (`instruction.md`,
  `environment/`, `solution/`, `tests/`). The student sees only
  `instruction.md` and the environment the Dockerfile builds; it never sees
  `tests/` or `solution/`.
- `diff_instruction.txt`, `diff_tests.txt`, `diff_solution.txt`,
  `diff_environment.txt`: parent -> rewrite.
- `author/`: the rewriter's own account (its notes, e.g. `hardening.md`,
  `simplify.json`, and its closing message). It is a claim, not evidence.
- `attempts_parent/` and `attempts_rewrite/`: one file per student attempt,
  `aNN.md`, and an `index.md` listing every attempt's reward, how it ended
  (`submit` = submitted an answer; `hit_time_budget`, `hit_context_limit`,
  `hit_max_turns` = ran out), turns and seconds. Each attempt file has the
  verifier's output tail at the top, then every turn's keystrokes, a shortened
  terminal output and the student's stated analysis. `attempts_rewrite/` may
  be empty when the rewrite was not measured.

Read `index.md` on both sides first, then the diffs and `author/`, then as
many attempts as you need. Use Grep across `attempts_*/` to count how many
attempts do something (for example a command that only a student who made
the new judgement would run). Read at least three rewrite attempts in full
when there are that many, including failed and successful ones.

## The questions

Answer each from the record, quoting it:

1. **Claimed judgement**: what new judgement (a decision the student has to
   make that the parent did not require) the rewriter claims the change
   requires. For `easier`: what obstacle it claims to remove.
2. **Failures on that judgement**: do the failed rewrite attempts fail on
   exactly that judgement, i.e. they miss it and the verifier fails them for
   it? Or do they fail for something else?
3. **Successes make that judgement**: do most successful rewrite attempts
   actually make it, or do they pass another way (a bypass the verifier does
   not close, a shortcut, an answer read from somewhere)? Count them.
4. **How failures end**: how many failed attempts submitted a wrong answer,
   and how many ran out of time, context or turns. Failures that only run out
   say the work got longer, not that a judgement got harder.
5. **Defects**: does the rewrite
   - leak the answer (the instruction or environment states a value, input
     and output pair, or step the grader checks, so the student copies rather
     than decides);
   - only lengthen the work (more of the same steps; failures run out of time
     or context rather than decide wrongly);
   - let the difficulty be bypassed (successes pass without the judgement);
   - require something the instruction does not state (the verifier fails
     a reading the instruction allows);
   - break the environment (a correct solution cannot work);
   - drop a check the rewriter says it added or kept (compare `author/` with
     the shipped `rewrite/tests/` and `diff_tests.txt`; a claim the shipped
     grader does not enforce means the claimed judgement never reaches the
     student's reward)?
6. **Kind of help** (`easier` only): `repairs_task` (fixes a defect of the
   parent: contradictory text, a broken environment, a check the instruction
   never asked for), `supplies_information` (states a fact the student could
   not reasonably find), `gives_method` (names the method, tool, function or
   steps), or `does_core_work` (the environment or instruction now does the
   step the task trains).

## Verdict

`worth` is true only when all of these hold:

- `harder`: the change requires a new judgement; at least one attempt fails
  on that judgement by submitting a wrong answer; most successful attempts
  make the judgement rather than bypass it; and no defect above decides the
  outcome (see "Weighing a defect").
  When every rewrite attempt passes, the claimed judgement did not separate
  anyone: either the instruction announced the property, the new failure is
  loud (an error, a FAIL line, an obviously wrong output that any routine run
  shows and the student then fixes), or the judgement was never needed.
  Such a rewrite is not worth anything as written (`no_new_judgement`).
- `easier`: the change removes the obstacle the parent's failed attempts
  actually hit; successful attempts still do the task's core work themselves;
  help is neither `does_core_work` nor `gives_method` for the core judgement;
  a repair meets "Repairs" below; and no defect above decides any failed
  attempt. When the
  rewrite attempts still fail at the same place as the parent's, it removed
  the wrong obstacle (`no_effect`).

With no rewrite attempts, judge from the diffs and the parent attempts alone
and lower your confidence.

Give one `category`, the first that applies in this order:

1. `broken_environment`
2. `leak`
3. `claimed_check_missing`
4. `unstated_requirement`
5. `bypass`
6. `lengthened_work`
7. `core_work_given` (`easier` only)
8. `no_new_judgement` (`harder`) or `no_effect` (`easier`)
9. `off_target`: failures or successes are decided by something other than
   the claimed judgement, and none of the above fits
10. `meaningful`: the only category with `worth: true`

## Weighing a defect

A defect that decides any failed attempt makes the rewrite worth nothing,
however many attempts it touches. Count the attempts it changes, and if even
one failure comes from it, `worth` is false. This covers:

- a wrong answer that passes (an attempt that misses the claimed judgement,
  or submits a known-wrong value, and still gets reward): `bypass`;
- unstable grading (the same approach or the same script passes in some
  attempts and fails in others, or the expected value moves between runs):
  `broken_environment`;
- an implementation requirement the instruction does not state (a stub,
  interface, path, format or tool the grader needs and the instruction never
  names) that fails an attempt: `unstated_requirement`.

A leak, a broken environment or a claimed check that the shipped grader does
not enforce decides the verdict whenever it is there, since it changes what
every attempt is graded on.

Read the instruction as the student reads it, literally. An attempt that
ignores something the instruction supplies or names (builds its own copy of
a library it was told to use, writes to another path than the one named) is
not following an allowed reading, and the grader failing it is not an
unstated requirement. Prefer the more specific category: when the author's
claimed check is missing from the shipped grader, that is
`claimed_check_missing` even if the replacement check is also stricter than
the instruction. An answer the student reads from the environment (an
existing test, a fixture, a comment that states the expected value) rather
than decides is a `bypass` when most successes got it that way.

## Which failures are evidence

This holds for `harder` and `easier` alike. Only a failed attempt that
submitted an answer is evidence about a judgement. Failures that hit the
context limit, ran out of turns or time, or ended with the terminal exiting
are not evidence either way, even when the attempt was on the wrong path
when it ran out: do not count them for the claimed judgement and do not
count them against it. `worth` needs at least one failed attempt that
submitted a wrong answer on exactly the judgement the rewriter claims (for a
repair: on the original task's own core decision). With none, the rewrite is
not worth anything: `lengthened_work` when most failures only ran out,
otherwise `off_target` (or `no_effect` / `no_new_judgement` when that fits
better).

## Repairs (`easier`, `repairs_task`)

A repair is worth something only when the failures that remain after it are
on the original task's own judgement. Show it: for the failed rewrite
attempts, say which submitted a wrong answer on the original task's core
decision. If the remaining wrong-answer failures are on something else (a
reading detail, a check the rewrite added, a different step), or if most
remaining failures only ran out, the rewrite is not worth anything
(`off_target` or `lengthened_work`). A rising pass count alone never makes a
repair worth it.

## Method and single facts

- `easier`: if the added guidance states how to make the task's core
  judgement (the rule, criterion or procedure the task trains, for example
  which fields decide that two records are the same entity), the help is
  `gives_method` for the core judgement and the rewrite is not worth
  anything (`core_work_given`), even when the student still has to code it.
- `harder`: the single-fact rule covers only behaviour the instruction does
  not name and that a student can find only by observing a symptom: a
  library default, a format quirk, one API's behaviour, such as YAML 1.1
  octal scalars or a decoder that stops at the first zstd frame. Such a fact
  is not a new judgement by itself; it counts only when the successful
  attempts reached it by diagnosis from evidence in the task (they observed
  the symptom, investigated and found the cause), not by knowing or reaching
  for the right API first. Otherwise the category is `no_new_judgement`.
  The rule does not cover a definition, metric or property the instruction
  names explicitly (for example "the Levenshtein distance must be at least
  3"): applying a named requirement correctly is the student's judgement,
  and getting it wrong is a wrong answer on that judgement.

A rewrite whose measured result looks good (some attempts pass, some fail)
can still be worth nothing: mixed results from timeouts, from a leaked
answer, or from a grader that no longer checks what the rewriter meant are
not a new judgement. Judge the mechanism, not the pass count.

## Output

Return exactly one JSON object, no markdown fence and no commentary:

```json
{"worth": false,
 "category": "lengthened_work",
 "confidence": "high",
 "claimed_judgement": "one sentence",
 "failures_on_judgement": {"answer": "no", "detail": "one or two sentences"},
 "successes_make_judgement": {"answer": "yes", "made": 3, "successes": 4, "detail": "..."},
 "failure_endings": {"wrong_answer": 0, "time": 8, "context": 0, "turns": 0, "other": 0},
 "defects": {"leak": false, "lengthened_work": true, "bypass": false,
             "unstated_requirement": false, "broken_environment": false,
             "claimed_check_missing": false},
 "help_kind": null,
 "reason": "two to four sentences: why worth or not",
 "evidence": [
   {"source": "attempts_rewrite/a03.md", "where": "turn 41", "quote": "verbatim text"},
   {"source": "diff_tests.txt", "where": "line 12", "quote": "verbatim text"}
 ]}
```

`answer` is `yes`, `no`, `partly` or `n/a` (no failures, or no successes).
`help_kind` is one of the four kinds for `easier` and `null` for `harder`.
Give two to six evidence quotes, each copied verbatim from the named file,
and at least one from `attempts_rewrite/` when it has attempts.

Do not edit any file and do not run anything.
