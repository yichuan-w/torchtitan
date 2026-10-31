# Rewrite loop: what 1031b showed and what to change

Read-only review of the evolve-both sidecar in run 1031b (Qwen3.5-9B, 723-task
seed mix, ~219 steps), against 1022. Numbers come from W&B `evolution/*`,
`training_lineage/events.jsonl` and `evolution/outcomes/*.jsonl`.

## What the loop did

| at step 150 | 1022 | 1031b |
|---|---|---|
| accepted (easier / harder) | 1512 (1039 / 473) | 1147 (672 / 475) |
| trained | 1300 | 985 |
| easier : harder | 2.2 : 1 | 1.4 : 1 |
| accept / train latency (steps) | 7 / 37 | 8.5 / 22 (35.5 at step 188) |

- The whole gap to 1022 is on the easier side: the cleaned seed set has fewer
  0/N groups (14% vs 18%), so fewer easier signals.
- Acceptance stayed at 82-85%. Provider safety filter 3.4% of sessions,
  `provider_stream_error` 20 (1022: 106), semantic-probe rejects ~27.
- 86 rewrites were interrupted by restarts (median 112 min each, ~164 agent-hours
  lost). This was the largest single waste.

## Do accepted rewrites become learnable?

- Easier rewrites: 43% are still 0/12 the first time they train (1022: 60%), 4%
  jump to 12/12. Mean solve rate of easier versions .35 (1022: .28).
- Harder rewrites: 18% go to 0/12 (overshoot), 17% stay 12/12. Mean solve rate
  .59, above the originals' .55 -- on average they are not harder.
- 29% of accepted rewrites are a second or later easier pass on the same task
  (385 e→e); 12 tasks were made easier six or more times. There were 48
  direction flips (15 easier→harder, 33 harder→easier).
- After step 150, ~82% of trained groups are mixed-outcome, so the loop does keep
  most groups carrying gradient.

## Drift from the eval distribution

- 79.5% of live-mix rows are rewritten (rev ≥ 1); 79% of trained groups at step 175
  ran a rewritten version. Of the 575 rewritten tasks, the latest rewrite is
  easier for 318.
- Easier rewrites mostly add hints to the instruction. Eval tasks have none, so
  the training distribution moves toward hinted tasks.
- Originals' solve rate plateaued at .63-.67 after step 100, while held-out
  pass@5 stayed at 41-46 of 89 tasks.

## Suggestions, by expected impact

1. **Retire easier versions once learned.** When an easier version reaches ~0.5
   solve rate or any 12/12, swap the original (or the parent) back instead of
   asking for a harder rewrite of the hinted text. This keeps hinted tasks a
   minority and costs nothing.
2. **Cap easier chains.** Freeze a task after two consecutive easier rewrites,
   and give the rewriter the previous version's actual outcome (it often predicts
   "5 of 12" for a version that came back 0/12).
3. **Keep originals in the batch.** Hold originals at ≥ 50% of trained groups
   (now 19-23%), e.g. by weighting the sampler by revision.
4. **Calibrate harder rewrites.** Target 2-9 of 12 and probe the candidate with the
   current policy before accepting; reject versions that land at 12/12 or 0/12.
5. **Do not lose work on restart.** Drain in-flight rewrites before a planned
   restart or make them resumable, and alert when one stage fails repeatedly (the
   oracle-stage failures at steps 25-50 stalled acceptance for 25 steps unnoticed).
6. Safety-filter refusals (3.4%) and probe rejects are small; leave them.

Items 1-4 change what the policy trains on, so treat them as training changes and
A/B them; 5 is infrastructure only.

Not pursued: training a folded task ahead of its epoch slot. Fold to first train
is 20 steps at the median, but every task comes round once per ~23 steps anyway,
only 68 of ~1300 rewrites were superseded before training, and jumping folded
tasks to the front would tilt batches further toward rewritten versions, against
item 3.
