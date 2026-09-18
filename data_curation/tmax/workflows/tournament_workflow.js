// tournament_workflow.js v2 — calibration tournament judge run (P-2). NOT AUTHORIZED TO RUN by this seat.
// tmax-coder, 2026-09-02. v2 applies the lead's review of dbb829f: QUARANTINED JUDGE OUTPUT.
//
// Why v2: the v1 return value carried `evidence` strings (verbatim task text, incl. security tasks)
// back into the caller's context — a D-8 leak. Now judges WRITE their full records themselves to
//   ${plan.output_dir}/judgements_${rubric}_bucket_${k}.jsonl   (gitignored: judge/**/judgements_*.jsonl)
// and RETURN labels only; revise agents read their own bucket file by path and write
//   ${plan.output_dir}/revisions_${rubric}_bucket_${k}.jsonl
// returning {task_id, changed, new_tier} only. The workflow returns paths + label summaries — never
// evidence, repro_cmd or reasons. This also removes the StructuredOutput large-array risk.
//
// Contract: args.plan = tools/tournament_prepare.py plan.json (routing per ledger D-7; per-bucket 8-column
// phase-0 TSV; per-bucket priors TSV for message 2). args.dry === true → log the plan and return without
// calling any agent. args.skip_revise === true → Judge stage only (revise_file null, revisions []) — used when the
// priors carry no phase-3 data for most rows (full run: 353/365) and the user chooses judge-only to save usage.
export const meta = {
  name: 'tmax-calibration-tournament',
  description: 'Judge a calibration set with one rubric arm (V4/V6/V7): per-bucket judge writes its own quarantined JSONL and returns labels only; then a priors-revision pass; returns file paths + label summaries',
  phases: [
    { title: 'Judge', detail: 'one agent per D-7 bucket; reads the rubric prompt + 3 task files + 8-column phase-0 row per task; writes ONLY its bucket file' },
    { title: 'Revise', detail: 'same model reads its own bucket file + priors TSV; may only add revised_after_priors; writes the revisions file' },
  ],
}

// ---- label-only schemas (no free text that could carry task content)
const TIER = { enum: ['PASS', 'REPAIR', 'REVIEW', 'REJECT-PROVEN'] }
const JUDGE_OUT = {
  type: 'object', required: ['judgements', 'file', 'prompt_sha256_16'],
  properties: {
    file: { type: 'string' },
    prompt_sha256_16: { type: 'string', pattern: '^[0-9a-f]{16}$' },   // run-time re-hash of the prompt file the judge actually read
    judgements: {
      type: 'array',
      items: {
        type: 'object', required: ['task_id', 'tier', 'soundness', 'verdict', 'has_repro', 'confidence'],
        additionalProperties: false,
        properties: {
          task_id: { type: 'string' },
          tier: TIER,
          soundness: { enum: ['accepts-wrong', 'rejects-right', 'unstable', 'sound'] },
          verdict: { type: 'string', maxLength: 40 },
          repro_cost: { enum: ['simple', 'medium', 'hard', null] },
          has_repro: { type: 'boolean' },
          honest_fix_lines: { type: ['integer', 'null'] },     // v3.16 (D-41): an integer label, not text — safe in the summary
          confidence: { type: 'integer', minimum: 1, maximum: 10 },
        },
      },
    },
  },
}
const REVISE_OUT = {
  type: 'object', required: ['revisions', 'file'],
  properties: {
    file: { type: 'string' },
    revisions: {
      type: 'array',
      items: {
        type: 'object', required: ['task_id', 'changed'], additionalProperties: false,
        properties: { task_id: { type: 'string' }, changed: { type: 'boolean' }, new_tier: { anyOf: [TIER, { type: 'null' }] },
                      supporting_repro: { type: ['string', 'null'] } },   // D-54: priors phase3 key whose measured reward supports a move to non-PASS
      },
    },
  },
}

const plan = args && args.plan
if (!plan || !Array.isArray(plan.buckets)) throw new Error('args.plan missing — run tools/tournament_prepare.py first and pass its plan.json as args.plan')
const out = plan.output_dir
if (!out) throw new Error('plan.output_dir missing')

// D-7 point 6: per-bucket agent and task counts, logged before anything runs
log(`rubric ${plan.rubric} · ${plan.counts.tasks} tasks · security ${plan.counts.security_tasks} tasks / ${plan.counts.security_agents} opus-xhigh agents · other ${plan.counts.other_tasks} tasks / ${plan.counts.other_agents} ${plan.other_model} agents · unknown→security ${plan.counts.unknown_routed_to_security}`)
for (const b of plan.buckets) log(`bucket ${b.index} [${b.name}] model=${b.model} effort=${b.effort} tasks=${b.task_ids.length}`)

const SKIP_REVISE = args.skip_revise === true
if (SKIP_REVISE) log('skip_revise: Judge stage only — no priors message, revised_after_priors stays null for every record')
if (args.dry === true) {
  log('DRY RUN: no agent called')
  return { dry: true, plan: { rubric: plan.rubric, counts: plan.counts, buckets: plan.buckets.map(b => ({ index: b.index, name: b.name, model: b.model, effort: b.effort, n: b.task_ids.length })) } }
}

// one rubric_version stamp for BOTH stages (judgements and revisions carried different strings in rejudge_v316):
// 'V7-v3.17' from plan.prompt.version 'V7-delta v3.17' (prepare-time stamp); falls back to the rubric name
const RUBRIC_STAMP = plan.prompt && plan.prompt.version ? `${plan.rubric}-${String(plan.prompt.version).replace(/^V7-delta\s+/, '')}` : plan.rubric
const judgeFile = b => `${out}/judgements_${plan.rubric}_bucket_${b.index}.jsonl`
const reviseFile = b => `${out}/revisions_${plan.rubric}_bucket_${b.index}.jsonl`

function judgePrompt(b) {
  return [
    `You are one judge in a calibration run of the TMAX audit rubric ${plan.rubric}.`,
    `1. FIRST run \`sha256sum ${plan.prompt_path}\` and keep the first 16 hex characters as prompt_sha256_16 (provenance of the exact bytes you judge with). Then read the judge prompt at ${plan.prompt_path} IN FULL and follow it verbatim; it is the rubric. Where it says {TASK_ROOT} read ${plan.tasks_dir}; where it says {TASK_IDS} read the list below; where it says {V7_OUTPUT_PATH} read ${judgeFile(b)}.`,
    `2. Your tasks (${b.task_ids.length}), ONE AT A TIME, in this order: ${b.task_ids.join(', ')}.`,
    `   For each id read exactly these three files in full: ${plan.tasks_dir}/<id>/instruction.md, ${plan.tasks_dir}/<id>/setup.sh, ${plan.tasks_dir}/<id>/tests/test.sh,`,
    `   and the task's row in ${b.phase0_tsv} (task_id + the 8 phase-0 lead columns; nothing else is provided at this stage — do not look for prior verdicts anywhere).`,
    `3. OUTPUT FILE: append ONE JSON line per task to ${judgeFile(b)} the moment you finish that task (create the file if absent; grep it for your ids first and skip any already present). Each line is exactly the prompt's JSON block — including its v3.16 fields honest_fix_lines (integer) and honest_fix_sketch (≤40 words) — plus "task_id", "agent": "${plan.rubric}-bucket-${b.index}", "rubric_version": "${RUBRIC_STAMP}", "revised_after_priors": null, "prompt_sha256_16": <the 16 hex chars from step 1>. The FIRST line for a task_id is its verdict of record.`,
    `4. Write ONLY that one file. Do not run containers. Do not print task text, evidence or repro commands in your reply.`,
    `5. RETURN (structured): {"file": "${judgeFile(b)}", "prompt_sha256_16": <from step 1>, "judgements": [{task_id, tier, soundness, verdict, repro_cost, has_repro, honest_fix_lines, confidence} × ${b.task_ids.length}]} — labels and one integer only; the full records (evidence, reasons, repro_cmd, honest_fix_sketch) live in the file.`,
  ].join('\n')
}

function revisePrompt(b, judged) {
  return [
    `Second message of the ${plan.rubric} calibration run (rubric D2: priors arrive only after your verdicts exist).`,
    `Read YOUR OWN judgements from ${judgeFile(b)} (the file you wrote; ${judged.judgements.length} tasks). Then read ${b.priors_tsv}: per task it lists audit_v4_verdict, v5_verdict, v6_tier and, where they exist, phase-3 container rewards (phase3_rewards_json: empty / gold / repro rewards — reward 1 on a repro means a non-solution scored).`,
    `Rules: you may NOT edit ${judgeFile(b)}. For each task append ONE JSON line to ${reviseFile(b)}: {"task_id", "agent": "${plan.rubric}-bucket-${b.index}", "rubric_version": "${RUBRIC_STAMP}", "revised_after_priors": null} if nothing changes, else "revised_after_priors": {only the fields you would now change among tier/soundness/verdict/confidence, plus "reason": one sentence citing the prior or the phase-3 reward that moved you, "cites_first_line": true, "supporting_repro": <the exact phase3_rewards_json key (e.g. "repro:aw:V7-v3.7:bucket_2") whose measured reward supports the new call — REQUIRED whenever the revision moves the record from PASS to a non-PASS tier and that key's reward is 1; null otherwise>}. A move to non-PASS without a supporting_repro of reward 1 is scored as REVIEW (D-54). Never overwrite anything.`,
    `Phase-3 evidence overrides static calls in either direction (D4): a repro that scored 1 refutes PASS; a probe that failed to reproduce clears a suspicion-based REVIEW.`,
    `Write ONLY ${reviseFile(b)}. Do not print task text, evidence or repro commands in your reply.`,
    `RETURN (structured): {"file": "${reviseFile(b)}", "revisions": [{task_id, changed, new_tier, supporting_repro} × ${judged.judgements.length}]}.`,
  ].join('\n')
}

// Judge → Revise per bucket, no barrier (pipeline): bucket k can be revising while bucket j still judges.
const results = await pipeline(
  plan.buckets,
  b => agent(judgePrompt(b), { label: `judge:${b.name}:${b.index}`, phase: 'Judge', model: b.model, effort: b.effort, schema: JUDGE_OUT }),
  (judged, b) => {
    if (!judged) { log(`bucket ${b.index}: judge returned nothing — skipping revision`); return null }
    const planSha = plan.prompt && plan.prompt.file_sha256_16
    if (planSha && judged.prompt_sha256_16 !== planSha) log(`bucket ${b.index}: PROMPT DRIFT — judge read ${judged.prompt_sha256_16}, plan prepared against ${planSha} (${plan.prompt.version || 'unstamped'})`)
    else log(`bucket ${b.index}: prompt_sha256_16 ${judged.prompt_sha256_16}${planSha ? ' = plan' : ' (plan carries no prompt hash)'}`)
    const got = new Set(judged.judgements.map(j => j.task_id))
    const missing = b.task_ids.filter(t => !got.has(t))
    if (missing.length) log(`bucket ${b.index}: ${missing.length} task(s) without a judgement: ${missing.join(', ')}`)   // no silent caps
    if (SKIP_REVISE) return { bucket: b.index, name: b.name, model: b.model, prompt_sha256_16: judged.prompt_sha256_16, judge_file: judged.file || judgeFile(b),
                              revise_file: null, labels: judged.judgements, revisions: [], missing }
    return agent(revisePrompt(b, judged), { label: `revise:${b.name}:${b.index}`, phase: 'Revise', model: b.model, effort: b.effort, schema: REVISE_OUT })
      .then(rev => ({ bucket: b.index, name: b.name, model: b.model, prompt_sha256_16: judged.prompt_sha256_16, judge_file: judged.file || judgeFile(b),
                      revise_file: rev ? (rev.file || reviseFile(b)) : null, labels: judged.judgements,
                      revisions: rev ? rev.revisions : [], missing }))
  },
)

const done = results.filter(Boolean)
const tiers = {}
for (const r of done) for (const j of r.labels) tiers[j.tier] = (tiers[j.tier] || 0) + 1
const changed = done.flatMap(r => r.revisions.filter(v => v.changed).map(v => ({ bucket: r.bucket, task_id: v.task_id, new_tier: v.new_tier })))
log(`done: ${done.reduce((n, r) => n + r.labels.length, 0)}/${plan.counts.tasks} judgements (tiers ${JSON.stringify(tiers)}), ${changed.length} revised, ${plan.buckets.length - done.length} bucket(s) failed`)
// Next steps (outside the workflow): tools/tournament_to_batch.py over the judge files → repro_exec.py batch →
// tools/tournament_score.py. The judge/revision files are quarantined (gitignored) and read only by the Opus log-reviewer seat.
return {
  plan: { rubric: plan.rubric, counts: plan.counts, buckets: plan.buckets.map(b => ({ index: b.index, name: b.name, model: b.model, n: b.task_ids.length })) },
  files: done.map(r => ({ bucket: r.bucket, judge_file: r.judge_file, revise_file: r.revise_file, prompt_sha256_16: r.prompt_sha256_16 })),
  prompt: { plan: plan.prompt || null, judged: [...new Set(done.map(r => r.prompt_sha256_16))] },
  labels: done.flatMap(r => r.labels.map(j => ({ ...j, bucket: r.bucket, model: r.model, rubric: plan.rubric }))),
  revised: changed,
  tier_counts: tiers,
  missing: done.flatMap(r => r.missing.map(t => ({ bucket: r.bucket, task_id: t }))),
  failed_buckets: plan.buckets.length - done.length,
  skip_revise: SKIP_REVISE,
}
