# Proposed V8 rubric delta

Status: **PROPOSED — pending Claude Opus5 review. No judge run authorized or performed.**

The derivative is the complete 658-line V7 prompt with only the edits enumerated below. The original V7 prompt, delta, task inputs and recovery judgments are unchanged. This is a decision-boundary proposal, not a new task audit or runtime acceptance result.

## Frozen source and patch

- Source: `rubrics/v7_prompt.md`, V7-delta v3.18.1.
- Source SHA-256 (whole file): `d5498e139f3386720456e1238adbeafc311b1f17d3b0242720192353d6b34ab4`.
- Source body hash prefix (its existing header): `07a853e26e2ef1dd`.
- Proposed V8 SHA-256 (whole file): `3f305309f11cbd0a1dd9370d81c57d8ad0ad2af44f3bd4b533da602dbe830e0b`.
- Unified patch: `v8_delta.patch`; target: `v8_prompt.md` (710 lines).
- The patch preserves every source byte outside its explicit edit spans. Patch round-trip verification applies the diff to an isolated copy and compares the exact bytes with the proposed prompt. It also checks the untouched original hash.

Reproduce from `reaudit_398/` without modifying V7 (choose a fresh scratch directory):

```sh
mkdir -p /tmp/v8-delta-review
patch --batch --output=/tmp/v8-delta-review/v8_prompt.md rubrics/v7_prompt.md reviews/overfilter_20260916/v8/v8_delta.patch
cmp /tmp/v8-delta-review/v8_prompt.md reviews/overfilter_20260916/v8/v8_prompt.md
sha256sum rubrics/v7_prompt.md reviews/overfilter_20260916/v8/v8_prompt.md
```

## What changes and what does not

**V7 already allows cosmetic PASS.** Its original lines 106–112 explicitly distinguish load-bearing requirements from cosmetic ones and say “If cosmetic ⇒ **PASS**”; line 481 already provides `unenforced_cosmetic`. V8 does not claim to invent that rule. It changes the boundary for secondary methods, languages, styles and packaging when a substantive requested result is independently checked. Only an unverified core requirement causing wrong reward supports an unenforced-clause IVM block. A missing operational system is not recast as cosmetic merely because a log exists.

VTW must exhibit a complete, rollout-visible wrong submission and trace full reward across every graded assertion. Limited coverage, fixed inputs and a possibly weak check alone do not create `accepts-wrong`. If no such exploit is found, this issue receives PASS, subject to every other V7 rule. Complete static payloads with assertion traces can establish a static finding; this does not claim measured reward 1. Runtime status is explicit.

The new scoped precedence paragraph resolves all conflicting legacy no-repro REVIEW language (D3 table, exploitability discussion, tier discussion and D4) only for hypothetical VTW and cosmetic/secondary IVM. The broad fixed-input GENERAL-PROGRAM sentence and two-question IVM boundary are edited directly. `sound` remains required for PASS; genuine `accepts-wrong` is never PASS. Defect flags remain false for the two newly non-blocking cases. C2 probes, full reading, evidence locators and all five confidence checks remain required. Confidence below 8, incomplete checks, base-image uncertainty, seed/entrypoint readiness, suspected instability and phase-3 refutation still use V7 REVIEW rules. Absence of an exploit alone is not missing work or a confidence penalty.

ANSWER-LEAK, a reachable/runnable complete answer, reward for nothing, mutable-input/oracle taint, input deletion, rejects-right, unstable reward, phase-3 authority, D1 protections, D5 repair policy, D8 costs, append-only first records, prior blinding and content quarantine are unchanged. V7 permits some grading-venv/task-owned-tool exploit classes that the recovery audit excluded; those V7 rules remain intact. In particular, the recovery of `task_000753_b376cf1d` is **not** imported as a V8 PASS expectation. Recovery labels are evidence for the proposed boundary, never blanket reclassification of their tasks.

## Operational eligibility versus V7 tiers

The user's operational REJECT means the original artifact is not admitted to training. It does not rename every V7 REPAIR as REJECT-PROVEN:

| V7/V8 tier | Original artifact training eligibility | Meaning retained |
|---|---|---|
| PASS | Eligible at the rubric stage, subject to unchanged downstream execution/admission gates | KEPT; `salvageable: true` is an annotation only |
| REPAIR | NOT_KEPT / operational REJECT | Bounded verifier repair remains possible; candidate is not admitted before repair and revalidation |
| REVIEW | NOT_KEPT / operational HOLD | Required evidence remains unresolved |
| REJECT-PROVEN | NOT_KEPT / operational REJECT | SIMPLE wrong-reward exploit with no bounded repair |

An ANSWER-LEAK or reward-for-nothing guard that yields REPAIR still blocks training admission. `salvageable` never overrides a non-PASS tier. No external workflow may count non-PASS leak guards as kept. Admission metadata is derived by the workflow; the prompt continues to emit the original four tiers.

## Actual recovery evidence and falsifiers

These are source-backed rationale examples, not measured V8 controls. The recovery report explicitly states that no task-container grading ran. Evidence locators below refer to the unchanged recovery directory (`reviews/overfilter_20260916/`).

| Case | Recorded recovery evidence | Boundary implication |
|---|---|---|
| `task_000499_964e537b` | `verdicts.jsonl:128`, `candidate_checks/preservation_07.jsonl:2`: independently supplied grade-time signature inputs at `tests/test.sh:17–42`; a weak auxiliary PASS witness does not bypass them. | Do not turn finite coverage or one bypassed auxiliary check into a full-reward wrong signature submission. |
| `task_000016_3ffaf505` | `top10_recoveries.json` and `candidate_checks/preservation_01.jsonl:5`: exact four original ETL records and MD5 hashes at `tests/test.sh:46–77`; shipped Rust is a skeleton. | Secondary Rust/cargo provenance can be salvageable while the substantive data result remains independently anchored. |
| `task_000792_f3d67730` | `verdicts.jsonl:181`, `candidate_checks/preservation_09.jsonl:6`, `adjudications/calibration.md`: exact active IDs and decoded metadata at `tests/test.sh:16–40`. | A correct alternate derivation of the requested final catalog is not a wrong-output exploit solely because the generator remains unrepaired. |
| `task_000012_58fdcccc` | `verdicts.jsonl:4`, `adjudications/calibration.md`: erasing original readings moves the verifier's expected result to `[]` (`tests/test.sh:31–65`). | Mutable-input/oracle taint is still blocking; independent recomputation on altered premises does not validate the original task. |
| `task_000404_66dd3c89` | `verdicts.jsonl:111`, `adjudications/calibration.md`: the complete payload satisfies real mounts but replaces core child/retry behavior with a no-op supervisor and fabricated log (`tests/test.sh:61–91`). | A core operation wholly absent behind self-authored evidence remains blocking; a partial payload is insufficient. |

## Structured fields

Only three new top-level fields are required in addition to the existing V7 schema. Existing `repro_cmd` carries the complete command/file; existing evidence fields carry the decisive assertion and rollout visibility.

| Field | Values | Constraint |
|---|---|---|
| `concrete_exploit_status` | `none_found`, `static_trace`, `runtime_confirmed`, `runtime_refuted`, `not_applicable` | Static is authored and traced, never observed reward; runtime states require exact grading evidence. |
| `core_mismatch` | `none`, `cosmetic_secondary`, `core_wrong_reward` | `core_wrong_reward` identifies an unverified core requirement causing wrong reward; `none` does not negate another defect. |
| `salvageable` | Boolean | True exactly for a `cosmetic_secondary` annotation with non-null `unenforced_cosmetic`; false otherwise. Never overrides tier. |

`rubric_version` becomes `v8`; output placeholder becomes `{V8_OUTPUT_PATH}`. No new training eligibility enum is emitted by the judge.

## Exact before → after edits

All line references below are to the original, unmodified `rubrics/v7_prompt.md`. Text outside these replacements remains byte-identical. The unified patch supplies surrounding context.

### 1. Provenance — original lines 1–4

Rationale: Replace the inherited generated-V7 header so the derivative does not claim the V7 body hash or assembler provenance.

Before:

```text
<!-- GENERATED by tools/assemble_v7_prompt.py — do not edit by hand.
     version: V7-delta v3.18.1   content_sha256_16: 07a853e26e2ef1dd (sha256 of everything after this comment; tools/prompt_sha.py recomputes it)
     inputs: v6_core.md@bc7a55f948a6  v6_prompt.md@21b6ac23f1c3  v7_rubric_delta.md@d5a6978f6dcc
     construction: reviews/v7_readiness_fable_20260902.md §1 steps 1-5 (see script docstring). -->
```

After:

```text
<!-- PROPOSED V8 delta v0.1 — pending Claude Opus5 review; do not launch judges.
     base: rubrics/v7_prompt.md, V7-delta v3.18.1, body_sha256_16: 07a853e26e2ef1dd
     construction: apply v8_delta.patch to the exact V7 source; see v8_delta.md.
     Only the scoped VTW / secondary-IVM decision boundary and output metadata change. -->
```

### 2. Version title — original lines 6–6

Rationale: Identify the proposed derivative explicitly; no judge execution is authorized by this file.

Before:

```text
# TMAX audit — V7 judge prompt
```

After:

```text
# TMAX audit — V8 judge prompt (proposed; pending Claude Opus5 review)
```

### 3. Scoped precedence and unchanged guards — original lines 86–86

Rationale: task_000499_964e537b illustrates a weak auxiliary witness without a complete wrong signature submission; task_000016_3ffaf505 illustrates independently checked ETL with secondary Rust packaging. task_000012_58fdcccc (input erasure) and task_000404_66dd3c89 (fabricated supervisor log) delimit the exception. Preserve unrelated REVIEW triggers and the original confidence floor.

Before:

```text
## D3. Elimination axis = soundness; tier follows soundness alone (v3.17 C1); D8 cost = triage + REPAIR/REJECT split
```

After:

```text
## D3. Elimination axis = soundness; tier follows soundness alone (v3.17 C1); D8 cost = triage + REPAIR/REJECT split

**V8 scoped decision boundary (takes precedence only for the two cases below).**
1. A VERIFIER-TOO-WEAK (VTW) finding may block PASS only when you exhibit a
   concrete complete wrong submission (`repro_cmd`) and trace why it earns full
   reward across EVERY graded deliverable/assertion. It must use only D1/D4
   rollout-visible facts. A fixed test set, possible hardcoding, an untested
   input, or a possibly weak assertion alone is not such a finding: record the
   concern in `checks`/`reason`, leave its `defects` flag false, and return
   `sound` / CLEAN / PASS if all other V7 rules are satisfied. No concrete VTW
   exploit found means PASS on this issue, not a no-repro REVIEW.
2. An unenforced cosmetic or secondary implementation/language/style/packaging
   clause is not a defect when the substantive requested result is independently
   checked. Record it in `unenforced_cosmetic`, set `core_mismatch` to
   `cosmetic_secondary` and `salvageable` true, and return `sound` / CLEAN / PASS
   if all other V7 rules are satisfied. An unenforced-clause IVM blocks PASS
   only when the unverified CORE requirement makes the reward wrong; identify
   that core, the independently checked result, and the wrong rewarded behavior.
   Do not rename a missing core operation as a secondary implementation choice:
   a fabricated operational log is not independent evidence of that operation.
For these two cases only, apply this boundary before assigning soundness, defect
flags, the D3 tier table, the two-question requirement test, and every D4/no-repro
REVIEW instruction below. V7 already allowed cosmetic PASS; V8 expands the
secondary-clause boundary and removes a hypothetical VTW as a blocking defect.
All other rules stay in force: ANSWER-LEAK, reachable/runnable answers, reward
for nothing, mutable-input/oracle taint, rejects-right, instability, D1/D4
protections, D5 repair policy, evidence requirements and phase-3 overrides.
A runtime-refuted repro (prep_rc 0, reward 0) still routes to REVIEW under D4;
base-image uncertainty, suspected instability, unavailable declared dependencies,
incomplete required checks and confidence < 8 also retain their V7 routing.
The absence of a VTW exploit or an unenforced secondary clause alone does not
lower confidence: complete and count the existing five checks, including the
candidate/probe trace and D1-protection check; do not award unperformed checks.
`salvageable` is an annotation, never permission to override another non-PASS
finding. Static traces are not observed runtime rewards; phase 3 remains required
where V7 requires it. REPAIR remains REPAIR, not PASS or REJECT-PROVEN.
```

### 4. IVM core boundary — original lines 106–107

Rationale: V7 lines 106-107 already distinguish load-bearing from cosmetic. The relaxation is the treatment of secondary methods, languages and packaging, not a newly invented cosmetic PASS rule. The complete original two worked examples remain intact.

Before:

```text
(2) is the clause load-bearing to the task's intent, or cosmetic (naming,
wording, phrasing)? If load-bearing and unenforced ⇒ label soundness
```

After:

```text
(2) under the V8 scoped boundary, is the clause a core requirement whose
non-enforcement makes reward wrong, or cosmetic/secondary while the substantive
result is independently checked? Only in the former case label soundness
```

### 5. Salvage annotation — original lines 111–112

Rationale: V7 already had cosmetic PASS at these exact lines; V8 records the expanded secondary-method exception without suppressing rejects-right or another defect.

Before:

```text
check, or behavioural check). If cosmetic ⇒ **PASS**, and list it in the new
`unenforced_cosmetic` field (D6). This is separate from `rejects-right`: here
```

After:

```text
check, or behavioural check). If cosmetic/secondary under V8 ⇒ **PASS** subject
to all other rules, list it in `unenforced_cosmetic` (D6), and set
`salvageable: true`. This is separate from `rejects-right`: here
```

### 6. Fixed-input blanket rule — original lines 213–215

Rationale: task_000499_964e537b checks submitted signatures on two grade-time inputs. Its auxiliary PASS witness does not establish a wrong signature receiving full reward. A finite test set alone must not trigger VTW.

Before:

```text
      exercises a handful of fixed inputs, each can be hardcoded case by case
      and the generality is never tested. VERIFIER-TOO-WEAK, however exact the
      comparison is.
```

After:

```text
      exercises a handful of fixed inputs, investigate whether a concrete wrong
      submission can earn full reward from rollout-visible facts. Limited coverage
      alone is not VERIFIER-TOO-WEAK; apply the V8 scoped decision boundary.
```

### 7. Two-question test — original lines 387–390

Rationale: task_000016_3ffaf505 still requires the correct four ETL records and recomputed hashes; task_000792_f3d67730 still requires the correct active catalog metadata. An alternate derivation is not a wrong final data product. The operational-log and input-erasure counterexamples remain defects.

Before:

```text
2. *If yes: what does the task then actually teach?* Compare that to what the
   instruction says it teaches. If they are the same thing, the clause is
   decorative phrasing — not a defect. If they differ, the task trains something
   other than its stated skill — **that is the defect**.
```

After:

```text
2. *If yes: does the missing clause leave a core requirement unverified and
   thereby reward a wrong result or absent core operation?* If so, it is a defect.
   If the substantive requested result is independently checked and only a
   cosmetic/secondary method, language, style or packaging clause is skipped,
   it is PASS on this issue with `salvageable: true` (V8 scoped boundary).
```

### 8. Output version — original lines 417–417

Rationale: Do not mislabel proposed V8 judgments as frozen V7 records.

Before:

```text
  "rubric_version": "v7",
```

After:

```text
  "rubric_version": "v8",
```

### 9. Minimal structured decision evidence — original lines 481–481

Rationale: Record the exploit evidence level, IVM boundary and salvage annotation without duplicating repro_cmd. Workflow derives admission from tier, never from salvageable.

Before:

```text
  "unenforced_cosmetic": "<a cosmetic instruction requirement the verifier skips (PASS case), else null>",
```

After:

```text
  "unenforced_cosmetic": "<a cosmetic/secondary instruction requirement the verifier skips under V8, else null>",
  "concrete_exploit_status": "none_found | static_trace | runtime_confirmed | runtime_refuted | not_applicable",
  "core_mismatch": "none | cosmetic_secondary | core_wrong_reward",
  "salvageable": <bool: true only for a cosmetic/secondary mismatch with independently checked substantive behavior; never overrides tier>,
```

### 10. Field semantics — original lines 492–493

Rationale: Separate authored counterexamples from measured rewards. A non-PASS leak guard cannot be counted as kept merely because another clause is salvageable.

Before:

```text
Return exactly this JSON — nothing else. `tier` is authoritative; `verdict` is a
descriptive label bound by `defects` (D3).
```

After:

```text
Return exactly this JSON — nothing else. `tier` is authoritative; `verdict` is a
descriptive label bound by `defects` (D3).

V8 fields: `none_found` means the VTW search found no valid complete exploit;
`static_trace` means a complete payload and full-reward assertion trace are
recorded, not run; `runtime_confirmed` / `runtime_refuted` require exact grading
evidence for that payload; `not_applicable` means no accepts-wrong candidate is
being assessed. Keep the payload in `repro_cmd` and its assertion/visibility
trace in the existing evidence fields. `core_wrong_reward` names an unverified
core requirement that causes wrong reward; `cosmetic_secondary` requires a
non-null `unenforced_cosmetic` and `salvageable: true`; otherwise `salvageable`
is false. `core_mismatch: none` does not negate another defect such as a leak.
```

### 11. Output destination — original lines 642–642

Rationale: Keep proposed V8 output separate from immutable V7 records.

Before:

```text
Append ONE line of JSON to `{V7_OUTPUT_PATH}`
```

After:

```text
Append ONE line of JSON to `{V8_OUTPUT_PATH}`
```

### 12. Record version — original lines 644–644

Rationale: Version all newly emitted rows without changing append-only or first-record rules.

Before:

```text
line carries `"task_id"`, `"agent"`, `"rubric_version": "v7"` and `"revised_after_priors": null`.
```

After:

```text
line carries `"task_id"`, `"agent"`, `"rubric_version": "v8"` and `"revised_after_priors": null`.
```

### 13. Quarantined destination — original lines 649–649

Rationale: Retain V7 quarantine verbatim apart from the output placeholder name.

Before:

```text
`{V7_OUTPUT_PATH}` is QUARANTINED:
```

After:

```text
`{V8_OUTPUT_PATH}` is QUARANTINED:
```
