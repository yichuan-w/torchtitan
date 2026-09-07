# Student difficulty calibration implementation plan

**Goal:** Repeatedly adjust valid tasks against measured student performance and verify that their difficulty remains in a useful interval on independent attempts.

**Architecture:** Reuse the task rewriter and evaluation harness. Feed measured outcomes and prior changes into the next proposal. Keep task validity, student performance, and independent confirmation separate.

**Tech stack:** Python, existing Codex rewriter, existing Qwen evaluation harness, Daytona.

1. Add a measured-feedback input to `feedback_loop.py` and `evolve_codex.py`. Preserve the feedback in the rewrite record and expose it only to the proposer. Test that it reaches the prompt without appearing in the student task.
2. Add `student_calibration.py` to classify complete evaluation batches. An incomplete or infrastructure-failed batch requires another measurement. A rate above 75% requires further hardening; below 25% requires reducing the change. Excessive turn-limit failures require reviewing execution burden. A batch in range is a candidate for independent confirmation, not a successful calibration by itself.
3. Run the existing development tasks through the feedback path using frozen student weights and evaluation settings. Preserve every proposal, validity result, attempt, and decision. Smoke-test one task before parallel generation.
4. Freeze the procedure before evaluating at least 20 additional eligible tasks. Count every selected task, including unsuccessful rewrites. Require at least 80% to reach the target interval in two independent batches of 16 attempts and inspect failure causes. Repeat across a second Qwen student checkpoint before claiming general stability. If these checks fail, continue development without tuning against the confirmation attempts of the same candidate.

Live training runs and their execution budgets remain untouched. Any experiment that changes a budget records the change and cannot be compared as if it used the old budget. The existing patch-size limits remain fixed during development; a constraint that prevents calibration is reported with evidence before changing the experiment design.
