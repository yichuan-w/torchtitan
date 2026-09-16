// V8 fresh-judgement fan-out for the Workflow JS host.
//
// Contract:
//   args.plan = contents of a plan.json produced by prepare_v8.py
//   args.dry = true performs guard/schema/fan-out validation without agent calls
//   args.launch_gate is required for calls (see WORKFLOW_README.md)
//
// Full records remain in the new run directory's per-task JSONL files. The host
// receives labels and hashes only. There is no model fallback and no automatic
// two-judge/worst-verdict aggregation.
export const meta = {
  name: 'tmax-v8-fresh-judge',
  description: 'Fresh V8 judgments over original archive artifacts, with blinded message 1, append-only message 2, regression guards, and labels-only host returns',
  phases: [
    { title: 'Message 1', detail: 'fresh blinded judgment; one fsynced row file per task' },
    { title: 'Guard gate', detail: 'mandatory smoke guards must be operational REJECTs' },
    { title: 'Message 2', detail: 'only after complete message-1 coverage; prior-aware append-only revisions' },
  ],
}

const EXPECTED_CONFIG_SHA256 = 'fd75760415c8e78f016c4f42cc742fdda0b35c9e32d60f0e18f1e138980311d3'
const EXPECTED_PROMPT_SHA256 = '3f305309f11cbd0a1dd9370d81c57d8ad0ad2af44f3bd4b533da602dbe830e0b'
const EXPECTED_SCHEMA_SHA256 = '2952d57185cf2eeab94740c7837f672e1fd33e741a2c876604b0802874ee375d'
const EXPECTED_ARCHIVE_SHA256 = '8e0fcfb61ea62ad234423bafa3c2e60cf16239e8be8a072daf950cf64cf7f016'
const EXPECTED_MODEL = 'gpt-5.4-2026-03-05'
const EXPECTED_EFFORT = 'high'
const EXPECTED_BATCH = 8
const EXPECTED_CONCURRENCY = 8
const TASK_ID = /^task_[0-9]{6}_[0-9a-f]{8}$/
const SHA256 = /^[0-9a-f]{64}$/

const TIER = { type: 'string', enum: ['PASS', 'REPAIR', 'REVIEW', 'REJECT-PROVEN'] }
const DEFECT = {
  type: 'string',
  enum: [
    'reachable_reference', 'preplaced_value', 'unenforced_requirement',
    'shape_only_assertion', 'nondeterministic_reward', 'mutable_ground_truth',
    'artifact_identity_unbound', 'rejects_correct_variant',
  ],
}
const RECEIPT = {
  type: 'object',
  additionalProperties: false,
  required: [
    'task_id', 'row_file', 'row_sha256', 'tier', 'soundness', 'verdict',
    'core_mismatch', 'salvageable', 'concrete_exploit_status', 'defects_true',
    'atomic_replace', 'file_fsync', 'directory_fsync',
  ],
  properties: {
    task_id: { type: 'string', pattern: '^task_[0-9]{6}_[0-9a-f]{8}$' },
    row_file: { type: 'string' },
    row_sha256: { type: 'string', pattern: '^[0-9a-f]{64}$' },
    tier: TIER,
    soundness: { type: 'string', enum: ['sound', 'accepts-wrong', 'rejects-right', 'unstable'] },
    verdict: { type: 'string', maxLength: 40 },
    core_mismatch: { type: 'string', enum: ['none', 'cosmetic_secondary', 'core_wrong_reward'] },
    salvageable: { type: 'boolean' },
    concrete_exploit_status: { type: 'string', enum: ['none_found', 'static_trace', 'runtime_confirmed', 'runtime_refuted', 'not_applicable'] },
    defects_true: { type: 'array', uniqueItems: true, items: DEFECT },
    atomic_replace: { const: true },
    file_fsync: { const: true },
    directory_fsync: { const: true },
  },
}
const BUCKET_RECEIPT = {
  type: 'object',
  additionalProperties: false,
  required: ['bucket', 'prompt_sha256', 'output_schema_sha256', 'configured_model', 'receipts'],
  properties: {
    bucket: { type: 'integer', minimum: 0 },
    prompt_sha256: { const: EXPECTED_PROMPT_SHA256 },
    output_schema_sha256: { const: EXPECTED_SCHEMA_SHA256 },
    configured_model: { const: EXPECTED_MODEL },
    receipts: { type: 'array', minItems: 1, maxItems: EXPECTED_BATCH, items: RECEIPT },
  },
}
const REVISION_RECEIPT = {
  type: 'object',
  additionalProperties: false,
  required: ['task_id', 'changed', 'row_file', 'revision_sha256', 'new_tier'],
  properties: {
    task_id: { type: 'string', pattern: '^task_[0-9]{6}_[0-9a-f]{8}$' },
    changed: { type: 'boolean' },
    row_file: { type: ['string', 'null'] },
    revision_sha256: { type: ['string', 'null'], pattern: '^[0-9a-f]{64}$' },
    new_tier: { type: ['string', 'null'], enum: ['PASS', 'REPAIR', 'REVIEW', 'REJECT-PROVEN', null] },
  },
}
const REVISION_BUCKET = {
  type: 'object',
  additionalProperties: false,
  required: ['bucket', 'revisions'],
  properties: {
    bucket: { type: 'integer', minimum: 0 },
    revisions: { type: 'array', minItems: 1, maxItems: EXPECTED_BATCH, items: REVISION_RECEIPT },
  },
}

const plan = args && args.plan
const stage = (args && args.stage) || 'message1'
if (!['message1', 'message2'].includes(stage)) throw new Error(`unknown stage: ${stage}`)
if (!plan || plan.schema_version !== 'tmax-v8-run-plan-v1') throw new Error('args.plan must be a prepare_v8.py tmax-v8-run-plan-v1 object')
if (!Array.isArray(plan.rows)) throw new Error('plan.rows missing')
if (plan.status !== 'PREPARED_NOT_AUTHORIZED') throw new Error(`unexpected plan status: ${plan.status}`)
if (plan.config_sha256 !== EXPECTED_CONFIG_SHA256) throw new Error(`config hash mismatch: expected ${EXPECTED_CONFIG_SHA256}, got ${plan.config_sha256}`)
if (plan.prompt_sha256 !== EXPECTED_PROMPT_SHA256) throw new Error(`prompt hash mismatch: expected ${EXPECTED_PROMPT_SHA256}, got ${plan.prompt_sha256}`)
if (plan.output_schema_sha256 !== EXPECTED_SCHEMA_SHA256) throw new Error(`schema hash mismatch: expected ${EXPECTED_SCHEMA_SHA256}, got ${plan.output_schema_sha256}`)
if (plan.archive_sha256 !== EXPECTED_ARCHIVE_SHA256) throw new Error(`archive hash mismatch: expected ${EXPECTED_ARCHIVE_SHA256}, got ${plan.archive_sha256}`)
if (plan.task_variant !== 'original_archive_only') throw new Error('only original archive artifacts are accepted')
if (plan.fresh_run_dir_created !== true || plan.old_output_appended !== false) throw new Error('run directory is not declared fresh and append-free')
if (plan.model !== EXPECTED_MODEL || plan.effort !== EXPECTED_EFFORT) throw new Error(`exact primary model required: ${EXPECTED_MODEL} effort ${EXPECTED_EFFORT}; fallback is disabled`)
if (plan.batch_size !== EXPECTED_BATCH || plan.max_concurrency !== EXPECTED_CONCURRENCY) throw new Error('batch/concurrency must both be 8')
if (!Number.isInteger(plan.scope_declared_count) || !Number.isInteger(plan.expected_judge_count)) throw new Error('scope counts missing')
if (plan.rows.length !== plan.expected_judge_count) throw new Error(`judge count guard: expected ${plan.expected_judge_count}, got ${plan.rows.length}`)
if (plan.scope_declared_count !== plan.expected_judge_count + plan.security_excluded_count + plan.security_unknown_count) throw new Error('declared scope is not completely partitioned into judge/security/unknown')
if (!plan.message2 || plan.message2.enabled !== true || plan.message2.starts_after_complete_message1 !== true) throw new Error('the two-message gate must be enabled')

const ids = plan.rows.map(row => row.task_id)
if (new Set(ids).size !== ids.length) throw new Error('duplicate task_id in plan')
for (const row of plan.rows) {
  if (!TASK_ID.test(row.task_id)) throw new Error(`bad task_id: ${row.task_id}`)
  if (!['NON_SECURITY', 'NON_SECURITY_GUARD_ATTESTED'].includes(row.security_status)) throw new Error(`security/unknown row reached judge plan: ${row.task_id}`)
  if (!row.task_dir.startsWith(plan.run_dir + '/tasks/')) throw new Error(`task path escapes fresh run: ${row.task_id}`)
  if (row.output_path !== `${plan.run_dir}/rows/${row.task_id}.jsonl`) throw new Error(`unexpected output path: ${row.task_id}`)
  if (!row.artifact_sha256 || !SHA256.test(row.artifact_sha256['instruction.md'] || '') || !SHA256.test(row.artifact_sha256['setup.sh'] || '') || !SHA256.test(row.artifact_sha256['tests/test.sh'] || '')) throw new Error(`artifact hashes missing: ${row.task_id}`)
  if (row.guard && row.guard.mandatory !== true) throw new Error(`malformed guard marker: ${row.task_id}`)
}

const chunk = (values, n) => {
  const out = []
  for (let i = 0; i < values.length; i += n) out.push(values.slice(i, i + n))
  return out
}
const buckets = chunk(plan.rows, EXPECTED_BATCH)
const guardRows = plan.rows.filter(row => row.guard)
const REQUIRED_GUARDS = ['task_000156_2f395118', 'task_000241_547763df', 'task_000311_9e22fec5', 'task_004863_67089cd4', 'task_004886_10504160']
if (plan.scope === 'smoke48' && (guardRows.length !== REQUIRED_GUARDS.length || REQUIRED_GUARDS.some(id => !guardRows.some(row => row.task_id === id)) || plan.scope_declared_count !== 48)) throw new Error('smoke48 requires all five mandatory guards and exactly 48 declared rows')

phase(stage === 'message1' ? 'Message 1' : 'Message 2')
log(`V8 preflight: scope=${plan.scope} declared=${plan.scope_declared_count} judge=${plan.expected_judge_count} security_excluded=${plan.security_excluded_count} security_unknown=${plan.security_unknown_count}`)
log(`fan-out: ${buckets.length} bucket(s), batch=${EXPECTED_BATCH}, max_concurrency=${EXPECTED_CONCURRENCY}, exact model=${EXPECTED_MODEL}, effort=${EXPECTED_EFFORT}, no fallback`)
log(`artifacts: original archive ${plan.archive_sha256}; prompt ${plan.prompt_sha256}; schema ${plan.output_schema_sha256}; run=${plan.run_dir}`)

if (args && args.dry === true) {
  return {
    dry: true,
    calls_made: 0,
    scope: plan.scope,
    declared: plan.scope_declared_count,
    judge: plan.expected_judge_count,
    security_excluded: plan.security_excluded_count,
    security_unknown: plan.security_unknown_count,
    buckets: buckets.length,
    mandatory_guards: guardRows.map(row => row.task_id),
    stage,
    launch_gate_required: plan.scope === 'smoke48' ? 'Opus-5 reviewed config' : 'Opus-5 reviewed config plus successful smoke guard evidence',
  }
}

const gate = args && args.launch_gate
if (!gate) throw new Error('launch_gate missing: Opus-5 must review the exact config/prompt/schema before calls')
if (gate.review_model !== 'claude-opus-5' || gate.review_status !== 'approved') throw new Error('launch requires approved claude-opus-5 review')
if (gate.reviewed_config_sha256 !== EXPECTED_CONFIG_SHA256 || gate.reviewed_prompt_sha256 !== EXPECTED_PROMPT_SHA256 || gate.reviewed_schema_sha256 !== EXPECTED_SCHEMA_SHA256) throw new Error('Opus-5 review hashes do not match this workflow')
if (gate.selected_scope !== plan.scope || gate.selected_model !== EXPECTED_MODEL || gate.selected_effort !== EXPECTED_EFFORT) throw new Error('reviewed model/scope do not match the prepared plan')

function validateSmokeGuardEvidence(receipt, claimedSha) {
  if (!receipt || receipt.schema_version !== 'tmax-v8-message1-validation-v1') throw new Error('guard evidence must be an independent message-1 validation receipt')
  if (receipt.scope !== 'smoke48' || receipt.config_sha256 !== EXPECTED_CONFIG_SHA256 || receipt.prompt_sha256 !== EXPECTED_PROMPT_SHA256 || receipt.output_schema_sha256 !== EXPECTED_SCHEMA_SHA256 || receipt.archive_sha256 !== EXPECTED_ARCHIVE_SHA256) throw new Error('guard evidence hashes/scope do not match this workflow')
  if (receipt.model !== EXPECTED_MODEL || receipt.effort !== EXPECTED_EFFORT || receipt.disk_validation_passed !== true || receipt.coverage_complete !== true) throw new Error('guard evidence model or disk-validation binding failed')
  const evidence = receipt.guard_gate && receipt.guard_gate.evidence
  if (!receipt.guard_gate || receipt.guard_gate.passed !== true || !Array.isArray(evidence) || evidence.length !== REQUIRED_GUARDS.length || REQUIRED_GUARDS.some(id => !evidence.some(row => row.task_id === id))) throw new Error('guard evidence does not cover all five mandatory guards')
  for (const row of evidence) if (row.pass !== true || !['REPAIR', 'REJECT-PROVEN'].includes(row.tier) || (row.missing_required_defect_flags || []).length) throw new Error(`guard evidence is not operational REJECT: ${row.task_id}`)
  if (!SHA256.test(claimedSha || '')) throw new Error('guard evidence file sha256 missing')
}

if (plan.scope !== 'smoke48') {
  if (!plan.prior_smoke_guard_gate || plan.prior_smoke_guard_gate.passed !== true) throw new Error('non-smoke plan was not prepared from validated smoke guard evidence')
  if (gate.guard_gate_evidence_sha256 !== plan.prior_smoke_guard_gate.receipt_sha256) throw new Error('guard evidence sha256 differs from the prepare-time validated receipt')
  validateSmokeGuardEvidence(gate.guard_gate_evidence, gate.guard_gate_evidence_sha256)
}

function message1Prompt(bucket, index) {
  const taskLines = bucket.map(row => [
    `- ${row.task_id}`,
    `  task_dir: ${row.task_dir}`,
    `  output_path: ${row.output_path}`,
    `  instruction_sha256: ${row.artifact_sha256['instruction.md']}`,
    `  setup_sha256: ${row.artifact_sha256['setup.sh']}`,
    `  tests_sha256: ${row.artifact_sha256['tests/test.sh']}`,
    `  image: ${row.image}`,
    `  domain: ${row.parquet_domain}`,
  ].join('\n')).join('\n')
  return `You are the PRIMARY V8 auditor for bucket ${index}. This is MESSAGE 1.

Read ${plan.prompt_path} and ${plan.output_schema_path} IN FULL. Verify their sha256 values are ${EXPECTED_PROMPT_SHA256} and ${EXPECTED_SCHEMA_SHA256}; refuse on mismatch. Follow V8 exactly.

Judge only the original archive artifacts below. Read instruction.md, setup.sh, and tests/test.sh in full and verify the supplied byte hashes before deciding. Do not run task code, setup.sh, tests/test.sh, containers, network calls, or payloads. Do not search for or read priors, earlier verdicts, repair overlays, repaired task trees, selection roles, guard expectations, or other result files. Your judgment must be fresh.

${taskLines}

For each task, substitute that task's output_path for {V8_OUTPUT_PATH}. The path must not exist. Immediately after finishing that task, serialize one initial V8 object (revised_after_priors:null) as one JSON line to a new temporary sibling; flush and fsync the file; atomically replace output_path; then fsync its parent directory. Validate the object against output_schema.json before continuing. Never batch records and never append an old output.

Return structured labels only: bucket=${index}, both verified hashes, configured_model=${EXPECTED_MODEL}, and exactly one receipt per assigned task. defects_true is the sorted list of true keys in the row's defects object. row_sha256 hashes the exact JSON line including its final newline. Do not return evidence, reason, repro_cmd, verifier_fix, task text, or any other free text.`
}

async function runMessage1Bucket(bucket, index) {
  return agent(message1Prompt(bucket, index), {
    label: `v8:m1:b${index}`,
    phase: 'Message 1',
    model: EXPECTED_MODEL,
    effort: EXPECTED_EFFORT,
    agentType: 'general-purpose',
    schema: BUCKET_RECEIPT,
  })
}

if (stage === 'message1') {
  const message1Buckets = []
  for (let start = 0; start < buckets.length; start += EXPECTED_CONCURRENCY) {
    const wave = buckets.slice(start, start + EXPECTED_CONCURRENCY)
    const waveResults = await parallel(wave.map((bucket, offset) => () => runMessage1Bucket(bucket, start + offset)))
    if (waveResults.some(result => !result)) throw new Error(`message-1 bucket failure in wave starting ${start}; no fallback attempted`)
    message1Buckets.push(...waveResults)
  }

  const receipts = message1Buckets.flatMap(result => result.receipts || [])
  const received = new Map()
  for (const receipt of receipts) {
    if (received.has(receipt.task_id)) throw new Error(`duplicate message-1 receipt: ${receipt.task_id}`)
    received.set(receipt.task_id, receipt)
    const expected = plan.rows.find(row => row.task_id === receipt.task_id)
    if (!expected) throw new Error(`unplanned message-1 receipt: ${receipt.task_id}`)
    if (receipt.row_file !== expected.output_path) throw new Error(`row path mismatch: ${receipt.task_id}`)
  }
  const missing = ids.filter(taskId => !received.has(taskId))
  if (missing.length) throw new Error(`message-1 coverage gap (${missing.length}): ${missing.slice(0, 20).join(', ')}`)

  phase('Guard gate')
  const guardOutcomes = guardRows.map(row => {
    const receipt = received.get(row.task_id)
    const missingFlags = (row.guard.required_defect_flags || []).filter(flag => !receipt.defects_true.includes(flag))
    const operationalReject = receipt.tier === 'REPAIR' || receipt.tier === 'REJECT-PROVEN'
    return {
      task_id: row.task_id,
      tier: receipt.tier,
      verdict: receipt.verdict,
      guard_class: row.guard.guard_class,
      missing_required_defect_flags: missingFlags,
      pass: operationalReject && missingFlags.length === 0,
    }
  })
  const failedGuards = guardOutcomes.filter(row => !row.pass)
  if (failedGuards.length) throw new Error(`mandatory V8 regression guard failure: ${failedGuards.map(row => `${row.task_id}:${row.tier}`).join(', ')}; REVIEW/HOLD/SKIP does not count as REJECT`)
  if (plan.scope === 'smoke48') log(`host-label guard check: ${guardOutcomes.length}/${guardOutcomes.length}; run prepare_v8.py validate-message1 before this evidence is trusted`)
  return {
    stage: 'message1',
    scope: plan.scope,
    declared_count: plan.scope_declared_count,
    judged_count: plan.expected_judge_count,
    security_excluded_count: plan.security_excluded_count,
    security_unknown_count: plan.security_unknown_count,
    message1_host_receipts_complete: true,
    disk_validation_required: true,
    message2_authorized: false,
    host_label_guard_check: { passed: failedGuards.length === 0, evidence: guardOutcomes, independently_validated: false },
    model: EXPECTED_MODEL,
    effort: EXPECTED_EFFORT,
    fallback_used: false,
    run_dir: plan.run_dir,
  }
}

const validation = args && args.message1_validation
if (!validation || validation.schema_version !== 'tmax-v8-message1-validation-v1') throw new Error('message2 requires prepare_v8.py validate-message1 receipt contents')
if (validation.scope !== plan.scope || validation.config_sha256 !== EXPECTED_CONFIG_SHA256 || validation.prompt_sha256 !== EXPECTED_PROMPT_SHA256 || validation.output_schema_sha256 !== EXPECTED_SCHEMA_SHA256 || validation.archive_sha256 !== EXPECTED_ARCHIVE_SHA256) throw new Error('message1 validation receipt hashes/scope do not match the plan')
if (validation.model !== EXPECTED_MODEL || validation.effort !== EXPECTED_EFFORT || validation.disk_validation_passed !== true || validation.coverage_complete !== true || validation.validated_rows !== plan.expected_judge_count) throw new Error('message1 validation receipt is incomplete or from another model')
if (!Array.isArray(validation.rows) || validation.rows.length !== ids.length || ids.some(id => !validation.rows.some(row => row.task_id === id))) throw new Error('message1 validation receipt does not cover the exact plan IDs')
const receipts = validation.rows
const guardOutcomes = (validation.guard_gate && validation.guard_gate.evidence) || []
if (plan.scope === 'smoke48') validateSmokeGuardEvidence(validation, (args && args.message1_validation_sha256) || '')

phase('Message 2')
function message2Prompt(bucket, index) {
  return `You are the SAME configured V8 judge role for bucket ${index}, now in MESSAGE 2. Message 1 is complete for the entire scope and every first row is already durable.

Read ${plan.prompt_path} in full. For each task below, read its existing first row, then and only then read its compact prior file. Priors are context, not authority. Re-open the same three original task files if needed. Do not read repair overlays.

${bucket.map(row => `- ${row.task_id}\n  first_row: ${row.output_path}\n  prior: ${plan.message2.priors_dir}/${row.task_id}.json`).join('\n')}

If priors change your judgment, append exactly one revision row to the SAME per-task JSONL using O_APPEND, flush and fsync the file, then fsync the parent directory. The revision row must validate as output_schema.json revision_row and cite the sha256 of the first line. Never overwrite the first line. If nothing changes, write nothing.

Return structured labels only: bucket=${index}, exactly one revision receipt per task, changed false with null fields when unchanged; changed true with the same row_file, appended-line sha256 and new tier when changed. No evidence, reasons, task text, commands, or free text.`
}

async function runMessage2Bucket(bucket, index) {
  return agent(message2Prompt(bucket, index), {
    label: `v8:m2:b${index}`,
    phase: 'Message 2',
    model: EXPECTED_MODEL,
    effort: EXPECTED_EFFORT,
    agentType: 'general-purpose',
    schema: REVISION_BUCKET,
  })
}

const revisionBuckets = []
for (let start = 0; start < buckets.length; start += EXPECTED_CONCURRENCY) {
  const wave = buckets.slice(start, start + EXPECTED_CONCURRENCY)
  const waveResults = await parallel(wave.map((bucket, offset) => () => runMessage2Bucket(bucket, start + offset)))
  if (waveResults.some(result => !result)) throw new Error(`message-2 bucket failure in wave starting ${start}; no fallback attempted`)
  revisionBuckets.push(...waveResults)
}
const revisionReceipts = revisionBuckets.flatMap(result => result.revisions || [])
const revisionIds = revisionReceipts.map(row => row.task_id)
if (revisionIds.length !== ids.length || new Set(revisionIds).size !== ids.length || ids.some(id => !revisionIds.includes(id))) throw new Error('message-2 receipt coverage/uniqueness failure')
for (const revision of revisionReceipts) {
  const expectedPath = plan.rows.find(row => row.task_id === revision.task_id).output_path
  if (revision.changed && (revision.row_file !== expectedPath || !SHA256.test(revision.revision_sha256 || '') || !revision.new_tier)) throw new Error(`malformed changed revision receipt: ${revision.task_id}`)
  if (!revision.changed && (revision.row_file !== null || revision.revision_sha256 !== null || revision.new_tier !== null)) throw new Error(`malformed unchanged revision receipt: ${revision.task_id}`)
}

// D2: the first blinded line is the verdict of record. Message-2 rows are
// append-only proposed revisions for review; they never silently replace the
// operational tier or admission computed here.
const verdictOfRecordTier = new Map(receipts.map(row => [row.task_id, row.tier]))
const admission = tier => tier === 'PASS' ? 'KEPT' : tier === 'REPAIR' ? 'NOT_KEPT_REPAIR_CANDIDATE' : tier === 'REVIEW' ? 'NOT_KEPT_HOLD' : 'NOT_KEPT_REJECT'
const tierCounts = {}
const admissionCounts = {}
for (const tier of verdictOfRecordTier.values()) {
  tierCounts[tier] = (tierCounts[tier] || 0) + 1
  const state = admission(tier)
  admissionCounts[state] = (admissionCounts[state] || 0) + 1
}
const positiveControlNonPass = plan.rows
  .filter(row => row.positive_control && verdictOfRecordTier.get(row.task_id) !== 'PASS')
  .map(row => ({ task_id: row.task_id, tier: verdictOfRecordTier.get(row.task_id) }))
const guardRevisionConflicts = revisionReceipts
  .filter(revision => revision.changed && guardRows.some(row => row.task_id === revision.task_id) && !['REPAIR', 'REJECT-PROVEN'].includes(revision.new_tier))
  .map(revision => ({ task_id: revision.task_id, verdict_of_record_tier: verdictOfRecordTier.get(revision.task_id), proposed_revision_tier: revision.new_tier }))

return {
  scope: plan.scope,
  declared_count: plan.scope_declared_count,
  judged_count: plan.expected_judge_count,
  security_excluded_count: plan.security_excluded_count,
  security_unknown_count: plan.security_unknown_count,
  message1_complete: true,
  message2_complete: true,
  message2_changed: revisionReceipts.filter(row => row.changed).length,
  tier_counts: tierCounts,
  operational_admission_counts: admissionCounts,
  verdict_of_record: 'first blinded row',
  proposed_revisions_applied_to_admission: false,
  guard_gate: {
    passed: plan.scope === 'smoke48' ? guardRevisionConflicts.length === 0 : true,
    first_row_evidence: guardOutcomes,
    revision_conflicts: guardRevisionConflicts,
  },
  positive_control_non_pass: positiveControlNonPass,
  second_opinion: {
    automatic: false,
    candidates: positiveControlNonPass.map(row => row.task_id),
    note: 'Pilot/control disagreements may be sent to an explicitly selected stronger judge; no worst-of-two aggregation was applied.',
  },
  model: EXPECTED_MODEL,
  effort: EXPECTED_EFFORT,
  fallback_used: false,
  run_dir: plan.run_dir,
}
