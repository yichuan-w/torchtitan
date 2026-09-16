# V8 re-judge results — 5 runs, 1998 judgments (2026-09-16)

Judge: `claude-opus-4-8`, effort `medium`, blinded (no prior verdicts shown), V8 rubric
(`../v8_prompt.md`, sha256 `3f305309f11cbd0a1dd9370d81c57d8ad0ad2af44f3bd4b533da602dbe830e0b`),
one JSON row per task written the moment that task was judged (`*.rows.jsonl`, one line per
task; schema `../output_schema.json`). Every run's judge model was verified from the agent
transcripts' top-level `model` field for the whole run (`../workflows/model_monitor.sh`).

## Per run

| run | tasks | PASS | REPAIR | REVIEW | REJECT-PROVEN | SKIP | guards non-PASS | note |
|---|---:|---:|---:|---:|---:|---:|---|---|
| trial100 | 100 | 17 | 80 | 2 | 0 | 1 | 5/5 | launched with the `opus` alias, which resolved to opus-4-8 (see manifest `model_note`) |
| trial500 | 500 | 109 | 372 | 9 | 7 | 3 | 3/5 | explicit full model id |
| trial200 | 200 | 41 | 148 | 4 | 2 | 5 | 5/5 | |
| trial600 (sec2) | 600 | 150 | 411 | 20 | 19 | 0 | 3/5 | security tasks in scope (148, dedicated batches); quota-interrupted, completed by re-judging only the 86 unwritten ids |
| trial600b (raw) | 598 | 183 | 400 | 7 | 8 | 0 | 3/5 | 570 never-audited raw tasks + last 25 audited; 2 security tasks unjudged (see COVERAGE_GAPS) |
| **total** | **1998** | **500 (25.0%)** | **1411 (70.6%)** | **42** | **36** | **9** | | 1978 unique tasks (the 5 guards are re-judged every run) |

Pools and category composition per run are in each `*.manifest.json` (task ids only; no
task content). SKIP rows are early-run self-skips of security tasks before security was
brought into scope.

## Coverage

- Previously LLM-audited pool (1409 tasks): **1409 / 1409 judged**.
- Never-audited raw pool: **569 judged**, 12,623 remaining (of the 14,601-task archive).
- Whole archive: 1978 / 14,601 unique tasks judged (13.5%).

## Observations (facts, not conclusions)

- Raw never-audited tasks pass at a higher rate (trial600b raw subset: PASS 179 / 566 = 32%)
  than the previously-audited pool (~22%).
- Security-domain tasks (trial600 subset: 148) pass at ~23%, in line with non-security.
- Under V8, `REPAIR` (70%) means "a bounded verifier fix exists; not admissible as-is";
  only `PASS` is admissible at the rubric stage. See `../recommendations.md`.
- Regression guards: 3 of 5 known-leak guards were non-PASS in every run; **two guards
  (`task_000156_2f395118`, `task_000241_547763df`) were PASS in 3 of 5 runs** under
  opus-4-8 medium, i.e. this judge is not deterministic on those known leaks. An
  independent Opus 5 adjudication of three trial100 PASS flips found all three to be floor
  failures (`../OPUS5_REVIEW_RECEIPT.md`). PASS rows should be floor-checked before use.
- `trial600b` coverage gap: 2 security tasks were refused twice by the opus-4-8 cyber
  safeguard (`COVERAGE_GAPS.json`); they are not counted as judged and are not PASS.
