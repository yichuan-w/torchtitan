export const meta = {
  name: 'v8-trial600-sec-opus48',
  description: 'V8 rubric run: 600 tasks (595 audited incl. security in dedicated batches + 5 guards), judge = EXPLICIT claude-opus-4-8 medium per invocation, security IN SCOPE, blinded, per-task JSONL, guard check',
  phases: [
    { title: 'Judge', detail: '101 batches x<=6 tasks, model claude-opus-4-8 medium (explicit per call), security judged', model: 'claude-opus-4-8' },
    { title: 'Aggregate', detail: 'tier distribution + regression-guard check' },
  ],
}

const A = args
const pad = (n) => String(n).padStart(3, '0')
const JUDGE_MODEL = 'claude-opus-4-8'
const JUDGE_EFFORT = 'medium'
const BS = A.batchSize

const RESULT_SCHEMA = {
  type: 'object',
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          task_id: { type: 'string' },
          tier: { type: 'string' },
          verdict: { type: 'string' },
          defects_true: { type: 'array', items: { type: 'string' } },
          skipped: { type: 'boolean' },
          row_written: { type: 'boolean' },
        },
        required: ['task_id', 'tier', 'skipped', 'row_written'],
      },
    },
  },
  required: ['results'],
}

const judgePrompt = (batchNo) => `You are a TMAX audit judge applying the V8 rubric. Judge strictly and honestly. You are BLIND to any prior verdict.

SETUP FIRST:
1. Read the V8 judge prompt IN FULL: ${A.promptPath}. Compute its sha256; confirm it equals ${A.promptSha}. On mismatch, STOP and return {"results":[]}. Use that prompt EXACTLY as your rubric.
2. Read the output schema: ${A.schemaPath} (sha256 ${A.schemaSha}); your per-task JSON object must validate against it.
3. Read your batch id list: ${A.batchDir}/batch_${pad(batchNo)}.json (a JSON array of up to ${BS} task_ids).

SCOPE NOTE FOR THIS RUN: security-tagged / security-control tasks ARE IN SCOPE. Judge them exactly like any other task under V8. Do NOT skip them and do NOT use a SKIP_SECURITY tier in this run.

FOR EACH task_id in your batch:
- Read ${A.tasksDir}/<task_id>/instruction.md, setup.sh, and tests/test.sh IN FULL. The graded agent NEVER sees tests/test.sh (uploaded only after it submits) - judge what the reward SIGNAL does; hold that asymmetry.
- Apply V8 and produce the exact V8 output JSON object (rubric_version "v8", revised_after_priors null, every required schema field). IMMEDIATELY after finishing that ONE task, write ONE JSON line (that object) to a NEW file ${A.rowsDir}/<task_id>.jsonl (it must not already exist; create it, write the single line). NEVER accumulate several tasks and write at the end - write each task's file the moment you finish it. Validate against the schema before continuing.
- tier is authoritative: PASS / REPAIR / REVIEW / REJECT-PROVEN.

Return structured labels ONLY (no task text, evidence, reasons, or commands): results = one entry per task_id with {task_id, tier, verdict (V8 verdict label or ""), defects_true (sorted true-defect keys, [] if none), skipped (always false in this run), row_written (bool)}.`

phase('Judge')
log(`judge model pinned per-invocation: ${JUDGE_MODEL} effort ${JUDGE_EFFORT}; ${A.nBatches} batches x<=${BS}; security in scope`)
const batchResults = await parallel(
  Array.from({ length: A.nBatches }, (_, k) => () =>
    agent(judgePrompt(k + 1), {
      label: `judge:b${pad(k + 1)}`,
      phase: 'Judge',
      agentType: 'general-purpose',
      model: JUDGE_MODEL,
      effort: JUDGE_EFFORT,
      schema: RESULT_SCHEMA,
    })
  )
)

phase('Aggregate')
const rows = batchResults.filter(Boolean).flatMap((r) => (r && r.results) || [])
const tiers = {}
for (const r of rows) tiers[r.tier] = (tiers[r.tier] || 0) + 1
const guardSet = new Set(A.guards)
const guardRows = rows.filter((r) => guardSet.has(r.task_id))
const guardHeld = guardRows.filter((r) => r.tier !== 'PASS' && r.tier !== 'SKIP_SECURITY')
const guardPassBlocker = guardRows.filter((r) => r.tier === 'PASS').map((r) => r.task_id)
const kept = rows.filter((r) => r.tier === 'PASS').length
const failedBatches = batchResults.map((r, i) => (r ? null : i + 1)).filter(Boolean)
log(`judged ${rows.length}/${A.total} | PASS(kept)=${kept} | guards held ${guardHeld.length}/${A.guards.length} | guard-PASS BLOCKERs=${guardPassBlocker.length} | failed batches=${failedBatches.length}`)
return {
  judge_model: JUDGE_MODEL,
  judge_effort: JUDGE_EFFORT,
  security_in_scope: true,
  total_judged: rows.length,
  expected: A.total,
  tiers,
  kept_pass: kept,
  guards: { total: A.guards.length, held_non_pass: guardHeld.length, pass_blocker_ids: guardPassBlocker },
  failed_batches: failedBatches,
  rows_missing_file: rows.filter((r) => !r.row_written).map((r) => r.task_id),
}
