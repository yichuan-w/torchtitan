# V12 — the TMAX task audit rubric

A judge prompt and its kit for deciding whether a TMAX task's automatic grader is sound enough to train on: whether
the reward can be obtained without doing the work the instruction asks for, and whether a faithful solution the
author did not anticipate would be refused. It replaces `../v8`, which this repository already carries together with
its 1998 judgments.

Judge: `claude-opus-4-8`, effort `medium`, one independent context per task, blind to any prior verdict. Input per
task: `instruction.md`, `setup.sh`, `tests/test.sh`, plus a candidate list the runner extracts (see below). Output:
one JSON line, 39 fields, validated against the schema. The tier and the label are **derived** from the fields; the
judge does not choose them.

## Pins

| File | sha256 |
|---|---|
| `v12_prompt.md` (543 lines) | `68558b02c8678fae96bf66d3f13c4cd665cb08503ae679f7e7326de112601c04` |
| `output_schema_v12.json` (39 required keys, 50 `allOf` invariants) | `4ab62ead51756cdcd75aa05224c72c73a1c2a582bad3e6fa9c8f857865169c66` |

## What is here

| Path | What it is |
|---|---|
| `v12_prompt.md` | The judge prompt. Self-contained: glossary, harness facts, a ten-step procedure, the output contract. |
| `output_schema_v12.json` | Draft 2020-12 schema for one row. The invariants are the part that matters: a recorded leak cannot coexist with PASS, REJECT-PROVEN requires a named `reject_shape`, a fired rule forces a non-CLEAN verdict. |
| `DESIGN.md` | The model the prompt is derived from: the failure taxonomy by mechanism, which rules are floors and which are judgments, the invariants, the field crosswalk, and a ledger of what changed and why. |
| `ACCEPTANCE.md` | The validation plan: per-axis criteria, the pre-registered tier class for each guard, the over-rejection instrument, and a hand dry run of 38 tasks done by procedure. |
| `FINAL_REVIEW.md` | The finishing review of the draft: ten edits, seven dry-run disagreements and how each was resolved in the procedure rather than per task. |
| `TRAINING_SIDE.md` | **Which findings the grading harness already neutralises** (`protected_paths` / the integrity baseline) and which cannot be, with the mechanical test for extracting the first set from the rows. |
| `tools/candidates_extract.py` | Runner-side candidate extraction: the program paths the three files mention, every line of `tests/test.sh` that can fail the run, and the third-party imports with whether `setup.sh` installs them. Paths and line numbers only; no task text is emitted. |
| `tools/lint_v12.py` | Row lints L1–L15, including "every supplied candidate was disposed of" and "every failing statement is covered". |
| `tools/score_v12.py` | Per-axis scorer: guards, controls, movers, the over-rejection instrument, the `hook_replaceable` tag. |
| `workflows/v12_judge_workflow.js`, `workflows/model_monitor.sh` | The runner used for the validation, and the monitor that verifies from the agent transcripts that every turn ran on the pinned model. |
| `results/validation70.*` | The 70-judgment validation: report, scorer output, and the rows. |

## How a run works

```
tools/candidates_extract.py <tasks_root> <out_dir>      # one candidate file per task, no task text
→ v12_judge_workflow.js                                  # one judge per batch, {TASK_ROOT} {OUTPUT_PATH} {CANDIDATES_PATH}
→ one JSON line per task, written the moment that task is judged
→ tools/lint_v12.py + tools/score_v12.py                 # schema, lints, per-axis scoring
```

The judge never runs a task script, never opens a container and never reaches the network; it may do its own
arithmetic, which rule B1 requires (a hardcoded expected value has to be re-derived, not trusted).

## What changed from v8, and what was measured

Same judge, same tasks, both versions. The v8 numbers are from this repository's `../v8/results` and from the
independent re-reading of those rows recorded in `ACCEPTANCE.md` and `FINAL_REVIEW.md`.

| | v8 | v12 |
|---|---|---|
| Ten known-leak guards, every repeat | two of them passed in 3 of 5 runs | 20/20 non-PASS, each naming the floor rule and the path |
| Ten known-clean controls, leak axis | no instrument | 20/20 clean; the four PASS-required controls PASS in every repeat |
| Movable ground truth (`task_000056`, `task_000927`) | PASS | REPAIR, with the movable path named |
| Wrong hardcoded expectations (six tasks) | PASS — the literal was read, not checked | REPAIR, each re-derived locally and confirmed independently |
| Source-text grep rejecting a faithful solution | PASS | REPAIR |
| Discards | "cheap exploit and no fix I could name" | only via one of four named shapes, stated in the row |
| 18 independently verified judgments | — | 16/18 reproduced by procedure |

Mechanically, the three changes that produced most of this: rules are fields (a recorded leak cannot be PASS, the
schema rejects the row); the candidate inventory is supplied by the runner and every entry must be disposed of, so
an omitted oracle is an error rather than a silent pass; and a discard must name one of four shapes instead of
resting on exploit cost.

The over-rejection side is instrumented rather than asserted: four named clauses in `ACCEPTANCE.md` §4 (the
PASS-required controls stay PASS, the ten controls stay clean on the leak axis, two tasks an earlier version wrongly
rejected come back as PASS, every discard names its shape). All four passed on the validation run. Method, language,
packaging and path clauses remain secondary unless the instruction states the method is the objective — the
deliberate policy choice recorded in `DESIGN.md`.

## Status and limits

The validation is 70 judgments (20 tasks × 2 repeats plus 30 single judgments chosen for being hard or previously
contested), not a population estimate. It does not measure the corpus-wide rate of anything. Three lenient misses
are listed in `results/validation70.RESULTS.md`; two discards reached through a named shape on tasks outside the
verified set are unverified.

`results/run600.*` is the production run: 600 tasks v8 already judged, stratified to oversample v8's PASS set,
with the twenty acceptance ids embedded as a live instrument — every clause of it passed. Headline: **of the 307
tasks v8 called PASS, v12 keeps 169 and moves 138 off** — 22 of those carry an outright leak field, 49 a core
clause the grader never checks, 29 a check that rejects faithful work, 20 a movable ground truth. In the other
direction v12 admits 19 of the 293 tasks v8 did not pass. No row in that run is independently verified; the
limits are in `results/run600.RESULTS.md` section 6.

Nothing in `results/` is a runtime receipt: every judgment is static, and a `verifier_fix` is a proposed change, not
a validated patch.
