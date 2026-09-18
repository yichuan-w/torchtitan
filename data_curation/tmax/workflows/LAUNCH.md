# How the audit workflow is launched

There are two kinds of run: the **judge/audit workflows** (LLM judges fanning out
over task batches) and the **runtime gates** (Python driving sandboxes). The
rubric audit is A; the empty-submission / solve / oracle / mutation gates are B
(see [`../gates/README.md`](../gates/README.md)).

## A. Judge/audit workflows (`*_workflow.js`)

Each `*_workflow.js` is a **Claude Code Workflow script**. Shape:

```js
export const meta = { name, description, phases: [{ title: 'Calibrate' }] }
const AGENTS = [ { label: 'cal-0', prompt: '<the judge rubric + a batch of task ids>' }, ... ]
// body fans AGENTS out in parallel and collects one structured verdict per task
```

- **Judge prompt = the rubric.** The embedded prompt is the version's audit
  rubric (`rubrics/reference/vN_*` / `rubrics/v7_*`). Wording is ours, the
  8 categories are the River paper's.
- **Two independent judges, worst reading.** Run the same prompt on two models
  (this project used Opus 5 and Fable 5) and keep the WORST verdict. A single
  judge's CLEAN is the least durable thing it says.
- **The asymmetry the judge must hold:** the judge reads `tests/test.sh`; the
  **agent never does** — tests are uploaded only after it submits. The judge's
  job is to score what the reward signal does, not what it could exploit knowing
  the verifier.
- **Security-domain tasks** route to a stronger model bucket, judged separately,
  never mixed into the general pool.

**Inputs staged per task** (the judge reads these three, in full, one task at a
time), by default under `/tmp/tmax_gate_work/<task_id>/`:

```
<task_id>/instruction.md      # spec shown to the agent
<task_id>/setup.sh            # built into the container before the agent starts
<task_id>/tests/test.sh       # the verifier (judge-only; agent never sees it)
```

**Launch** (conceptually): pass the `*_workflow.js` to the Workflow runner, which
fans the `AGENTS` out concurrently and writes one verdict record per task. The
tournament variant (`tournament_workflow.js`) adds routing + calibration:

```
# 1. build the batch + routing (pure Python, ids/labels only, no model call)
python workflows/tournament_prepare.py    --ids <id-list>  --out batch.json
# 2. run the audit workflow (two judge models) over the batch  → verdicts.jsonl
# 3. score / tabulate
python workflows/tournament_score.py       verdicts.jsonl   --out scored.tsv
python workflows/tournament_to_batch.py    scored.tsv       --out next_batch.json
python workflows/tournament_reward_table.py scored.tsv
```

Version history of the judge prompt and the per-version filtering counts:
`rubrics/CHANGELOG.md` and [`../docs/FUNNEL.md`](../docs/FUNNEL.md).

## B. Runtime gates

The empty-submission, solve, capture/replay and mutation gates run Python against
**Daytona** sandboxes. The Daytona key is supplied via environment only
(`DAYTONA_API_KEY` / project convention), never committed, and **is shared** —
see the hazard note in `../gates/README.md`. Order and per-gate mechanics are in
[`../gates/README.md`](../gates/README.md).

## Reproducing the funnel

Run in order: gate 1 (empty-submission) → stage survivors → audit workflow
(gates 2–3, two judges) → solve gate (gate 4) → capture + oracle replay
(gates 5–6) → independent re-gate (gate 7) → mutation (gate 8) → assemble the
`train` / `checking` / `legacy` splits. Expected counts at each step:
[`../docs/FUNNEL.md`](../docs/FUNNEL.md).
