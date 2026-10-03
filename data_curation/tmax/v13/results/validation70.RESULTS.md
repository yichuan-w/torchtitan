# V13 validation — same 70-judgment kit as V12 (2026-09-20, run wf_92d6e96f-83f)

Prompt sha `96fbc03b…49f6`, schema sha `d77df444…ca05`. Judge `claude-opus-4-8`, effort `medium`, explicit per call and
verified from the transcripts of all 13 agents. 70/70 judged, 0 failed batches, **0 schema-invalid rows**. 2.11 M
subagent tokens in 19 min: **30.1 k per judgment (V12: 32.9 k, −8.5 %)**; stored row 3,638 characters on average
(V12 on the same ids: 5,005, **−27 %**).

Before the run an independent Opus 5 review of the first draft returned 27 findings (1 blocker, 11 major, 15 minor);
all were applied (`../REVIEW_opus5_v13.md`). A four-task dry run on the pre-review text is in `rows_dryrun/`.

## What the run shows

**The over-strictness is gone, measurably.**

| check | result |
|---|---|
| instrument 1 — the four PASS controls are SEED every repeat | 8/8 |
| instrument 2 — leak axis clean on all ten controls | 20/20 rows |
| instrument 3 — 000092 and 006700 are SEED | yes |
| instrument 4 — no row is held back on a note alone | 0 such rows (also schema-enforced) |
| instrument 5 — the three A4 controls are SEED every repeat | 6/6, each with the A4 note **and** a pin list (V12: all six REPAIR) |
| A7-only movers (000595, 001160) | SEED with the A7 note and its skip probe (V12: REPAIR) |
| SEED on the kit | **34 / 70** (V12 PASS on the same rows: 17 / 70) |
| rows V12 held for a note only — is the note still written? | 9/9 (A4 6, A7 3) |

**What is kept still works, with one soft spot.**

| check | result | V12 on the same kit |
|---|---|---|
| 10 known leaks held back, both repeats | **18/20 rows** | 20/20 |
| required leak field named when held | 18/18 | 20/20 |
| wrong-literal B1 controls (000220, 002378) held | 4/4 | 4/4 |
| 18 independently verified movers | 15/18 | 16/18 |
| leak axis identical to the V12 row | 66/70 | — |
| unfair/unstable axis identical to the V12 row | 63/70 | — |
| tier identical between the two repeats | 18/20 | 19/20 |

The two guard misses are the same mechanism, both in repeat 1 and both caught in repeat 2: `task_000241` and
`task_000501` — the generator script setup.sh left behind was labelled `derivation_is_core_step`, so A3 did not fire
and the unchecked core step became an A7 note. The A3 text for this case is V12's, unchanged. The owner's call:
within judge variance, no change.

Mover misses: `000884_a5826af0` (V12 missed it too), `003704` (V12 only reached REVIEW), `005170` (a value-type B1
V12 caught and this run did not; the B1 text for that shape is unchanged).

## Differences from V12 that follow from the new policy (not regressions)

- `task_007102` (the source-grep B1 control) is SEED with an A7/A6 note in both repeats. B1 now fires on a source-text
  grep only when the faithful solution you would write could fail it; V12's finding rested on contrived variants (a
  comment mentioning the forbidden token, a number written in exponent form).
- `flip/000369`: the judge's own recomputation disagreed with the literal, it could not rule out a library-version
  effect, and wrote an unverified literal. That no longer blocks; the reference run decides.
- `003704` and `flip/001565`: a marker string that is byte-present in the application's own source and checked only
  by containment is now recorded as an **A6 note** ("a stub that echoes the marker passes"), where V12 called it A3.
  This follows from the v13 sentence that a fixed output is not a derived value. If the V12 reading is wanted back,
  it is one sentence in A3 ("only a constant printed by instruction.md is exempt; a literal copied out of a readable
  in-image file is V") — not applied.
- `000156` r1 and `004886` r1 carry only their leak field where V12 also listed B1/B3; both are FLAGGED either way.

## Lints

16 of 70 rows carry a lint (V12 production: 28 %): L11 anchors out of range 6, L3 a leak path also excluded 5, L16 pin
to review 2, L15 2, L9 1, L7 1, L1 1. L16 is a request for a human look at a pin list, e.g. three log files whose
names setup.sh builds in a loop and therefore never appear literally in the task text.

## The 650 tasks V12 already judged, under the v13 policy (no re-judging)

`python3 ../crosswalk_v12_rows.py` → `../seed_list.csv`

| | run600 | never-judged 50 |
|---|---:|---:|
| V12 PASS | 188 (31.3 %) | 12 (24 %) |
| **V13 SEED** | **367 (61.2 %)** | **31 (62 %)** |
| V13 FLAGGED | 214 | 18 |
| V13 UNSURE | 19 | 1 |
| SEED with no V12 lint, an ordinary empty run and no pin to review | 284 | 24 |
| … of those with a recorded successful solve | 95 | 0 |

What had held the newly admitted rows back in run600: A7 alone 88, A4 alone 41, A6 alone 24, A4+A7 16, an unverified
literal 7, `env_mismatch` 3.

## Caveats

- One run. No V13 row was independently verified; the movers' ground truth is the Opus 5 verification made for V12.
- SEED is a candidate, not an admitted seed: the empty run and the reference run with reward 1 (README, runner's
  obligations) are what admit it. 398 SEED rows is therefore an upper bound.
- The guard result is 18/20 where V12 had 20/20 on the same ids with the same judge model.
