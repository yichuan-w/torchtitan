# TMAX audit rubric — CHANGELOG

Every rubric edit gets a line here. Dates America/New_York. "Measured effect" is
verified against the archive; "not yet run" = no judge/phase-3 pass yet.
Paths: $BASE = tb_check/rebench_tmax_recovery_2026-09-02/work; RA = tb_check/reaudit_398.
This file lives at RA/rubrics/CHANGELOG.md; the live delta stays at RA/v7_rubric_delta.md;
rubrics/reference/ holds the frozen V2–V6 prompts only.

## V4 — 2026-08-30 → 08-31 (shipped release's own audit)
- Files: $BASE/tmax-audit-files/v4_prompt.md, v4b2_prompt.md, v4_workflow.js /
  v4b2_a.js / v4b2_b.js / v4_disputed.js; outputs v4_sample.jsonl (100),
  v4_batch2.jsonl (298), v4_disputed.jsonl (180).
- What it asked: full-coverage single-verdict audit of the SHIPPED 398 (100
  sample + 298 remainder) plus 180 previously-disputed = 578; sole rubric delta
  over V3 = a two-question decision procedure for `unenforced_requirement`.
- Why: first audit that covered every released task, not a sample.
- Measured effect: on the 398 → CLEAN 123 / IVM 171 / VTW 58 / AL 34 / OTHER 8 /
  IEM 2 / IAM 2. 123/398 = 30.9% CLEAN. (On the 180 disputed: CLEAN 5.)

## V5 — 2026-08-31 (re-audit of V4's CLEANs)
- Files: $BASE/tmax-v5-v6/v5_core.md, v5_prompt.md, v5_workflow.js, v5_recheck.jsonl (123).
- What it changed: re-judged the 123 V4-CLEAN tasks; added MUTABLE-GROUND-TRUTH,
  task_kind (fixed-instance vs general-program), artifact-identity, and a
  stricter `reference_guarded` (islink/realpath/compiled-source no longer count).
- Why: V4 had no check for mutable ground truth or artifact identity — the two
  most common real defects.
- Measured effect: 123 → CLEAN 38 (MGT 39 / CLEAN 38 / VTW 24 / IVM 20 / AL 2).
  Only 38 of 123 survived; 8 of 12 previously-shipped `final_clean` are no longer PASS.

## V6 — 2026-08-31 (two-axis, latest pre-V7)
- Files: $BASE/tmax-v5-v6/v6_core.md, v6_prompt.md, v6_cal_workflow.js /
  v6_b1_workflow.js, v6_cal.jsonl (12), v6_b1.jsonl (50), v6_old_policy.tsv.
- What it changed: replaced the single verdict with two orthogonal axes
  (soundness × exploitability) + a PASS/REPAIR/REVIEW/REJECT-PROVEN tier + an
  anti-over-rejection burden of proof + a new hunt for `rejects-right`.
- Why: V4/V5 over-rejected and never separated "reward is wrong" from "who can
  trigger it"; across 578 prior judgements `rejects-right` was recorded once.
- Measured effect: b1 50 tasks → REPAIR 23 / REJECT-PROVEN 18 / REVIEW 5 / PASS 4;
  soundness accepts-wrong 40 / sound 5 / rejects-right 5. Calibration 10/12 tier-hit
  (2 under-graded REJECT-PROVEN→REPAIR). Never re-run over the full 398.

## V7-delta v1 — 2026-09-02 (soundness axis + repro gate)
- Files: RA/v7_rubric_delta.md (D0–D7 initial), on top of v6_core.md.
- What it changed: soundness is the ONLY elimination axis (exploitability sets
  severity, not tier); every non-PASS verdict must carry an executable
  reproduction (D4, else → REVIEW); REPAIR must name a scriptable `verifier_fix`
  (D5); the judge is given the harness-root contract (D1) and the phase-0 facts
  (D2); stale v4 header + nonexistent example id removed (D0).
- Why: phase-0 proved the agent runs as root/unsandboxed, so exploitability-based
  elimination is moot; unproven leaks must be driven, not asserted.
- Measured effect: not yet run (design).

## V7-delta v2 — 2026-09-02 (IVM sub-rule, controls, anti-anchoring)
- Files: RA/v7_rubric_delta.md (D3 IVM sub-rule, D6 `unenforced_cosmetic`,
  D7 rewrite, D2 anti-anchoring line).
- What it changed: a `sound` verifier with a load-bearing unenforced instruction
  requirement → REPAIR (cosmetic → PASS + `unenforced_cosmetic`), via V4's
  two-question procedure; D7 control set reconciled into three sourced classes
  (proven-hackable / guarded / no-reference) against tmax_prov_known.jsonl +
  prov400_hack + v6_cal; prior verdict columns delivered only AFTER the judge
  writes its own verdict.
- Why: user ruled instruction constraints must not be learnable-ignorable; Part-A
  control list was internally inconsistent; avoid anchoring on prior verdicts.
- Measured effect: not yet run (design).

## V7-delta v3 — 2026-09-02 (exploit cost ladder)
- Files: RA/v7_rubric_delta.md (new D8; D3 tier row, D4 + D6 fields).
- What it changed: D8 SIMPLE/MEDIUM/HARD ladder. SIMPLE (≤5 generic lines, no
  task understanding) → REPAIR if a bounded fix exists else REJECT-PROVEN;
  MEDIUM (read oracle/corpus + reimplement = doing the task) → PASS + tag
  `shortcut_medium`; HARD (saves one check, rest is real work) → PASS + tag
  `shortcut_hard`, RL-monitored; `rejects-right`/`unstable` always REPAIR.
  REJECT-PROVEN reserved for SIMPLE-no-fix. Added repro_cost / repro_lines /
  shortcut_tag output fields.
- Why: phase-3 showed the strong solver reads-and-reimplements (never a wrap), so
  discarding a task over a MEDIUM/HARD shortcut would throw away legitimate work;
  only SIMPLE holes justify REJECT-PROVEN.
- Measured effect — phase-3 HARNESS measurement (tools/repro_exec.py container runs;
  files RA/repro/batch13_reward_table.tsv, RA/repro/batch6_reward_table.tsv, raw
  records batch13.jsonl / batch6.jsonl), NOT the reviewer's static verification;
  empty baseline 0 on every task:
  batch13 — SIMPLE accepts-wrong reward==1 on 000022, 000197, 004863, and 000984
  with the tracer's prep (the TSV one-liner had a wrong output path → 0).
  batch6 — 8/9 reward==1: the six corpus/venv stubs 000121, 000173, 000353,
  000718, 000753, 001149 and the two fixed rows 000165, 000959; 003462 timed out
  (test.sh rc 124 at both the 150 s and 600 s caps, prep rc 0) ⇒ unstable/REPAIR.
  Overall: 14 static exploitables → 12 container-confirmed SIMPLE, 1 refuted
  (000381: container repro scored 0 → HARD by evidence, tier PASS + shortcut_hard —
  settled here and in the ledger), 1 unstable (003462 → REPAIR).

## V7-delta v3.1 — 2026-09-02 (unstable routing made explicit)
- Files: RA/v7_rubric_delta.md (D3 tier table REPAIR/REVIEW rows, D8 closing paragraph);
  CHANGELOG moved to RA/rubrics/CHANGELOG.md.
- What it changed: D3 REVIEW row now reads "suspected-but-undemonstrated
  instability" instead of bare `unstable`; REPAIR row and D8 say "diagnosed
  `unstable` ⇒ REPAIR always" with 003462 as the worked example. No ladder change.
- Why: v3 listed `unstable` under both REPAIR and REVIEW, leaving the routing
  ambiguous; batch6 gave the first demonstrated instability to anchor the rule.
- Measured effect: wording only; not yet run.

## V7-delta v3.2 — 2026-09-02 (003462 caps wording, CHANGELOG attribution)
- Files: RA/v7_rubric_delta.md (D8 closing example); RA/rubrics/CHANGELOG.md (v3 effect line).
- What it changed: D8 example now states 003462 timed out at BOTH the 150 s and
  600 s caps (was "600 s twice"); the v3 measured-effect line attributes the
  batch6 8/9 to phase-3 container runs and cites repro/batch6_reward_table.tsv
  (and repro/batch13_reward_table.tsv for the first batch), not the reviewer's
  static verification. Reviewer (tmax-log-reviewer) APPROVED v3.1 with these two
  non-blocking notes; applied in commit a7eb8fc, entry regularised here.
- Why: judges read the D8 example — the evidence must be stated exactly; every
  measured number must be tied to a file.
- Measured effect: wording + attribution, not yet run.
- Independent confirmation (tmax-log-reviewer, adversarial read of repro/batch6.jsonl
  tails, same day): 8/9 reward==1 are genuine pytest passes in test.sh (no prep
  touches /logs/verifier), empty baseline 0 on all 9; 003462's tail shows test 1
  passing and the 1000× `go run` test stalling to the 600 s cap — unstable/REPAIR stands.

## V7-delta v3.3 — 2026-09-02 (Fable readiness review §6 fix list)
- Files: RA/v7_rubric_delta.md (D0, D2, D3, D4, D5, D6, D7, D8, new D9); README.md links;
  repro/batch13_reward_table.tsv + batch6_reward_table.tsv gain `repro_lines`.
- What it changed — BLOCKING: (B1) PASS = `sound` OR accepts-wrong rated MEDIUM/HARD with
  `shortcut_tag`+`repro_cmd` mandatory; D3 opening says soundness plus D8 cost. (B2)
  load-bearing unenforced requirement added to the D8 not-on-ladder list; third D4 repro
  format (clause-violating solution, expected reward==1 (unenforced requirement)); D5 says
  behavioural/toolchain check, never a token grep. (B3) `unstable` repro contract: expected
  "no reward.txt / rc 124 at cap N s" or "reward varies over N runs", in D4 and the D6 enum.
  NON-BLOCKING: D2 "exactly these 8 columns; m_*, prior verdicts, HF labels withheld";
  repro_cmd required when shortcut_tag ≠ null; "≤5 shell STATEMENTS" + repro_lines backfilled
  for the 12 confirmed repros (statement count = split on newline/;/&&/||, pipelines = 1);
  D7 "12 v6_cal (6 in the 398, 2 security → Opus judge)", "adds" not "extends", re-derive
  expected V7 tiers, "via the symlink vector" restored; agent_is_root "per gate2; RL harness
  unverified (OQ-14)"; phase-3 result overrides repro_cost, a repro scoring 0 ⇒ REVIEW;
  base-image-dependent calls ⇒ REVIEW + request empty/gold run; confidence<8 applies to PASS;
  "tier authoritative, verdict a label"; stale id note (v5_prompt.md:24 only). 000381 settled
  as PASS + shortcut_hard (container repro 0 ⇒ HARD by evidence). NEW D9 quotes ledger D-7
  (1–6) and D-8 verbatim; `security_union` column added to the two tracked tables.
- Why: independent readiness review (reviews/v7_readiness_fable_20260902.md) found three
  tier-path contradictions that would give one record two tiers, and no routing text
  outside the ledger.
- Measured effect: not yet run (no judge call yet). Backfill finding: statement counts
  of the 12 container-confirmed repros (split on newline/;/&&/||, quoted bodies masked,
  pipelines = 1): 000022 2, 000121 2, 000165 4, 000173 4, 000197 2, 000353 2, 000718 6,
  000753 4, 000959 3, 000984 (tracer prep) 10, 001149 6, 004863 1 — 9/12 within the ≤5
  SIMPLE bound, 3/12 above it (000718, 001149, 000984) although none needs task
  understanding. The bound is a ceiling on generic lines, not the definition; D8's
  "needs no task understanding" clause decides, the count is recorded for audit.
- Amended (lead, after the Opus 5 re-verification of 000024): D8 SIMPLE now names
  "the verifier invokes an agent-owned build/test target (make/npm/script) and trusts
  its exit code or a log it writes"; D3 now reads "exploitability is recorded for
  triage; the tier ceiling comes from the D8 repro cost". Prompt re-assembled.

## V7 prompt v1 assembled — 2026-09-02
- Files: RA/rubrics/v7_prompt.md (generated), RA/tools/assemble_v7_prompt.py (the
  deterministic assembler; re-run after any delta change).
- What it is: readiness review §1 steps 1–5 — D1 first; v6_prompt.md:1-12 with task paths
  rewritten to scratch/tasks/<id>/; v6_core.md:11-23; D3 replacing 25-57 and 249-260;
  85-95 and 165-171 rewritten to "privileged-test-only no longer changes the tier"; 324-329
  (`reference_guarded`) replaced by the D1(b) definition + wrapper caveat; ONE merged JSON
  block (v6 313-364 ∪ D6 fields) with "exactly this" reissued; then D2, D4, D5, D8, D7, and a
  recording section with the V7 output path placeholder; 379-384 rewritten.
- Measured effect: not yet run.

## V7-delta v3.4 — 2026-09-02 (log-reviewer review of v3.3 + assembled prompt)
- Files: RA/v7_rubric_delta.md (D4 repro_lines caveat, D6 rename + repro_cmd null rule, D7
  calibration correction); RA/tools/assemble_v7_prompt.py (FIX 1 rename, FIX 2, FIX 4);
  RA/rubrics/v7_prompt.md regenerated (574 lines).
- What it changed: FIX 1 `checks.artifact_identity_bound` → `checks.artifact_identity_unbound`
  (name AND polarity now match the `defects` flag; historical `defects` key kept). FIX 2
  `repro_cmd` may be null only for a PASS with shortcut_tag null OR a REVIEW, with the reason
  stated — removes the schema contradiction D4's downgrade-to-REVIEW created. FIX 3 D7: the
  six v6_cal tasks in the 398 are named; task_000022's v6_cal_expected "REVIEW or PASS
  (empirically defended)" is FALSE by measurement (batch13: 2-statement validator.go rewrite →
  reward 1, empty 0) → expected V7 tier REPAIR; 000001/000128 carry the same untested
  "defended" provenance → probe before use; 006224/000908 named as the non-security
  SIMPLE-class controls. FIX 4 the inherited "578 judgements … recorded exactly once"
  statistic (unreproducible: `mismatch_direction` empty in every V2–V4 record) replaced by
  the sourced statement. Caveat: `repro_lines` is gameable (a one-statement printf can hide a
  reimplementation) — D8's "no task understanding" clause decides, the count is advisory.
- Why: reviewer verdict on v3.3 — assembly clean (one tier table, no spliced V6 contradiction),
  but the polarity flip breaks downstream joins, the null rule manufactured schema-violating
  records, and a false-premise control would train the judge to under-call SIMPLE.
- Measured effect (phase-3 container runs, tools/repro_exec.py): flake probe (gold ×5 each,
  --cpus 0.5, 2 contained busy loops; repro/flake.jsonl): 000097 5/5 reward 1, 000437 5/5,
  000984 control 5/5 — no instability shown at half CPU under contention (7–9.5 s/run, no
  timeouts); the "suspected unstable" label on 000097/000437 is not supported by this probe.
  batch7 (repro/batch7.jsonl): 000024 Opus-5 4-statement repro → reward 0 (prep ok, empty 0),
  tail handed to the reviewer. batch8 (repro/batch8_cal_probe.jsonl): 000001 and 000128 empty
  0 / gold 1 — gate-1 property holds; their "defended" claim still lacks a repro probe.
  Judge runs: none yet.
- Aligned to the lead's rulings (same day): D7 000022 expectation restated verbatim as
  "accepts-wrong SIMPLE ⇒ REPAIR (verifier_fix: verifier must not trust an agent-writable
  checker), evidence repro/batch13_reward_table.tsv empty 0 / repro 1"; 000001/000128 "V6
  claims defended; NOT re-measured for the exploit (batch8: empty 0 / gold 1 only); do not
  use as positive controls until phase 3 runs the reviewer's repros"; `repro_expected` gains
  "none: <reason>" for the REVIEW case (D4 + D6 + JSON say the same); the withdrawn statistic
  is footnoted in the prompt ("Statistic withdrawn: not reproducible from the archived
  records"). Prompt regenerated (580 lines); delta 262 lines.
- batch7 row 2 (repro/batch7.jsonl, label opus5-reverify-fix2): the reviewer's corrected
  5-statement repro for task_000024 → reward 1 (prep 0, test_rc 0); the first attempt's 0 was
  a transcription error (missing `tests/` path segment) that phase 3 caught before it became a
  verdict — the D4 gate working as designed. 000024 now: accepts-wrong, SIMPLE, REPAIR
  (verifier_fix: build with a pristine Makefile and run the verifier's own regression binary
  instead of trusting `make test` + an agent-written log); the git-history token leak half was
  already confirmed by the first run (test 1 passed).

## V7-delta v3.5 — 2026-09-02 (ruling D-16: calibration expectations from phase 3 only)
- Files: RA/v7_rubric_delta.md (D7 calibration paragraph rewritten); rubrics/v7_prompt.md
  regenerated.
- What it changed: the V6 calibration set is withdrawn as a source of expected tiers.
  Expected tiers for every calibration task come ONLY from phase-3 records in this repo:
  000022, 000128, 000024 ⇒ accepts-wrong SIMPLE ⇒ REPAIR (empty 0 / repro 1; verifier_fix =
  never trust an agent-writable checker/input, hash-pin or carry a pristine copy at grade
  time); 000001 ⇒ expected tier PENDING (empty 0 / gold 1; first repro attempt scored 0 —
  tail with the reviewer); 006224 / 000908 ⇒ V6 REJECT-PROVEN kept only once a phase-3 repro
  exists, else pending (repros requested); 000400 (security) same rule, Opus judge only; the
  11 behavioural controls unchanged. New line: "a calibration expectation without a phase-3
  record is not an expectation".
- Why: the one "defended" V6 claim that was measured (000022) was false; expectations must
  be as evidence-bound as the verdicts they calibrate.
- Measured effect (repro/batch8_controls_reward_table.tsv, 7 rows, 4 s): 000001 empty 0 /
  gold 1 / repro 0; 000128 empty 0 / gold 1 / repro 1 (6 statements); 000024 repro_fixed 1.
  Deviation from the ruling's expectation: 000001 was expected REPAIR but its repro did not
  score, so it is recorded as pending, not REPAIR. Judge runs: none yet.
- Amended (lead ruling, same day): CLEARING rule — a suspicion-based REVIEW (suspected
  instability, suspected base-image dependence, or any non-PASS without a repro) is cleared to
  PASS when a pre-registered phase-3 probe fails to reproduce the defect; probe, parameters
  (runs, cpu limit, contention) and reward vector recorded in the new `cleared_by_probe`
  field (D3 note, D4, D6, merged JSON). Phase-3 evidence in either direction overrides the
  static call. Evidence: 000097 and 000437 cleared by the flake probe, gold ×5 at --cpus 0.5
  with 2 busy loops, 5/5 reward 1 each (repro/flake.jsonl); the reviewer withdrew its
  suspicion. Prompt regenerated.

## V7-delta v3.6 — 2026-09-02 (D7 expectations from batch9_cal + batch8 fix; controls backlog)
- Files: RA/v7_rubric_delta.md (D7 calibration list); rubrics/v7_prompt.md regenerated;
  tools/repro_exec.py gains mode `gold_patch` (gold extract, then repro_cmd, then tests).
- What it changed: D7 now lists every in-398 calibration task with a phase-3-derived tier:
  000022 / 000128 / 000001 / 000024 ⇒ accepts-wrong SIMPLE ⇒ REPAIR; 000908 ⇒ SIMPLE ⇒ REPAIR
  (verifier_fix: behavioural metric check); 006224 ⇒ unenforced artifact ⇒ REPAIR
  (verifier_fix: compile+run analyze.c; V6's REJECT-PROVEN was an over-call); 000400 ⇒
  rejects-right ⇒ REPAIR (verifier_fix: behavioural truncation test, not a token grep; Opus
  judge only). "Controls backlog" added for the six non-398 v6_cal ids (4 proven-hackable to
  re-measure under repro_exec; 002119 caveat; 000252 flake probe).
- Why: ruling D-16 — expectations only from phase-3 records; batch9_cal and the 000001 fix
  supplied the missing records.
- Measured effect (repro/batch9_cal_reward_table.tsv, 9 rows, 7 s; repro/batch8_controls_
  reward_table.tsv row 8): 006224 empty 0 / gold 1 / gold_patch 1 (1 statement); 000908 empty
  0 / gold 1 / repro 1 (5 statements); 000400 empty 0 / gold 1 / faithful-fix repro 0
  (8 statements, expected 0 = rejects-right demonstrated); 000001 corrected repro
  (opus5-mgt-degenerate-fix, 2 statements) → 1 — the first attempt's 0 was again a reviewer
  transcription slip caught by the D4 gate. All four lead expectations met. Judge runs: none yet.
- Note (reviewer sign-off on the 59a543e prompt: APPROVED, all fixes verified line by line):
  006224 is the first case where V6 OVER-called (REJECT-PROVEN for a bounded, fixable
  existence-only check; the numeric check binds against a reference-internal curve, not the
  agent-writable CSV) rather than under-called — evidence that D-16 was right to withdraw the
  V6 expectation set wholesale instead of patching it per task. Open point for the lead:
  000908 — reviewer holds V6's REJECT-PROVEN (12/12 assertions pass on hollow stubs); the
  ruling applied here is REPAIR with verifier_fix = behavioural metric check (D8: SIMPLE with
  a bounded fix ⇒ REPAIR). Both agree it is accepts-wrong SIMPLE with repro reward 1.

## V7-delta v3.7 — 2026-09-02 (Fable pass-2 blocker: calibration out of the judge prompt)
- Files: RA/v7_rubric_delta.md (D3 header, D4, D6, D7, D8), RA/tools/assemble_v7_prompt.py,
  rubrics/v7_prompt.md regenerated (557 lines), NEW rubrics/v7_calibration.md (scorer-only, generated
  from D7), repro/batch9_cal_reward_table.tsv (+faithful_confirmed_by), reviews/v7_readiness_fable_pass2_20260902.md.
- What it changed (BLOCKING P1): the assembler no longer emits D7 into the judge prompt — D7 is
  written to rubrics/v7_calibration.md (expected tiers + phase-3 citations, never shown to a judge);
  every in-398 task id in the D3/D4/D8 examples is anonymised (mechanism described; the ids stay in
  the CHANGELOG entries cited); new post-conditions: no `task_0…` and no six-digit prefix anywhere in
  the prompt, no D7/v6_cal text, placeholders present. Ids that were in the prompt and are now only
  here: D3 clearing example = 000097, 000437 (flake 5/5); D4 example = 000381 (HARD), 000984 (path
  typo); D8 SIMPLE examples = 000022, 000165, 000753, 000121/000173/000353/000718/001149/000959;
  D8 HARD example = 000381; D8 unstable example = 003462; D7 = 000022, 000128, 000001, 000024, 000908,
  006224, 000400 and the 11 behavioural controls.
- Non-blocking, all applied: `evidence` ≤200 chars verbatim + new `reason` ≤40 words (D6 + JSON);
  JSON block carries task_id / agent / rubric_version / revised_after_priors; recording section:
  {V7_OUTPUT_PATH} marked QUARANTINED (never committed, Opus log-reviewer seat only), FIRST line per
  task_id is the verdict of record, revisions cite it; {TASK_ROOT}/<task_id> and {TASK_IDS}
  placeholders replace the cwd-relative path and "ids listed at the end"; `repro_expected` tokenised
  (reward==1 | reward==0 | reward==1-unenforced | no-reward-timeout | reward-varies | none: <reason>);
  D8 SIMPLE "typically ≤5 statements; the class is defined by no task understanding"; D3 header
  "soundness; tier ceiling = D8 exploit cost"; D-16 cited to CHANGELOG v3.5 until the ledger carries
  it; source list = repro/*_reward_table.tsv (batch13, batch6, batch8_controls, batch9_cal),
  batch7.jsonl, flake.jsonl, batch10/11 when run; editorial V6-directed suffixes stripped by the
  assembler (the judge never saw V6).
- D7 content changes (rulings): 000908 → "REPAIR (provisional): verifier_fix = test.sh must execute
  the emitter and assert real metric content; if not expressible as a bounded test.sh diff (≤ ~40
  lines, no task redesign) it becomes REJECT-PROVEN" — reviewer concurred; open point CLOSED.
  000400 → rejects-right evidence must cite gold 1 AND the reviewer-confirmed faithful variant
  (batch9_cal label opus5-rejects-right-substr; `faithful_confirmed_by` column added) — a bare reward
  0 is not evidence. GUARDED class replaced by "SYMLINK-BLOCKED (mechanism unverified)" for
  001957/002223 with the reviewer's correction quoted verbatim (its earlier islink/realpath claim came
  from a token scan; the asserts sit in agent-deliverable subtests, not around the oracle-compared
  deliverable). Ruling D-19: D-16 extends to tmax_prov_known.jsonl — no expectation inherited from
  its verdict field. All 11 behavioural controls: "expected tier pending batch10/batch11", exempt from
  the miscalibration rule until measured; images for all 11 resolved in unified/image_map_all.json.
- Why: independent pass-2 review (reviews/v7_readiness_fable_pass2_20260902.md) — 20 of the 398
  were pre-labelled inside the prompt (5 of them security_union=1, and the prompt goes to Fable
  seats), defeating D2's anti-anchoring and D-7 point 4.
- Measured effect: none yet (no judge run). batch10/batch11 container runs in progress; their
  records will fill rubrics/v7_calibration.md in the next revision.

## V7-delta v3.8 — 2026-09-02 (behavioural controls measured; D-19)
- Files: RA/v7_rubric_delta.md D7 (→ rubrics/v7_calibration.md, scorer-only, regenerated);
  repro/batch10_controls.{tsv,jsonl}, batch10_controls_reward_table.tsv, batch11_noref.{tsv,jsonl},
  batch11_noref_reward_table.tsv; tools/repro_exec.py (flake-summary console line).
- What it changed: the behavioural-controls section is now a MEASURED table (task_id,
  security_union, image, empty, gold, shortcut rows with labels + rewards, expected tier, source);
  ruling D-19 — D-16 extends to tmax_prov_known.jsonl, no expectation inherited from its verdict
  field. The "NO-REFERENCE / sound task with nothing to leak stays PASS" bullet is DELETED.
  001957/002223 stay "SYMLINK-BLOCKED (mechanism unverified)" pending the reviewer's tail read.
- Why / finding: prov_known's classes were a probe-coverage artefact — it only tried a symlink onto
  /app/*oracle*. Measured (batch10 + batch11, 178 s wall, 45 records, 0 leftover containers):
  6 PROVEN-HACKABLE controls (000877, 003036, 004869, 001078, 000601, 003033): empty 0 / gold 1 /
  recorded symlink 1 (1–2 statements) — confirmed on our harness. 3 "NO-REFERENCE" controls (000164,
  000529, 001069): empty 0 / gold 1 / oracle symlink or exec-wrapper 1 — i.e. hackable, not clean.
  Diagnostics (all reward 0, prep ok, tails with the reviewer): 001957 symlink 0 (expected) + wrapper 0;
  002223 symlink 0 (expected) + wrapper 0 (no gold: held-back has only its rollout); 002119
  plaintext-copy 0 + symlink 0, gold 1. 000252 flake gold ×5 at 0.5 cpu + contention: 5/5 reward 1,
  stable. Executor: gold now also resolves from gold_held_back.tar; batch TSV rows with mode=flake run
  the flake routine; batch10 exited rc 1 only because the console formatter choked on the flake
  summary AFTER all 36 records were written — fixed.
- Measured effect: 9 of 11 behavioural controls now carry a phase-3 record with reward 1 on a
  ≤2-statement shortcut; 2 pending tail read. Judge runs: none yet.

## V7-delta v3.9 — 2026-09-02 (Fable pass-3 fixes; calibration file only)
- Files: RA/v7_rubric_delta.md D7 → rubrics/v7_calibration.md regenerated; tools/assemble_v7_prompt.py
  (editorial residues); tools/tournament_score.py (disjunction hit rule); reviews/v7_readiness_fable_pass3_20260902.md.
- Pass-3 verdict (reviews/…pass3): judge prompt READY, zero blocking; independent regeneration
  byte-identical; id grep 0/0/0. Fixes applied here, all in the scorer-only file: legend "sec=1 ⇒
  Opus-xhigh judge only (D9)" with 003036 and 004869 flagged; 000252's security_union resolved
  to 0 from the parquet/tb2 label sources (was "?"); D-16/D-19 cited to the ledger directly
  (it now carries D-14…D-22); stale "controls backlog" paragraph and the old PROVEN-HACKABLE/
  SYMLINK-BLOCKED miscalibration rule deleted (the measured table + new rule stand alone); scorer
  rule for the disjunctive expectation: HIT = accepts-wrong AND repro_cost simple AND tier ∈
  {REPAIR, REJECT-PROVEN} (also coded in tournament_score.py); new "PASS controls" subsection —
  none fully measured yet (flake-cleared pair, 000252, 001957/002223 pending tail read), so the
  tournament cannot yet score over-rejection and must say so. Assembler strips the last V6
  editorial residues ("(NEW sub-rule)", "replaces the V6 tier table", "the V6 eight labels",
  "The V6 tier table is replaced"). batch10/11 reward tables now tracked (284103f) — closes the
  pass-3 addendum's "unsupported expectations" point.
- Measured effect: prompt text unchanged in substance (557 lines, id-free); judge runs: none by me.

## V7-delta v3.10 — 2026-09-02 (rulings D-24, D-21, D-26; calibration file only)
- Version bookkeeping for the scribe: v3.8 = b77b2b4 (D-19 measured table); the Fable pass-3
  one-liners landed as v3.9 = 18acb3b; the lead's "v3.9 (D-24)" instruction is this entry, v3.10.
- Files: RA/v7_rubric_delta.md D7 → rubrics/v7_calibration.md regenerated; judge prompt unchanged.
- D-24 (reviewer tail read of the batch10 diagnostic rows): 001957 and 002119 = NO GUARD — the
  symlink/copy PASSED the fuzz-equivalence test and scored 0 only because the tasks demand
  unrelated extra deliverables ⇒ oracle vector = accepts-wrong SIMPLE, expected tier REPAIR;
  002223 = INCONCLUSIVE (both vectors fail an unrelated directory requirement before the fuzz test)
  pending a decisive row; the SYMLINK-BLOCKED label is deleted entirely; tmax_prov_known.jsonl's
  verdict field is declared unusable metadata (all three classes failed inspection; only its recorded
  symlink measurements survive, re-measured 6/6). 000252: cleared_by_probe (flake 5/5) ⇒ expected PASS.
- D-21: first measured unenforced-requirement ⇒ REPAIR records — batch12: 000213 unenforced-script
  solution 1, 000333 wrong-language solution 1 (empties 0, golds 1; repro/batch12_ivm_reward_table.tsv).
- D-26: PASS-controls section with placeholders "expected tier PASS — pending batch14" for 000097,
  000437, 000252, 000443, 004160 (reviewer drafting batch14_pass.tsv: gold 1 + best-effort shortcut 0).
- Table citations now name the tracked files exactly (batch10_controls_reward_table.tsv,
  batch11_noref_reward_table.tsv; both committed in 284103f).
- F-3 (tools/derive_f3_usable_now_holes.py, adopted from the scribe, canonical): see
  repro/f3_derivation_counts.txt for the current printed counts — quote them whenever F-3 is cited.
- Measured effect: 11 behavioural controls now carry a measured expectation (9 reward-1 + 2 NO GUARD
  ⇒ REPAIR), 1 inconclusive, 000252 PASS; 2 IVM REPAIR records; PASS controls pending. Judge runs
  by me: none (the lead's small V7 run is separate; its judgement files are gitignored).

## V7-delta v3.11 — 2026-09-02 (batch13b closes 001957/002223; answer key; scorer v2)
- Files: RA/v7_rubric_delta.md D7 → rubrics/v7_calibration.md regenerated; NEW rubrics/v7_calibration.tsv
  (machine-readable answer key, 22 rows; assembler asserts every key id appears in the calibration text);
  tools/tournament_to_batch.py v2, tools/tournament_score.py v2 (scripts review, tmax-log-reviewer);
  repro/batch13b_controls.{tsv,jsonl,_reward_table.tsv}.
- Measured (batch13b, 22 s): 001957 completion-symlink 1 (10 statements), gold 1, empty 0; 002223
  decisive-symlink 1 (6) and decisive-wrapper 1 (7), empty 0 (no gold exists) ⇒ both accepts-wrong ⇒
  REPAIR; 002223 leaves INCONCLUSIVE; 002119 keeps NO GUARD ⇒ REPAIR with no full repro by design.
  All 11 behavioural controls + 000252 now have a measured expectation (11 accepts-wrong, 1 PASS).
- Scorer v2 (review fixes): verdict-of-record = first judge line per task_id, revision records separated;
  truth = the answer key only (no re-derivation → no "clean by accident", F-20); soundness P/R + tier ∈
  expected set (+ SIMPLE-control rule); gates derived from the key (accepts-wrong controls non-PASS with
  repro; probe-cleared controls not called unstable) — the stale symlink gate is gone; per-bucket
  breakdown with --bucket-note (F-19); --results-jsonl writes a reward table; --extra paths resolve
  against cwd then repo and fail loudly; D-22 ivm_lead_recall with scan_version=2 parsed from `note`
  (36 rows) and the F-18 blind spots stated. to_batch v2: `timeout`/`varies` tokens → repro + flake
  rows; `expected_reward` column; several --judgements files; output defaults under judge/ (judge-written
  repro_cmd may embed test literals — not committed).
- Measured effect: no judge scored yet by me; the lead's small V7 run will be the first input.

## V7-delta v3.12 — 2026-09-02 (batch14 PASS controls; answer key emitted from D7; scoring v2 aligned to rulings)
- Files: RA/v7_rubric_delta.md D7 (fenced `calibration-key` block = single source; PASS-controls
  measured), rubrics/v7_calibration.md + rubrics/v7_calibration.tsv (now EMITTED by the assembler from
  the block: task_id, security_union, expected_soundness, expected_tier_set, expected_repro_cost,
  cleared_by_probe, source_row — 27 rows), tools/assemble_v7_prompt.py, tools/tournament_to_batch.py,
  tools/tournament_score.py, NEW tools/tournament_reward_table.py, repro/batch14_pass.{tsv,jsonl,_reward_table.tsv}.
- Measured (batch14, 38 s): gold 1 on 000252, 000097, 000443, 004160, 000437; best-effort shortcut 0
  on all five (000437's hollow-build shortcut was EXPECTED 1 by the reviewer and scored 0 — PASS control
  by the ruling, tail handed over). Five PASS controls enter the key with the verbatim caveat: "a PASS
  control is only as strong as the adversary who wrote its shortcut; gold 1 + best shortcut 0 makes it
  usable as an over-rejection control, not a certificate of soundness." Finding: agent-owned build
  chain = recurring static-review blind spot (000024 scored 1 on it, 000437 did not; both needed a
  container to settle).
- Scoring v2 aligned to the lead's rulings on the scripts review: answer key emitted (not hand-kept);
  to_batch unstable token → gold + flake + repro rows, default output under tracked repro/judge_batches/
  (judge-written shell, not task text); score: verdict of record = first line, revisions applied only
  into a separate after_priors column, positive class also reported as (soundness≠sound OR tier≠PASS)
  against the key so a correct PASS+shortcut_tag is a true negative, gates from expected_tier_set only
  (stale symlink gate removed), per-bucket with --bucket-note, D-22 ivm_lead_recall (scan_version, F-18);
  tournament_reward_table.py turns any batch JSONL into the reward table. Fable pass-4 stale phrases fixed.
- Statement count vs the D8 clause, third case: 001957 scored 1 with a 10-statement repro needing no
  task understanding — third confirmed case (after 000718 6, 001149 6) where the statement count and
  the "needs no task understanding" clause disagree; the clause was right every time; count stays
  advisory. Scoring note: batch14's table compares each row against the TSV's expected_reward (4
  shortcut rows expect 0 by design — a 0 there is the desired result, not an anomaly).
- Measured effect: the calibration set now has 22 defect expectations + 5 PASS controls, all with
  phase-3 records; over-rejection is scoreable. Judge runs by me: none.

## V7-delta v3.13 — 2026-09-02 (ruling D-30 and additions; prompt regenerated)
- Files: RA/v7_rubric_delta.md (D3 alignment sub-rule, D3 BOTH-directions reminder, D4 wording, D8
  ladder note, D7 key rows), rubrics/v7_prompt.md + v7_calibration.{md,tsv} regenerated,
  tools/tournament_score.py, tools/README_tournament.md.
- D-30 (1): a load-bearing unenforced requirement is labelled soundness `accepts-wrong` (not `sound`),
  repro_expected reward==1-unenforced, repro_cost rated; D4's "accepts-wrong only" wording updated; D8
  says it is on the ladder with tier always REPAIR (bounded fix = enforce the clause). Additions: D3 —
  "when you record accepts-wrong you must still fill rejects_correct_variant and mismatch_direction; a
  task can be both; an empty defects block with a non-sound soundness is invalid output"; D4 — "a repro
  that relies on values discoverable only from reward feedback (test.sh-only constants) is NOT
  rollout-visible and must not be used to rate repro_cost".
- Key (D-30 2–5): 000400 expected_soundness rejects-right|accepts-wrong (accept either when
  mismatch_direction==both; judge's file-only repro added to the small-run batch); 002119
  expected_tier_set REPAIR|REVIEW; 001957/002223 REPAIR|PASS with the reasoning in source_row (oracle
  vector SIMPLE; unrelated deliverables inflate statement count; PASS+shortcut_hard = partial); 000437
  PENDING (PASS|REPAIR) until the reviewer's fix2 rerun (batch14b) — not a PASS control yet.
- Scorer: expected_soundness as a set; bucket note keyed on the agent label's trailing integer
  ("V7-bucket-k") or `bucket`; rubric printed from rubric_version OR rubric; per-task verdict list
  (hit/partial/miss/unmeasured) with the judge's repro reward; v3.7 IVM caveat flagged per task
  (expected-by-construction, not judge error). README scoring section rewritten for v2.
- Small-run caveat (D-30 8): the run was judged under prompt v3.7, so 000213/000333 soundness misses
  are expected-by-construction and are reported separately.
- Measured effect: none new here; the full small-run batch is running.
- Addendum (D-32, reviewer confirmation): the 13 holes first measured by the small run (000034, 000112,
  000127 [security]; 000128_5892b832, 000015, 000091, 000121_264aae5e, 000311, 000347, 000510, 000580 ur,
  000635 ur, 000729) are promoted from candidate to key rows — accepts-wrong, {REPAIR} (REJECT-PROVEN if no
  bounded fix), repro_cost from the judge labels (simple; 000580/000635 medium), source = smallrun_b013 —
  after the reviewer verified every reward-1 record is a genuine grader pass (no "failed", test_rc 0,
  prep_rc 0; all 24 empties 0). 000381 → accepts-wrong SIMPLE {REPAIR}: the judge's repro scored 1 ('5
  passed'); the HARD label (v3.3) is withdrawn by measurement. The two judge-repro zeros (000001 quoting
  bug, 000753 degenerate-tie CSV) re-tier THOSE JUDGE RECORDS to REVIEW per D4; the tasks stay measured
  holes (reviewer repros 1); corrected rows are in smallrun_reviewer_fixes.tsv (5 rows).

## V7-delta v3.14 — 2026-09-02 (Fable NOT-READY items + rulings D-37; prompt regenerated)
- Files: RA/v7_rubric_delta.md (D5, D6, D8, D7 key block), rubrics/v7_prompt.md + v7_calibration.{md,tsv}
  regenerated, tools/tournament_score.py, phase0_README.md.
- (1) D8 HARD example replaced by an anonymised hypothetical ("a shortcut that satisfies one assert while
  the remaining asserts require building a real service") stating that NO measured HARD instance exists —
  the one candidate was refuted when a judge's repro collected full reward. (2) Key gains
  `derived_from_run` (reviewer | smallrun_b013); tournament_score.py --exclude-derived-from <run> excludes
  and reports self-derived rows (the 13 promotions + 000381 for the small run); rows stay valid for other
  arms. (3) Reviewer confirmations: 12 promotions certified (cost_call: 000127, 000580, 000635 medium; rest
  simple) with expected_tier_set REPAIR|REJECT-PROVEN like the other SIMPLE controls; 000091 → PENDING
  (uncertified: repro hardcodes graded outputs; rollout-visibility unresolved), excluded from every metric.
  (4) D5 gains "assert the input is non-empty and hash-pinned before recomputing from it"; D8 SIMPLE
  examples gain "delete or truncate the agent-writable input so the recomputed expectation collapses"
  (D-37 input-deletion class: 000121_264aae5e, 000347, 000510, 000729). (5) Pending rows are excluded from
  P/R and from the OR-positive metric. (6) D-14 (000381 PASS + shortcut_hard) is SUPERSEDED by D-32
  (accepts-wrong SIMPLE REPAIR, measured). (7) OQ-14 wording in D6 and phase0_README: RL harness verified
  for the hamishivi/tmax fork at 7387d2f9 (Docker no user=, Apptainer --fakeroot); root also depends on the
  image having no USER directive; RL script line refs :76 (four scripts), :62 qwen35_2b_1gpu.sh, :72
  qwen36_27b.sh (as supplied by the lead).
- Measured effect: none new; the small-run chain is still running (D-36: judge run on hold).

## V7-delta v3.14.1 — 2026-09-02 (re-ruling 000091 certified; D-38)
- 000091 re-certified by the reviewer (both hardcoded values derivable from instruction.md + the
  setup-created graph.json; no test.sh-only constant) → key row accepts-wrong, simple,
  REPAIR|REJECT-PROVEN, derived_from_run smallrun_b013 (was PENDING in v3.14). Pending rows now: 000437 only.
- D-38 single-input function testing class: D5 gains the named verifier_fix "call each graded function on
  several grade-time-generated inputs (multiple n, multiple start nodes incl. a cycle); a single fixed
  input is satisfiable by a lookup table"; D8 SIMPLE examples gain "constant-returning stub for a function
  the verifier calls with exactly one fixed input". Prompt regenerated.

## V7-delta v3.15 — 2026-09-02 (full small-run phase 3; key updates from measurement)
- Measured (repro/judge_batches/smallrun_all_reward_table.tsv, 68 rows + flake; 671 s wall incl. two
  600 s timeouts; smallrun_reviewer_fixes_reward_table.tsv; repro/batch14b_437_reward_table.tsv):
  judge repros 27 × 1, 4 × 0, 2 × timeout; empties 33 × 0. Bucket 2: 000456 1, 006224 1, 000891 1,
  001149 1, 004863 1, 003462 rc 124 (unstable, as the key says), 000984 shortcut_hard repro 0 (the
  reviewer's git-history row 1 — the judge's PASS is a tier miss and its repro used reward-feedback-only
  state, D4), 001125 gold 0 on all six gold runs + judge repro rc 124 (faithful solution never scores;
  tail read pending), 000006 PASS with no repro (unmeasured). Reviewer fixes: 000001 1, 000753 1
  (judge records re-tier to REVIEW, tasks stay holes), 000984 1; the reviewer's re-transcription of
  000381's judge repro scored 0 while the judge's own row scored 1 in both runs — the judge row is the
  evidence. batch14b: 000437 hollow-build fix2 = 1 ⇒ accepts-wrong SIMPLE REPAIR (agent-owned build
  chain, same class as 000024); PASS controls are now four (000252, 000097, 000443, 004160).
- Key: 000437 flipped (derived reviewer); rows added for 000984 (accepts-wrong|rejects-right, REPAIR),
  003462 (unstable, REPAIR), 001125 (rejects-right|unstable, REPAIR, tail pending); bucket-2 candidates
  000456/000891 listed, not keyed (D-32). Pending rows: none.
- Score (tools/tournament_score.py, --exclude-derived-from smallrun_b013): 33 judge records; 8 scored
  against non-self-derived key rows — soundness P 1.0 / R 0.875 (the one fn is 000213 under the v3.7
  IVM caveat, expected-by-construction), tier-in-set 8/8, OR-positive 1.0/1.0; 14 self-derived rows
  excluded; 11 unmeasured. Fable-readable copies: *_nosec.* (D-35).
- Tools: tournament_to_batch writes repro_cmd with `\\`/`\n` escaped and a `cmd_escaped=1` column;
  repro_exec unescapes ONLY when that column is 1 (reviewer TSVs with literal \n in printf are untouched);
  README gives reviewers a csv-module projection one-liner (a bare awk/cut projection of multi-line cells
  leaked judge shell into a Fable context).
- v3.15.1 (coder, flagged for the lead — revertible): the ten batch13/batch6 reviewer-confirmed holes
  (000165, 000197, 004863, 000121_a56b5844, 000173, 000353, 000718, 000753, 000959, 001149) had phase-3
  records but no key row, so the scorer reported judged tasks among them as "unmeasured"; added as
  accepts-wrong / REPAIR|REJECT-PROVEN / simple / derived reviewer. Key = 53 rows. Also: the assembler's
  key↔prose check had been masked by a pipe in v3.15 and was red for the three new rows — prose added,
  check green; repro_exec unescape order fixed (sentinel) and round-trip verified.
- v3.15.2 (Fable pass 6/7 notes, lead ruling): tournament_score.py hard gates are built from the same
  key subset as the metrics (--exclude-derived-from and pending rows excluded, count printed) so promoted
  rows cannot pass g1 trivially on the run they came from; the --fable-export header states that aggregate
  counts still include security tasks (only per-task lines are stripped); tools/assemble_v7_prompt.py now
  runs every post-condition — including the key↔prose check — on in-memory text BEFORE writing any
  artefact (negative test: a bogus key id aborts with nothing written). For the record, the three key rows
  added in v3.15 (40 → 43) were 000984, 003462 and 001125; v3.15.1 added the ten reviewer-confirmed
  batch13/batch6 holes (→ 53), approved by the lead.

## V7-delta v3.16 — 2026-09-02 (post-defence rulings D-40/D-41/D-42; prompt regenerated)
- D-40: the judge ANNOTATES only, never fixes (D3 opening); fixes happen in a later REPAIR stage that
  reads `verifier_fix`. D-41: D8 no longer asks whether a shortcut is "cheaper than compliance" or
  RL-learnable; the judge reports two lengths — `repro_lines` (D4) and new `honest_fix_lines` (+ ≤40-word
  `honest_fix_sketch`, D6/JSON) — and tournament_score.py derives `no_gradient = repro_lines >=
  honest_fix_lines` per non-PASS record into a new `rl_relevance` column (hole_cheaper_than_fix |
  no_gradient | unmeasured); the tier is unchanged. D-42 (reviewer's 001125 read): named rejects-right
  pattern "non-reaping PID 1" in D3 (sleep infinity is PID 1 in gate2 and the RL backend, so stopped
  children become zombies and "no process X remains" asserts fail correct solutions) with the D5 fix
  "run under a reaping init or skip state Z in /proc/<pid>/stat".
- Key: new column `expected_no_gradient` (true for the defender's three DEFENDED tasks 000001, 000197,
  000015 — reviews/defence/defence_smallrun_20260902.md — they stay REPAIR; the scorer checks the flag);
  001125 → rejects-right {REPAIR} (unstable dropped); 000456 and 000891 added (certified; accepts-wrong
  simple REPAIR|REJECT-PROVEN, derived smallrun_all). Key = 55 rows.
- Scorer: records whose judge repro did not run (prep_rc ≠ 0) or timed out (rc 124) are labelled
  "REVIEW-by-D4" in the per-task list, separately from misses; rl_relevance counts and
  expected_no_gradient hits printed. Small-run records carry no honest_fix_lines (judged under v3.7), so
  their rl_relevance is `unmeasured` by construction.
- Probes: repro/batch15_deps.tsv — 000753 and 000729 empty + gold (dependency/network check; rc and
  reward only). Results in the next data commit.
- v3.16.1 addenda: (a) scorer rl_relevance gains `medium_unenforced` — repro_cost medium AND
  (defects.unenforced_requirement true OR repro_expected mentions unenforced) — the defender's pattern 4
  (a MEDIUM repro demoted only via the D-30 clause); tier unchanged. (b) D-42 record: the 001125 key row's
  source_row now carries the reviewer's diagnosis — "rejects-right, deterministic: gold 0 on 6/6 runs; every
  assert passes until `assert pgrep_result.returncode != 0`; the exporter is orphaned to PID 1 = `sleep
  infinity` (backends.py:236) which never reaps, so the exited process stays a zombie and `pgrep -f` matches
  it; gold's own wait loop skips state Z; verifier_fix: reaping init (--init/tini) or skip /proc/<pid>/stat
  state Z instead of trusting pgrep's exit code" — previously only in the reviewer's message.

## V7-delta v3.16.2 — 2026-09-02 (Fable pass-9 notes; F-32 key notes)
- Scorer: rl_relevance is derived only for accepts-wrong records (or the unenforced-requirement format);
  rejects-right/unstable records get `n/a` (their repro_lines is a faithful solution, not a shortcut);
  a repro killed without rc (reward NONE) is a third REVIEW-by-D4 trigger. JSON/D6: honest_fix_lines /
  honest_fix_sketch are "null unless accepts-wrong", and the sketch line says "(the agent's own work on
  the task, not a test.sh change — that is verifier_fix)". Prompt regenerated.
- F-32 (key notes, tiers unchanged): 000753 and 000729 — env-dependent rejects-right: the honest
  solution needs packages absent from the image (numpy/scipy; networkx>=3 pagerank needs scipy); gold
  scored 1 in batch15 only because this harness and the RL harness allow network — gate2's OpenAI-only
  egress would fail it; env_dependency=network. 000580 — wall-clock assert duration <= 1.5 s (test.sh:81):
  rejects-right risk under load, flake probe advisable (not keyed unstable).
- Re-judge plan prep (judge/rejudge_v316_20260902/, 28 tasks, 10/9/9) committed in 6135412/bf18c51/654ff57.
- v3.16.2 addendum: F-32 key notes for 000753/000729 replaced by the reviewer's verbatim wording
  (env_dependency=network-or-prebuilt-venv / scipy-absent) and their expected_soundness set to
  "accepts-wrong|rejects-right(env)" (the scorer strips the parenthesised qualifier when matching).
  P-5 record: the lead launched the v3.16 re-judge of the 28 non-security small-run tasks on
  judge/rejudge_v316_20260902/plan.json (run id rejudge_v316; 28/28 judgements, failed_buckets 0).
  Prompt provenance — SETTLED (scribe, independent): the exact bytes returned by each judge's Read of
  rubrics/v7_prompt.md (full file, three judges, 17:14:03-05 UTC) hash to sha16 1e275d0370833ff7 = v3.16.2
  (blob at 77b3ab0). The workflow's reported prompt_sha256_16 2f365e945af2c16d is PLAN-time metadata:
  plan_abs.json was written at 17:13:22 UTC, ten seconds before commit 77b3ab0 (17:13:32) landed v3.16.2, and
  the judges read the live file ~41 s later. Effective prompt for rejudge_v316 = v3.16.2 (with the honest_fix
  fields and the "null unless accepts-wrong" / verifier_fix clause).
  D2 priors — NOT DELIVERED in rejudge_v316 (scribe: all three judges made zero reads of bucket_N_priors.tsv,
  one prompt each). The run was single-message: the 28 verdicts of record were formed with priors unseen and
  every revised_after_priors is null; no revisions_V7_bucket_*.jsonl exists. Code check (tmax-coder):
  tools/tournament_workflow.js DOES wire the priors step as a second agent() per bucket (revisePrompt :93-100
  reads the bucket priors TSV and writes the revisions file; pipeline second stage :105-115), and its judge
  prompt (:80-90) deliberately withholds priors per D2. Lead's root cause: the rejudge_v316 judge stage ran
  from an ad-hoc 49-line in-session script without the Revise turn; the revise pass was run separately at
  ~17:45 UTC (3 Opus-xhigh agents, repo revisePrompt text + D4 override wording) writing
  revisions_V7_bucket_{0,1,2}.jsonl. RULE: all judge runs go through tools/tournament_workflow.js via
  scriptPath, never inline. Scorer: --apply-revisions overlays revised_after_priors onto the verdict of
  record; rejudge_v316 is scored twice (first-line-only and with-revisions).
  rejudge_v316 RESULTS (phase-3 batch batch_rejudge_v316: 53 rows = 28 empty + 25 judge repros, 0 errors, 0 live
  containers; empty 28/28 = 0; aw 18/19 = 1 (003462 NONE: rc 124 at the 600 s cap → REVIEW-by-D4, expected
  unstable); ur 4/4 = 1; tag 2/2 = 1; no expected≠reward except that NONE). Scored against 41 key rows with
  smallrun_b013,smallrun_all excluded (14 self-derived rows, 1 unmeasured 000006): 13 tasks scored.
    first-line-only : soundness P 1.0 / R 0.923 (tp 12, fn 1 = 000984 PASS/sound), tier-in-set 10/13,
                      tier_soundness_conflict 2 (000333, 000908: PASS with accepts-wrong), gate FAIL on
                      000333/000908/000984, simple rule 6/10, no_gradient 1 (000197), expected_no_gradient 1/2.
    with-revisions  : 28 revision rows (10/9/9), 6 changed (000333, 000908 tier→REPAIR; 000984 →REPAIR/accepts-wrong
                      without repro; 003462 soundness→unstable; 001125 soundness→rejects-right; 000015 →REPAIR/
                      accepts-wrong): soundness P 1.0 / R 1.0, tier-in-set 13/13, tier_soundness_conflict 0,
                      gate still FAIL on 000984 (revised call carries no repro_cmd → unmeasured), simple rule 6/10.
  Judgement files complete: 10/9/9 records = plan ids, 31 keys each, mtimes 17:29:35-17:32:03 UTC.
  Key change: task_001125_015b6ff8 expected_soundness rejects-right → accepts-wrong|rejects-right(env), cost
  n/a → simple (reviewer's second-defect finding, condition met: the judge's aw repro scored 1 in this run);
  the prose carries the reviewer wording; derived_from_run stays smallrun_all (the aw half is derived from
  rejudge_v316 — exclude both when scoring either run). Key rows: 58 (unchanged count).
  Observation: rubrics/v7_prompt.md's file sha16 changes on key-only delta edits because the header stamps
  v7_rubric_delta.md@<sha> (this re-assembly: judge-facing text byte-identical, header line only). A run-time
  content hash should exclude the stamp line — folded into the version+sha tooling item. Consequence for scoring: the after_priors block is empty by construction for this run;
  judgement files are complete (10/9/9 records = plan, 31 keys each).
  Tooling follow-up (open, no code change yet): the priors turn must be un-skippable in whatever launches a run
  (run tools/tournament_workflow.js itself, or make revised_after_priors a required non-null step with the
  scorer rejecting null); the workflow should re-hash the prompt at judge-run time,
  or the judge should report the sha of the file it read, so plan-time and run-time versions cannot
  diverge silently. Scorer accepts a comma list for --exclude-derived-from
  (the re-judge is scored with smallrun_b013,smallrun_all excluded; its own run id is rejudge_v316).
- Key row 000387_5cbd2f2c added (rejects-right(env), {REPAIR}, derived reviewer) with the env_dependency
  wording from reviews/gold_workaround_scan_README.md "Follow-up outcomes" — same harness mechanism as
  000984. 000944 REFUTED, no key change; rule established for the class: a `pip-install` hit in the gold
  scan is not a finding until its guard is checked — most are defensive fallbacks that never execute.
  Open probe (reviewer, superseding its earlier three-task empty+gold request — NOT to be run in that form):
  repro/batch16_venv.tsv, 6 rows / 2 tasks with a decisive third arm — 001022 strips only the vendored
  third-party packages (venv/pip/setuptools kept so venv-exists asserts hold); 000122 (security_union=1;
  any tail read → Opus seat) removes the installed package and reinstalls from the in-image source with
  --no-index (tests pip build isolation). 000396 needs no probe — settled by inspection: its gold venv
  vendors zero third-party packages. Companion rule to the pip-install one: a `vendored-site-packages`
  hit is not evidence until the third-party remainder is compared against the image's own installs —
  every venv seeds pip and setuptools, so the raw member count is non-zero for any venv (000396 = the
  false positive that establishes it). Expected rewards and reasoning: the TSV's note column and the
  README's final section. Not run yet (awaiting the lead's go after the re-judge).

## V7-delta v3.17 — 2026-09-02 (ruling D-49: C1+C2+C3 from reviews/v317_proposal_20260902.md; D-56 key rows; prompt regenerated)
- Motivation: the v3.16.2 re-judge regressed 4 of 28 ids against v3.7 (000015, 000984 → sound/PASS with no
  falsifiable artifact; 000333, 000908 → accepts-wrong rated MEDIUM → PASS). Bisect and clauses: the reviewer's
  proposal (reviews/v317_proposal_20260902.md); adopted in full by the lead (D-49). D-41 stays: the judge never
  decides RL-learnability; the scorer derives no_gradient.
- C1 — tier follows soundness alone. D3 framing + tier-table PASS row rewritten: PASS requires `sound`; an
  `accepts-wrong` record is never PASS at any D8 cost — bounded verifier change ⇒ REPAIR, SIMPLE with no
  bounded fix ⇒ REJECT-PROVEN; `shortcut_tag`/`repro_cost` are triage + no_gradient inputs only. D8 MEDIUM and
  HARD now resolve to REPAIR (tag kept; the `verifier_fix` becomes REQUIRED per D5, was "recommended").
  Anti-over-rejection guard (reviewer's defence, recorded so it is not re-litigated): D8 was built so that
  "discarding a task over a MEDIUM/HARD shortcut would throw away legitimate work" — discarding is
  REJECT-PROVEN, not REPAIR. **C1 sends such records to REPAIR, not REJECT-PROVEN, so the anti-over-rejection
  guard is untouched**; the sentence "MEDIUM/HARD accepts-wrong never reach REJECT-PROVEN — see D8" is retained
  verbatim in the tier table (assembler asserts it). Only the PASS label moves.
- C2 — soundness probe. New D3 sub-rule + D6/JSON field `soundness_probe`: a `sound` call on a task whose
  verifier compares against hardcoded expected literals AND whose graded artifact is agent-supplied must record
  the cheapest wrapper/stub/passthrough considered and the assertion that defeats it; `sound` with the field
  null on such a task is invalid output. Both sound-regressions (000015, 000984) are of this shape.
- C3 — one line in D8 MEDIUM: a `repro_cmd` that does not itself reimplement the graded behaviour (hardcodes
  expected values, stubs, no-ops, wraps) is SIMPLE regardless of statement count; MEDIUM requires the
  reproduction to contain the work.
- D-56 key change: task_001957_cc826ac1 and task_002223_e0a30625 expected_tier_set REPAIR|PASS → REPAIR (under
  C1 the PASS disjunct is unreachable; the reviewer scanned all 58 rows — these are the only two). Key rows 58.
- GATE for adopting v3.17 (rulings D-49/D-50/D-53 final): regression run on the 34-id set = the same 28 ids +
  the 4 PASS controls task_000097_601162e4, task_000252_aa3c814d, task_000443_90a5ee10, task_004160_27a6a850 +
  001957/002223 as ordinary defect rows (expected REPAIR). Scored on the JUDGE-ONLY layer vs the v3.16.2
  judge-only layer: (a) all 4 PASS controls come back PASS on the judge-only layer (gold 1, best adversarial
  shortcut 0) — 4 controls against 28 defect tasks is a floor on over-rejection, not a certificate; (b) ≥3 of
  the 4 regressed ids recovered; (c) no new loss vs v3.16.2 on the other rows. Judge-only and post-priors
  layers are scored separately from the same run; the post-priors layer is reported as "post-priors
  (near-circular on keyed tasks)", never as accuracy (D-50). The run goes through tools/tournament_workflow.js
  (Judge + Revise) by scriptPath.
- Scorer: `soundness_probe` is an ignored free-text field for tools/tournament_score.py (it reads labels only);
  the quarantine on judge files is unchanged (the field can carry task-derived text — it stays in judge/**).

## V7-delta v3.17.1 — 2026-09-02 (Fable pass NOT-READY items on 99d5853; rulings D-57 / D-58; prompt regenerated)
- Three stale pre-C1 lines removed (Fable pass, 3 lines, all else PASS): D4 "required for a PASS that carries
  shortcut_tag … null ONLY for a PASS without shortcut_tag and for a REVIEW" → "required for a REPAIR that
  carries shortcut_tag … null ONLY for PASS and for REVIEW"; D6/JSON repro_cmd "null ONLY for (a) a PASS with
  shortcut_tag null or (b) a REVIEW" → "null ONLY for PASS or REVIEW"; repro_expected "null (PASS without
  shortcut_tag)" → "null (PASS)". The assembler's pre-C1-route assertion now lists the three old phrases (and
  the D-57 conjunct) so they cannot return. tournament_score.py docstring updated (PASS+tag is no longer a
  correct tier).
- D-57: the C2 trigger is the single mechanical condition `verifier_expected_answer_from == "hardcoded-literal"`;
  the "graded artifact supplied by the agent" conjunct is removed from D3 and the D6 field text. Scorer
  enforces it: soundness sound AND verifier_expected_answer_from hardcoded-literal AND soundness_probe null ⇒
  `c2_violation` (count + ids; tier unaffected).
- D-58 gate addition: "REJECT-PROVEN count on the judge-only layer, baseline v3.16 = 0/28; any REJECT-PROVEN is
  new by construction and is read individually for whether a bounded fix truly does not exist". Scorer prints
  reject_proven_count + ids (with the baseline).
- judge/regress_v317_20260902/plan.json re-prepared so plan.prompt carries the v3.17.1 content sha (bucket TSVs
  unchanged) — otherwise the Revise stage would log PROMPT DRIFT on a correct run.

## Key/CHANGELOG addendum — vendored-venv class resolved (reviewer traces; lead rulings)
- Key rows added: 001022_75ea0120 and 000048_b0bd6829 — rejects-right(env, venv-dependent), {REPAIR}, sec 0,
  derived reviewer, wording from reviews/gold_workaround_scan_README.md final section. 000753's hedge
  ("unless the RL sandbox permits PyPI") replaced by the reviewer's discharged text (trace: pip install exit 1
  via proxy 403; proxy-stripped retry DNS failure; pip list = pip+setuptools). NOT keyed: 000122 (honest
  offline path from an in-image wheel succeeds — batch16 arm 1), 000396 and 001173 (zero third-party
  members). batch16_venv: 6/6 rows matched the reviewer's expectations (001022 strip-arm 0 = load-bearing;
  000122 offline reinstall 1).
- OQ-21 (ledger; closed-as-retracted — the egress inference of the earlier OQ-14 check): absence of network flags is NOT evidence of egress — the restriction lives in
  env-injected proxy config (HTTPS_PROXY allow-list; fetches 403, DNS fails without the proxy), one layer below
  container flags; validation-rollout traces show egress blocked. Pointer added to
  reviews/oq14_rl_harness_20260902.md.
- OQ-22 OPENED (ledger number; replaces the interim "OQ-22"): does the RL TRAINING rollout run under the same network restriction as the validation rollout?
  Unsettled by images (no baked proxy) or harness code; needs the training-cluster network config or an
  RL-rollout trace. Until answered, the three env-dependent rows (000753, 001022, 000048) keep tier {REPAIR}
  with env_dependency=venv/network and a "tier conditional on OQ-22" note.
  Code-level check (tmax-coder, reviews/oq14_rl_harness_20260902.md appendix): no proxy/network restriction in the
  RL launch path (podman netns=host); validation used Daytona with domain_allow_list=api.openai.com; RL egress
  therefore depends on Beaker cluster/host policy — still OPEN at cluster level.
  Training egress OPEN per the torchtitan cotrain rollouter (D-76, reviews/harness_torchtitan_cotrain_coder_20260902.md:
  no network argument in the Daytona create params; corpus tooling confirms apt/pip/curl work); the validation allow-list
  was gate2-only. Tenant-level Daytona policy remains the one unknown.
- Method rules (next to the pip-install and third-party-remainder rules): absence of network flags is not
  evidence of network; a strip arm proves a package is load-bearing, not that it is unobtainable — cite it for
  load-bearing-ness only, the trace is the instrument for supply.
- Open tooling item (after the handoff commit, separate commit; no rubric text change): the judgement record
  stamps rubric_version "V7-v3.16" — the assembler should stamp the full version string and the prompt's sha16
  into the prompt header so judges echo it, and tournament_workflow.js should re-hash the prompt at run time
  (plan-time vs run-time divergence caught by the scribe in P-5). Scorer gains `tier_soundness_conflict`
  (tier PASS + soundness accepts-wrong + repro attached; count + ids only, no rule change).

## Tooling — 2026-09-02 (prompt provenance; no rubric text change)
- Closes the two open items from the P-5 record. tools/assemble_v7_prompt.py now stamps the header with
  `version:` (derived from the newest `## V7-delta vX.Y[.Z]` heading in this file — single source) and
  `content_sha256_16:` = sha256 of everything after the header comment, so key-only / stamp-only re-assemblies
  leave the content hash unchanged (proof: body byte-identical to the pre-stamp prompt; current stamp
  V7-delta v3.16.2 / content 8366cc4f38b2ec84; the file sha (what `sha256sum` prints) is fd7db9a6ba39550f).
- New tools/prompt_sha.py: {file_sha256_16, content_sha256_16, version, stamp_matches_content} for any prompt.
  tools/tournament_prepare.py records it in plan.json as `prompt` with hashed_at=prepare-time (regress_v317 plan
  regenerated to carry it; bucket TSVs unchanged).
- tools/tournament_workflow.js: the judge FIRST runs `sha256sum <prompt>` and returns prompt_sha256_16 (required
  in the structured return, pattern ^[0-9a-f]{16}$) and stamps it on every JSONL line; the Revise stage logs
  PROMPT DRIFT when it differs from plan.prompt.file_sha256_16. tools/tournament_score.py prints a
  prompt_sha256_16_census over the verdicts of record (rejudge_v316: {None: 28} — predates the field).
- Effect: plan-time vs run-time prompt versions can no longer diverge silently (the P-5 2f365e94 / 1e275d03 case).

## Tooling 2 — 2026-09-02 (rulings D-51 / D-54 / D-55 / D-52 note / stamp unify; no rubric text change)
- D-51: tools/tournament_score.py derives no_gradient from the EXECUTOR's statement count of the command
  actually run (`repro_lines_exec`, first repro record per task in --results-jsonl, counted by the single
  definition now in tools/repro_stats.py, shared with tournament_reward_table.py — its table is byte-identical
  after the refactor). The judge's self-count is kept as `repro_lines_judge`; rows where the two differ by >2x
  are counted and listed (rejudge_v316: 6 of 25 repro rows). Without executor data the row is `unmeasured`.
- D-55: `no_gradient_marginal` = repro_lines_exec / honest_fix_lines >= 0.67 (annotation only, never a tier
  input), counted alongside the strict no_gradient; `--per-task-export <path>` writes the defender's _nosec TSV
  (task_id, tier, soundness, honest_fix_lines, repro_lines_judge, repro_lines_exec, no_gradient,
  no_gradient_marginal; security/unknown rows dropped). rejudge_v316 judge-only: no_gradient 1 (000197),
  marginal 2 (000197, 000891); export 28 rows / 0 dropped.
- D-54: revision schema/prompt in tools/tournament_workflow.js gain `supporting_repro` (the priors
  phase3_rewards_json key whose measured reward backs the new call), REQUIRED when a revision moves a record
  from PASS to non-PASS; `tournament_score.py --apply-revisions` looks the key up in bucket_<k>_priors.tsv
  (--priors-dir, default = judgement dir) and treats reward==1 as D4-supported, else routes the record to REVIEW
  and counts `revision_unsupported`. Applied retroactively to rejudge_v316's revise pass (which predates the
  field): revision_unsupported 4 = 000015, 000333, 000908, 000984 → REVIEW on the post-priors layer; soundness
  P 1.0 / R 1.0 unchanged there; the layer is labelled "post-priors (near-circular on keyed tasks)" (D-50).
- D-52 note: 001125's revision reason attributes a "returned NONE" to this run's repro; the aw repro in
  batch_rejudge_v316 scored 1 (rc 0) — the NONE was the v3.7 small-run timeout (rc 124). Revise-agent error;
  key row (accepts-wrong|rejects-right(env), simple) confirmed.
- Stamp unify: judgements carried rubric_version "V7-v3.16" and revisions "v7-v3.16.2" in rejudge_v316. The
  workflow now uses ONE `RUBRIC_STAMP` for both stages, derived from plan.prompt.version ("V7-delta v3.17" →
  "V7-v3.17"); the scorer prints a rubric_version_census for both layers. The prompt's JSON placeholder
  ("rubric_version": "v7") is unchanged — the workflow message overrides it, so v3.17's content sha is untouched.

## Tooling 3 — 2026-09-02 (rulings D-59 / D-60; v3.17.1 item (g)/(h); no rubric text change)
- D-60 tools/repro_stats.py: heredoc bodies (<<'X'/<<X/<<-X … X) are masked before splitting exactly like -c/-e
  payloads, so a heredoc is ONE statement; `analyze()` emits repro_statements (masked statements), payload_lines
  (non-empty lines of all masked -c/-e payloads + heredoc bodies) and repro_effort = statements + payload_lines.
  Quote blanking is one left-to-right pass ("…" with escapes | '…'), fixing the apostrophe-inside-double-quotes
  mis-pairing. 7 unit cases pass (heredoc=1 stmt; -c payload lines; apostrophe; <<- with comment line; empty).
  tournament_reward_table.py gains payload_lines + repro_effort columns (batch_rejudge_v316 table regenerated).
- D-59 tools/tournament_score.py: no_gradient = repro_effort_min >= honest_fix_lines and marginal on the same
  ratio, where repro_effort_min = MIN repro_effort over ALL measured reward-1 repro records for the task across
  every results file (--all-results, default repro/*.jsonl + repro/judge_batches/*.jsonl: 21 files, 109 reward-1
  records, 49 tasks with a min). This run's components stay as repro_lines_exec_thisrun / payload_lines_thisrun /
  repro_effort_thisrun (min-effort record of the run). Export gap fixed: components are set BEFORE the PASS
  short-circuit, so tag-labelled PASS rows (000333, 000908) now carry executor counts.
- (h) unit: exec_counts_available was TASKS (25 = 28 − 3 tasks with no repro record: 000006, 000015, 000984);
  the earlier "23" was export rows carrying a count (25 minus the 2 PASS rows hit by the gap). Renamed
  exec_counts_available_tasks. Screen renamed/split: repro_lines_judge_vs_exec_effort_gt2x (judge self-count vs
  executor repro_effort, every row incl. PASS) and …_vs_exec_statements_gt2x.
- rejudge_v316 judge-only under the new definitions: exec_counts_available_tasks 25; no_gradient 1 (000197),
  marginal 2 (000197, 000891) — unchanged ids; rl_relevance {hole_cheaper_than_fix 17, no_gradient 1,
  medium_unenforced 4, unmeasured 1}; effort_min < this run's effort on 7 tasks (000333 2 vs 21, 000908 5 vs 11,
  000580, 000635, 000753, 004863, 006224); judge-vs-effort >2x = 8 [000333, 000456, 000635, 000908, 001125,
  003462, 004863, 006224], judge-vs-statements >2x = 0 (judges count statements like the executor; the gap is
  payload). Export census (28 rows): statements 1-10, payload 0-19 (13 rows > 0), effort_thisrun 1-21.
- (g) judge/fullrun_v317_20260902/plan.json re-prepared: plan.prompt = V7-delta v3.17.1 / 6c92b57e51d9b9cf; 74
  bucket TSVs byte-identical (diff count 0).
- D-59 caveat (numbers computed by tournament_score.py `repro_coverage`, not by hand; dedup rule: a measured
  reward-1 repro is a distinct (task_id, label) pair with reward 1 across the 16 base reward tables, *_nosec
  duplicates excluded): repro_effort_min is a downward-biased estimator whose bias tracks probe coverage, not the
  task — 49 tasks have a measured reward-1 repro; distinct-label histogram 1 label: 23 tasks, 2: 16, 3: 10; on the
  rejudge_v316 run the min bites (min < this run's effort) on 7 of 25 measured tasks. Use per task, next to
  n_reward1_repros (now printed on the per-task line and in the export); never roll no_gradient into a
  corpus-level rate without stating the probe-count denominator. (The reviewer's 44/24 and the scribe's 45/29 were
  hand counts under other dedup rules; the scorer's figures above are the ones of record.)
- D-62 (scorer only): the over-rejection floor gate reads `tier == PASS` on exactly the 4 controls
  task_000097_601162e4, task_000252_aa3c814d, task_000443_90a5ee10, task_004160_27a6a850 and prints on its own
  line; `c2_violation` (sound + verifier_expected_answer_from hardcoded-literal + soundness_probe null) stays a
  separate list and is never counted against the controls. 000006 remains unmeasured (no key row) this run.
- effort_min pool inclusion rule (one sentence, scribe re-check): a record enters the pool iff its results-JSONL
  `mode == "repro"`, `repro_cmd` is non-empty and `reward == "1"` — over the 21 results files that is 109 records /
  49 tasks; the 111 / 50 count includes the 2 `mode == "gold_patch"` reward-1 records (+1 task), which are modified
  gold solutions, not shortcut reproductions, and are excluded (no rc/prep_rc/security filter is applied).
- Export flag rule: no_gradient and no_gradient_marginal are computed for EVERY row that has both repro_effort_min
  and honest_fix_lines, regardless of tier or soundness (annotation only) — previously blank on PASS+tag rows
  (000333, 000908) and on the `n/a` soundness classes (003462 unstable); rl_relevance keeps its tier/soundness gating.

## regress_v317 — 2026-09-02 (v3.17.1 regression run scored; rulings D-64/D-68 key edits; D-66(4)/D-67 scorer tooling; batch17)
- Run: wf_1beef2cb, tools/tournament_workflow.js by scriptPath, 34 ids, 5 buckets (1 security-routed + 4 other), Judge +
  Revise; 34 judge records (1/9/8/8/8), all self-reporting prompt_sha256_16 aea72566b0d6ee23 = plan.prompt.file_sha256_16
  (scorer `prompt_provenance.match: true`), rubric_version V7-v3.17.1 ×34. Judge-only tiers REPAIR 32 / PASS 1 (000006) /
  REVIEW 1 (000984); soundness accepts-wrong 33 / sound 1. Revisions 34 rows, 4 changed (000097 REPAIR→PASS, 004160
  REPAIR→PASS, 000443 REPAIR→REVIEW, 000984 REVIEW→REPAIR with supporting_repro resolving to reward 1).
- Phase 3 (batch_regress_v317: 66 rows = 34 empty + 29 aw + 3 ur; 0 errors): empty 34/34 → 0; aw 26 → 1, 2 → 0 (000097
  prep_rc 2; 000580), 1 → NONE (003462 rc 124 at the 600 s cap, as in every prior run); ur 3/3 → 1. The 4 former PASS
  controls were run FIRST: 000252, 000443, 004160 judge repros → reward 1 (rc 0, prep 0); 000097 → 0 with prep_rc 2
  (repro prep-broken: missing libc headers in the image) → REVIEW-by-D4.
- Reading: on 3 of the 4 over-rejection controls a judge-written non-solution collected the reward — D4 refutes the key's
  PASS, not the rubric. Ruling D-64 (amended, reviewer classification, all three holes real): 000252 → accepts-wrong /
  {REPAIR} / SIMPLE; 000443 → accepts-wrong / {REPAIR} / MEDIUM|SIMPLE (D-68: reviewer SIMPLE vs defender MEDIUM → MEDIUM
  recorded, conservative for discard eligibility; both calls kept in the cell, scorer flags `cost_disputed`); 004160 →
  accepts-wrong / {REPAIR} / MEDIUM, expected_no_gradient true (read-and-reimplement + decoy content; trainable-now per
  D-41, verifier hardening a to-do). All three: source batch_regress_v317, derived_from_run regress_v317. 000097: PASS row
  retained with the prep-broken note, corrected repro pending. Key rows 58. Key-only re-assembly: prompt content sha
  unchanged 6c92b57e51d9b9cf, file sha 5b13… → 35770450c03a7d7f (header stamp only) — plans that record the file sha need
  re-preparing before launch (regress plan keeps aea72566, the sha its judges actually read).
- Scores vs the amended key (regress_v317 rows excluded as self-derived → 16 scored, 1 unmeasured 000006):
    judge-only : soundness P 0.9375 / R 1.0 (tp 15, fp 1 = 000097 whose key row is still PASS), tier-in-set 14/16,
                 REJECT-PROVEN 0 (baseline 0/28), c2_violation 0, tier_soundness_conflict 0, simple rule 11/12, no_gradient
                 2 [000015, 000197], marginal 2 (same), review 1 [000984]; gates: accepts-wrong→non-PASS+repro FAIL [000984
                 REVIEW without a run repro], probe-cleared PASS 1/1, over-rejection floor FAIL 0/4 (all four controls
                 judged accepts-wrong; three are phase-3 confirmed, 000097 undecided).
    post-priors: P 1.0 / R 1.0 (tn 1 = 000097 revised to PASS), tier-in-set 16/16, revision_unsupported 0 (no PASS→non-PASS
                 moves), c2_violation 2 [000097, 004160 — revised to sound without soundness_probe], review 1 [000443],
                 over-rejection floor 2/4 (000097, 004160 revised to PASS — 004160 against a measured reward 1).
    Before the key edits the judge-only layer read P 0.789 (fp 4 = the four controls), tier-in-set 14/19.
  v3.16 → v3.17.1 on the shared 28: the 4 regressed ids (000015, 000984, 000333, 000908) all left their v3.16 calls
  (000015/000333/000908 → REPAIR/accepts-wrong hits; 000984 → REVIEW/accepts-wrong, partial); no new loss on the other rows.
- Tooling (scorer): `prompt_provenance` (judges' self-reported file sha vs plan.prompt.file_sha256_16, both printed);
  `discard_candidates` (D-66(4)/D-67(2): REJECT-PROVEN ∧ clean reward-1 repro (rc 0, prep 0) ∧ cost simple ∧ no
  verifier_fix; others listed with reasons → REVIEW; `--discard-ids-out`); `review_count` + `unmeasured_ids` +
  `followup_needed` with `--followup-tsv` (D-67(1): REVIEW tier, D4 non-run, no clean reward-1 repro this run, no key
  row — regress_v317: 5 ids 000006, 000097, 000580, 000984, 003462); `cost_disputed` + key-vs-judge cost agreement (28/31
  agree; disagree 000443, 000984, 006224). New tools/discard_rejudge_prepare.py: candidate-only second-judgement plan
  (Opus xhigh, D-7 routing, first verdict never projected; discard final only with second judge REJECT-PROVEN + reviewer
  read + defender concession). regress_v317: 0 discard candidates.
- batch17 controls (000006, 002119): defender rows + empty/gold (repro/batch17_defender_plus): 6/6 as expected — 000006
  empty 0 / gold 1 (first gold run) / defender repro 0; 002119 empty 0 / gold 1 / defender oracle-wrap repro 1. Reviewer
  rows (repro/batch17_controls): 000006 gold 1, two repros 0/0 as expected; 002119 gold 1, two repros expected 1 → observed
  0/0 (reviewer-oracle-exec-complete, reviewer-symlink-complete). The conditional 002119 key edit (REPAIR|REVIEW → REPAIR)
  is NOT applied: the reviewer's refutation tests failed; the defender's did succeed — ruling pending. No key row for 000006.
- Priority per D-66: v3.18 HELD (no rubric text change in this entry); the full run uses v3.17.1 as-is.
- Full-run launch prep (commits 82e68b8 + this): tools/tournament_workflow.js gains args.skip_revise (Judge stage only,
  revise_file null; node --check ok). judge/fullrun_v317_20260902 re-prepared: plan.prompt file sha 35770450c03a7d7f /
  content 6c92b57e51d9b9cf (v3.17.1); plan_abs_half1.json = buckets 0-17 (18 buckets, 175 tasks, 5 security buckets /
  45 tasks, 13 other) and plan_abs_half2.json = buckets 18-36 (19 buckets, 190 tasks, 0 security), all paths absolute
  and existing. CORRECTION to 82e68b8's message: the re-prepare changed 3 priors TSVs (buckets 9, 19, 36 — one row each:
  000097, 000443, 004160 now carry their regress_v317 phase-3 rewards), not 0; phase-0 projections unchanged. Rows with
  phase-3 data: 12 → 12/365 of 365. Launch: Workflow({scriptPath: "<abs>/tools/tournament_workflow.js", args: {plan:
  <plan_abs_halfN.json contents>, skip_revise: true|false}}).

## Key + records — 2026-09-02 (rulings on 002119, post-priors noise; batch18/batch19 follow-ups)
- Ruling: task_002119_e871c336 expected_tier_set REPAIR|REVIEW → REPAIR (accepts-wrong, SIMPLE), source
  repro/batch17_defender_plus_reward_table.tsv (defender-oraclewrap-exp1 reward 1, rc 0, prep 0; gold 1; empty 0);
  the reviewer's two refutation rows (batch17_controls: reviewer-oracle-exec-complete, reviewer-symlink-complete → 0/0)
  are recorded in the row as failed vectors. derived_from_run batch17. Key rows 58.
- Ruling (record): the regress_v317 post-priors layer showed c2_violation 2 (000097, 004160 revised to `sound` with
  soundness_probe null) and 004160 revised REPAIR→PASS against a measured judge-repro reward 1 — evidence that the
  Revise stage is noisier than the Judge stage when the priors carry no phase-3 data (353/365 full-run rows). Supports
  running the full run judge-only (args.skip_revise).
- batch18_000097 (reviewer): gold 1 ✓; corrected repro reviewer-000097-nolibc expected 1 → observed 0 with prep_rc 2
  again → PASS row retained; note "env: image gcc" for the reviewer (the image lacks a working C toolchain for the
  repro's prep step; the reviewer's local pre-validation did not reproduce the container).
- batch19_followup (reviewer): 000580 reviewer-580-fixed → 1 (rc 0, prep 0): its accepts-wrong stands (key unchanged:
  accepts-wrong / REPAIR|REJECT-PROVEN / medium). 000984 reviewer-984-githistory → 1 (rc 0, prep 0): the regress_v317
  REVIEW resolves to REPAIR with this row as the supporting repro (key row already accepts-wrong|rejects-right / REPAIR;
  the row enters the priors and the effort_min pool). Follow-up list after these: 000006 (no key row), 000097 (env),
  003462 (cap) remain.
- Tooling — repro_cmd transcription lint (flag, never fix; single definition tools/repro_stats.py `lint`): codes
  literal_backslash_n_in_dq_c (the two characters backslash+n inside a double-quoted -c/-e payload), unterminated_heredoc,
  crlf, empty_command. Emitted as `repro_lint_warn` by tournament_to_batch.py (batch TSV column) and
  tournament_reward_table.py (table column), printed by tournament_score.py next to followup_needed (count + ids +
  codes). 8 unit cases pass. Retroactive counts: regress_v317 1 of 32 repro rows flagged — 000580,
  literal_backslash_n_in_dq_c, the row whose "clean 0" was a transcription defect (batch19's corrected row scored 1);
  rejudge_v316 0/25, smallrun_all 0/33, smallrun_b013 0/24. batch_regress_v317.tsv re-emitted with the column
  (66 rows, commands/labels/expected byte-identical).

## v3.18 backlog — OPEN items (recorded 2026-09-02 under the launch freeze; NO rubric text changed; D-66 holds v3.18)
- (D-65, held) Fable pass-13 items 2, 5, 3 as amended in reviews/regress_v317_review_20260902.md §(b): accepts-wrong
  `reason` must name the decisive assertion the non-solution satisfies (D4 repro paragraph, delta D4 :120 sentence);
  `who_could_trigger` gains "…and the decisive assertion the non-solution satisfies" (assembler head.replace on the
  v6_core:358 field line); consequence when missing = REVIEW, not "invalid output" (delta D4 :145 sentence). Assembler
  assertion for the new sentence; scorer `aw_unnamed_assertion` count + ids.
- (a) C2 probe scope (reviewer, from the only C2 miss in slice 1's 60): "the cheapest bypass must be drawn from
  rollout-visible state — instruction + image + directory listings — not the instruction alone" (D3 C2 sub-rule + D6
  soundness_probe field text).
- (b) Confidence field: replace the bare 1-10 at prompt:389 with the anchored 5-check count drafted in the Fable §Pass 13
  addendum (+ the :470-471 companion edit); threshold ≥ 8 = four of five checks done. Scorer keeps reading `confidence`
  as an integer; the JSON schema in tools/tournament_workflow.js (confidence 1-10) needs the same anchor.
- All three land together as "## V7-delta v3.18" after the lead lifts the freeze; the fullrun_v317 slices keep running on
  v3.17.1 (content 6c92b57e51d9b9cf) so slice-to-slice numbers stay comparable.

## fullrun_v317 slice 1 — 2026-09-02 (judge-only, v3.17.1 file 5afa213f0fc3a3ad / content 6c92b57e51d9b9cf)
- Run wf_4ee529a3 (lead): 10 buckets of the per_agent-6 plan (security idx 0-2 = 18 tasks, other idx 8-14 = 42), 60/60
  judgements, every judge self-reported prompt sha 5afa213f0fc3a3ad (scorer provenance match true), skip_revise true.
  Labels: REPAIR 53 / PASS 7; accepts-wrong 52 / sound 7 / rejects-right 1; cost simple 44 / medium 7 / hard 1;
  REJECT-PROVEN 0; REVIEW 0; non-PASS without repro 0; c2_violation 0 (6 of 7 sound calls carry soundness_probe);
  honest_fix_lines on 52/52 accepts-wrong; verifier_fix on 53/53 REPAIR.
- Phase 3 (batch_fullrun_v317_slice1: 113 rows = 60 empty + 53 repros, 0 errors, 60 images pulled): empty 60/60 → 0;
  aw 32 → 1 / 4 → 0; ur 14 → 1 / 2 → 0; rr 1 → 0 (expected). Repro reward-1 rate 46/53 overall — security buckets 11/14,
  other 35/39; per bucket 4/5, 4/5, 3/4 | 4/5, 2/4, 6/6, 6/6, 6/6, 6/6, 5/6. Non-clean rows 6: prep_rc 1 on 000139 (0),
  000009 (rr 0), 000010/000012/000011 (reward 1 despite prep 1), prep_rc 2 on 000023 (0). repro_lint_warn 1 (000087,
  literal_backslash_n_in_dq_c; its repro scored 0 — same defect class as 000580). discard_candidates 0.
- Follow-up (D-67(1)) 10 ids → batch_fullrun_v317_slice1_followup.tsv: D4 non-run 000009, 000010, 000011, 000012,
  000023, 000139; clean 0 without a reward-1 repro 000024, 000045, 000087, 000101. Scorer rule fix: "no key row" is no
  longer a follow-up reason (the full run is unkeyed by design; those ids stay in unmeasured_ids) — before the fix the list
  was 59; regress_v317's list becomes 4 (000097, 000580, 000984, 003462).
- Key overlap: 2 slice ids keyed (both hit; P 1.0 / R 1.0 on n=2 — not a calibration statement). no_gradient 2,
  marginal 3 (000070, 000083, 000084); judge-vs-effort >2x 9, judge-vs-statements >2x 1 (000016);
  exec_counts_available_tasks 53. repro_coverage: 22 base tables, 99 tasks with a reward-1 repro.
- batch20_pass_challenge (reviewer, task_000161 security): empty 0 ✓, gold 1 ✓, repro reviewer-161-instruction-leak
  expected 1 → observed 1 (rc 0, prep 0). The slice PASS record is refuted: overlay accepts-wrong/REPAIR/SIMPLE via a follow-up row (no rubric edit).
- Housekeeping: reviews/defence/nogradient_validation_v316_20260902.md, reviews/defence/slice1_defence_20260902.md,
  reviews/fullrun_slice1_review_20260902.md and the appended pass-6 readiness file are committed for the ledger.
- Ruling overlays + id hygiene (tooling, no rubric text): tournament_score `--overlay-tsv` applies a lead ruling
  (tier/soundness/repro_cost) to a verdict of record ONLY if the named supporting repro record has reward 1, rc 0, prep 0
  (D4); prints overlays.applied / rejected. judge/fullrun_v317_20260902/overlays.tsv row 1: task_000161_931b8abb →
  REPAIR / accepts-wrong / simple, supporting repro/batch20_pass_challenge.jsonl:reviewer-161-instruction-leak (slice-1 PASS
  refuted). Follow-up list now also written as `<name>_nosec.tsv` (security/unknown rows dropped) for the defender.
  tools/ids.py: FULL_ID = ^task_\d{6}_[0-9a-f]{8}$ asserted on every id list/join in tournament_prepare, to_batch,
  reward_table and score; prefixes are display-only. tools/id_collisions.py lists 6-digit-prefix collisions over the 398 +
  controls with security_union per member (the overlay row itself was first written with a wrong hash by hand and caught
  by re-reading the id from the batch20 TSV — ids are copied from files, never typed). 000010/000011/000012 (reward 1 with
  prep_rc 1) stay outside clean_r1 until the reviewer confirms the partial prep is benign.
- Slice-1 follow-ups (rulings 2026-09-02): 000010/000011/000012 reward 1 under prep_rc 1 are VALID (prep probed files
  absent at agent time by design; exploit ran, post-stop test passed) → scorer `prep_probe_nonfatal_valid_reward1`;
  000023/000045 = review_carry (REVIEW-by-D4, mechanism recorded, not follow-up-blocking; judge/fullrun_v317_20260902/
  review_carry.tsv); D-70 as amended (rejects-right confirmed only with an executed test under clean prep; 000009 waits
  for its prep-clean re-run). batch21_slice1_followup (reviewer, 4 rows): 000024 touch-makefile → 1 ✓, 000087
  tie-dataset → 1 ✓ (both accepts-wrong confirmed; the lint-flagged 000087 repro was indeed a transcription defect),
  000101 redact → 0 (clean; refutation failed → reviewer's read), 000139 nolibc → 0 with prep_rc 1 again (no-libc
  class, same as 000097). Scorer: a clean reward-1 record in ANY results file (`clean_r1_any`, follow-up batches included)
  clears the follow-up reason and supersedes this run's non-run — slice-1 follow-up 5 → 3 (000009, 000101, 000139),
  `cleared_by_followup_batches` printed.
- Lint code `c_include_in_payload` (flag-only): a payload/heredoc/written file containing `#include <` when the repro
  invokes gcc/cc/g++/clang — "image may lack libc headers (3 slice-1 repros failed on this); mirror gold's build".
- v3.18 backlog (OPEN, no edit under the freeze): before writing a compiled repro, the judge must read how gold builds
  (headers, flags) and mirror it; a prep failure is not a refutation.
- batch21 extension (reviewer, 3 rows): 000009 reviewer-009-prepclean-rr expected 0 → 0 (rc 0, prep 0); 000023
  reviewer-023-uncomment-heredoc expected 1 → 1 (rc 0, prep 0); 000045 reviewer-045-blob-responder (security) expected 1 →
  1 (rc 0, prep 0). review_carry rows whose corrected repro scored a clean 1 are dropped from review_carry.tsv; 000009 is
  rejects_right_confirmed_D70 only if its row shows reward 0 with an executed test under prep 0.
- batch22_slice1_lastvariant (reviewer, 2 rows, expected 1): 000139 reviewer-139-shell-validator → 0 (rc 0, prep 0);
  000101 reviewer-101-full-exploit → 1 (rc 0, prep 0). Slice-1 follow-up list after fold: see the committed
  batch_fullrun_v317_slice1_followup.tsv. D-70 now confirms rejects-right from the all-files pool (000009 via batch21).
- D-72 tools/repair_ledger.py: per-task cost picture (labels/integers only; verifier_fix_lines = line count of the judge's
  verifier_fix field, text never written; repro_confirmed = clean reward-1 in any results file; _nosec twin) + stdout rollup
  (verdict × cost, verifier_fix_lines by verdict, four-way split SIMPLE&confirmed / MEDIUM-HARD-or-no_gradient / PASS /
  rejects-right). repro/judge_batches/repair_ledger_fullrun_v317_slice1.tsv committed.
- batch23_slice1_000139 (reviewer, 1 row, expected 1): reviewer-139-complete → 1 (rc 0, prep 0).
  Slice-1 follow-up list after fold: see the committed batch_fullrun_v317_slice1_followup.tsv.
- v3.18 backlog (OPEN, reviewer's lesson from 000139's four variants): a repro must satisfy every graded deliverable, not
  only the exploited assert; local pre-validation proves the mechanism, not completeness — read the exact failing assert
  in the tail before calling a 0 a refutation.
- D-76 (user): external/torchtitan-cotrain = read-only clone of github.com/yichuan-w/torchtitan @ yichuan/qwen35-port-cotrain
  (HEAD 74de5fa, gitignored, code only) for the actual-RL-harness checklist; findings in the ledger, not here.

## D-76 / D-78 / D-79 — training-harness record, executor harness mode, schema (2026-09-02; no rubric text change)
- D-76 record: reviews/harness_torchtitan_cotrain_coder_20260902.md (9 items, file:line receipts, unit tests + docs as
  spec) + phase0_README.md "Training harness (torchtitan cotrain @74de5fa)". Headline differences vs gate2: /tests path,
  nonce-guarded reward.txt, open egress, own rollouter, submit-gated grading; MGT/oracle mechanisms transfer 1:1.
- D-78 tooling: tools/repro_exec.py `--harness {gate2,torchtitan}` (default gate2 until the lead rules after the second
  reader): torchtitan mode uploads tests to /tests, runs `bash /tests/test.sh` as root after the agent slot, nonce-guards
  /logs/verifier/reward.txt exactly as grading.py:61-80/:247-275 (sentinel + chattr -i; reward accepted only if replaced),
  caps agent commands at 120 s and the verifier at 600 s, leaves network at the docker default; records `harness` and
  `reward_note`; a batch TSV `harness` column overrides per row. tools/tournament_to_batch.py `--harness` writes that
  column. Unit test tools/tests/test_nonce_guard.py: 4 cases (verifier replaces sentinel → accepted; sentinel unreplaced → 0;
  pre-written reward voided → 0; unwritable path → 0) — all pass locally. No batch re-run yet.
- D-79 (user: the breeding oracle must not be in the training image): tools/repro_exec.py `run_one` read end to end —
  it injects only the agent-slot payload (gold/solution tar or prep.sh, deleted after use) and tests/ at grade time; no
  mounts, no gold/, no generators. Versus training we OMIT the image ENTRYPOINT start and the /workspace seeds (our staged
  task dirs hold only instruction.md/setup.sh/tests/test.sh, 411/411). Table in the review file. Nothing to remove from
  --harness torchtitan; the two omissions are follow-ups.
- Schema (values blank until the second harness read): rubrics/v7_calibration.tsv gains `harness_dependence`
  (reference-file-writable | env-network | env-toolchain | tests-after-upload | verifier-only | none) and `harness_verdict_training`
  (PASS | REPAIR | rejects-right | unknown) — added to the delta's calibration-key block (58 rows, 11 columns); assembler
  run: prompt body byte-identical (asserted by diff after the header), content sha 6c92b57e51d9b9cf unchanged, file sha →
  2ae75cbe1f2a7cde (stamp only). tournament_score prints `harness_two_layer` (0 training verdicts yet; dependence census 58 blank);
  repair_ledger carries both columns. fullrun_v317 plan + slice2/rest/halves re-prepared to the new file sha (slice1 keeps
  the sha its judges read); 10 priors TSVs gained batch20-23 rewards for slice-1 ids. Candidates to fill later per the
  lead: 000753, 001022, 000048 (env-network → PASS under open egress); 000387, 000984 (env-toolchain → unchanged); any
  reward.txt-prewrite mechanism → void.
- Correction: commit 0b20f47 wrote these two sections through an unquoted shell heredoc, which ate every backticked token;
  this commit rewrites them verbatim.

## OQ-22 CLOSED; D-76 / D-79 CLOSED; D-78 submit gating; schema fill; D-75 amendments (2026-09-02; no rubric text change)
- OQ-22 CLOSED: training egress is OPEN (torchtitan cotrain rollouter, D-76: no network argument in the Daytona create
  params; corpus tooling confirms apt/pip/curl work); the api.openai.com allow-list was gate2-only (validation). Consequence
  for the venv/network rows: rejects-right(env) held under gate2 only → training verdict PASS (schema fill below).
- D-76 CLOSED: two independent reads agree on all 9 items (coder: reviews/harness_torchtitan_cotrain_coder_20260902.md;
  reader: reviews/harness_torchtitan_cotrain_20260902.md). Reader additions adopted: /oracle mounted only in offline audits;
  sessions deliberately retained (daytona.py:1055-1057); the only api.openai.com is offline synthesis. Coder addition
  adopted: grading is submit-gated (rollouter.py:1240-1270). Terminology (user): "gold" = breeding product, never in the
  image; "reference file" = setup.sh/image artefact test.sh reads; the dependence value is `reference-file-writable`.
- D-79 CLOSED by three consistent findings — scribe (60/60 expansions: only instruction.md/setup.sh/tests; 0 gold/, 0
  solution/, 0 Dockerfile; 0 build references to gold/solution/tests; filed as reviews/slice1_expansion_vs_image_20260902.md),
  defender (0/30 D-74 rows mutate a packaging artefact; every mutated file is setup.sh-created; 15 of 17 (a)-rows under
  /home/user via chmod), harness reader (/oracle only in offline audits) — plus the coder's direct image check on 3 MGT
  tasks (000011, 000012, 000024: each repro mutates an EXACT image-resident setup.sh-created reference file; no /oracle,
  no /tests in the images). All 21 slice-1 MUTABLE-GROUND-TRUTH rows touch image-resident, training-reachable files. Side
  finding: 4/53 slice-1 judge repros reference gate2's `/tmp/taskdata/tests/test.sh` (000009, 000010, 000011, 000012) — harness-path-dependent.
- D-78: `--require-submit` (default ON in torchtitan mode, OFF in gate2; `--no-require-submit` to override): a row whose
  TSV `submitted` column is 0 scores 0 without running test.sh (reward_note not_submitted_no_grading), mirroring
  rollouter.py:1240-1270. Our repro batches always "submit" (column default 1) — a repro is the agent's work plus an
  implicit submit. Default harness stays gate2 until the lead rules.
- Schema fill (D-80 as briefed — no D-80 text received beyond the fill instruction): 58 key rows now carry
  harness_verdict_training = current tier (REPAIR 52 / PASS 4 / rejects-right 2); harness_dependence filled where a
  source exists: env-network 3 (000753, 001022, 000048 — verdict PASS, prose note "rejects-right(env) under gate2 allow-list
  only"; 000122 is not a key row), env-toolchain 1 (000097, PASS unchanged), reference-file-writable 1 (000024 — the only
  key row in the defender's D-74 (a)/(b)/(c) table; that table classifies 30 slice-1 ids, 17 (a) / 0 (b) / 13 (c)); 53 rows
  blank pending a per-row source (000139 has no key row). Assembler: prompt body byte-identical, content sha 6c92b57e51d9b9cf,
  file sha → see plan.prompt (stamp only); plans re-prepared.
- D-75 amendments (design, still HELD, from the defender's read of torchtitan repair_hackable): our gate keeps gold FIXED
  (never edited) and adds (4) an independent reviewer-authored faithful-variant row (repro/batch24_faithful_variants*.tsv,
  mode repro, expected 1) must still score 1 after the patch — guards repair-induced rejects-right — and (5) a post-repair
  shortcut hunt: one Opus-xhigh judge pass on the patched task through tools/tournament_workflow.js (candidate-only plan,
  patched tests_dir); any new reward-1 repro rejects the patch. Confirmations: (a) the repair patches tests/ only (test.sh +
  fixtures it needs); setup.sh and the image are never modified; (b) the tests_dir override serves the patched test.sh plus a
  reference-file sha256 manifest built by the digest-probe row from the pristine image and checked before grading; (c) the
  faithful-variant rows run as mode repro, expected 1, against the patched tests_dir and are part of ACCEPT; (d) MGT-PERMS
  is that integrity pin (hash-pin setup-created reference files), not a permission change — the agent is root in training, so
  chmod is moot. MGT-PERMS candidate recorded as the single highest-leverage mechanical fix (defender: 15 of 17 (a)-rows),
  pending the reviewer's task→template mapping.

## TODO — repair (D-86, user: the repair track is parked; nothing implemented, nothing run)
- Mapping on disk: repairs/templates_fullrun_v317_slice1.tsv (reviewer) — 53 rows, validated: 53/53 full task ids, 0
  duplicate ids, 0 unknown templates, params parse. 9 distinct templates: MGT-PIN-HOME 18 (absorbs MGT-PIN; the
  integrity pin of setup.sh-created reference files), IVM-BUILDRUN 9, VTW-EXECUTE 8, VTW-FRESHDATA 7, IVM-ASSERTEXISTS 5,
  MGT-REGEN 3, IVM-RELAX 1 (the one that RELAXES — never under a blanket tighten), VTW-BINDHASH 1, AL-DELEAK 1.
- Accepted D-75 gate design (held): tools/repair_apply.py patches tests/ ONLY (test.sh + fixtures it needs; setup.sh and
  the image never change); repairs/<run>/<task_id>/{tests/test.sh, manifest.json} committable, NO unified .patch; repro_exec
  gains a `tests_dir` batch column; ACCEPT iff gold (FIXED, never edited) → 1 AND empty → 0 AND every prior reward-1
  repro → 0 AND the reviewer's independent faithful-variant row → 1 AND a post-repair Opus-xhigh shortcut hunt through
  tools/tournament_workflow.js finds no new reward-1 repro; `--mechanical-only` refuses IVM-RELAX/AL-DELEAK by hard assert;
  MGT-PIN-HOME = sha256 manifest of the pristine-image reference files built by a digest-probe row (not a chmod — the agent
  is root). Digest-probe batch NOT run (D-86); repairs/manifests/ not created.
- Executor omissions sized (from the open-instruct 15k source record tmax15k_generic.parquet, 411/414 of our ids present,
  plus `docker image inspect` on the 116 already-pulled task images, no pulls): ENTRYPOINT or CMD directive in
  container_def 0/411 (security 0, other 0, slice 1 0); pulled images Entrypoint=null, Cmd=["/bin/bash"] 116/116;
  `environment/seeds` or `seeds` keys in any record field 0/411 (test_initial_state is non-empty for 411/411 but carries no
  seeds — torchtitan's seed_workspace is a no-op without environment/seeds). Conclusion: neither omission affects any of
  the 414; our gold/empty/repro results stand. The HF Tmax-Tasks-Clean splits (39 columns) carry no seeds/entrypoint column.
- D-78 flags added anyway for fidelity: `--start-entrypoint` (torchtitan mode: detached `setsid nohup <ENTRYPOINT> &`
  before the agent slot; no-op when the image defines none — a bare CMD is a shell, not a service) and `--seeds-dir <path>`
  (uploaded to /workspace before the agent; batch TSV `seeds_dir` column overrides per row); both default off. run_one
  now follows a pure `harness_plan()` step list recorded per record; tools/tests/test_harness_plan.py (4 cases) asserts
  the gate2 plan is byte-identical regardless of flags and that torchtitan adds steps only when asked.
- Queued (not run; needs the reviewer's reviews/env_rows_remeasure_20260902.tsv): repro/batch25_env_remeasure.tsv — gold +
  empty per listed key row under harness=torchtitan with --start-entrypoint, plus 000753/001022/000048/000122 so OQ-22
  closes on a measurement. Caveat for its header: egress from our docker host is not the Daytona tenant's policy.
- Rulings on 7e8ce8a: harness_dependence blanks STAY blank — the key records sourced values only, no defaulting. The 4
  slice-1 repros referencing /tmp/taskdata/tests/test.sh (000009, 000010, 000011, 000012) are the judges' trailing
  self-probes, not exploit steps: lint code `gate2_path_in_repro` (warn) added; in --harness torchtitan mode repro_exec strips
  TRAILING lines that reference the verifier path or reward file before running (count logged and recorded as
  self_probe_lines_stripped; lines before the last real step are never touched). v3.18 backlog: "never probe the verifier
  path or reward file inside a repro". Unit test added (trailing-only strip; leading probe untouched; lint code).
- OQ-23 CLOSED on three consistent sources: coder 0/411 ENTRYPOINT + 0/411 seeds; reviewer 0/60 path-level dependence;
  reader: seeds live only in tmax-15k rows' tmax["fixtures"], absent → no-op.
- TODO — repair, additions: mechanical set = 32 rows (MGT-PIN-HOME 18 + VTW-EXECUTE 8 + IVM-ASSERTEXISTS 5 + VTW-BINDHASH 1);
  batch24 faithful-variant scoping: 14 output-grading tasks need a variant (~6 locally validatable, ~8 need container
  pre-validation); pin rows need no variant; 000161 still needs a template row; except-fallback graders 000069, 000147
  (+000066 borderline) per the reviewer's hand scan — to be cross-checked by the training repo's static scanner (D-89).
- batch25_env_remeasure rebuilt to the lead's set (HELD): 10 tasks × (gold + empty) = 20 rows — 000729, 001022, 000048,
  000753 (network), 000097, 000139 (toolchain; apt works under training), 001125 (missing-service; --start-entrypoint),
  000122 (security venv), 000387, 000984 (gitconfig, expected unchanged); header caveats: host egress ≠ Daytona tenant
  policy; gold must pip/apt on its own, nothing pre-installed.
- D-89 static grader scan: tools/scan_degenerate_graders_wrapper.py imports the training repo's
  evolution/scan_degenerate_graders.py (never edited) and applies its classifiers (`fallback_hits`, NEGATIVE, NET) to the
  python our test.sh scripts embed (header-agnostic heredoc/-c extraction; body must parse as Python). Slice 1: 60/60 tasks
  carry exactly one embedded pytest block (0 tests/*.py files, 0 parse errors). Labels → reviews/slice1_degenerate_graders_scan.tsv
  (task_id + counts only): fallback 1 [000092], all_negative 0, verifier_network 2
  [000045, 000070]. Agreement with the reviewer's hand scan: all_negative 0 = 0 ✓; except-fallback
  DISAGREES — the reviewer's 000069, 000147 (+000066 borderline) are not flagged by the tool (its `fallback_hits` requires an
  except branch that assigns the value the try block fetched; 31/60 scripts contain an `except` token, 30 of them unflagged),
  while the tool flags 000092 which the reviewer did not name. Both are leads, not verdicts; the two
  definitions differ and neither side has read the other's rows here.
- TODO — repair (reviewer retag of repairs/templates_fullrun_v317_slice1.tsv grader_shape after b215b94, committed):
  except-fallback-lenient 000069, 000147; borderline 000066; except-correct-fail 000040, 000045. 000147: a pin-only fix
  leaves its except:continue lenient — add branch hardening; 000069 is covered only incidentally by the non-empty-log
  assert. (D-89's static `fallback_hits` flagged neither.) 000092_a7e4987d: the reviewer re-read the full function — the same asserts run after the fallback, a robust alternate-source read: scanner false positive, NO change; both D-89 verifier_network hits (000045, 000070) are loopback to the agent's own deliverable, not egress.
- D-91 (coder, independent of the reader's timeline): reviews/training_rollout_timeline_coder_20260902.md — 9 phases
  (0 create … 8 teardown) with user/paths/writability/agent influence and file:line receipts; harness_plan() mapped onto
  them. Unmodelled by --harness torchtitan: (1) `_prepare_runtime` (pre-agent mkdir of /workspace /output /logs/verifier,
  /app symlink, shell wrapper, cwd /app); (2) the turn-loop mechanics (per-turn 120 s, observation truncation, format-error
  termination, 64 turns / 2400 s, submit marker beyond a boolean); (3) dense CTRF reward, wrong-submit penalty,
  infra_failed → NaN; (4) Daytona platform/resources (cpu 2 / mem 4 GiB vs our 1 / 2); (5) verifier cwd = Daytona session
  default (unknown here). Phases 3-7 otherwise 1:1 (state persistence, reset scope = reward+ctrf only, upload set, root
  exec, nonce guard, submit gating).
- D-91 reconciled: reviews/training_rollout_timeline_reconciled_20260902.md is the record the rubric cites. 7 differences
  between the reader's and the coder's timelines, 7 resolved by code re-read (6 for the reader: pre-grade literal incl.
  `chattr -R -i <dir>` + `mkdir -p`; [0,1] clamp in `_parse_reward`; budgets 2400/1200/7200-floor; guard formula; verifier
  stdout discarded; `submitted is not False`; 1 ordering label: uploads precede the reset). Two were executor fidelity gaps,
  fixed in tools/repro_exec.py --harness torchtitan (same five-statement reset; clamped reward parse, raw kept as reward_raw);
  unit tests 6/6 + 5/5. `_prepare_runtime` fidelity (dirs before the agent, /app symlink, cwd /app) landed in 384a17f.
- v3.18 DRY RUN (D-93; scratch only — rubrics/, the delta and the assembler untouched; freeze holds): the Fable
  reviewer's reviews/v318_edit_script_20260902.md (38 pairs P01-P38; 15 assembler splices; C40 revisions for
  P10/P22/P23/P32/P37; reconciliation-update NEW for P01/P02/P03 + the P05 glossary paragraph) applied by exact anchor to
  scratch copies with the 15 splices injected as unique-anchor `.replace()` calls on the assembled body (editing v6_core.md
  is NOT viable: the assembler slices it by line number). Result: 37/38 pairs apply (every anchor matches exactly once);
  P32 fails to parse — its target "delta:173, append after the D-42 fix bullet" carries no quoted anchor (needs one). Scratch
  assembly: delta 484 → 512 lines, prompt 607 → 642 lines, content sha 3d0e076357c46fe3, stamp V7-delta v3.18; assembler
  post-conditions ok; global asserts all pass (no "oracle", no /tmp/taskdata, no "codex exec", 0 task ids, D7 absent,
  placeholders present, 7 forbidden phrases absent, JSON block issued twice); per-pair asserts 42/45 pass — the 3 failures are
  P32's two (not applied) and P04's "not grade-time-only" (the NEW text reads "NOT grade-time-only": a case mismatch in the
  script's assert, not in the text). verifier_network ×3, harness_class ×4, repro_cost/soundness_probe field lines ×1. The 18
  "Source fill-in" rows are citation notes (pid, snippet, phase) — not machine-applicable until they name the exact sentence
  and § number. Line count lands below the script's ≈660 estimate (642) with P32 missing. The real apply is one command once
  P32 gets an anchor and the P04 assert is fixed.
- D-94 executor fidelity audit (reader, reviews/executor_fidelity_audit_20260902.md) — fixed before any torchtitan run:
  (O2) the self-probe strip called log() before `def log` and outside the try → UnboundLocalError escaped run_one whenever a
  line was stripped; now a pure `prepare_agent_cmd()` called inside the try after the logger exists, plus an AST test that
  `def log` precedes the first log() call in run_one. (A7) tests upload used `docker cp <dir> cid:/tests`, which NESTS to
  /tests/<basename>/ when /tests already exists (agent-created) — now `mkdir -p` + `docker cp <dir>/. cid:/tests` (contents
  form, as training's mkdir + write_file overwrite in place). (A4) the 120 s agent cap was a host-side subprocess timeout
  killing the docker-exec client, not the in-container process — torchtitan mode now wraps the agent command in an
  in-container `timeout --signal=TERM --kill-after=10s 120s bash` (daytona.py:166-172 form); host timeout kept as backstop.
  (A5, documented approximation) we run `bash -lc`; training runs a non-login `bash <script>`. Unit tests 14 total
  (nonce 6 + plan/strip/O2/A7/A4 8).
- batch25 (HELD) control rows per the defender's review: rewrite-post now a bounded continuous overwrite loop (300 ×
  echo 1 + sleep 0.05) so a surviving writer can win the verifier-writes/executor-reads race; the 000011 pre-write row
  relabelled ctrl-reward-prewrite-weak (overdetermined — its verifier also writes 0 when unsolved); an isolating pre-write
  row on a task whose test.sh does not write reward.txt when unsolved requested from tmax-log-reviewer; header notes the
  single-read + force-teardown window. batch24_faithful_variants_defender.tsv filed with expected_reward=1 on all 39 rows
  (defender's stated intent); unrun under D-86.
- Reconciled timeline rewritten with § numbering for citation (reviews/training_rollout_timeline_reconciled_20260902.md,
  §0-§8): adopts the reader's 12-difference notes (1 the coder's way — /app→/workspace symlink only when /app is absent;
  11 the reader's way — recursive `chattr -R -i` reset scope, discarded verifier stdout, grade order, immutable pre-placed
  test.sh → infra_failed → NaN, adapter session in §0, per-turn cap = SamplingConfig, budgets 2400/1200/7200, submit break
  before the observation append, `_prepare_runtime` = vanillux agent's first statement and vanillux-only; unknowns U1 cwd,
  U2 image contents answered empirically for slice 1). New §0.net egress row (no network parameter, no field, no launcher
  knob; egress works in-repo; tenant policy unknown). Implication recorded: training DISCARDS verifier stdout/stderr
  (grading.py:263-268) — a reward-0 rollout carries no diagnostic in training; (a) any rubric sentence implying inspectable
  verifier output from training is false; (b) our executor keeps capturing tails in torchtitan mode (reward fidelity, not
  logging fidelity); (c) unexplained gold-0s in later slices can only be diagnosed by us. Executor approximation list
  (single command vs turn loop; bash -lc vs bash <script>; host timeout backstop; 1/2 vs 2/4 resources; single read then
  force-remove).
- D-97 (defender's design) in tools/repro_exec.py --harness torchtitan: after test.sh the reward is read at t0 (canonical —
  training reads once; full nonce verdict + clamp) and again after `--grade-read-delay` (default 2.0 s; TSV column
  `grade_read_delay`; 0 disables) as reward_tdelta, labelled "upper-bound, not training-faithful"; `exploit7_observed` is
  set when the two reads differ or when t0 is a verifier-written 1 on a row whose expected_reward is 0 (a value the unsolved
  test cannot produce); scoring uses reward_t0 only. Unit test on the pure decision (5 cases). gate2 plan unchanged.
- Isolating pre-write control (lead ruling after the log-reviewer read all 42 non-security slice-1 verifiers: every one ends
  in `if pytest; then echo 1; else echo 0; fi`, 0 early-exit — no shipped verifier isolates the reset): implemented as a
  container-backed test, tools/tests/test_prewrite_isolating_container.py — synthetic tests_dir whose test.sh writes reward 1
  ONLY on success, agent slot pre-writes reward.txt=1, real torchtitan path on a small local image (ubuntu:22.04; skips if
  docker/image absent) → reward '0', reward_note sentinel_unreplaced_by_verifier — PASSED. batch25 (HELD): the defender's
  repro/batch25_ctrl_prewrite_isolating.tsv loop row (ctrl-reward-rewrite-loop, 000011, expected record) merged as the row of
  record; my sleep-40/loop row dropped; the 000011 pre-write row stays as ctrl-reward-prewrite-weak. 22 rows / 11 tasks.
  Unit tests: nonce/clamp/D-97 7 + plan/strip/O2/A7/A4 8 + container-backed 1 = 16.
- v3.18 DRY RUN #2 (scratch only; freeze holds) with the reviewer's "Dry-run fixes" (P32 exact anchor, P04 assert case,
  fill-in rows non-applying, P05 after P04's sentence): 38/38 pairs apply (24 delta-side replacements incl. P33's two, 15
  assembler splice ids / 16 replacements on the assembled body); assembler post-conditions ok; 8/8 global asserts pass; 45/45
  per-pair asserts pass. Scratch delta 513 lines, prompt 643 lines, content sha 4f5a70ec5b40365b, stamp V7-delta v3.18;
  diffstat vs the live prompt 85 lines only-scratch / 49 only-live. Size note: +1 line vs dry-run #1, not the reviewer's
  expected +4 — the C40-refined revision of P32 carries ONLY the external-network bullet, so under the override rule the base
  pair's post-verifier-reward-rewrite bullet is dropped; the reviewer must say whether the revision replaces the whole pair
  or only its first bullet. Snapshot for §Pass 15b: scratch/v318_dryrun/pass15b/. The real apply is one command on the lead's
  go after the freeze lifts (scratch/v318_dryrun/apply_v318_v4.py pointed at the live paths).
- §Pass 15 NOT READY → applier v5 + D-99 asserts + DRY RUN #3 (scratch/v318_dryrun3; freeze holds; rubrics/ and tools/ prompt
  text untouched). Applier rules implemented: NEW cells verbatim (¶ newline, ␣ space, ⏎ trailing newline; no quote
  stripping on correction rows); CONDITIONAL rows skipped; `\|` literal; multi-line cells whole; precedence Pass-15
  corrections > Reconciliation update > C40 refined > base, P32's final two-bullet text from the "Script rule" section;
  multi-fragment cells (P25, P33) parsed from the RAW cell on both sides — the earlier applier parsed NEW after
  un-escaping, which silently mis-paired P25's two fragments and dropped P33's second replacement in dry-runs #1/#2 (the
  mechanical asserts passed; D-99 now catches the class at assembly). Result: 38/38 pairs apply (24 delta-side replacements
  incl. P33's two; 15 assembler splice ids / 16 replacements); strict assembler (D-99 a-d) PASSES; global asserts 8/8;
  per-pair asserts 46/46 (incl. P10 "EXTERNAL host", P04 "NOT grade-time-only", P32's three). Scratch delta 519 lines, prompt
  651 lines (reviewer's estimate 645-647), content sha a61de39935ce4428, stamp V7-delta v3.18; diffstat vs live 91 / 47;
  harness_class ×5, verifier_network ×4, JSON block parses after placeholder normalisation. Snapshot for §Pass 15c:
  scratch/v318_dryrun3/pass15c/. Real apply = apply_v318_v5.py on live paths + assembler, on the lead's go.
- D-97 amendments (reader's re-audit of b54d35e): `exploit7_observed` now means class #7 only (t0 ≠ tdelta divergence); the
  expected-0 / t0 = 1 case is a class-neutral `unexpected_reward1` (+reason: a verifier paying an unsolved task, classes 1-3);
  reward_t0 labelled "lower bound on training's exposure window" (our single docker-exec gap vs training's two Daytona RPCs,
  grading.py:263/:269), reward_tdelta "upper-bound, not training-faithful"; the single-task `run` path takes
  `--expected-reward`; all D-97 keys are ALWAYS emitted (null / "not measured" when --grade-read-delay is 0). Unit tests
  for the split and the always-emit. Dry-run #3 snapshot mirrored to scratch/v318_dryrun/pass16/ (the path the lead named).
- TODO — repair/batch24: Tier C (11 static-only rows incl. the pip/cargo group) must run under --harness torchtitan because
  they need egress; tiers per reviews/defence/batch24_run_order_20260902.md. Unrun (D-86).
- §Pass 16 (Fable): all seven Pass-15 breaks fixed as written, regeneration byte-identical; two residuals from the reviewer's
  own rows → "Pass-16 corrections" (P25 soundness_probe field verbatim, no U+2026; P03 grade-order sentence: uploads precede
  the reset, per the reconciled timeline §4). DRY RUN #4 (scratch/v318_dryrun4; freeze holds): applier v6 adds the Pass-16
  tier (whole-line replacement of the soundness_probe field on the assembled body; the P03 sentence replaced in the delta after
  all pairs). 38/38 pairs + 2 partial corrections; strict D-99 assembler passes; global 8/8; Pass-16 asserts 3/3 (no U+2026 in
  the JSON block; "the uploads precede the reset" present; "rollout-visible state only (the instruction" ×1 in the block);
  per-pair 46/46. Delta 517 lines, prompt 649 lines, content sha 4e72f867d12ceaee, stamp V7-delta v3.18; diffstat vs live
  89/47. Snapshot scratch/v318_dryrun4/pass16b/. Non-blocking, for the apply commit (both are prompt-text/assembler changes
  under the freeze): P18 confidence placeholder should use single quotes around Look for BOTH directions; F9 — the header
  suffix-strip regex should drop a trailing "(replaces …)" even after an earlier parenthetical.
- Repair mapping FINAL (log-reviewer): repairs/templates_fullrun_v317_slice1.tsv re-validated — 53 rows, 53 unique full ids,
  9 templates unchanged; grader_shape: except-fallback-lenient 2 (000069, 000147 — fixer must harden the lenient branch),
  except-fallback-borderline 1 (000066), except-correct-fail 2 (000040, 000045 — no action; 000045 also verifier_network-
  localhost, loopback to its own webhook), scanner_candidate 2 (000092_a7e4987d, 000070_a7d62d01 — read and cleared,
  candidates not defects), blank 46. Filed. Still unrun (D-86).
- D-99 gains two permanent members: (e) no literal U+2026 inside the JSON block (holds on v3.17.1); (f) for version ≥ v3.18 the
  D1 grade-order sentence must contain "the uploads precede the reset" (version-gated: v3.17.1 has no such sentence). Assembler
  re-run on v3.17.1: passes, prompt byte-identical; the scratch #4 assembler with the same asserts passes on the v3.18 candidate.
  Reader's R1/R2 on 28e89d1: one shared `unexpected_reward1_flag()` helper (R1) and the reason set even at delay 0 (R2), unit
  test added. F11 (single quotes in P18's confidence placeholder) has no correction row in the script yet — the reviewer's
  authoring item; recorded for the apply commit.
- D-97 carry-forward (reader): the exploit7_observed figure never travels alone — tournament_reward_table.py adds
  exploit7_observed / unexpected_reward1 / exploit7_note columns (the note is a column, not a `#` header line, because
  tournament_prepare, tournament_score and repro_coverage DictReader these tables directly); tournament_score prints a `d97`
  block (second reads measured, exploit7 count + ids, unexpected_reward1 count + ids) with the lower-bound footnote whenever
  a results file carries the fields. gate2 records leave the columns blank; verified on batch_regress_v317 (66 rows, 0 D-97
  fields → 0 d97 block).

## V7-delta v3.18.1 — 2026-09-02 (contradiction sweeps: Fable S-01…S-19 ∪ Opus C1…C7; 19 merged rows / 25 applier entries / 31 operations)
- Source reviews/v3181_change_list_20260902.md (Fable's adjudicated union; Opus gates: list 19/19 OK, hunks 23/23; Fable §Pass 19 on dry-run #1
  18 READY + M-02c wording → #1b; M-12 folded into M-02c → #1c). Provenance: dry-run #1 content sha ebeea01ca41a3128, #1b-pre 58d3351ca7ad5453 (aa8cb8f), intermediate aaf0378a9eb9162f, real #1b (final, after Fable's adjacency sweep: M-02c re-anchored,
  M-12 folded, M-06/M-08/M-09 re-worded) 07a853e26e2ef1dd, under scratch/v3181_dryrun1d/ (the lead's #1d = a93ab71's real #1b, byte-identical); applier scratch/v3181_dryrun1d/apply_v3181.py (v6 cell rules, every anchor count==1, 31 ops / 0 failed).
- BLOCKING resolutions: M-01 (S-01) post-verifier reward rewrite removed from the D8 SIMPLE ladder, class stated once below it with its tier rule
  (harness_class: reward_rewrite, repro_cmd null, tier unmoved, no per-task verifier_fix); M-02 (S-02) `external-fetch` D4 format (a–d: enum
  token, D3→D4 link with the single coding of the class (M-12 folded in), D4 downgrade-sentence parenthetical); M-03 (S-03) cosmetic unenforced clause is CLEAN, load-bearing is
  INSTR-VERIFIER-MISMATCH + accepts-wrong; M-04 (C1) INSTR-ENV-MISMATCH vs OTHER precedence for a never-started service.
- MINOR M-05…M-18 and STYLE M-19 per the change list (harness_class guidance, CLEAN-list confidence wording, applier residue removed (S-06/C7),
  D1 cwd per harness + grade-time no-cd rule (S-07, reader-verified), dual-case soundness `unstable` (S-08), nine verdict labels (S-09/C2),
  rollout vs grade-time network (S-10), single coding for the external-network class (S-11/C6), judge-time REJECT-PROVEN (S-12), PASS row
  `checks` field (S-13), D6 doc parity (S-14), VTW→MGT precedence (C3), D7 terminology with labels preserved (C4), `/verify` dropped (C5),
  CHANGELOG-id parentheticals deleted (S-19)).
- M-17 applied assert = generalised form (lead ruling A): batch labels, the oracle-symlink / oracle-execwrapper tokens and the /app/*oracle* glob
  are exempt as literal keys / probe patterns; the five prose sites reworded.
- D-99(h) "no applier residue" is now permanently ON (--no-strict-residue is the only escape hatch); the v3.18 known-failing gate is resolved here.
- Prompt 652 → 658 lines; delta 520 → 524; content sha 1d69121f0bd0b41d → 07a853e26e2ef1dd.

## V7-delta v3.18 — 2026-09-02 (D-93 script applied under D-98 gates ①-④; freeze lifted; prompt regenerated)
- Source: reviews/v318_edit_script_20260902.md (Fable) — 38 pairs P01-P38 (23 delta-side edits incl. P33's two + 15
  assembler splices anchored on v6_core text or the assembler's own d6_fields/mgt_note strings) + 2 Pass-16 corrections
  (P25 soundness_probe field verbatim; P03 grade-order sentence "the uploads precede the reset") + 3 non-blocking items
  (P18 single quotes around Look for BOTH directions inside the confidence placeholder; F9 header suffix-strip now removes
  the LAST "(replaces …)"/"(NEW …)" parenthetical even after an earlier one; the P03 sentence re-wrapped at ≤100 columns).
  Precedence: Pass-16 > Pass-15 corrections > Reconciliation update > C40 refined > base; P32 = both bullets. Applied by
  scratch/v318_dryrun4/apply_v318_v6.py logic on the live paths; the 16 splices live in tools/assemble_v7_prompt.py as
  count==1-asserted replacements on the assembled body.
- Provenance: dry-run #4 snapshot scratch/v318_dryrun4/pass16b/ content sha 4e72f867d12ceaee (Fable §Pass 17 READY); the
  live v3.18 differs from it only by the three non-blocking items (quotes, header strip, wrapping) — content sha in the
  header stamp. New permanent D-99 members this commit: (g) no double quote inside a `<…>` placeholder in the JSON block.
  Gates: assembler rc 0 with the full D-99 set, byte-identical regeneration, unit suites green.
- What v3.18 changes for judges (from the script's ids): harness facts rewritten to the training rollouter (D1: root, Daytona,
  /tests at grade time only and submit-gated, nonce-guarded reward, open egress, seeds → /workspace, /app→/workspace symlink,
  no PID 1; glossary reference file / gold / seeds; needs: network|seeds|entrypoint on repro_cmd); C2 probe from
  rollout-visible state only; accepts-wrong `reason`/`who_could_trigger` must name the decisive assertion (REVIEW if not);
  `verifier_network` none|loopback|external and `harness_class` reward_rewrite fields; anchored 5-check confidence;
  MEDIUM requires the repro to contain the work; compiled repros mirror gold's build; token-in-data is not a mechanism.
- Tooling (ruling A, reward tables): repro/judge_batches/batch_regress_v317_reward_table(.tsv|_nosec.tsv) (66/64 rows) and
  batch_fullrun_v317_slice1_reward_table(.tsv|_nosec.tsv) (113/81 rows) regenerated from their results JSONL so the D-97 columns
  exploit7_observed / unexpected_reward1 / exploit7_note exist (blank on every row: gate2 records); row counts unchanged, every
  pre-existing column byte-identical EXCEPT repro_lint_warn on 4 slice-1 repro rows — task_000009_dbc34e08 (rr), task_000010_7c427a6f,
  task_000012_58fdcccc, task_000011_24e54d45 (aw) — '' → gate2_path_in_repro, because that lint code was added at 0b825a8 after the
  table's last generation at 0db4e4c; lint is a screen, the scorer re-lints at run time, no reward or verdict changes.
- D-99(h) "no applier residue" (Fable S-06) — known-failing gate on v3.18, RESOLVED in v3.18.1 (residue assert permanently on): tools/assemble_v7_prompt.py
  applier_residue() flags prose lines (JSON block and fenced code excluded) containing `.append`, `.replace(`, `""`, or a double quote
  directly after a sentence-ending period. On live 9fc6e11 it fails on rubrics/v7_prompt.md :272 (`… attach the repro (D4).append "`)
  and :274 (`MUTABLE-GROUND-TRUTH."`) — an edit-script verb pasted into the D2 prose that every existing assert and §Pass 18 missed.
  Enforced only with `--strict-residue` (default off: stderr WARNING listing the lines; commit gate rc==0 kept); the flag flips on in
  the v3.18.1 apply. Prose NOT edited here (it is part of the v3.18.1 script). Unit test tools/tests/test_assembler_residue.py 4/4
  (four classes caught, clean prose passes, JSON/fence exclusion, live prompt known-failing at :272/:274).
- v3.18.1 dry-run #1 (scratch only; live frozen): reviews/v3181_change_list_20260902.md 25 rows/31 applier ops all applied, scratch assembler with
  --strict-residue rc 0 byte-identical, prompt 657 lines, content sha ebeea01ca41a3128; record reviews/v3181_dryrun1_record_20260902.md.
- Correction + fix (reader flag, scribe gaps): commit 094ac42's message said tools/tests/test_cwd_fidelity.py 7/7 — the file had 6 tests (the
  runner printed a literal 'ok 7'); a real 7th test now covers the reader's vectors. repro_stats.relative_paths() found split points on the
  raw string, so a quoted `|` (sed -i 's|a/b|c/d|') tore the statement and fired relative_path_in_repro; split points are now found on a
  same-length quote mask. The earlier 8/232 census was an ad-hoc glob with no persisted file; tools/lint_census.py persists it as
  reviews/lint_census_relative_path_20260902.tsv (scope in README): corrected count 7/223 repro rows.
- v3.18.1 dry-run #1b (scratch only; live frozen): change list as edited by Fable (M-02c "(D3 tier table)", M-17 generalised assert): 31 ops / 25
  rows / 0 failed, strict D-99 a–h incl. residue rc 0 ×2 byte-identical, prompt 657, content sha 58d3351ca7ad5453; diff vs #1 = stamp pair + :95 only.
- v3.18.1 dry-run #1c (scratch only; live frozen): Fable folded M-12 into M-02c 29 s after the #1b assemble; re-run on the folded list — 31 ops /
  25 rows / 0 failed, strict a–h incl. residue rc 0 ×2 byte-identical, prompt 657, content sha aaf0378a9eb9162f; diff vs #1b = stamp pair + :95.
- v3.18.1 real dry-run #1b (scratch only; live frozen; aa8cb8f = #1b-pre, 09ac29a = intermediate): list after Fable's adjacency sweep (M-02c re-anchored
  before the loopback sentence + M-12 folded, M-06/M-08/M-09 re-worded): 31 ops (30 + M-12 no-op) / 25 rows / 0 failed, strict a–h incl. residue rc 0 ×2
  byte-identical, prompt 658, content sha 07a853e26e2ef1dd; diff vs #1 = stamps + :13 + :95 + :504-506 (13 lines); asserts 32/0.
- v3.18.1 #1d = the a93ab71 real-#1b run re-emitted into scratch/v3181_dryrun1d/ (byte-identical, content sha 07a853e26e2ef1dd); staged apply gates on it.


## SPEC_RULES rev11 + SPEC_AGENT_PROMPT (2026-09-03, tmax-log-reviewer)
- Folded the full §7.1–§7.7 template sections (Fable wave-4 pass b3ba7b2, all 15 rows) into SPEC_RULES.md (406 lines):
  VTW-FRESHDATA / AL-DELEAK / IVM-ASSERTEXISTS / MGT-REGEN / IVM-RELAX / VTW-BINDHASH / VTW-SLOW.
- R4.7 rerun-safety now stated in END-STATE form (patched-verifier final state == unpatched, 000027 graph_index).
- R4.9 (no-safe-mandated-compile) promoted to a TOP-LEVEL, every-template rule; header-less image family 000080/000024/
  000070/000711/001209 (5 tasks / 4 templates) + the 000023 site-packages install caveat (gold tars cover /app,/home,/tmp).
- §7.5 IVM-RELAX carries soundness_cite + relaxed_lines; the adds-only apply-gate is waived only for it (coder).
- Prompt: the diff addendum now scopes to every diff-emitting section (§4/§5/§7.1–§7.7), IVM-RELAX-modifies-only vs adds-only.
- Coder items: B2 (schema/applier waiver), B6 (validator DIFF_TARGETS), M3 (repair_rows pair table + probe.correct_input), W3 probe shape.

## SPEC_RULES rev11 corrections — 001209 drop, R1.6 corpus-conditional, E-32 CLOSED (2026-09-03, tmax-log-reviewer)
- R4.9 header-less family corrected: DROP 001209 → 000080/000024/000070/000711 (4 tasks / 3 templates:
  IVM-BUILDRUN ×2, VTW-EXECUTE, AL-DELEAK). 001209 is NOT a member — it is a daytona-repro harness gap (gold
  builds fine on the training image) with an over-tight Makefile pin (cb654f5), per my batch29 review row; the
  earlier rev11 fold's "5 tasks / 4 templates" reflected a crossed retract that I have now reversed on my
  committed evidence. 000023's site-packages install caveat is a caveat of the SAME class, not a fifth member.
- R1.6 rewritten as CORPUS-CONDITIONAL (was: "ENTRYPOINT STARTED for every capture", E-32). A pin is captured in
  the filesystem state training actually produces for its corpus. TMAX = NO entrypoint — the training rollouter
  gates the start on `if sample.entrypoint:` (rollouter.py:1197) and TMAX samples carry entrypoint=None, so the
  backend execs directly against the built image and never auto-runs ENTRYPOINT [verified adversarially in
  reviews/entrypoint_training_verification_20260903.md, VERDICT HOLDS]. Capture TMAX WITHOUT --start-entrypoint;
  captured_with.entrypoint = not_applicable:tmax_corpus. RTS-shaped rows carry a real entrypoint
  (prepare_rts_data.py:482-484) → capture STARTED. An entrypoint-started capture of a TMAX task is the
  NON-matching state.
- E-32 CLOSED (amended per ops verification): NO capture ever started an entrypoint for a TMAX task — the daytona
  backend never resolves argv (daytona_backend.py:219-223) and 0 of 120 pin specs carries an entrypoint_started:True
  row; the only statuses seen are no_entrypoint / inspect_failed / not_requested. So every TMAX capture to date is
  already the training state; captured_with.entrypoint = not_applicable:tmax_corpus on ALL pin specs. As a RULE an
  entrypoint-started TMAX capture WOULD be the non-matching state, but this backend cannot produce one — closed by
  construction, not by re-capture. The image-inspect outcome lives in captured_with.inspect and is NOT a validity
  condition (the backend never starts the entrypoint regardless). The only drift measurement taken is batch W1S
  (2aaa84c), a 10-spec seeded sample: 28/28 pins match. The old rule wrongly mandated --start-entrypoint for every corpus (the non-matching state for TMAX,
  and one this backend never produces); the 27 held no-entrypoint pins already sit in the training state and are
  promotable. [Supersedes the earlier draft of this bullet, which spoke of re-capturing wave-1 started-captures —
  ops confirms none exist.]

## SPEC_RULES rev11.1 / rev11.2 — §3 validation-arm discipline (2026-09-03, tmax-log-reviewer)
- rev11.1: Fable structure pass folded (N1–N11 + D1); §7.7 VTW-SLOW single-probe form; R3.5 (BINDHASH/000083) — every
  ADD-ONLY-diff validation arm must pass ALL originals and fail ONLY at the added check (IVM-RELAX inverted at the
  relaxed check, §7.5, D1).
- rev11.2: R3.6 (000023) — a 0 counts only if the arm demonstrably RAN; the spec DECLARES a per-arm execution marker
  (echo ARM-RAN:<label> >&2 / touch /logs/verifier/arm.<label>), and a marker-absent 0 is VOID; a broken-but-recoverable
  judge row is reconstructed and becomes the primary exploit arm.
- R3.6 fifth VOID class — the command that RAN differs from the arm as AUTHORED (000606 / 000701-part2). CORRECTED
  attribution: the coder proved the executor byte-exact (round-trip 38925df + base64-encoded daytona write), so the
  mangling was NOT the executor and NOT the repairer's hand-append (both earlier drafts): ops built
  repro/wave2_batchH6_run.tsv by SPLITTING the repairer's CORRECTLY-quoted rows on TABS and rewriting them WITHOUT a csv
  round-trip, which re-quoted the two cells that contained quotes. Cause = a derived TSV written by any writer that does
  not re-parse its own output. Guards: append-reconstructed subcommand (1ee063a), leading-quote emission lint,
  writer-side re-parse check (ops), and cmd_sha256_cell/cmd_sha256_executed invariant (mismatch = VOID).
- UNDER-TIGHT reachability (ops 2471abb): repair_tally's UNDER-TIGHT branch was UNREACHABLE all campaign — the reward
  table never emitted tests_source. Fixed; prior batches re-tallied. §3 R3.7 now maps verdict names to evidence:
  UNDER-TIGHT = the repair measurably did not fire; INCONCLUSIVE = no measurement.
- UNDER-TIGHT widening LANDED (ops b95bca2): repair_tally flags any repaired-tests row with expected 0 that scored 1,
  with the procedural-gold-0 and R3.3/R3.4 control-twin-0 exceptions (matches §3 R3.7).
- Wave-3 pin-order scope (ops scan 69160f4): the empty/PENDING-manifest class hit 33 of the 39 wave-3 pin specs (18
  validated, 15 applied), NOT 8 — cause was the wave-3 pipeline ordering apply before capture. The six second-pass specs
  are the positive control (captured waves 1–2 before apply → pins active). Legitimate demotion validated → applied when
  validation evidence is voided (pin-dependent rows measured with no pin): repair_apply --refresh-pins does it (rewrites
  the manifest, sets status applied, moves validation_ref → validation_ref_prev, notes "pins refreshed; re-validation
  required", in the structured refresh_note field mirrored into notes — coder 6a386c6 + follow-up); the 18 are demoted
  and re-measured in batch I3. Fail modes: a MISSING manifest fails closed (pin_block.sh's _pin_fail "reference pin manifest missing" guard); an
  EMPTY/all-PENDING one fails open (its "has no active entries; integrity check skipped" echo) — so 000083 (candidate,
  uncaptured, no out tree) is not in the fail-open set.
- repair_tally --demote (99a34af) shipped before the ruling changed — an operator tool for evidence-voided demotions
  (validated → applied, validation_ref kept as history, reason in notes); it fired on NOBODY, the 18 empty-manifest
  validated specs define no tamper, so the routine demotion path stays --refresh-pins.
- Batch I/I2 reward tables now carry pin_manifest_empty, set from the run's OWN verifier output (not the scratch tree);
  the tally VOIDS such control and discriminating rows in BOTH directions (repair_tally 2f8772b :86-87), and
  repair_apply --audit-manifests is the pre-run check on the applied tree — so the class is no longer 'invisible to the
  tally / verify on the applied tree' (superseded). Batch I re-read: 47 REPAIRED / 1 UNDER-TIGHT
  / 19 INCONCLUSIVE / 3 OVER-TIGHT — eight of the nine prior UNDER-TIGHT verdicts were the empty manifest.
- Wave-3 pin-order finding: capture PRECEDES apply for pin specs. The pin block manifest is built from the spec's shas
  at apply time, so apply-before-capture yields an EMPTY manifest and the integrity check silently skips ("reference
  pin manifest has no active entries"); 8 pin specs (000770/000817/001105/001190/001318/000377/001055/001297) had
  every tamper grade a clean tree. Rule added to §0 pipeline + §1 R1.7. Guards (coder): repair_apply refuses ANY null-sha/PENDING pin (not just an empty manifest — a mixed B3 manifest would
  leave the new pin unprotected) unless --allow-pending-pins; --refresh-pins regenerates the block after a late capture and
  returns the spec to applied (new validation batch). The tally
  cannot see it (tamper scores 1 → reads UNDER-TIGHT); caught on the applied tree.
- Wave-3 status fix: 6 second-pass agents demoted status validated→captured (kept validation_ref, added
  second_pass:true); ops restored the six to validated through a tool path. Rule added (prompt second-pass + §0): a
  second pass NEVER changes status; the tool path is validated → apply (applied, validation_ref preserved) → validation
  batch → validated; the agent adds only second_template/second_pass/the diff fields.
- Applicability: the execution-marker requirement binds specs written under rev11.2 and later (wave 4 onward); wave-3
  specs authored under rev11.1 are exempt and tally under prior semantics; the tally keys on the spec's declared-markers field (arm_markers),
  which the coder is implementing as opt-in.
- Tooling to coder: tally VOID flag for marker-absent exploit/tamper 0s; reconstructed-arm path (<label>_reconstructed
  row in the repro batch TSV + exploit_source.reconstructed: true); schema/validator halves of N2/N5; repair_rows §7 pair table.

## §4 R4.7c + R3.8 builder (2026-09-03, tmax-log-reviewer)
- §4 R4.7c (repairer 68c51be, 000702): gold-row extraction overlays gold's tar members onto setup's originals without
  removing them, so a gold rename/move leaves BOTH names at grade time (000702: 'app server.log' + 'app_server.log';
  archive.sh exited on the collision — a harness artefact, not a gold defect); a rename/move/archive probe must run on
  an isolated input dir (only its fixtures), snapshot+restore in finally.
- R3.8's combined table (newest row per (task_id,mode,label) across a spec's batches) is built by
  tools/tournament_reward_table.py :44-50, not repair_tally.

## Wave-3 CLOSE — batch I3 (2026-09-03, tmax-log-reviewer)
- Batch I3 ran with ZERO fail-open rows. 28 promoted, including the 18 re-promoted on PINNED evidence with
  `validation_ref_prev` kept. 000319 and 001335 fixed by the non-fatal-rebuild pattern (R4.9(c)/(d)); 000702 still
  OVER-TIGHT, with the repairer.
- Wave-3 status: validated 61 / applied 17 (the 16 gold-void parked + 000702) / no-spec 2 (003407; 000087 pending
  set-status).

## rev11.2.1 — §7.5 IVM-RELAX arm_markers exemption (2026-09-03, tmax-log-reviewer)
- §7.5's R3.6 note EXEMPTS IVM-RELAX from arm_markers: its arms are report INPUTS to the modified check (no executable first statement to carry a marker), and the correct variant is self-validated by its 0→1 transition. Documents existing validator behaviour (arm_markers already optional, repair_spec.py:186); no spec change, no re-validation. Surfaced by the wave-4 security review (000299/000318/000425 correctly carry no arm_markers).

## rev11.2.2 — prompt gaps from the wave-4 review (2026-09-03, tmax-log-reviewer)
- Two explicit prompt lines from the repairer's wave-4 review (e01677c, OK 33/FIX 24/BLOCK 0): (a) a recompute/regenerate observable's `source` is LITERALLY `candidate` until gold scores 1 (instruction:N goes in the TEXT) — 15/16 FRESHDATA specs had written source=instruction:N; (b) every subprocess/exec arm's FIRST statement is its ARM-RAN marker, listed in arm_markers (IVM-RELAX exempt, rev11.2.1) — 12 specs ran subprocess arms with no marker. Prompt-only; joins the next Fable rules pass with rev11.2.1.

## rev11.2.3 — §7.4 regen-diff observable = candidate (2026-09-03, tmax-log-reviewer)
- §7.4's REGEN-DIFF branch ADOPTS observable.source = candidate until gold scores 1 (its expected is regenerated at
  grade time, same B7 logic as §7.1/§7.2/§7.6); the pin branch has no observable and is untouched. rev11.2.2 prompt
  line (a) already names §7.4, so it stands. CONFIRMED read-only: tools/repair_tally.py keys NO verdict on
  observable.source (it keys on label/expected/reward/tests_source and exploit_source.reconstructed only), so
  observable.source is BOOKKEEPING — the field fix needs no re-measure.

## rev11.2.4 — candidate-source is mechanism-based (2026-09-03, tmax-log-reviewer)
- Stated ONCE in the §7 preamble: ANY observable whose expected value is RECOMPUTED at grade time carries
  observable.source=candidate until gold=1, regardless of template (§7.1/§7.2/§7.4/§7.6 are instances; a recompute
  under any other template, e.g. IVM-BUILDRUN 000101, is included); a plain read of an existing check is not a
  recompute. Surfaced by 000101. No spec/verdict change; joins the same Fable pass as rev11.2.1-11.2.3.

## rev11.2.5 — arm_markers two key kinds; the void-arm check had never fired (2026-09-03, tmax-log-reviewer)
- Campaign finding (ops J-sec): the R3.6 void-arm rule NEVER fired. repair_rows looked arm_markers up by emitted arm
  label (val-gold / val-exploit:<label> / val-tamper-*), but 58 of the 59 marker-declaring specs key by PROBE name
  (probe-fresh-corpus …) — so batch J's and J-sec's '0 void-arm' were INERT, not exercised.
- Ruling (rev11.2.5): arm_markers KEY has two kinds — a KNOWN arm label = a per-arm marker for a command-borne arm;
  ANY OTHER key = a PROBE-name marker the in-diff probe emits, expected on EVERY repaired-verifier row
  (gold/exploit/tamper), not original-verifier rows; absent -> void-arm VOID. Updated R3.6, §8, §7.5 (RELAX still
  exempt), prompt line (b). NO spec needs editing — probe-name keys are now the correct convention. Ops changes the
  tool in parallel. Joins the same Fable pass as rev11.2.1-11.2.4.

## rev11.2.6 — Fable pass on 11.2.1-11.2.5 (2026-09-03, tmax-log-reviewer)
- B1: status:candidate is authoring-only (pre-first-apply); an applied/validated spec keeps status + validation_ref
  when it takes the observable.source field fix. B2: the probe marker is expected on GOLD and EMPTY rows too (absent
  -> VOID); retroactivity — pre-11.2.5 records carry no marker expectation, only a re-run exercises the rule (ops
  implemented; J3 running). M1: prompt line (a) is mechanism-based, not a template list. M2: the candidate->validated
  status promotion (validation batch/tally) IS the record that gold scored 1. M3: val-gold/val-empty are emitted ROW
  labels, not authored arms (per-arm key permitted, normal form is a probe key). M4: a probe-name key must match an
  ARM-RAN:<key> emission in the diff (validator cross-checks on-disk; ops after J3). Rules+prompt only.

## rev11.2.7 — marker placement: unconditional first statement (2026-09-03, tmax-log-reviewer)
- J3 (69 marker specs / 222 rows): 83 seen / 139 not. 21 specs declared a marker their test.sh never emits; 48
  placed the probe where the exploit condition is evaluated (seen on exploit 46/48, gold 12/48, empty 0/48). Ruling:
  the probe marker is emitted UNCONDITIONALLY as the FIRST statement of the added check, before any condition, so gold
  and empty rows see it; a marker inside a branch is a misplacement and a FIX (a check hidden behind a condition gold
  never triggers is the B2 over-tight blind spot). R3.6 + prompt line (b). No spec semantics change.

## rev11.2.8 — file marker canonical; expected rows = gold+exploit+tamper (2026-09-03, tmax-log-reviewer)
- J2 controlled comparison (repairer's 12): the 10 print-to-stderr first-statement markers = gold_seen False /
  exploit True; the one FILE-marker spec (001278) = gold_seen True; 000097's print was seen on gold ONLY because its
  gold FAILED. Cause: pytest captures the output of PASSING tests, and empty rows never reach the added check under
  pytest -x. Ruling: (1) the canonical marker is a FILE — open('/logs/verifier/arm.<key>','w').close()/touch — as the
  unconditional first statement; a print/echo inside pytest is a channel defect + FIX; a shell echo outside pytest is
  ok but file is canonical; the executor checks /logs/verifier/arm.* after grade, output as fallback. (2) expected-row
  set = GOLD + exploit + tamper; EMPTY is informational (pytest -x stops at an earlier original failure) — narrows
  11.2.6's B2 to gold, which is what B2 was about. R3.6 + prompt (b) + §7.5. No spec semantics change.

## rev11.2.9 — Fable closure on 11.2.7/11.2.8 (2026-09-03, tmax-log-reviewer)
- B1: the probe marker is expected on every repaired-verifier row that REACHES the added check — GOLD always;
  exploit/tamper unless their 0 was produced upstream (pin-block MISMATCH line, or earlier original failure under -x),
  where the marker is informational and the 0 stands; decided from the run's own output (as for EMPTY). B2: the
  prompt's Execution-marker bullet drops the echo-first form — the probe is a FILE marker; command-borne arms may
  echo (they run outside pytest). M1: 'ARM-RAN:<key> emission' -> 'creates /logs/verifier/arm.<key>' (the M4
  cross-check looks for that PATH; ARM-RAN in output is only the fallback). M2 (amended): a marker misplacement /
  channel defect on an existing spec is NOT re-spec — it takes the in-force fix loop (diff edit -> re-apply voids
  validation_ref on patch_sha256 drift, status applied -> re-validate next batch, reason in notes; VOID until then).
  R3.6 + §8 + prompt. No spec semantics change.

## rev11.2.10 — measurability gate (2026-09-03, tmax-log-reviewer)
- Batch K (21 never-measured drafts, ops e08b5c7): both validators passed all 21, yet 11 had no tests.diff, 0 a
  probe marker, 1 no exploit_source, 0 tamper.control — the gates checked field SHAPES, not measurability. New §8b
  MANDATORY authored-gate requirements: (1) an adds-a-check spec (every template except RELAX + §7.4 pin branch)
  declares exactly ONE probe-name file marker (no longer optional); (2) exploit_source required where the template has
  an exploit pair; (3) tamper.control required where EXPECTED_PAIRS include a control row; (4) a non-pin-branch spec
  with no tests.diff on disk is a validate --as-applied ERROR, not a silent pin-only apply. Ops implements the
  validator side in parallel. R3.6-consistent; prompt updated.

## rev11.2.11 — Fable pass on 11.2.10 (2026-09-03, tmax-log-reviewer)
- G1: §8b item 2 (exploit_source) mandatory ONLY where the judge row carries an executed repro or a
  <label>_reconstructed row exists; where the (1,0) arm is the in-diff probe and no repro exists, the probe IS the
  exploit arm and exploit_source stays optional (reconciled §7.1); IVM-RELAX dropped from the exploit-pair set.
- G2: §8b.0 applicability — binds specs authored under 11.2.10+ and existing specs at their NEXT fix-loop entry as
  FIX (never a demotion); a validated spec that does not re-enter keeps status; wave-3 exempt from item 1 until
  re-entry; item 4 is an apply-host check (--patch-root), patch_sha256 recipe elsewhere.
- G3: 'pin-only' defined once (MGT-PIN-HOME/CONTENT + §7.4 pin branch; a pin spec with rewrite second_template +
  second_pass is NOT pin-only) — exempt from items 1 and 4, subject to item 3; prompt aligned. M1: no-exploit-row
  templates named (VTW-EXECUTE/IVM-BUILDRUN/VTW-SLOW/in-diff-probe-no-repro). M2: §7.7 VTW-SLOW marker line. M3:
  exactly one PROBE key, label keys for command-borne arms may coexist. Ops implements the validator side.

## rev11.2.11a — wording (2026-09-03, tmax-log-reviewer)
- §8b item 4 and the prompt: 'non-pin-branch' -> 'non-pin-only' (Fable 56eeeda), so the old §7.4-only term does not survive in the one item it matters for. Wording only.

## rev11.3 — new template VTW-AGENTMETA (2026-09-04, spec-writer wave 21 via tmax-ops)
- SPEC_RULES.md §7.8 adds VTW-AGENTMETA, the tenth class: VERIFIER-TOO-WEAK / accepts-wrong where the verifier's ONLY
  source of truth about the graded artifacts is something the AGENT produced -- a manifest, an index, a report, or the
  members of an agent-built archive -- and no grade-time read ever reaches the material those artifacts are supposed to
  be derived FROM. The tell is a verifier that is internally CONSISTENT and externally UNANCHORED, so a self-consistent
  set of decoys scores 1. Found on task_008904_5bac056c, which no existing template covered: not a weak assertion, not
  a leaked reference, but the verifier taking an agent-authored artifact as its own ground truth. 44 lines added, 0
  removed. Written by the agent that read the task rather than paraphrased from the verdict text.
- NOT YET HUMAN-REVIEWED. The spec carries the flag and 008904 stays out of measure and apply until the lead says so.
- tools: VTW-AGENTMETA added to repair_spec DIFF_TARGETS and EXPLOIT_PAIR_TEMPLATES and to repair_rows EXPECTED_PAIRS
  as (1,0), which the template's own text names as the blocking item.
- RULE CHANGE in the same commit, and it is a loosening, so it is called out rather than buried: repair_spec's blanket
  presence check no longer demands `exploit_source` from a PIN-BRANCH spec. The semantic rule in the same file
  (EXPLOIT_PAIR_TEMPLATES with `not _pin_branch`, rev11.2.11) already said the field is mandatory only where an exploit
  row is actually EXECUTED and exempted the pin branch; the two rules disagreed. It never mattered for the 398, whose
  specs all had a recorded repro row to cite, and it blocked 6 specs for pool tasks that were never in the repair
  campaign and have no repro row in existence. A spec cannot cite evidence that does not exist. The existing assertion
  in test_repair_spec was re-pointed at the new rule with the reasoning in place, not deleted.

## rev11.4 — 2026-09-04 (RULES CHANGE, user ruling)
- SPEC_RULES §9: pure pin repairs are HOOK-REPLACEABLE and are no longer specced, applied or validated. The
  training-side pin hook re-materialises pinned files before grading, so a pin-block-only repair repeats what the
  harness already does. This is a RULES change, not a tooling tweak: it removes a whole template branch from the
  work, MGT-PIN-HOME and MGT-PIN-CONTENT, at plan time.
- New spec status `hook-replaceable`: the spec file is KEPT for the record, out of the pre-test export (which
  exports applied and validated, so the demotion is enough), out of the validate arm set, and counted separately
  via the new `hook_replaceable` column in tree_status. It is not `dropped` — nothing is wrong with those specs.
- Applied to wave 21: SIX specs demoted from applied, not seven. The seventh pin-branch spec in that wave is
  MGT-REGEN, which carries no pins and a diff, so the ruling as written does not reach it. Validate for the wave
  therefore runs over 13 specs, not 12.
- Hybrids are explicitly out of scope: a spec with pins AND a diff, or whose second pass rewrites to a diff
  template, adds a check the hook does not add. repair_spec._is_pure_pin is the one definition and tests all three
  conditions. The first cut of the rule tested only the template and would have condemned a pin-plus-VTW-EXECUTE
  spec that adds a real check; the test that caught it is in test_repair_spec.
- Grandfathering: the 94 pin specs already in the tree are untouched and listed by id in
  repairs/pin_specs_grandfathered.txt (86 pure pin, 8 pin-plus-diff). The boundary is a list, not a date: a date
  needs a trustworthy one inside every spec and none carries it. repair_spec validate fails a pure pin spec at
  applied or validated that is not on the list.

## rev11.5 — 2026-09-04 (second pin ruling + the three review items on rev11.4)
- USER: the grandfathered pure pin specs are demoted as well. 86 moved from validated (79) and applied (7) to
  hook-replaceable, joining the 6 from wave 21: 92 in all. The 8 pin-plus-diff hybrids stay repairs. There is no
  grandfathering left and repair_spec reads no exemption list; pin_specs_grandfathered.txt is renamed
  pin_specs_hook_replaceable.txt and is now a record of what moved rather than a list of what is exempt.
- CORRECTION to rev11.4, and the important line here: §9 said hook-replaceable specs leave the pre-test export.
  That was WRONG. The export IS the hook's input — the pin manifest the training grade path runs before test.sh is
  built from these spec files, not from the block inside the verifier — so dropping them would have stopped their
  pins being checked anywhere, for 92 tasks, while the rule's own justification is that the hook keeps checking
  them. pretest_export --statuses now defaults to applied,validated,hook-replaceable. What a demotion removes is
  the IN-FILE block, which seed_strip_pins already ships as the ORIGINAL test.sh for this class.
- Fable's three items on 9e9301c, all fixed here. (1) repair_apply refuses status hook-replaceable: --specs takes
  globs, so a wave-wide apply would have re-inserted the block and --write-status would have flipped the spec back
  to applied, with nothing saying so until the next validate. (2) The purity test now reads the spec's tracked
  patch_sha256 instead of the gitignored patches directory, and refuses to rule when neither signal exists — from
  a clean checkout the old test read all 8 hybrids as pure and would have failed them. (3) The plan-time exclusion
  is code now, not prose: spec_workflow drops MGT-PIN-* candidates before any agent is spawned and logs which.
- Counts: tree_status carries hook_replaceable=1 on 92 rows. Repair counts that include them overstate the
  campaign. One pure pin spec from the empty-domain wave sits at draft and was left at its own status rather than
  demoted on another seat's behalf; it is named in pin_specs_hook_replaceable.txt.

## rev11.6 — 2026-09-04 (RULES CHANGE, user ruling: authored exploit arms for pool tasks)
- SPEC_RULES §10: a pool-task spec (outside unified/verified_398.ids) must carry authored_exploit {cmd,
  cited_defect} to reach validated. Pool tasks were never in the measured repro corpus, so exploit_source can never
  resolve for them, and without an arm the tally will never call the repair REPAIRED - correctly, since the only
  evidence would be gold-passes/empty-fails, which a patch that does nothing also produces. This is what left 13
  wave-21 specs unpromotable today.
- The arm is a PAIR: repair_rows emits val-exploit-authored on the REPAIRED verifier (expected 0, inverted for
  IVM-RELAX) and ctl-exploit-authored-original on the ORIGINAL verifier (expected 1). The control is the half that
  makes the other half readable - a command that fails for its own reasons scores 0 on the repaired tests too and
  looks exactly like a repair that fired.
- repair_tally: a control scoring 0 gives INCONCLUSIVE "authored exploit arm VOID"; an authored row with no control
  row is INCONCLUSIVE too, since the pair is the unit. is_exploit no longer counts the control as an arm - its
  label contains "exploit" and a substring test would have let the one row that can never show a repair firing
  satisfy the promotion-time presence check on its own.
- repair_spec: the field is type-checked, forbidden alongside exploit_source (one arm per spec, and a recorded row
  outranks an authored command), and REQUIRED at validated for pool tasks. It deliberately does not bite at
  applied: failing the tree's validate over work in progress teaches everyone to reach for a bypass flag.
- SPEC_AGENT_PROMPT: mode exploit-arm, which adds this one field to an existing spec and touches nothing else.

## rev11.6.1 — 2026-09-04 (§10 addendum: the second authoring mode)
- SPEC_AGENT_PROMPT gains mode spec+arm: a NEW pool-task spec written together with its authored_exploit in one
  pass, for the staged waves whose specs do not exist yet. mode exploit-arm stays for the specs that do.
- The mode fixes the ORDER: the exploit command is written BEFORE the repair. An arm authored after the repair
  tends to be one the repair already stops, which proves nothing about the cited defect - and the failure is
  invisible afterwards, because a passing pair looks the same either way.
- No tool change: repair_spec, repair_rows and repair_tally already treat authored_exploit identically whichever
  mode produced it, which is the point of putting the rule in the field rather than in the wave.

## rev11.6.2 — 2026-09-04 (§10 correction + the spec-wave security guard)
- CORRECTION to rev11.6, caught by tmax-log-reviewer before the 45-arm wave ran: the authored arm's ORIGINAL-side
  expectation was hardcoded 1, which reads every IVM-RELAX arm as VOID. IVM-RELAX inverts BOTH halves - its pair is
  (0, 1) because the arm is the rejects-right CORRECT variant, so 0 on the original verifier IS the defect. Both
  expectations now come from the same EXPECTED_PAIRS entry (repair_rows.exploit_control_expected), so they cannot
  drift apart, and the tally VOIDs on any control mismatch in either direction rather than on a literal 0. This is
  the exact mirror of the hardcoded 0 that once produced five false UNDER-TIGHTs on the repaired half; the 45 wave
  has 2 IVM-RELAX tasks that would have been mis-VOIDed.
- spec_workflow.js gains the security split guard the judge template has always had: args.security_ids is required
  (pass [] to declare none), no bucket may mix security and non-security ids, and a bucket holding security ids
  must be labelled kind security. The file's own header has claimed "security buckets isolated" since it was
  written while nothing checked it; two waves ran that way. Neither bit, because both had zero security ids - and
  with zero security ids a working guard and a missing one produce identical output, which is why the absence
  could not be noticed from any result.

## rev11.7 — 2026-09-04 (§10: mode respec, for specs whose SHIPPED verifier is the defect)
- SPEC_AGENT_PROMPT gains mode respec and SPEC_RULES §10 the rule: an APPLIED spec that is wrong because the
  shipped verifier is defective is rewritten under the spec+arm contract, not added to. Template re-derived from
  the actual defect (rejects-right -> IVM-RELAX or equivalent, pair INVERTED: 0 original / 1 repaired), a new
  tests.diff replacing the old, an arm that is the correct variant the verifier rejects, status back to candidate,
  notes naming why the old spec failed, pins dropped where the old pin was the only content.
- Why a third mode rather than second-pass: the log-reviewer measured that second_template is None on all ten
  candidates and eight of ten carry no pins, so the second-pass contract fits none of them. A second pass adds a
  check to a spec whose template still holds; here the template was written against a defect that is not the one
  present.
- Two directions, not one. If the SHIPPED verifier is defective the pair INVERTS (0 original / 1 repaired). If OUR
  repair is the over-tight party - gold 1 on the original and 0 on the repaired, measured on 002379 and 000474 -
  the re-spec loosens OUR check and the pair stays STANDARD. Reading the direction off the gold pair is what tells
  the author which of the two they are in.
- And where the arm's control is unreachable until the task itself is fixed (001583's oracle exceeding the 120 s
  cap, 001564's gold at 0), the author DECLINES with the reason rather than writing an arm that can only record
  VOID: an arm that cannot fail looks like evidence in a table.
- The mode carries the warning the split table found: three of the ten repairs CARRY the shipped defect forward -
  copying the wrong accuracy constant, calling the same missing python binary, importing the same uninstalled
  module - so a task-only fix leaves them failing and the old diff has to be read for the defect before the new
  one is written.
