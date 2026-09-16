# V8 operational regression guards

## Contract

This is a five-task, non-security regression set for the exact original task
artifacts in `tmax-task-data.tar.gz` at SHA-256
`8e0fcfb61ea62ad234423bafa3c2e60cf16239e8be8a072daf950cf64cf7f016`.
Every guard's V8 operational decision is **REJECT**.

V7 used `REPAIR` to mean that a bounded verifier or task repair might exist.
That label must not be translated into current eligibility for the original
artifact. A repaired derivative needs a distinct artifact identity and a fresh
V8 review. It cannot silently replace the original member named by a guard.

No task, setup script, verifier, counterexample, or network operation was run
while constructing this set. All three task files were read in full for every
selected ID. Runtime claims below come only from archived receipts; the other
claims are explicitly static traces.

## Guard set

| Guard | Task | Class | Old V7 | Runtime evidence | V8 original |
|---|---|---|---|---|---|
| `V8-GUARD-AL-RUNNABLE-NUMERIC-001` | `task_000156_2f395118` | Numeric runnable reference | REPAIR | None claimed; static trace | **REJECT** |
| `V8-GUARD-AL-DETERMINISTIC-REGEN-001` | `task_000241_547763df` | Deterministic regeneration plus unbound deliverable | REPAIR | None claimed on original verifier; static trace | **REJECT** |
| `V8-GUARD-AL-PLANTED-OUTPUT-001` | `task_000311_9e22fec5` | Planted solved output | REPAIR | Empty 0, counterexample 1 | **REJECT** |
| `V8-GUARD-AL-RUNNABLE-DIFFERENTIAL-001` | `task_004863_67089cd4` | Reachable differential oracle | REPAIR | Empty 0, wrapper 1 | **REJECT** |
| `V8-GUARD-EMPTY-REWARD-001` | `task_004886_10504160` | Observed reward for nothing | Not judged after gate 1 | Untouched image 1 | **REJECT** |

The first four are `security_union=0` in the shipped-398 ledger. The fifth is
outside that ledger; its instruction is a database performance/correctness task
and this guard constructs no security behavior.

## Per-guard evidence

### Runnable numeric reference — `task_000156_2f395118`

The instruction requires extracting constants, repairing the Rust service,
implementing `calculate_sequence`, and executing a regression test
(`instruction.md:7-10`). Setup retains `/app/legacy_calc`, which computes the
exact sequence for arbitrary `n` (`setup.sh:8-27`). The grader merely checks
that `regression_test.py` is executable (`tests/test.sh:18-21`) and queries
whatever service owns port 8080 (`tests/test.sh:23-41`); it never builds or
executes the requested Rust crate. A service that delegates every request to
the oracle meets the observable grade without implementing the task.

V7-v3.18.1 called this `REPAIR` / `VERIFIER-TOO-WEAK` in
`judge/fullrun_v318_20260902/judgements_V7_bucket_19.jsonl:4`. The current
complete static review is `REJECT` in
`reviews/overfilter_20260916/results/other_02.jsonl:5`. No reward-1 receipt is
claimed in this guard.

### Deterministic regeneration — `task_000241_547763df`

The instruction's core is recovery of a still-open deleted log plus robust
malformed-line handling and exact aggregation (`instruction.md:7-16`). The
readable generator fully specifies and can regenerate the input
(`setup.sh:9-38`). The original grader checks only a line count and fixed total
(`tests/test.sh:9-26`); it neither establishes recovery provenance nor executes
`aggregator.py`.

V7-v3.18.1 called this `REPAIR` / `VERIFIER-TOO-WEAK` in
`judge/fullrun_v318_20260902/judgements_V7_bucket_23.jsonl:5`. The current
complete static review is `REJECT` in
`reviews/overfilter_20260916/results/other_02.jsonl:1`. Later repaired-verifier
failures are deliberately excluded: they do not measure the original verifier.

### Planted solved output — `task_000311_9e22fec5`

The instruction requires dump analysis, asset cleaning, threshold repair, and
a real build (`instruction.md:4-10`). The rollout-visible `build.sh` states both
exact values later graded: `VALID_ASSET_123` and `BUILD SUCCESSFUL`
(`setup.sh:35-45`). The verifier reads only the two directly writable artifacts
and never runs the build path (`tests/test.sh:9-25`).

The archived discriminating run is exact: the empty control received 0 and two
direct writes received 1 on image `fd04f429a606`
(`repro/judge_batches/smallrun_b013.jsonl:39,41`, file SHA-256
`114dac3d13fe6172d8058056fae9f89cb632f1184c76222fc7508c608de9d3b5`).
V7-v3.17.1 still used tier REPAIR; V8 treats repairability separately from the
original artifact's eligibility.

### Reachable differential oracle — `task_004863_67089cd4`

The task requests an independent pure-Python decoder (`instruction.md:5-13`).
Setup retains the executable reference at `/app/backup_decoder`
(`setup.sh:51-56`). The grader runs the submission and that same reachable
binary on each fuzz input and compares return code, stdout, and stderr without
isolating the oracle (`tests/test.sh:12-54`).

The archived receipt records empty 0 and a subprocess wrapper reward 1
(`repro/batch13.jsonl:26-27`, file SHA-256
`8ccab9f5b9af62ff6fd3042fd5b18b58b39ee523af4a430bbf7bd525f2128eee`).
V7-v3.16 labeled it REPAIR. The observed wrapper success makes the original
artifact a stable V8 REJECT guard.

### Observed reward for nothing — `task_004886_10504160`

This task asks the agent to optimize and compile a Rust graph-backup analyzer
and meet a two-second correctness threshold (`instruction.md:5-12`). Setup
creates the database and golden result (`setup.sh:14-40`), writes the original
program (`setup.sh:56-115`), and pre-builds it (`setup.sh:153-154`). The verifier
deletes only the output and runs whatever implementation is already present;
it does not require a rollout change (`tests/test.sh:24-68`).

The stage-1 receipt is an observed runtime fact, not an inference:
`unified/rounds/gate1_empty_submission.json` array index 13986 (zero-based),
file SHA-256
`2bea072bd5c0947bea9dbaff1433b003a35eb9f04e85e9ac89ebb6269dd6ec8d`,
records image `54d7fce2c18c`, `setup_rc="skipped"`, `test_rc=0`,
`empty_reward="1"`, and `status="free_reward"`. An exact original artifact
that rewards the untouched image is operationally REJECT.

## Machine-readable interface

`regression_guards.json` is a top-level object with:

- `schema_version`
- `guard_count`
- `selection`
- `artifact_policy`
- `guards[]`

Each guard has `task_id`, `guard_id`, `guard_class`, `artifact_variant`,
`expected_decision`, original member hashes, `static_evidence[]`,
`runtime_evidence[]`, `runtime_evidence_status`, `old_v7`, `reject_invariant`,
and `repair_boundary`. Consumers should fail closed unless:

1. `guard_count == len(guards)`;
2. every `expected_decision` is `REJECT`;
3. every `artifact_variant` is `original`;
4. the archive and member hashes match; and
5. no repaired overlay is substituted for an original member.

The JSON is the workflow interface. This Markdown file explains the evidence
and the boundary between an original artifact and a separately reviewed repair.
