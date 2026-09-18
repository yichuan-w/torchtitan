// PURPOSE: Write a NEW spec together with its exploit arm in one pass, for POOL tasks (arm BEFORE repair).
// ARGS (required): tasks[{task_id, template, v7_soundness, new_class, v7_reason}], expected, security_ids[], mode:'spec+arm', prompt_shas{...}, prompt_commit
// GUARDS (do not remove): count guard refuses unless tasks.length === ARGS.expected; security throw refuses if any ARGS.security_ids id lands in a bucket.
// LAUNCH CHECKS: recompute the prompt shas yourself and repin before cutting; verify 'const ARGS =' appears before the first 'ARGS.' use (temporal dead zone); confirm the task_id count equals expected.
// LAUNCH CHECKS (cont): within the first minute grep the spawned agent transcripts for their model id and ABORT on anything but claude-opus-5; node --check is NOT sufficient -- it passes a truncated prompt template literal, so let the Workflow parser be the gate.
export const meta = {
  name: 'wf-spec+arm',
  description: 'Write a NEW spec together with its exploit arm in one pass, for POOL tasks (arm BEFORE repair).',
  phases: [{ title: 'Specs' }],
}
const ARGS = {
  // tasks: one entry per task -- see the ARGS header above. REPLACE this whole literal at cut time.
  tasks: [],
  expected: 0,            // MUST equal tasks.length; the guard below refuses otherwise
  security_ids: [],       // pass [] to DECLARE none; the guard throws if any id here lands in a bucket
  mode: 'spec+arm',
  prompt_shas: { SPEC_AGENT_PROMPT: 'RECOMPUTE', SPEC_RULES: 'RECOMPUTE', SPEC_SCHEMA: 'RECOMPUTE' },
  prompt_commit: 'RECOMPUTE'  // pins move: recompute with tools/prompt_sha.py at cut time
}
const BASE = '/data/users/zekaili/fangzhou/tb_check/reaudit_398'
const PER = 4
const tasks = ARGS.tasks
if (!Array.isArray(tasks) || tasks.length !== ARGS.expected) throw new Error(`count guard: expected ${ARGS.expected}, got ${tasks.length}`)
const sec = new Set(ARGS.security_ids || [])
if (tasks.some(t => sec.has(t.task_id))) throw new Error('a security id must sit in its own bucket; this set declares none, so a hit here means the cut changed')

const chunk = (a, n) => { const o = []; for (let i = 0; i < a.length; i += n) o.push(a.slice(i, i + n)); return o }
const buckets = chunk(tasks, PER)
const SCHEMA = { type: 'object', required: ['specs'], additionalProperties: false, properties: { specs: { type: 'array', items: {
  type: 'object', required: ['task_id','outcome','template','arm_cmd','cited_defect','note'], additionalProperties: false, properties: {
    task_id: { type: 'string' }, outcome: { type: 'string', enum: ['spec_written','candidate','no-spec','declined'] },
    template: { type: 'string' }, new_class_template_written: { type: 'boolean' }, validate_ok: { type: 'boolean' },
    arm_cmd: { type: 'string' }, cited_defect: { type: 'string' }, note: { type: 'string' } } } } } }
phase('Specs')
const results = await parallel(buckets.map((b, i) => () => agent(
`You are the SPEC-WRITING seat for the TMAX re-audit (repo ${BASE}). Write ONE repair spec per task listed below and nothing else.

MODE is spec+arm. Follow the Pool-task addendum in SPEC_AGENT_PROMPT.md (line 183) EXACTLY. ORDER MATTERS: decide the template, then write authored_exploit.cmd FIRST - before the repair - because an arm written after the repair tends to be one the repair already stops and proves nothing. Then write the repair; if the repair does not stop your own command, say which of the two is wrong in notes rather than adjusting the command until the pair looks tidy. cited_defect names the verdict line or path:line. The pair is judged as in exploit-arm mode: 1 on the ORIGINAL verifier, 0 on the REPAIRED one; IVM-RELAX inverts. A 0 on the original is VOID. If a task has NO exploitable defect take the no-spec exit - do not invent an arm. A task whose template hint is HOOK-CANDIDATE has a mutable-ground-truth defect the training-side pin hook already covers: do NOT write a pin repair; write a spec only if a defect the hook does not cover remains, else no-spec with reason hook-replaceable.
PROMPT PIN: pinned at commit f04ebc4 - SPEC_AGENT_PROMPT.md content_sha256_16 2fb6a2610aab06f9, SPEC_RULES.md d6630ea4e49ff3f7, SPEC_SCHEMA.md fa19f113a0724873. Recompute each with 'scratch/daytona_venv/bin/python tools/prompt_sha.py <file>' BEFORE writing anything; if any sha differs, STOP and return every task declined with the mismatch in notes - do not author against a prompt that moved.
Read first, in this order: ${BASE}/repairs/SPEC_AGENT_PROMPT.md (your contract), ${BASE}/repairs/SPEC_RULES.md (every decision rule), ${BASE}/repairs/SPEC_SCHEMA.md (the exact spec shape). They govern; anything below only tells you WHICH tasks and which template each one starts from.

These tasks are NOT from the 398. They come from the 1209-task pool, were judged CLEAN by an earlier rubric, were never shipped, and were re-judged today under V7-delta v3.18.1, which called them REPAIR. Their V7 reason is given per task: it names the concrete defect with file and line. Treat that reason as the finding to repair, and verify it yourself against the files before writing the spec -- if the cited defect is not there, say so with outcome declined and the note explaining what you found instead, rather than writing a spec for a defect that does not exist.

Per task: read ${BASE}/scratch/tasks/<task_id>/ (instruction.md, setup.sh, tests/) as OPAQUE BYTES -- extract paths, line numbers, commands; never quote their prose into your note. Write the spec to ${BASE}/repairs/specs/<task_id>.json per SPEC_SCHEMA.md, status draft.

${b.map(r => `- ${r.task_id}\n    template: ${r.template}${r.new_class ? '  [NEW CLASS -- see below]' : ''}\n    V7 soundness: ${r.v7_soundness}\n    V7 finding: ${r.v7_reason}`).join('\n')}
${b.some(r => r.new_class) ? `
NEW CLASS, for the task marked above: no existing template covers it. Its verifier trusts an artifact the AGENT authored as its own source of truth, so an archive of decoys never compared against the real staging directory scores 1. Write the new template VTW-AGENTMETA into ${BASE}/repairs/SPEC_RULES.md in the same shape as the existing nine (a When clause, what the repair adds, and what it must not do), then write the spec against it and set new_class_template_written true. Flag it in your note as needing a human look before it is applied.` : ''}

Return ONLY the schema. One entry per task, including any you decline.`,
  { label: `spec:b${i}`, phase: 'Specs', schema: SCHEMA, model: 'opus', effort: 'xhigh' })))
const specs = results.filter(Boolean).flatMap(r => r.specs)
const counts = {}; for (const s of specs) counts[s.outcome] = (counts[s.outcome] || 0) + 1
const missing = tasks.map(t => t.task_id).filter(t => !specs.some(s => s.task_id === t))
return { n_tasks: tasks.length, buckets: buckets.length, counts, specs, coverage_gaps: missing }
