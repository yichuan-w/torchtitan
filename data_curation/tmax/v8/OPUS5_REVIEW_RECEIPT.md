# Claude Opus 5 review receipt — V8 rubric-relaxation package

**Verdict: GO-WITH-FIXES for the smoke-48 run.** The smoke-48 is safe to launch
as-is; the three fixes below must land before smoke evidence is used to authorize
the canary-1678 / raw-14601 stages. Model choice, scope, and the launch itself
remain the user's decision. This receipt is a review record, not a launch
authorization — `launch_enabled` stays the user's to flip.

Reviewer: Claude Opus 5 (session review, 2026-09-16). Reviewed against real task
content for the sampled non-security tasks; 37 security tasks left unread.

## Reviewed artifacts (hashes, verified consistent)
- V7 base: `rubrics/v7_prompt.md` sha256_16 `d5498e139f338672` (matches the frozen record)
- V8 prompt: `v8/v8_prompt.md` sha256_16 `3f305309` — patch round-trips byte-identical from the V7 base (verified independently)
- workflow_config `fd757604`, output_schema `2952d571`, regression_guards `8ed0d913` — all match the constants baked into `v8_workflow.js:21-26`

## Five checks
1. **Leak-floor: FLOOR-INTACT.** The two relaxations (hypothetical-VTW->PASS,
   cosmetic/secondary-IVM->PASS) are subordinated to "all other V7 rules
   satisfied"; ANSWER-LEAK / reachable-runnable-answer / reward-for-nothing /
   mutable-input-oracle-taint / rejects-right / instability / D1/D4 all stay in
   force (v8_prompt.md:110-112); "missing core relabelled as secondary" blocked
   (104-105, 425-429); `salvageable` annotation-only, never overrides tier
   (119-121; config `salvageable_overrides_non_pass:false`).
2. **Regression guards: all 5 HOLD.** task_000156 / 000241 / 000311 / 004863 /
   004886 are genuine leaks and each traces to non-PASS under V8. No blocker.
3. **Recovery correctness: 9 AGREE / 1 DISAGREE** (10 sampled). Disagreement =
   `task_000283_b8c17c0d` (Codex salvaged; it is a real mutable_ground_truth +
   artifact_identity_unbound defect — the V8 rubric itself would REJECT it, and
   Codex rejected the structurally identical task_000011). Not in the smoke-12,
   so the smoke is unaffected; but the 72-recovery list is not ground truth.
4. **Upheld spot-check: all 3 still correctly REJECT** (000086 / 000011 / 000050).
5. **Config: consistent, one robustness gap.** Guards are hard-wired as mandatory
   pre-gates (v8_workflow.js:136-137, 240-253 throws if any guard tier not in
   {REPAIR, REJECT-PROVEN}); downstream deterministic gates retained; launch
   fails-closed. Gap: `regression_guards.json` has no `required_defect_flags`, so
   the gate checks the tier but not the reject REASON (right-reason check is a
   no-op). Floor still protected.

## Required fixes before promoting smoke -> canary/raw
1. **Populate `required_defect_flags` per guard** in `regression_guards.json`
   (e.g. 000311->preplaced_value; 000156/004863->reachable_reference;
   004886->artifact_identity_unbound), then rebuild the config/workflow hash
   chain. Turns the right-reason check from a no-op into a real gate.
2. **Flag `task_000283_b8c17c0d` out of the recovery set** and correct the
   recovery count; never treat the recovery list as auto-PASS.
3. **Treat smoke-48 as a live gpt-5.4 calibration**, not a formality: require all
   5 guards to REJECT *and* a reasonable recovery share on the 12 positive
   controls (any positive_control_non_pass routed to the stronger second-opinion
   judge) before promoting to canary-1678 / raw-14601.

## Standing caveats (from the diagnosis, carried forward)
- The recovery/guard evidence was produced by a strong model (gpt-5.6-sol /
  gpt-6-class), not the proposed production judge gpt-5.4; gpt-5.4's recall and
  over-rejection on V8 are unmeasured — that is exactly what smoke-48 establishes.
- The diagnosis ran on the shipped-398 survivors, disjoint from the 852 actually
  filtered out; recovery rates do not directly predict raw-14601 yield.
- No task-container grading ran; recovery rationale is static/source-backed.
