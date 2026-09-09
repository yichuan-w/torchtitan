# Infrastructure failures and environment validation

The change is complete when the regression tests pass and every task in the frozen training inventory passes a fresh Daytona environment check. A failed task requires diagnosis, a repair preserving task semantics, and another validation attempt with the repaired inputs recorded.

1. Fix generation request ownership in `routing/inter_generator_router.py` and `actors/rollout_worker.py`. Cancellation must reach the generator that received the request. In `actors/generator.py`, settle every removed future, reject new requests after either background loop fails, and acknowledge an engine abort only after every rank succeeds. Test late results, duplicate cancellation, metric failures, failed loops, and a failed abort on a peer rank.
2. Propagate unexpected Terminus exceptions in `harness/agents/terminus.py` to the rollouter's existing infrastructure-failure handling. Test startup and execution errors, ordinary incorrect answers, and the sample builder's treatment of invalid siblings under both group-drop configurations.
3. Use the actual drop reason in `controller.py` and classify groups with no scored results separately from groups whose scored answers all failed.
4. Require tmux in `examples/tmax/prepare_rts_data.py`. Extend the existing environment validation scripts to exercise the same terminal startup used by training, then run task reference solutions and grading where supplied. Freeze the complete task inputs and code revision, save per-task timestamped start/end records and independent results, and verify resume before the full sweep. Diagnose every failed environment and repeat validation after repairs.

Use isolated worktrees and new result directories: the active training checkout and original datasets/results are outside this change's scope. Preserve task instructions and grading semantics during environment repairs.
