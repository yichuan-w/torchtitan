// spec_workflow.js — spec-writing sibling of tournament_workflow.js (repair scale-up).
// One agent() per bucket writes repairs/specs/<task_id>.json for its task_ids by following plan.prompt_file
// (repairs/SPEC_AGENT_PROMPT.md) against the per-task mapping row in plan.mapping_files. Agents write the spec
// FILES; the return value is labels-only (no free text that could carry task/verifier content). The lead runs
// `tools/repair_spec.py validate repairs/specs/*.json` after the run.
// Contract: args.plan = repairs/plans/plan_specs_mgt_wave1.json (buckets with index/name/model/effort/task_ids;
// per_agent; prompt_file/rules_file/schema_file/mapping_files; security buckets isolated). args.dry === true →
// log the plan and return the shape without calling any agent.
export const meta = {
  name: 'spec-workflow',
  description: 'Write one repair spec per task (spec-writing agents; labels-only return)',
  phases: [{ title: 'Specs', detail: 'one agent per bucket writes repairs/specs/<id>.json' }],
}

// ---- labels-only return schema (SPEC_AGENT_PROMPT.md: {task_id, status, template, pins_by_kind:{path,cmd}, exits})
const PERTASK = {
  type: 'object',
  required: ['task_id', 'status', 'template', 'pins_by_kind', 'exits'],
  additionalProperties: false,
  properties: {
    task_id: { type: 'string', pattern: '^task_[0-9]{6}_[0-9a-f]{8}$' },
    status: { type: 'string', enum: ['draft', 'captured', 're-spec', 'no-spec', 'candidate'] },
    template: { type: 'string' },
    second_template: { type: ['string', 'null'] },   // M2: the rewrite template a second pass targets (spec/second-pass modes)
    second_pass: { type: 'boolean' },                 // M2: true when this agent edited an existing spec as a second pass
    pins_by_kind: {
      type: 'object', required: ['path', 'cmd'], additionalProperties: false,
      properties: { path: { type: 'integer' }, cmd: { type: 'integer' } },
    },
    exits: { type: 'array', items: { type: 'string' } },   // exit labels incl. no-spec / new_pin
    // DECLINE SLOT. spec+arm and respec both authorise an agent to write no arm when the control is unreachable
    // until the task itself is fixed (001583's oracle exceeds the cap, 001564's gold is 0). Without somewhere to
    // SAY so, the honest outcome and a forgotten field look identical in the return, and the wave reads as if the
    // arm exists. wrote_arm false plus the reason in exits is the declared form.
    wrote_arm: { type: 'boolean' },
    decline_reason: { type: 'string' },
  },
}
const BUCKET_OUT = {
  type: 'object', required: ['bucket', 'specs'], additionalProperties: false,
  properties: { bucket: { type: 'integer' }, specs: { type: 'array', items: PERTASK } },
}
// mode exploit-arm returns an ARM, not a spec: the agent adds one field to a spec that already exists, so a
// spec-shaped schema would demand template/status/pins it never touched and invite it to restate - or invent -
// fields it was told not to write. The COMMAND is never returned: it is a shell line for that task's environment
// and this return is labels-only. cmd_sha256_16 ties the row a reader later sees to the arm authored here.
const PERARM = {
  type: 'object',
  required: ['task_id', 'wrote_arm', 'cited_defect', 'cmd_sha256_16'],
  additionalProperties: false,
  properties: {
    task_id: { type: 'string', pattern: '^task_[0-9]{6}_[0-9a-f]{8}$' },
    wrote_arm: { type: 'boolean' },
    cited_defect: { type: 'string' },
    cmd_sha256_16: { type: 'string' },
    inverted_pair: { type: 'boolean' },
    exits: { type: 'array', items: { type: 'string' } },
  },
}
const ARM_OUT = {
  type: 'object', required: ['bucket', 'arms'], additionalProperties: false,
  properties: { bucket: { type: 'integer' }, arms: { type: 'array', items: PERARM } },
}

const plan = args && args.plan
if (!plan || !Array.isArray(plan.buckets)) throw new Error('args.plan missing — pass repairs/plans/plan_specs_mgt_wave1.json as args.plan')

// SPEC_RULES §9 (user 2026-09-04, Fable item 3 on 9e9301c): pure pin templates are hook-replaceable, so the wave
// must not spend an agent writing a spec that will be demoted the moment it lands. The exclusion was process-only
// until now — written in the rules and in nobody's code — which lasts exactly as long as the person who read it.
// Hints live in plan.tasks[].template (wave-21 shape) or plan.per_task[id].template; both are read.
const PIN_HINT = /^MGT-PIN-/
const hintOf = (id) => {
  const fromTasks = Array.isArray(plan.tasks) ? (plan.tasks.find(t => t && t.task_id === id) || {}).template : null
  const fromPer = plan.per_task && plan.per_task[id] ? plan.per_task[id].template : null
  return fromTasks || fromPer || null
}
const droppedPin = []
for (const b of plan.buckets) {
  if (!Array.isArray(b.task_ids)) continue
  const keep = b.task_ids.filter(id => {
    const h = hintOf(id)
    if (h && PIN_HINT.test(h)) { droppedPin.push(`${id}:${h}`); return false }
    return true
  })
  b.task_ids = keep
}
if (droppedPin.length) log(`§9: dropped ${droppedPin.length} pure-pin candidate(s) before any agent ran: ${droppedPin.join(', ')}`)
plan.buckets = plan.buckets.filter(b => !Array.isArray(b.task_ids) || b.task_ids.length > 0)
if (!plan.buckets.length) throw new Error('§9: every bucket was a pure-pin candidate — nothing left to spec')

// SECURITY SPLIT (tmax-log-reviewer, 2026-09-04). The header of this file has claimed "security buckets isolated"
// since it was written, and NOTHING checked it: the property lived in a comment while the plan was trusted to have
// done the separating. Two waves ran that way. It never bit because both had zero security ids -- and that is the
// point: with zero security ids a working guard and a missing one produce identical output, so the absence could
// not be noticed from any result. The judge template has carried this throw all along; the spec side never did.
const _sec = args && args.security_ids
if (!Array.isArray(_sec)) throw new Error('args.security_ids is required (pass [] to declare there are none): an absent split silently routes a security id into a shared bucket, and an unknown domain counts as security')
const _secSet = new Set(_sec)
for (const b of plan.buckets) {
  const _ids = Array.isArray(b.task_ids) ? b.task_ids : []
  const _s = _ids.filter(i => _secSet.has(i))
  const _n = _ids.filter(i => !_secSet.has(i))
  if (_s.length && _n.length) throw new Error(`bucket ${b.index}: ${_s.length} security id(s) share it with ${_n.length} non-security id(s) — a security task never shares a bucket`)
  // Fable: `b.kind && b.kind !== 'security'` let an UNLABELLED bucket through, because an absent kind is
  // falsy - so the one case most likely to occur in a hand-cut plan was the one case the check skipped.
  // A security id belongs in a bucket that SAYS security; anything else, labelled wrongly or not at all, throws.
  if (_s.length && b.kind !== 'security') throw new Error(`bucket ${b.index} holds ${_s.length} security id(s) but is labelled kind=${b.kind === undefined ? '(none)' : b.kind} — a security bucket must say so`)
}
log(`security split: ${_secSet.size} security id(s) declared; ${plan.buckets.filter(b => (b.task_ids || []).some(i => _secSet.has(i))).length} bucket(s) hold them, none mixed`)

phase('Specs')
log(`spec plan: ${plan.buckets.length} bucket(s), ${plan.counts ? plan.counts.tasks : plan.buckets.reduce((n, b) => n + b.task_ids.length, 0)} task(s), per_agent ${plan.per_agent}; prompt ${plan.prompt_file}; rules ${plan.rules_file}; schema ${plan.schema_file}; mapping ${(plan.mapping_files || []).join(', ')}`)
for (const b of plan.buckets) log(`bucket ${b.index} [${b.name}] mode=${b.mode || plan.mode || 'spec'} model=${b.model} effort=${b.effort} tasks=${b.task_ids.length}`)

if (args && args.dry) {
  return { dry: true, plan: { wave: plan.wave, counts: plan.counts, buckets: plan.buckets.map(b => ({ index: b.index, name: b.name, mode: b.mode || plan.mode || 'spec', model: b.model, effort: b.effort, n: b.task_ids.length })) } }
}

// One line per mode, so a new mode is a row here rather than a nested ternary nobody re-reads. §10 added two
// of them: with no branch, exploit-arm and spec+arm both fell through to "write a spec from scratch", which
// contradicts the addendum they exist for and would have had the 45 wave rewriting specs that already exist.
const MODE_LINE = {
  'second-pass': 'This is a SECOND PASS: EDIT the existing repairs/specs/<task_id>.json for each id — add run_cmd/observable/constraint/probe and a repairs/patches/<task_id>/tests.diff, set second_pass:true and status back to applied, edit second_template only (never the primary template). Follow the second-pass section of the prompt file.',
  'exploit-arm': 'This is an EXPLOIT-ARM pass over specs that ALREADY EXIST: add the single field authored_exploit {cmd, cited_defect} to repairs/specs/<task_id>.json and change NOTHING else - not template, not status, not pins, not the diff. Follow the exploit-arm addendum in the prompt file. The command must be the CHEAT the verdict cites: it scores 1 on the ORIGINAL verifier and 0 on the REPAIRED one, and for IVM-RELAX BOTH invert (original 0, repaired 1) because its arm is the correct variant the over-strict verifier wrongly rejects. If no arm is possible, return wrote_arm false with the reason in exits rather than inventing one.',
  'spec+arm': 'This is a POOL-TASK pass: write repairs/specs/<task_id>.json from scratch AND its authored_exploit {cmd, cited_defect} in the SAME pass. Follow the pool-task addendum in the prompt file, including its ORDER: write the exploit command BEFORE the repair, so the arm comes from the cited defect rather than from the fix you already have in mind. Ignore the second-pass section.',
  respec: 'This is a RE-SPEC pass over specs that are already APPLIED and are WRONG: rewrite each one under the spec+arm contract instead of adding to it. Follow the re-spec addendum in the prompt file. Read the direction off the GOLD PAIR, not the verdict text: if the SHIPPED verifier is defective (rejects right answers, calls a missing binary, carries an impossible constant, imports an uninstalled module) re-derive the template and the pair INVERTS (arm 0 on the original, 1 on the repaired); if OUR repair is the over-tight party (gold 1 on the original, 0 on the repaired) loosen OUR check and the pair stays STANDARD. Write a NEW tests.diff replacing the old one, set status back to candidate, name in notes why the old spec failed, and drop pins where the old pin was the only content. If no command can reach its expected value on the ORIGINAL verifier until the task itself is fixed, return wrote_arm false with the reason rather than an arm that can only record VOID.',
  spec: 'This is a NEW spec pass: write repairs/specs/<task_id>.json from scratch. Ignore the second-pass section of the prompt file.',
}


function specPrompt(b) {
  const mode = b.mode || plan.mode || 'spec'   // per-BUCKET mode (bucket.mode) falling back to plan.mode; the prompt's second-pass section applies only when mode === 'second-pass'
  return [
    `MODE: ${mode}. ${MODE_LINE[mode] || MODE_LINE.spec}`,
    `Read ${plan.prompt_file} and follow it EXACTLY for each of these task_ids: ${b.task_ids.join(', ')}.`,
    `Every decision rule is in ${plan.rules_file}; the spec schema is ${plan.schema_file}. The mapping row for each task_id is in ${(plan.mapping_files || []).join(' or ')} (match the task_id column).`,
    ...(plan.notes ? b.task_ids.filter(t => plan.notes[t]).map(t =>   // per-task instruction from the plan (fix_mode + what_to_change); without this the map is inert and the agent cannot know what to change
      `TASK ${t}: fix_mode ${plan.notes[t].fix_mode || plan.notes[t]}. ${plan.notes[t].what_to_change || ''}`.trim()) : []),
    `Write each spec to repairs/specs/<task_id>.json (one file per task), valid against the schema. Do not read or copy task/verifier prose into the spec — paths, shell lines, ids, shas and short tags only.`,
    mode === 'exploit-arm'
      ? `RETURN (structured) labels only: {"bucket": ${b.index}, "arms": [{task_id, wrote_arm, cited_defect, cmd_sha256_16, inverted_pair, exits} × ${b.task_ids.length}]}. cmd_sha256_16 is the first 16 hex of sha256(cmd); NEVER return the command itself. No free text.`
      : `RETURN (structured) labels only${mode === 'spec+arm' || mode === 'respec' ? ' (include wrote_arm, and decline_reason when it is false)' : ''}: {"bucket": ${b.index}, "specs": [{task_id, status, template, second_template, second_pass, pins_by_kind: {path, cmd}, exits${mode === 'spec+arm' || mode === 'respec' ? ', wrote_arm, decline_reason' : ''}} × ${b.task_ids.length}]} — exits may include "no-spec"/"new_pin". LABELS ONLY: no evidence, no task or verifier text${mode === 'spec+arm' || mode === 'respec' ? ', and decline_reason is the ONE permitted sentence — say why no arm could be written, at label level, quoting nothing' : ', and no free-text reasons'}.`,
  ].join('\n')
}

const done = (await parallel(plan.buckets.map(b => () =>
  agent(specPrompt(b), { label: `spec:${b.name}:${b.index}`, phase: 'Specs', model: b.model, effort: b.effort, agentType: 'general-purpose',
    schema: (b.mode || plan.mode) === 'exploit-arm' ? ARM_OUT : BUCKET_OUT })
    .then(out => ({ bucket: b.index, name: b.name,                      // an arm bucket reports arms; the
      specs: (out && out.specs) || (out && out.arms) || [] }))          // coverage check counts ids either way
))).filter(Boolean)

const returned = new Set()
for (const r of done) for (const s of r.specs) returned.add(s.task_id)
const planned = []
for (const b of plan.buckets) for (const t of b.task_ids) planned.push(t)
const coverage_gaps = planned.filter(t => !returned.has(t))

const byStatus = {}
for (const r of done) for (const s of r.specs) byStatus[s.status] = (byStatus[s.status] || 0) + 1
log(`done: ${returned.size}/${planned.length} specs returned (status ${JSON.stringify(byStatus)}); ${coverage_gaps.length} coverage gap(s); ${plan.buckets.length - done.length} bucket(s) failed`)
if (coverage_gaps.length) log(`coverage_gaps: ${coverage_gaps.join(', ')}`)

return {
  wave: plan.wave,
  buckets: done.map(r => ({ bucket: r.bucket, name: r.name, n: r.specs.length, specs: r.specs })),
  status_counts: byStatus,
  coverage_gaps,
  failed_buckets: plan.buckets.length - done.length,
  note: 'spec files are on disk under repairs/specs/; run tools/repair_spec.py validate repairs/specs/*.json next',
}
