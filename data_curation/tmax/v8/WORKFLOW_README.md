# V8 workflow preparation and launch from Opus 5

## Runtime boundary

`v8_workflow.js` follows the existing package's Workflow JS host pattern: `export const meta`, top-level `args`, and injected `agent`, `parallel`, `phase`, and `log`. It is **not a standalone Node program**. Load it through the Workflow capability available to the Opus 5 session and pass the argument object below. The precise host tool name and live model route were not available to this Codex session; no fictional MCP invocation is implied.

The proposed primary is pinned to `gpt-5.4-2026-03-05`, high, eight tasks per worker and at most eight concurrent workers. There is no fallback. The Opus 5 orchestration seat can review and change the proposed worker model; it is not automatically the worker model. Changing the model, batch/concurrency, schema or prompt requires updating `workflow_config.json`, re-pinning the corresponding `EXPECTED_*` constants and schema constants in `v8_workflow.js`, and regenerating a fresh plan. An exact config hash is deliberately enforced; editing only one side must fail.

`prepare_v8.py` requires Python with `pyarrow` and `jsonschema`. The existing interpreter `/data/users/zekaili/fangzhou/envs/alice/bin/python` was checked for both; system `python3` lacks pyarrow. No dependency installation is required in this workspace.

## 1. Prepare a fresh smoke directory

Run from `/data/users/zekaili/fangzhou/tb_check`. Choose a **new** run directory; the example must not already exist.

```sh
/data/users/zekaili/fangzhou/envs/alice/bin/python reaudit_398/reviews/overfilter_20260916/v8/prepare_v8.py prepare \
  --scope smoke48 \
  --run-dir /tmp/tmax-v8-opus-smoke-001

/data/users/zekaili/fangzhou/envs/alice/bin/python reaudit_398/reviews/overfilter_20260916/v8/prepare_v8.py validate-plan \
  --plan /tmp/tmax-v8-opus-smoke-001/plan.json
```

Preparation performs no judge calls. It verifies pinned source hashes, inventories all original archive members, selects deterministic IDs, extracts original files into the new run directory, writes separate scope/exclusion manifests, and places compact historical context in `priors/` for message 2 only. It rejects a reused directory, source drift, invalid membership and incomplete artifacts. A known non-security guard with a missing metadata label can use its explicit guard attestation; this must be recorded rather than silently treating all unknown labels as benign.

Smoke48 contains five mandatory guards, twelve recovery candidates, and thirty-one prior-category-stratified rows. Recovery candidates are calibration inputs, not imported V8 PASS labels. `task_000753_b376cf1d` is excluded from that positive-candidate selection because unchanged V7 rules may still block it.

## 2. Dry-run the Workflow host

Load the **contents** of `plan.json`, not its path string, as `args.plan`:

```json
{
  "stage": "message1",
  "dry": true,
  "plan": "REPLACE_WITH_THE_PARSED_PLAN_JSON_OBJECT"
}
```

The quoted placeholder above is explanatory; the actual runtime value must be an object. To make a concrete argument file without model calls:

```sh
python3 - <<'PY'
import json
from pathlib import Path
run = Path('/tmp/tmax-v8-opus-smoke-001')
args = {'stage': 'message1', 'dry': True, 'plan': json.loads((run/'plan.json').read_text())}
(run/'workflow_args.dry.json').write_text(json.dumps(args, indent=2)+'\n')
PY
```

Open `reaudit_398/reviews/overfilter_20260916/v8/v8_workflow.js` in the Opus Workflow host with those parsed arguments. Dry mode must make zero `agent` calls and report exact scope partition, judge count, bucket count and all five guard IDs.

## 3. Review, then run blind message 1

Opus 5 reviews the exact delta and config. Record an actual review receipt; do not label a proposal or mock test approved. The user's direct launch request in that Opus session can supply launch authorization—an extra confirmation is unnecessary when it is already authorized.

The runtime `launch_gate` object requires:

```json
{
  "review_model": "claude-opus-5",
  "review_status": "approved",
  "reviewed_config_sha256": "ACTUAL_REVIEWED_CONFIG_SHA256",
  "reviewed_prompt_sha256": "ACTUAL_REVIEWED_PROMPT_SHA256",
  "reviewed_schema_sha256": "ACTUAL_REVIEWED_SCHEMA_SHA256",
  "selected_scope": "smoke48",
  "selected_model": "gpt-5.4-2026-03-05",
  "selected_effort": "high"
}
```

This is a field template, not an approval receipt. Fill it only from the actual review and selected configuration. Pass it with the parsed plan, `stage: "message1"`, and `dry: false`. Verify that the host can route the exact requested snapshot before starting expensive work; an unavailable model must fail without substitution.

Workers see original task files, V8 instructions, byte hashes, image/domain metadata and their own output paths. They must not read selection roles, guard expectations, priors, repaired overlays or other results. Each task writes one fsynced initial JSON line in `rows/<task_id>.jsonl` before the next task begins. Host returns contain labels/hashes only, with task text and commands remaining quarantined on disk.

## 4. Independently validate message 1 on disk

Self-reported writer receipts are not independent file verification. After message 1 completes, run:

```sh
/data/users/zekaili/fangzhou/envs/alice/bin/python reaudit_398/reviews/overfilter_20260916/v8/prepare_v8.py validate-message1 \
  --plan /tmp/tmax-v8-opus-smoke-001/plan.json \
  --receipt /tmp/tmax-v8-opus-smoke-001/message1_validation.json
```

This reads the durable row files, validates the complete schema, exact coverage, unique task IDs, line hashes and mandatory guards. Missing/malformed rows and a guard returning PASS or REVIEW/HOLD fail. It writes a new receipt only on success. Never replace missing outputs with PASS and never forge a validation receipt from mocked results.

The original guard tasks may have a V7/V8 tier of REPAIR: this means their original artifacts are operationally rejected while a separate repair might be possible. It does not make them eligible. The same original artifacts must be used; no repair overlay can silently satisfy a guard.

## 5. Message 2 without replacing the first verdict

Invoke the same workflow with:

- `stage: "message2"` and the same parsed `plan` and actual `launch_gate`;
- `message1_validation`: the parsed independent validation receipt;
- `message1_validation_sha256`: SHA-256 of that receipt file;
- `dry: false`.

Only now may workers read their compact prior files. They append a separate revision record if context changes their opinion, citing the initial line's hash. The initial row remains the verdict of record. Prior-aware suggestions do not silently replace initial admission decisions; explicit phase-3 authority remains a separate unchanged V7 process. A guard revision conflict must be visible and must not be counted as a newly kept guard.

Inspect positive-control disagreements and any guard conflict. The original diagnosis is not a gold oracle for all unchanged V7 rules. No automatic worst-of-two judge aggregation is used.

## 6. Expand scope after the smoke guard gate

The non-smoke preparer requires an independently validated successful smoke receipt. Example:

```sh
/data/users/zekaili/fangzhou/envs/alice/bin/python reaudit_398/reviews/overfilter_20260916/v8/prepare_v8.py prepare \
  --scope expanded1678 \
  --run-dir /tmp/tmax-v8-opus-canary-001 \
  --guard-evidence /tmp/tmax-v8-opus-smoke-001/message1_validation.json
```

Available scope keys are `smoke48`, `audited1422`, `expanded1678`, and `raw14601`. The expanded set is audited1422 plus256 deterministic SHA256-ranked IDs from the raw complement. The full raw target is the archive's exact14601 IDs. Security and unresolved-domain rows are separately declared exclusions, not silently dropped tasks; membership coverage and semantic judge coverage are distinct.

For a non-smoke Workflow launch, the actual review gate must select that scope and additionally include both `guard_gate_evidence` (parsed smoke validation receipt) and `guard_gate_evidence_sha256` (its actual file hash). Preparation and the workflow check successful guard contents and model/prompt/config/archive bindings. A random 64-character string is not sufficient. Use a fresh run directory at each scope and repeat plan validation, message1, independent disk validation and message2.

If the model or prompt changes after smoke, the old guard evidence does not authorize expansion under the changed configuration. Re-review the changed version and obtain a fresh smoke receipt. No full-corpus result exists in this handoff.

## Output and limitations

- `manifests/scope_manifest.jsonl`: every selected ID and its routing.
- `manifests/security_exclusions.jsonl` and `security_unknown.jsonl`: explicit exclusions.
- `tasks/`: original extracted artifacts with pinned byte hashes.
- `rows/`: quarantined initial verdicts and separately appended revisions.
- `plan.json`, `run_manifest.json`, and independently generated validation receipts: provenance and coverage.

The workflow performs static rubric judging only. It does not run task containers or replace empty-submission, solve, capture, fresh-container replay, independent re-grade or mutation gates. Missing phase-3 evidence remains unresolved under V7/V8. Local syntax, preparation and mocked-host tests validate orchestration mechanics; they do not establish live Workflow host compatibility, actual model access, guard behavior from a real judge, or downstream task success.
