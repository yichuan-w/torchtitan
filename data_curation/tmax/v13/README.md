# V13 — seed audit for training that runs with an evolve loop

A static per-task audit that answers one question: **is there a reason this task must not be a seed?**
It is cut from V12 (`../v12/`, unchanged and still valid for its own question) and is meant to be read next to the
evolve loop on this branch, because what it checks and what it deliberately ignores are chosen against that loop.

## Why it is not V12

V12 asked "is this verifier sound?" and held a task back for every way it was not. For choosing seeds that is the
wrong question, for two reasons:

1. **The evolve loop already repairs most weak verification.** A task the policy solves nearly every time is
   rewritten: the author changes inputs or conditions from the policy's own traces, a second author who never sees
   the solution re-derives the verifier, and the rewrite must pass an empty-run probe, a reference run, negative
   controls per falsifiable clause and a positive control in a different permitted representation.
2. **A seed pool with no weak spots leaves that loop nothing to show.** The weak spots are the work; the audit's job
   is to record them precisely so the loop's gain can be measured, not to remove them in advance.

So V13 blocks only on what the loop cannot see or will never reach, and records the rest.

## What blocks, what is a note, what is gone

| | rules | effect |
|---|---|---|
| **blocks** | A1 oracle reachable, A2 expectation revealed, A3 value derivable | an answer leak: no detector for this exists on the training side, and the environment is inherited by every bred child |
| **blocks** | B1 over-specific check, B3 unstable reward | a faithful solution is refused, or the same solution scores differently run to run: such a task never reaches the solve rate at which the loop would rewrite it |
| **note, no tier effect** | A4 movable expectation (+ `protected_paths`), A6 a wrong submission passes, A7 unenforced core clause | recorded with the same precision as a blocking field; these are hardening hints and the "before" record |
| **gone** | A5 untouched image passes, `trivial`, B2 environment mismatch, the dependency hold | decided by runtime gates outside the audit (below) |
| **gone** | `verifier_fix`, the fix catalogue, the four reject shapes, REPAIR / REJECT-PROVEN, `repro_cost`, `repro_knowledge`, `secondary_clauses_unenforced`, per-side statuses | no consumer; a flagged task is skipped for a campaign, never discarded |

Tiers: **SEED** / **FLAGGED** (a blocking rule fired and was reproduced) / **UNSURE** (a blocking rule fired but
could not be driven, or an ambiguity or an unsettled fact). FLAGGED means "not a seed as it stands", never "discard".

Also deliberately narrowed so that less is held back than under V12: a source-text grep is B1 only when a faithful
solution could fail it; a generous timeout guard is not B3; a flake that cannot be demonstrated is neither B3 nor an
uncertain fact; an unverified literal never blocks. Added to B1: **a name the verifier depends on that neither the
instruction nor any readable file states** — the evolve side's own top cause of tasks a policy fails 16 of 16 times
(`training_side/EVOLVE_PROMPTS_VS_V12.md`).

Size: 30 fields, 23 cross-field schema rules, 490 prompt lines (V12: 39, 50, 543).

## What the audit leaves to runtime gates — the runner's obligations

1. **Empty run.** Every candidate seed must score 0 on an empty submission in its real image.
2. **Reference run.** A seed needs `environment/Dockerfile` and `solution/solve.sh`, and the reference must score
   **reward 1** in the real image with the row's final protected lists. An exit code is not a reward:
   `evolution/oracle_validate_seeds.py` marks a seed `pass` from `test.exit_code == 0` without reading the reward
   file, so a verifier that writes reward 0 and exits 0 passes it today.
3. **Pins.** `protected_paths` in a row are candidates, not decisions. Use them only where the public task gives no
   leave to edit the file: nothing on the training side validates a pin against the instruction, and adding any
   protected entry to a row switches that row's `pre_test_sh` hook off entirely (`grading.py:325-326`).

## The RST variant (`rst/`)

V13 judges TMAX and is unchanged. The RST corpus (Harbor-format tasks) is judged with a separate variant,
`rst/v13_rst_prompt.md`: the same rules, a different file shape (a Dockerfile instead of a setup.sh, a pytest verifier
started by a shared bootstrap), the harness facts that differ, and one added field. `rst/DIFF_v13_to_v13rst.patch` is
the exact text difference and `rst/README.md` has the detail. What it found that matters beyond the audit:

- **Grade-time network is the rule, not the exception.** 174 of 181 staged RST tasks install their test runner at
  grade time (`apt-get`, the uv installer from astral.sh, PyPI). Under V13's B3 that would hold back 96 % of the corpus
  for a property of the harness, so the variant records it on every row in its own field, `bootstrap_fetch`, with no
  tier effect, cross-checks it against facts the stager reads mechanically, and the scorer prints the tier both ways.
- **A `CMD` without an `ENTRYPOINT` never runs in the training sandbox** (`prepare_rts_data._entrypoint_command`
  returns `None` without one): a service an instruction assumes is running, but which only a CMD would start, is absent.
- **Every staged task that ships a `docker-compose.yaml` failed or never finished the oracle/empty gate** (12 of 12);
  168 of the 169 without one pass.
- **16 instructions point the agent at a generator file** (`rewrite_contract.json`, `rewrite_step_verifier.json`, ...);
  in 15 the file is not in the image, so the requirements it would have listed are unstated.
- **Tag-based security exclusion leaks.** Beyond the 16 ids the corpus's own `security_union` marks, an instruction
  screen found 15 security-shaped tasks among 196 untagged ones.

## Files

| file | what |
|---|---|
| `v13_prompt.md` | the judge prompt |
| `tools/build_schema_v13.py` -> `output_schema_v13.json` | the schema, generated so each cross-field rule is stated once |
| `tools/schema_selftest.py` | rows that must validate and rows that must not (17 + 25 cases) |
| `tools/lint_v13.py` | post-hoc row lints; V12 ids kept where the check survives, L4/L6/L8/L12 retired with their fields, L16 new for the `protected_paths` guards |
| `tools/score_v13.py` | scorer for the 70-judgment validation kit |
| `tools/crosswalk_v12_rows.py` | the V13 tier of tasks already judged under V12, without re-judging |
| `tools/compare_efforts.py` | compares judge arms against the reference run |
| `tools/candidates_extract.py` | the runner-side inventory every judge prompt is given: program paths the three files mention and the verifier lines that can fail the run (regex output, leads only) |
| `rst/` | the RST variant: prompt, diff against V13, schema, stager, lints, renderer, scorer, self-tests, review, smoke |
| `workflows/v13_judge_workflow.js` | the judge workflow (explicit `claude-opus-4-8`, effort medium) |
| `workflows/run_glm_judges.py` | headless-worker launcher used for the GLM arms: one task per worker, several API keys round-robin, retry rotates the key, rows already written are skipped |
| `REVIEW_opus5_v13.md` | independent review of the first draft (27 findings, all applied before any run) |
| `training_side/` | five read-only readouts of this branch that justify the design: architecture and gates, leak detectors and pin safety, the evolveloop deployment, the three agent prompts rule-by-rule against V12, and a citation check of an external review |

## Results in `results/`

Same 70-judgment kit throughout (20 acceptance ids x 2 repeats, 18 independently verified movers, 12 flippers), so
the arms are comparable. Judge model is explicit per call and verified from the transcripts.

| arm | SEED | guards held (of 20) | controls SEED (of 14) | movers ok (of 18) | median s | median output tokens |
|---|---:|---:|---:|---:|---:|---:|
| claude-opus-4-8, medium | 34 | 18 | 14 | 15 | | |
| glm-5.3, low | 28 | 20 | 14 | 15 | 71 | 6,526 |
| glm-5.3, high | 28 | 20 | 14 | 17 | 239 | 23,217 |
| glm-5.3, xhigh | 27 | 20 | 13 | 17 | 804 | 80,217 |

Guards are the ten known leaks that must be held back in both repeats; controls are the four clean and three
movable-expectation tasks that must come back SEED. Every arm produced 0 schema-invalid rows. xhigh costs 2.6x the
tokens of high for no gain on the guard or control axes and is the only arm that let a control through.

`v12rows_under_v13.seed_list.csv` applies the V13 policy to the 650 tasks V12 already judged, with no new judgments:
SEED rises from 31% to 61% on the 600-task production sample and from 24% to 62% on the 50 never-judged tasks. What
had held the newly admitted rows back is, in order: an unenforced core clause (88), a movable expectation (41), a
wrong submission that passes (24), both (16), an unverified literal (7), an environment mismatch (3).

Caveats: one run per arm; no V13 row is independently verified; SEED is a candidate, not an admitted seed, until the
two runtime gates above have answered.
