# V12 half-scale validation (2026-09-18) — run wf_1d95c23e-1be

Final rubric on this line: `v12_prompt.md` 543 lines, sha `68558b02c8678fae96bf66d3f13c4cd665cb08503ae679f7e7326de112601c04`;
`output_schema_v12.json` sha `4ab62ead51756cdcd75aa05224c72c73a1c2a582bad3e6fa9c8f857865169c66` (39 keys, 50 allOf).
Judge `claude-opus-4-8` medium, explicit per call, verified from every transcript (13/13 agents, 70/70 rows, model
clean). Composition per ACCEPTANCE §4: 20 acceptance ids × 2 (guards + FINE controls), 18 verified movers × 1, 12
stratified flippers × 1 = 70. Every judge received the runner's candidate list (`candidates/<id>.json`). 13 batches,
30 min, 2.50M subagent tokens (35.8k per judgment; V10.1: 32.6k). 0 schema-invalid rows, 0 failed batches.
Scoring `score_v12.py` → `score_v12.json`.

## 1. Pre-registered acceptance

| bar | result |
|---|---|
| 10 guards non-PASS, both repeats | **20/20** |
| required floor field named, both repeats | **20/20** (primary path agrees on all 10) |
| pre-registered tier class | **9/10** — 000501 r1 REPAIR / r2 REJECT-PROVEN with `reject_shape: instruction_prints_answer`. The shape is real by the rubric's own definition (instruction.md prints the graded Mean/Variance for a fixed-instance task); the plan pre-registered REPAIR because a test.sh-only fix exists (regenerate values not in the instruction). The rubric does not state which wins when a shape and a fix both apply — the same gap the finishing pass resolved for 000311 by accepting either class; the identical treatment applies here. Not a leak miss and not an over-discard of a clean task |
| 000241, 000311 REJECT-PROVEN via `deliverable_is_leaked_material` | **4/4 rows**, both repeats each — the class is stable for the first time (V10.1 d1 flipped on both) |
| 004886 R5 | `yes`/`unknown` both repeats ✓ |
| A7 add-on (`core_clause_unenforced`) flips between repeats | 4 guards (000311, 000872, 001257, 000501) — secondary flag, tier unaffected |
| ten FINE controls, leak axis clean, both repeats | **20/20 rows** |
| PASS-required 4 (000316, 001104, 000854, 005042) | **PASS ×2 each**; 000316 and 000854 PASS-held (requests) |
| A4 controls (000681, 000927, 000056) | **REPAIR ×2 each** with `expectation_movable` — tier stable (000681 spanned REVIEW/REJECT/REPAIR in V10.1) |
| B1 controls (007102, 000220, 002378) | **REPAIR ×2 each** with `overspecific_check` — 007102 caught both times (source-text trigger; V10.1 d2 PASSed it twice) |
| 18 verified movers | **16/18**; misses: 000884_a5826af0 PASS-held (the percent-encoding / one-read-per-connection rejects-right — missed by V10 and V10.1 d2 as well; the Opus verifier found it only by exercising `requests` locally), 003704 REVIEW (A3 fired; no fix named — REVIEW, not a discard) |
| **over-rejection instrument (ACCEPTANCE §4, four clauses)** | **all four pass**: PASS-required controls PASS every repeat; leak axis clean on all ten controls; 000092 and 006700 PASS; every REJECT-PROVEN names a shape |

## 2. The 12 flippers and the full-run distribution

Flippers: PASS 3 (000135, 000694, 002298), REPAIR 7, REJECT-PROVEN 2 — 000390 (`deliverable_is_leaked_material`:
the minimization deliverable's graded values are byte-present in the shipped service.c) and 004056 (same shape: the
bad-commit deliverable is graded against a build-time golden file). Both shapes read as stated in their `reason`; both
are unverified. **002298 PASS is a lenient miss**: it is one of the six V8 misses the Opus seat verified in the V10
round (config values derivable, the CLI interaction never checked); V12's row excluded `deploy_edge.py`/`config.json`
as `benign` and the CLI as `library_to_wrap`.

Whole run (70): PASS 17 (8 held), REPAIR 42, REJECT-PROVEN 10 (every one with a named shape: leaked material 7,
broken premise 2, instruction prints answer 1), REVIEW 1. Per side: A static_trace 39 / none_found 28 / not_driven 3;
B static_trace 14 / none_found 50 / not_applicable 6 — the per-side split did its job (001110 REPAIR on the B fix with
the A side not driven).

## 3. Lints (22/70 rows)

L11 7 (a per-field anchor out of range or not in tests/test.sh), L7 7 (the global evidence line is not an assertion
line), L13 4 (a supplied candidate left without a disposition — visible, no longer a silent omission), L3 3, L9 1,
L4 1. No L14 (every supplied failing statement was covered — the inventory replaces the self-census that fired L2b on
28/140 V10.1 rows), no L12, no L15. Nothing lint-flagged changes a tier here; L13 rows are the ones to read first if
any row is disputed.

## 4. Against the best-measured baseline (V10.1 draft 1, 50 shared ids)

Agreement 41/50. Moves: 000241 REPAIR→REJECT-PROVEN (pre-registered); 000681 and 001110 REVIEW→REPAIR (right, both
verified); 001565 REJECT-PROVEN→REPAIR (less strict); 000884_a5826af0 REPAIR→PASS-held and 002298 REVIEW→PASS
(lenient misses, both verified defects); 003704 REPAIR→REVIEW; 000390 REVIEW→REJECT-PROVEN and 004056
REPAIR→REJECT-PROVEN (shape-based, unverified).

## 5. Read-out

- Every pre-registered floor bar and all four over-rejection clauses are met on this run — the first version for which
  that is true, and with the discard lever replaced by named shapes the REPAIR/REJECT-PROVEN flips that dominated
  V10.1 are down to one (000501, a stated shape-vs-fix precedence gap).
- What it still gets wrong is in the lenient direction on hard tasks: 000884 (both versions before it too), 002298,
  003704-as-REVIEW. None is on the leak axis; none discards a clean task.
- Unverified: the two new shape discards (000390, 004056). Reading their two rows against the task files is the one
  cheap check left before any corpus-wide discard is trusted; not required for the acceptance.
- Cost +10% per judgment over V10.1 for the candidate list; REVIEW essentially disappeared (1/70), so the output is
  actionable: PASS / REPAIR-with-a-named-fix / REJECT-with-a-named-shape.
- Owner items unchanged: the `requests` base-image probe (8 of the 17 PASS rows are held on it); whether shape (a)/(b)
  should outrank an available fix (one sentence in Step 9 either way).
