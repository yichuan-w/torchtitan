# 7b. Predicted recovery and its limits

**Observed diagnosis: 72 recovery recommendations among 238 decided V4 rejection labels (30.25%); 166 upheld, 37 security-related rows excluded.** These are static calibrated judgments, not observed outcomes from the proposed V8 judge or completed runtime admission gates.

| Prior group | Original reviewed cohort | Decided | Recovery candidates | Upheld | Security skips | Candidate false-negative rate |
|---|---:|---:|---:|---:|---:|---:|
| IVM | 171 | 145 | 67 | 78 | 26 | 46.21% |
| VTW | 58 | 49 | 5 | 44 | 9 | 10.20% |
| others | 46 | 44 | 0 | 44 | 2 | 0.00% |

Here “false negative” means a prior V4 non-CLEAN call that our requested relaxed standard would recover. It does not mean a task was excluded by the original 1422-to-570 filter: the shipped398 and original rejected852 are disjoint. See [scope reconciliation](../../overfilter_scope_20260916.md) and [per-task evidence](../verdicts.jsonl).

## What this supports forecasting

- For the exact reviewed, non-skipped V4 cohort, there are **72 gross diagnostic recovery candidates**. The net V8 PASS count is unmeasured: every unchanged V7 rule must still apply, and the chosen judge has not run. In particular, task_000753_b376cf1d was recovered under the brief’s exclusion of generic grader-venv tampering, while unchanged V7 mutable-ground-truth treatment may still block it. Its diagnostic label is not a guaranteed V8 PASS or a positive-control oracle. See [the V8 delta preservation caveats](v8_delta.md). None of the72 is a runtime-certified training addition.
- If the measured category rates transferred to the **different original rejected852**, the arithmetic would be `97 × 67/145 + 333 × 5/49 + 422 × 0 = 78.8`, or about **79** recoveries. This is an explicitly unvalidated planning scenario. Selection on earlier acceptance and subsequent solve gates makes transfer questionable; model disagreement and security composition are unmeasured.
- **No defensible numerical recovery forecast exists for the full raw14601 yet.** The 13179 outside audited1422 lack comparable prior LLM rejection labels. A new admission from that group is not a measured historical false negative. Re-judge from original artifacts, then measure yield on a separately sampled raw-corpus canary.
- Suggested measurement: after regression smoke, include a deterministic random256 previously unaudited raw tasks alongside the1422 cohort; report raw-canary and historical-pool yield separately. The deliberately enriched smoke48 is for checking rules and guards, not for estimating corpus yield.

## Exclusion sensitivity

If skipped rows were all non-recoverable versus all recoverable, the purely arithmetic cohort bounds would be:
- IVM: 39.18%–54.39% of the original 171 rows.
- VTW: 8.62%–24.14% of the original 58 rows.
- others: 0.00%–4.35% of the original 46 rows.

These are missing-label bounds, not confidence intervals or permission to relax security/answer-leak rules. Security exclusion was not random. No binomial confidence interval is presented because this targeted surviving-task cohort is not a probability sample of original rejections or raw tasks.

Reproduce with `python3 reaudit_398/reviews/overfilter_20260916/v8/build_predicted_recovery.py` after rebuilding the parent aggregate.
