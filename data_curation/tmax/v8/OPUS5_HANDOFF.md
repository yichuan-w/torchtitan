# Handoff to Claude Opus 5: review and run a new V8 workflow

The user will open/run this workflow from the Opus 5 seat. This package prepares that handoff; no V8 judge call or full rerun has been performed here. The objective is a **new review of original dataset artifacts**, not a patch to the old accepted output.

## Read first

1. [Exact V8 delta](v8_delta.md), [unified patch](v8_delta.patch), and [full proposed prompt](v8_prompt.md).
2. [Measured diagnostic rates and conditional predictions](predicted_recovery.md).
3. [Recommended model/scope/aggregation choices](recommendations.md), [configuration](workflow_config.json), [workflow guide](WORKFLOW_README.md), and [workflow code](v8_workflow.js).
4. [Mandatory rejection guards](regression_guards.json) and [their source/runtime evidence](regression_guards.md).
5. [Original per-task diagnostic list](../verdicts.jsonl) and [scope reconciliation](../../overfilter_scope_20260916.md) as reviewer evidence, never as first-message judge labels.

## Review the delta and configuration

- Verify the exact V7 base hash and patch round trip. All non-scoped V7 rules must remain intact. V7 already allowed cosmetic PASS; V8 changes the secondary/core boundary and requires a complete concrete VTW counterexample before that concern blocks PASS.
- Preserve answer-leak, runnable-complete-answer, reward-for-nothing, mutable-input/oracle taint, rejects-right, instability, the harness contract, phase-3 authority and downstream admission gates.
- Inspect the explicit precedence over legacy no-reproduction REVIEW wording: absence of a VTW exploit alone must not recreate the old rejection through another label. Unrelated uncertainty and confidence rules still apply.
- Do not transfer diagnostic recovery labels directly into V8. Task `task_000753_b376cf1d` illustrates a remaining boundary with unchanged V7 grader-venv/mutable-ground-truth rules and is not a guaranteed positive control.
- Check exact model routing and config/prompt hashes. The proposed primary is `gpt-5.4-2026-03-05`, high, batch8, concurrency8. Running the orchestration from Opus 5 is distinct from choosing the worker judge model. Model and scope remain the user's choices; reflect the user's launch instructions in the reviewed config instead of silently substituting models.

Record the actual review result and the exact reviewed hashes. A prepared config or dry run is not an Opus approval receipt. If edits are needed, regenerate hashes/manifests and review the changed version.

## Execute through the existing workflow host

Follow `WORKFLOW_README.md` for preparation, the exact workflow file, runtime arguments, receipt fields and local verification commands. The implementation reuses the package's fan-out pattern. Live host/model availability is not established by local mocked tests.

Recommended sequence:

1. Regression/calibration smoke 48: all five negative guards, twelve positive recovery candidates, and thirty-one deterministic stratified rows.
2. Expanded canary 1678: the prior audited 1422 plus 256 deterministic random previously unaudited raw IDs. Keep the two measurement strata separate. The smaller audited 1422 option remains available.
3. Fresh raw 14601 coverage after the reviewed policy and regression gate pass. Security and unresolved-domain exclusions must be explicit rows/manifest entries, never missing tasks or PASS labels. The current proposed config identifies 2078 known-security and 39 unresolved-security IDs from source metadata; validate those counts during preparation. “Complete raw coverage with exclusions” is different from “all 14601 semantically judged.”

Use original archive members only; do not apply repair overlays, seed the judge with previous verdicts, or append results to an earlier audit. Persist each completed task immediately. Preserve first-message verdicts and separately append any permitted post-prior revisions; first records remain authoritative under unchanged V7/V8 rules.

## Mandatory regression guards

Every original-artifact guard must receive operational **REJECT**:

- `task_000156_2f395118`
- `task_000241_547763df`
- `task_000311_9e22fec5`
- `task_004863_67089cd4`
- `task_004886_10504160`

A missing row, malformed result, model failure, security skip, or REVIEW/HOLD is not a passed rejection guard. `REPAIR` can count as operational REJECT of the original artifact while retaining V7's separate repairability meaning; it must never be counted as a kept original. Require the documented floor finding where the guard schema specifies one. Preserve static versus archived-runtime evidence distinctions.

A failing guard blocks expansion. Do not repair the guarded task or weaken the guard to make the new rubric pass. Diagnose the rubric/workflow result and repeat a fresh smoke with the changed version identified.

## Report back

Report selected scope and exclusions; model snapshot and prompt/config/archive hashes; exact coverage and duplicate/missing/error counts; guard results; positive-control and inter-judge disagreements; fresh V8 tiers and operational admission counts; and the durable result paths. Preserve the distinction between rubric-stage PASS and completed execution/admission gates.

The preceding diagnosis found 72 recovery recommendations among 238 decided V4 non-CLEAN rows, with 166 upheld and 37 excluded. The IVM/VTW/other rates are46.21%/10.20%/0%. Those rows were already in the earlier retained population; they are disjoint from the original 852 rejects. The illustrative approximately79 recovery transfer scenario is not a measured raw-corpus yield prediction.
