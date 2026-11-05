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

## Do rewrites land in gradient groups?

A gradient group is a finalized group with 0 < solved < scored (non-zero reward
std; group size 12). Counted from `training_lineage/events.jsonl`, rewrite
direction from the hot-reload chain plus `lineage.jsonl` folds.

All finalized groups (mix / all-0 / all-12):

| run | original | easier | harder |
|---|---|---|---|
| 1022 | .68 / .18 / .14 | .64 / .33 / .03 | .74 / .09 / .17 |
| 1026b | .73 / .14 / .13 | .74 / .24 / .03 | .80 / .09 / .11 |
| 1031b | .72 / .14 / .14 | .74 / .22 / .04 | .76 / .10 / .14 |

The first group of each new version is the honest number; the table above is
flattered by survivors (a version that misses is replaced after one group, a
version that lands keeps collecting groups):

| run | easier: mix / all-0 | harder: mix / all-12 / all-0 |
|---|---|---|
| 1022 | .40 / .59 | .61 / .21 / .18 |
| 1026b | .42 / .57 | .64 / .14 / .22 |
| 1031b | .54 / .42 | .65 / .16 / .19 |

- Easier rewrites under-simplify. In 1031b the rewriter predicted 4.8/12 on
  average and the first group solved 2.7/12; versions predicted at 4-5 solved
  1.6-2.4, predictions of 6-7 solved 4.4-7.3.
- Chain depth does not change the hit rate: the first-group mix of e1 / e2 / e3+
  is .53 / .54 / .51 in 1031b (.33-.39 in 1022 and 1026b), so each further easier
  pass behaves like an independent retry. Very deep revisions do sink, though:
  rev ≥ 8 is mostly 0/12 in 1022 and 1026b.
- Direction flips land better: a rewrite made right after one in the opposite
  direction hits .54-.74, against .33-.54 for a same-direction chain, because the
  rewriter then sits between a too-hard and a too-easy version.
- Inside one version, a mixed group is followed by another mixed group 81-89% of
  the time. After a 0/12 group the next group is mixed only 12-29% of the time,
  while a fresh rewrite lands at .40-.65, so rewriting an easier version on its
  first 0/12 is right. Harder is closer: after a 12/12 the next group of the same
  version is mixed 31-46% of the time, against .61-.65 for a fresh harder rewrite.
- Share of rollout time that ends up in a trained gradient group: harder
  .58 / .65 / .72, easier .42 / .55 / .66, originals .45 / .51 / .61 (1022 /
  1026b / 1031b). Groups dropped as stale are 13-22% in 1022 / 1026b (5-11% in
  1031b with a 7-step window), and 70-85% of them were mixed; that loss is the
  off-policy window, not the rewrite loop.

## Suggestions, by expected impact

1. **Make easier rewrites land.** Ask the rewriter for 6-7/12 instead of ~5 (it
   over-predicts by ~2), and gate the fold on a cheap probe with the current
   policy (k ≈ 4, at least one solve). This targets the 42-59% of new easier
   versions that come back 0/12 and the trained 0/12 groups, which also have the
   longest rollouts.
2. **Hand the rewriter both neighbours and the measured outcome.** Give it the
   previous version, which way it missed and its actual solve count, and the last
   version that produced a mixed group, so it bisects instead of stepping blindly.
3. **Retire by misses, not by depth.** Depth alone does not lower the hit rate, so
   instead of a fixed chain cap: after three consecutive versions whose first
   group misses, revert to the last version that produced a mixed group (the
   original counts) and stop rewriting that task for a while.
4. **Retire easier versions once learned.** When an easier version reaches ~0.5
   solve rate or any 12/12, swap the original (or the parent) back instead of
   asking for a harder rewrite of the hinted text. This keeps hinted tasks a
   minority and costs nothing.
5. **Keep originals in the batch.** Hold originals at ≥ 50% of trained groups
   (now 19-23%), e.g. by weighting the sampler by revision.
6. **Calibrate harder rewrites.** Target 3-5/12 and probe the candidate before
   accepting; 14-21% of new harder versions still come back 12/12 and 18-22%
   overshoot to 0/12. Harder is the most gradient-efficient direction, so it can
   take a larger share of rewrite slots.
7. **Do not lose work on restart.** Drain in-flight rewrites before a planned
   restart or make them resumable, and alert when one stage fails repeatedly (the
   oracle-stage failures at steps 25-50 stalled acceptance for 25 steps unnoticed).
8. Safety-filter refusals (3.4%) and probe rejects are small; leave them.

Items 1-6 change what the policy trains on, so treat them as training changes and
A/B them; 7 is infrastructure only.

Not pursued: training a folded task ahead of its epoch slot. Fold to first train
is 20 steps at the median, but every task comes round once per ~23 steps anyway,
only 68 of ~1300 rewrites were superseded before training, and jumping folded
tasks to the front would tilt batches further toward rewritten versions, against
item 5.
