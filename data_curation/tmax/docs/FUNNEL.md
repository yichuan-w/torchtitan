# The funnel — counts, and what each stage filtered

All numbers are from the published audit docs (`PROCESS.md` / `README.md`) and
the rubric changelog (`rubrics/CHANGELOG.md`). Percentages are of the stated
denominator.

## Top-level gate funnel

| stage | in | out | filtered | note |
|---|---:|---:|---:|---|
| empty-submission gate | 14,601 | — | **7** free-reward (+14 env-error, 1 grader-broken) | verifier pays out for doing nothing |
| rubric audit (2 judges, worst reading) | 1,422 audited | 570 usable | **852** | 40.1% usable — matches River's <40% |
| solve gate (3× competent agent, keep ≥1/3) | 570 | 425 | **145** | 0/3 = nobody could solve it |
| adversarial withdraw + gold coverage | 425 | 398 train | 27 → `checking` | reachable ref / leak verdict / load-dependent reward |

Verified gold coverage on the 398: static gold 327 + procedural gold 46 = **373 /
398 (93.7%)**; independent re-gate confirmed **350 / 351 (99.7%)**, catching 1
genuinely invalid artifact (runtime-derived filenames baked in).

## Per-rubric-version filtering

Every version **from V2 on** has its prompt kept (`rubrics/` + `rubrics/reference/`);
each version's measured effect is the "Measured effect" line in
`rubrics/CHANGELOG.md`. **V1 has no prompt file** — it implemented the River rubric
(App. C) directly, and survives only as the paper itself plus the narrative in
`docs/AUDIT_PROMPT.md` §"What went wrong with v1" (that file also reproduces the
full V2 prompt). See the V1–V3 note below.

| version | prompt / launch script | pool | result → filtered | changelog |
|---|---|---:|---|---|
| **V4** (shipped release's own audit) | `reference/v4_prompt.md`, `reference/workflows/v4_workflow.js` (+ `v4b2_a/b.js`, `v4_disputed.js`) | 398 | CLEAN 123 / IVM 171 / VTW 58 / AL 34 / OTHER 8 / IEM 2 / IAM 2 → 30.9% clean, **drop 275**; 180 disputed → CLEAN 5 | §V4:17-18 |
| **V5** (re-audit of V4's CLEANs) | `reference/v5_prompt.md`, `reference/workflows/v5_workflow.js` | 123 | survived 38 (MGT 39 / CLEAN 38 / VTW 24 / IVM 20 / AL 2) → **drop 85**; 8/12 shipped `final_clean` lost PASS | §V5:27-28 |
| **V6** (two-axis soundness×exploit) | `reference/v6_prompt.md`, `reference/workflows/v6_b1_workflow.js` + `v6_cal_workflow.js` | b1 50 + cal 12 (never re-run over full 398) | b1 50 → REPAIR 23 / REJECT-PROVEN 18 / REVIEW 5 / PASS 4; calibration 10/12 tier-hit | §V6:38-40 |
| **V7** (soundness axis + repro gate) | `rubrics/v7_prompt.md` + `v7_rubric_delta.md`; `workflows/*` | full 398 (5 slices) | PASS 25 / REPAIR 340 (after overlay) | v7 delta series |

**V1–V3** have no per-version drop line: V1 was the initial River-rubric
implementation; V2/V3 were the earlier build behind the `legacy` (641) split,
whose numbers live in this funnel (1,422 round-2 / legacy 641) rather than a
standalone version count.

## Domains (drift is a survival artifact, not a quota)

Usable rate ran from 30.6% (software-engineering) to 67.2% (data-processing):
domains whose verifiers are easiest to write well survived best — which is not
the same as the domains you want most. Do not read the surviving domain mix as a
target distribution.
