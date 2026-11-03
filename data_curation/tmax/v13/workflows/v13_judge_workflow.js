export const meta = {
  name: 'v13-validation-judge',
  description: 'V13 seed-audit validation (~70 judgments: acceptance ids x2, 18 verified movers, 12 flippers) with the sha-pinned v13 rubric; each judge gets the runner-supplied candidate list for every task; judge = EXPLICIT claude-opus-4-8 medium, blinded, per-task JSONL validated against output_schema_v13.json; labels-only return',
  phases: [
    { title: 'Judge', detail: 'all sets, batches of <=6, model claude-opus-4-8 medium (explicit per call), v13 rubric', model: 'claude-opus-4-8' },
    { title: 'Aggregate', detail: 'per-set tier distribution + must-hold / must-seed checks' },
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
          fired_fields: { type: 'array', items: { type: 'string' } },
          row_written: { type: 'boolean' },
        },
        required: ['task_id', 'tier', 'row_written'],
      },
    },
  },
  required: ['results'],
}

const judgePrompt = (set, batchNo) => `You are a TMAX seed-audit judge applying the v13 rubric. Judge strictly and honestly. You are BLIND to any prior verdict on these tasks and you do not look for one. Your agent label is "judge:${set.label}:b${pad(batchNo)}" (write it in the row's "agent" field exactly).

SETUP FIRST:
1. Read the v13 judge prompt IN FULL: ${A.promptPath}. Compute its sha256 (sha256sum); confirm it equals ${A.promptSha}. On mismatch, STOP and return {"results":[]}. Use that prompt EXACTLY as your rubric. Wherever it says {TASK_ROOT}, the directory is ${A.tasksDir}; wherever it says {OUTPUT_PATH}, the file for a task is ${set.rowsDir}/<task_id>.jsonl; wherever it says {CANDIDATES_PATH}, the file for a task is ${A.candidatesDir}/<task_id>.json (the runner's candidate list: programs to dispose of, failing statements to cover).
2. Read the output schema: ${A.schemaPath} (sha256 ${A.schemaSha}). Every row you write must validate against it (rubric_version "v13"; the allOf entries are hard constraints). Validate with: ${A.python} -c 'import json,jsonschema,sys; s=json.load(open("${A.schemaPath}")); r=json.loads(open(sys.argv[1]).readline()); jsonschema.Draft202012Validator(s).validate(r); print("valid")' <rowfile>  -- before you consider the task finished; fix the row if it fails.
3. Read your batch id list: ${set.batchDir}/batch_${pad(batchNo)}.json (a JSON array of up to ${BS} task_ids).

FOR EACH task_id in your batch, in order:
- Read ${A.tasksDir}/<task_id>/instruction.md, setup.sh, and tests/test.sh IN FULL, and the task's candidate list ${A.candidatesDir}/<task_id>.json; nothing else about the task. The graded agent NEVER sees tests/test.sh; judge what the reward SIGNAL does.
- Apply the v13 procedure and produce the exact v13 output JSON object (every key). Every entry of the candidate list's programs gets exactly one disposition and every failing_statements line falls inside an assertions entry, as the rubric requires. You have an interpreter for your own arithmetic: ${A.python} in a scratch file of your own; NEVER execute the task's setup.sh, tests/test.sh or any task program, no docker, no network.
- IMMEDIATELY after finishing that ONE task, write the object as ONE JSON line to ${set.rowsDir}/<task_id>.jsonl (the file must not already exist; create it, write the single line), validate it against the schema as in step 2, then move to the next task. NEVER accumulate several tasks and write at the end.

Return structured labels ONLY (no task text, evidence, reasons, or commands): results = one entry per task_id with {task_id, tier, verdict, fired_fields (names of the non-null blocking and note fields), row_written (bool)}.`

phase('Judge')
const jobs = []
for (const set of A.sets) for (let k = 1; k <= set.nBatches; k++) jobs.push({ set, k })
log(`V13 validation: ${jobs.length} batches over ${A.sets.length} sets | judge ${JUDGE_MODEL} ${JUDGE_EFFORT} explicit | prompt ${A.promptSha.slice(0, 8)} schema ${A.schemaSha.slice(0, 8)}`)
const batchResults = await parallel(
  jobs.map(({ set, k }) => () =>
    agent(judgePrompt(set, k), {
      label: `judge:${set.label}:b${pad(k)}`,
      phase: 'Judge',
      agentType: 'general-purpose',
      model: JUDGE_MODEL,
      effort: JUDGE_EFFORT,
      schema: RESULT_SCHEMA,
    }).then((r) => ({ set: set.label, k, r }))
  )
)

phase('Aggregate')
const mustHold = new Set(A.mustHold || [])
const mustSeed = new Set(A.mustSeed || [])
const perSet = {}
for (const set of A.sets) perSet[set.label] = { expected: set.total, judged: 0, tiers: {}, must_hold_but_SEED: [], must_seed_but_held: [], failed_batches: [], rows_missing_file: [] }
for (const job of batchResults) {
  if (!job) continue
  const S = perSet[job.set]
  if (!job.r) { S.failed_batches.push(job.k); continue }
  for (const r of job.r.results || []) {
    S.judged++
    S.tiers[r.tier] = (S.tiers[r.tier] || 0) + 1
    if (mustHold.has(r.task_id) && r.tier === 'SEED') S.must_hold_but_SEED.push(r.task_id)
    if (mustSeed.has(r.task_id) && r.tier !== 'SEED') S.must_seed_but_held.push(`${r.task_id}:${r.tier}:${(r.fired_fields || []).join('+')}`)
    if (!r.row_written) S.rows_missing_file.push(r.task_id)
  }
}
const nullJobs = batchResults.filter((j) => !j).length
for (const [label, S] of Object.entries(perSet)) log(`${label}: judged ${S.judged}/${S.expected} | tiers ${JSON.stringify(S.tiers)} | must-hold but SEED=${S.must_hold_but_SEED.length} | must-seed but held=${S.must_seed_but_held.length} | failed batches=${S.failed_batches.length}`)
return { judge_model: JUDGE_MODEL, judge_effort: JUDGE_EFFORT, prompt_sha: A.promptSha, schema_sha: A.schemaSha, batches_total: jobs.length, batches_null: nullJobs, per_set: perSet }
