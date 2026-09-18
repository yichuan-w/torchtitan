export const meta = {
  name: 'axisb-spec-writing',
  description: 'Write one repair spec per axis-B task (32 non-PASS), Opus xhigh, security isolated',
  phases: [{ title: 'Specs' }],
}
const BASE = '/data/users/zekaili/fangzhou/tb_check/reaudit_398'
const buckets = args.buckets                 // [{name, task_ids:[...], rows:{task_id:{...}}}]
if (!buckets || !buckets.length) throw new Error('args.buckets is required (name, task_ids, rows)')

const SCHEMA = {
  type: 'object', required: ['results'], additionalProperties: false,
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        required: ['task_id', 'outcome', 'template', 'validate_ok', 'note'],
        additionalProperties: false,
        properties: {
          task_id: { type: 'string' },
          outcome: { type: 'string', enum: ['spec_written', 'declined', 'no-spec', 'candidate'] },
          template: { type: 'string' },
          second_template: { type: 'string' },
          validate_ok: { type: 'boolean' },
          matched_when_clause: { type: 'string' },
          note: { type: 'string', maxLength: 300 },
        },
      },
    },
  },
}

phase('Specs')
const results = await parallel(buckets.map((b, i) => () => agent(
`You are the SPEC-WRITING seat for the TMAX re-audit (repo ${BASE}). Write ONE repair spec per task listed below and nothing else.

Read first, in this order: ${BASE}/repairs/SPEC_AGENT_PROMPT.md (your contract), ${BASE}/repairs/SPEC_RULES.md (every decision rule), ${BASE}/repairs/SPEC_SCHEMA.md (exact JSON keys), and ${BASE}/repairs/PIN_BLOCK.md §7 if you write CONTENT pins.

ONE DIFFERENCE FROM PREVIOUS WAVES, and it is the only thing not covered by that prompt. These tasks have NO mapping row: repairs/templates_fullrun_v317_slice1.tsv and templates_fullrun_v3181_slices2to5.tsv cover the 340 tree tasks and contain none of these ids. So you SELECT the template yourself from SPEC_RULES, whose template sections each open with a "**When:**" clause keyed on the verdict and the grader shape. Read the task's files, match the clause, and report which clause you matched in matched_when_clause. If no clause matches, that is a real answer: take the EXITS route in SPEC_RULES (candidate / no-spec) rather than forcing a template.

Your tasks in this bucket (${b.name}). Each line is task_id | winning tier | verdict | soundness | security_union | tier_disagreement:
${b.task_ids.map(t => `${t} | ${b.rows[t].tier} | ${b.rows[t].verdict} | ${b.rows[t].soundness} | sec=${b.rows[t].security_union} | disagree=${b.rows[t].tier_disagreement}`).join('\n')}

READ YOUR OWN VERDICT ROW. The remaining fields you must copy verbatim into exploit_source -- reference_paths,
verifier_fix, mismatch_direction, shortcut_tag, repro_cmd, repro_expected -- are NOT in this prompt on purpose: they
describe the task's verifier, and the seat that cut this workflow does not read task content. Take them from the
judgement record itself, matching on task_id in ${BASE}/judge/<dir>/judgements_V7_bucket_*.jsonl where <dir> is
regress_v317_20260902 for tier regress_v317 and run_small_20260902 for tier smallrun_all. Copy them; never re-derive.

Per task: read ${BASE}/scratch/tasks/<task_id>/ (instruction.md, setup.sh, tests/) as OPAQUE BYTES — extract paths, line numbers, commands; never quote their prose or a revealed value. Copy the verdict-row fields VERBATIM into the spec's exploit_source; never re-derive them. Write ${BASE}/repairs/specs/<task_id>.json conforming to SPEC_SCHEMA.md, status "draft", every pin's sha256 left null (the capture step fills digests; you never compute one). Emit a tests.diff under ${BASE}/repairs/patches/<task_id>/tests.diff if your template is diff-emitting.

Then RUN, for each task you wrote: python3 ${BASE}/tools/repair_spec.py validate --as-applied ${BASE}/repairs/specs/<task_id>.json — and report validate_ok from its exit status. A spec that does not pass --as-applied is not finished; fix it or take the EXITS route.

One task carries tier_disagreement=true: its two judge tiers disagree (rejudge_v316 said OTHER, regress_v317 said VERIFIER-TOO-WEAK, and precedence took the latter). You may DECLINE it with outcome "declined" and a reason if you judge the verdict unsafe to author against; that is an accepted outcome, not a failure.

Do not touch any file outside repairs/specs/, repairs/patches/ and your own reading. Do not modify another task's spec. Return the structured results only; notes are label-level (paths, line numbers, template names) and must not quote task text.`,
  { label: `spec:${b.name}`, phase: 'Specs', schema: SCHEMA, model: 'opus', effort: 'xhigh' })))

const all = results.filter(Boolean).flatMap(r => r.results)
const want = buckets.flatMap(b => b.task_ids)
return {
  n_tasks: want.length,
  buckets: buckets.length,
  written: all.filter(r => r.outcome === 'spec_written').length,
  validate_ok: all.filter(r => r.validate_ok).length,
  declined: all.filter(r => r.outcome === 'declined').length,
  exits: all.filter(r => r.outcome === 'no-spec' || r.outcome === 'candidate').length,
  results: all,
  coverage_gaps: want.filter(t => !all.some(r => r.task_id === t)),
}
