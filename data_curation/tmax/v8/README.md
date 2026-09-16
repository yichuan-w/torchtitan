# V8 review and rerun handoff package

Start with [OPUS5_HANDOFF.md](OPUS5_HANDOFF.md). The user will run the workflow from Opus 5; this package has not launched a V8 judge.

| Requested deliverable | Files |
|---|---|
| 7a — exact minimal rubric relaxation | [before/after delta](v8_delta.md), [unified diff](v8_delta.patch), [complete prompt](v8_prompt.md) |
| 7b — recovery rates and bounded forecast | [analysis](predicted_recovery.md), [machine-readable rates](predicted_recovery.json), [reproducer](build_predicted_recovery.py) |
| 7c — model/scope/workflow proposal | [recommendations](recommendations.md), [config](workflow_config.json), [workflow](v8_workflow.js), [schema](output_schema.json), [workflow guide](WORKFLOW_README.md) |
| 7d — mandatory negative regression set | [guard JSON](regression_guards.json), [evidence](regression_guards.md) |
| Step 1 — per-task diagnostic decisions | [report](../report.md), [JSONL](../verdicts.jsonl), [TSV](../verdicts.tsv), [coverage](../coverage.jsonl) |

The V8 delta is proposed for Opus 5 review. Judge and scope choices remain editable recommendations. Current V7, task artifacts and old output are preserved. Static/mock validation is not a live model run, runtime task validation or proof of corpus yield.
