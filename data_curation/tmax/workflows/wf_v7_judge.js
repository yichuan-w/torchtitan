// PURPOSE: V7 rubric judge pass: one tier+soundness verdict per task, read-only, no prior verdicts.
// ARGS (required): rows[{task_id, tb_domain, image, luna_passrate, gold_type}], expected, security_ids[]
// GUARDS (do not remove): count guard refuses unless tasks.length === ARGS.expected; security throw refuses if any ARGS.security_ids id lands in a bucket.
// LAUNCH CHECKS: recompute the prompt shas yourself and repin before cutting; verify 'const ARGS =' appears before the first 'ARGS.' use (temporal dead zone); confirm the task_id count equals expected.
// LAUNCH CHECKS (cont): within the first minute grep the spawned agent transcripts for their model id and ABORT on anything but claude-opus-5; node --check is NOT sufficient -- it passes a truncated prompt template literal, so let the Workflow parser be the gate.
export const meta = {
  name: 'wf-v7-judge',
  description: 'V7 rubric judge pass: one tier+soundness verdict per task, read-only, no prior verdicts.',
  phases: [{ title: 'Judge' }],
}
const ARGS = {
  // rows: one entry per task -- see the ARGS header above. REPLACE this whole literal at cut time.
  rows: [],
  expected: 0,            // MUST equal rows.length; the guard below refuses otherwise
  security_ids: [],       // pass [] to DECLARE none; the guard throws if any id here lands in a bucket
}
const BASE = '/data/users/zekaili/fangzhou/tb_check/reaudit_398'
const PROMPT = BASE + '/rubrics/v7_prompt.md'
const PER = 4
const rows = ARGS.rows
if (!Array.isArray(rows) || rows.length !== ARGS.expected) throw new Error(`count guard: expected ${ARGS.expected}, got ${rows.length}`)
const sec = new Set(ARGS.security_ids || [])
if (rows.some(r => sec.has(r.task_id))) throw new Error('a security id must sit in its own bucket; this set has none, so a hit here means the cut changed')
const chunk = (a, n) => { const o = []; for (let i = 0; i < a.length; i += n) o.push(a.slice(i, i + n)); return o }
const buckets = chunk(rows, PER)
const SCHEMA = { type: 'object', required: ['verdicts'], additionalProperties: false, properties: { verdicts: { type: 'array', items: {
  type: 'object', required: ['task_id', 'tier', 'soundness', 'reason'], additionalProperties: false, properties: {
    task_id: { type: 'string' },
    tier: { type: 'string', enum: ['PASS', 'REPAIR', 'REJECT-PROVEN', 'REVIEW'] },
    soundness: { type: 'string', enum: ['sound', 'accepts-wrong', 'rejects-right', 'unstable'] },
    shortcut_tag: { type: 'string' },
    reason: { type: 'string' } } } } } }
phase('Judge')
const results = await parallel(buckets.map((b, i) => () => agent(
`You are the V7 auditor. Read the rubric at ${PROMPT} IN FULL and follow it exactly; it is the only rubric that applies and it is version V7-delta v3.18.1.

Judge these ${b.length} tasks, one verdict each, independently:
${b.map(r => `- ${r.task_id}  (domain ${r.tb_domain}, image ${r.image}, luna passrate ${r.luna_passrate}, gold_type ${r.gold_type})`).join('\n')}

For each task read ${BASE}/scratch/tasks/<task_id>/instruction.md, setup.sh and tests/test.sh. Do not run containers and do not modify anything: this is a read-only audit.
Do NOT look for or read any prior verdict for these tasks; they were judged by earlier rubric versions and those calls must not anchor you.
Assign exactly one tier per the rubric's D3 table (PASS requires soundness sound), record the soundness class, and give a one-sentence reason naming the concrete evidence you found in the files. Return ONLY the schema.`,
  { label: `v7:b${i}`, phase: 'Judge', schema: SCHEMA, model: 'opus', effort: 'xhigh' })))
const verdicts = results.filter(Boolean).flatMap(r => r.verdicts)
const counts = {}; for (const v of verdicts) counts[v.tier] = (counts[v.tier] || 0) + 1
const missing = rows.map(r => r.task_id).filter(t => !verdicts.some(v => v.task_id === t))
return { n_rows: rows.length, buckets: buckets.length, counts, verdicts, coverage_gaps: missing }
