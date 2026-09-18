export const meta = {
  name: 'axisb-security-second-read',
  description: 'Second independent Opus xhigh read of the axis-B security REWRITE specs before they are applied',
  phases: [{ title: 'SecondRead' }],
}
const BASE = '/data/users/zekaili/fangzhou/tb_check/reaudit_398'
const ids = args.ids
if (!ids || !ids.length) throw new Error('args.ids is required')

const SCHEMA = {
  type: 'object', required: ['results'], additionalProperties: false,
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['task_id', 'verdict', 'template_agrees', 'exploit_closed', 'honest_solution_survives', 'note'],
        additionalProperties: false,
        properties: {
          task_id: { type: 'string' },
          verdict: { type: 'string', enum: ['ACCEPT', 'ACCEPT-WITH-NOTE', 'REJECT'] },
          template_agrees: { type: 'boolean' },
          exploit_closed: { type: 'boolean' },
          honest_solution_survives: { type: 'boolean' },
          over_tight_risk: { type: 'string' },
          note: { type: 'string', maxLength: 300 },
        },
      },
    },
  },
}

phase('SecondRead')
// one agent per task: an independent read is worth less when one agent reads them all and carries its own first
// impression from task to task
const results = await parallel(ids.map((t, i) => () => agent(
`You are the SECOND, INDEPENDENT reviewer of one axis-B repair spec (repo ${BASE}). You did not write it. Your job is
to say whether it should be APPLIED as written, not to rewrite it.

Task: ${t}. This is a SECURITY-domain task and a REWRITE template (not a pin-only repair), which is why it gets this
read: the wave-4 rule is that a security rewrite is applied only after a second reader agrees.

Read: ${BASE}/repairs/specs/${t}.json, its diff at ${BASE}/repairs/patches/${t}/tests.diff if one exists, the task at
${BASE}/scratch/tasks/${t}/ (instruction.md, setup.sh, tests/) as opaque bytes, the rules in
${BASE}/repairs/SPEC_RULES.md, and the judge verdict row for ${t} in
${BASE}/judge/run_small_20260902/judgements_V7_bucket_*.jsonl (match on task_id).

Answer four questions, each from evidence you can cite as path:line, never from the spec's own claims:
  template_agrees — does the '**When:**' clause the spec matched actually describe this task? If another template
    fits better, say which in the note.
  exploit_closed — would the judge's exploit, and the cheapest variant of it you can think of, now score 0 against
    the repaired verifier? Think like a reward hacker for one round before answering.
  honest_solution_survives — would an HONEST solution still score 1? This is the one that has bitten this campaign:
    four repairs were tighter than the model's honest solve and one pin sat on the exact file the honest solution
    rewrites. Look for grade-time regeneration that clobbers agent work, pins on paths the task tells the agent to
    modify, and checks that assume one implementation of an underspecified instruction.
  over_tight_risk — free text: the specific way this repair could refuse an honest episode, or "none found".

verdict ACCEPT (apply as written), ACCEPT-WITH-NOTE (apply, but the note records a real residual), REJECT (do not
apply; say what would have to change). A REJECT is a useful outcome, not a failure.

Change NOTHING: do not edit the spec, the diff, the task, or any file. This is a read. Return the structured result
only; the note is label-level (paths, line numbers, template names) and must not quote task text.`,
  { label: `secread:${t.slice(5, 11)}`, phase: 'SecondRead', schema: SCHEMA, model: 'opus', effort: 'xhigh' })))

const all = results.filter(Boolean).flatMap(r => r.results)
return {
  n: ids.length,
  accept: all.filter(r => r.verdict === 'ACCEPT').length,
  accept_with_note: all.filter(r => r.verdict === 'ACCEPT-WITH-NOTE').length,
  reject: all.filter(r => r.verdict === 'REJECT').length,
  honest_at_risk: all.filter(r => !r.honest_solution_survives).map(r => r.task_id),
  results: all,
  coverage_gaps: ids.filter(t => !all.some(r => r.task_id === t)),
}
