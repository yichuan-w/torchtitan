# 7c. Recommended V8 rerun choices — proposed for user and Claude Opus 5 review

## Recommendation

Use a **single pinned GPT-5.4 primary judge with the relaxed V8 rubric**, compare it with Claude Opus 5 on the initial calibration set, and obtain evidence-based adjudication for genuine disagreements. Do not combine two opinions with an automatic worst-reading rule. The full target should be the **original raw corpus of 14,601 tasks**, reached through regression smoke and an expanded canary. Every stage judges original artifacts afresh; historical verdicts are selection/provenance metadata and are withheld during the blind first message.

These are proposed choices, not decisions already made by the user. The configuration and rubric are prepared for Claude Opus 5 review before a full run is trusted. No new model judge or full-corpus run has been launched.

## Judge model

| Option | Why consider it | Limitation | Recommendation |
|---|---|---|---|
| GPT-5.4-class primary | Tests the user's requested reference-aligned alternative under explicit evidence rules; an exact snapshot gives a reproducible model boundary. | This diagnosis used Sol/GPT-6; it does not establish GPT-5.4's recall or safety. | Proposed primary: `gpt-5.4-2026-03-05`, reasoning `high`. |
| Strong Claude with V8 | Tests whether correcting the rubric alone removes over-rejection while retaining detailed code reading. | A strong judge can still reinterpret “core”; its recall must be measured rather than presumed. | Use Claude Opus 5 to review the delta/config and for a blinded paired pilot. Exact runtime route is a user/environment choice. |
| Both on every task | Measures disagreement throughout the corpus. | Roughly doubles judge work; automatic “either rejects => reject” reintroduces the target false-negative mechanism. | Optional after pilot; not the default. |

Official OpenAI documentation lists `gpt-5.4-2026-03-05`, supports `high` reasoning, structured outputs, and the Responses/Chat Completions/Batch endpoints. It does **not** establish that GPT-5.4 is the best-recall judge for this task, nor that this account/workflow router can invoke it. Verify the exact model route before launching; fail on an unavailable route instead of silently substituting. [GPT-5.4 model documentation](https://developers.openai.com/api/docs/models/gpt-5.4).

The brief's claim that a weaker judge should recover more is a hypothesis. Compare both models on the same blinded V8 pilot and score false rejections against calibrated positive controls, failures against negative guards, and unresolved evidence separately. Choose the primary based on that result. Keep the requested model fixed for a run and record the resolved snapshot and full prompt hash.

## Complete-dataset scope

1. **Smoke 48:** all regression guards, 12 positive recovery controls, and the remaining deterministic stratified examples. This tests rule application and reporting; it is intentionally enriched and cannot estimate raw-corpus yield.
2. **Expanded canary 1678:** the full prior audited 1422 plus 256 deterministic random tasks from the previously unaudited remainder (seed `20260916`). Re-judge these from original artifacts. Report those two strata separately. An audited 1422-only alternative is provided for a smaller budget.
3. **Full raw 14601:** the proposed complete target. Every raw ID gets either a fresh V8 disposition or an explicit authorized exclusion/error/hold record. Known security exclusions must remain visible; a run with excluded security tasks is “raw-corpus coverage with exclusions,” not “all 14601 semantically judged.” Unknown security routing is not evidence of a benign task.

The old 852 rejections alone are not an adequate from-scratch scope. The shipped 398 recovery list is also not a full-dataset manifest. Both are diagnostic strata. The frozen archive contains 14601 IDs; raw minus audited 1422 leaves 13179 without comparable prior LLM labels.

At batch size 8, the blind first pass requires 6 smoke batches, 178 audited-pool batches, 210 expanded-canary batches, or 1826 raw-corpus batches before retries/exclusions. The retained V7 two-message protocol adds a prior-aware call of the same configured judge after first-pass completeness; the current workflow schedules a second call per bucket. Budget approximately twice those invocation counts for the complete protocol, before exclusions. This is distinct from paying two independent judge models. These are invocation counts, not price estimates. Full raw task volume is about 10.27 times the 1422 pool. Two judges on every ID roughly doubles that protocol's judge work; selective second opinions preserve a lower default cost.

## One judge, with selective review

The proposed production path is one judge per task. Retain a second independent opinion for the paired pilot, evidence contradictions, and a recorded calibration sample; disagreement routes to review and does not automatically take the more severe label. A concrete exploit, meaningful original-input corruption, reachable complete answer, or failed negative guard is never neutralized by majority vote.

V7's distinction between `REPAIR`, `REVIEW`, and `REJECT-PROVEN` remains. For admission of an **original** artifact, only `tier: PASS` qualifies; all other tiers are operationally rejected/held. A `salvageable: true` tag does not override a non-PASS tier. “REJECT” in the regression guard means the original task cannot be admitted, even if a later separate repair could make it usable.

## Readiness gates

The workflow config must record the user's chosen model/scope, the exact V8 prompt hash, a Claude Opus 5 review receipt for the delta/config, the unchanged task-archive hash, and a fresh run directory. Run every negative guard and positive control before trusting a full run. Missing/malformed rows, security skips, model failures, and unexecuted probes remain distinct; no missing result becomes PASS.

The existing deterministic pipeline gates remain required after re-judging: empty-submission checks, solve attempts, scored-solution capture, fresh-container oracle replay, independent re-grading and mutation checks. This deliverable prepares the new rubric and workflow; it does not waive those gates or certify runtime behavior from static evidence.

See [workflow configuration](workflow_config.json), [workflow implementation](v8_workflow.js), [output schema](output_schema.json), and the workflow README for exact preparation and launch commands. The config is a proposal until the user confirms its choices and the required review/gate evidence exists.
