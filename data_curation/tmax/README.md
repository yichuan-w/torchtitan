# TMAX data curation — how the RL task set was cleaned

A reproducible pipeline for turning a raw terminal-agent task pool into an
RL-trainable set: **how the data came to be**, and **how the audit workflow is
launched**. Built for the TMAX / Terminal-Bench task family.

The problem it solves: these tasks ship **no reference solutions**, so you cannot
prove a task correct by running a known-good answer through its verifier. A weak
or leaky verifier is not a cosmetic defect — under RL it trains reward hacking
(the model learns to `cp` a reachable reference and collect reward 1 without
doing the task). This pipeline therefore filters for **hack-free** tasks: reward
is unreachable without doing the work.

## Start here

| you want… | read |
|---|---|
| where the data came from + the split definitions | [`docs/PROVENANCE.md`](docs/PROVENANCE.md) |
| the exact funnel and per-version drop counts | [`docs/FUNNEL.md`](docs/FUNNEL.md) |
| how to launch the audit workflow | [`workflows/LAUNCH.md`](workflows/LAUNCH.md) |
| what each gate checks + how to run it | [`gates/README.md`](gates/README.md) |
| the judge rubric (current) | [`rubrics/v7_prompt.md`](rubrics/v7_prompt.md) + [`rubrics/v7_rubric_delta.md`](rubrics/v7_rubric_delta.md) |
| the rubric's full version history + measured effect per version | [`rubrics/CHANGELOG.md`](rubrics/CHANGELOG.md) |
| V1's story (the River-rubric start) + the full V2 prompt | [`docs/AUDIT_PROMPT.md`](docs/AUDIT_PROMPT.md) |

## The pipeline in one picture

```
raw pool (14,601)
  │  gate 1  empty-submission        → drop verifiers that pay out for nothing
  ▼
  │  gates 2–3  rubric audit          → two blind judges, take the WORST reading
  ▼           (8-category rubric after the River paper, arXiv 2608.22631)
usable (570 / 1,422 audited = 40.1%)
  │  gate 4  solve gate               → a competent agent must actually solve it (≥1/3)
  ▼
kept (425)
  │  gates 5–6  capture + oracle replay → the solution reproduces reward in a CLEAN container
  ▼
  │  gate 7  independent re-gate       → pass 6 was not a harness artifact (350/351)
  ▼  gate 8  mutation test             → the verifier reads the answer, not just its shape
train (398)
```

The rubric was **iterated V1→V7** across several audit workflow runs; each
version's prompt and the number of tasks it filtered are in
[`docs/FUNNEL.md`](docs/FUNNEL.md) and `rubrics/CHANGELOG.md`.

## Provenance / basis

The 8-category rubric is modelled on **River** — *Learning Generalizable
Behaviors for Terminal Agents*, Yao, Pang, Nguyen, Zhao, Joty & Yavuz (Salesforce
AI Research), [arXiv 2608.22631](https://arxiv.org/abs/2608.22631) — which audited
the same source and reports >60% of environments have ≥1 quality issue, leaving
<40% suitable for RL training. This pipeline reaches a comparable first-pass
rate (40.1% usable) and then filters harder for hack-freeness.

## What is NOT in this package

No task content (instructions/gold/traces), no credentials, no runtime keys.
The runtime gates (4–8) drive sandboxes through a **shared** Daytona key that is
supplied via environment only and never committed — see `gates/README.md` for the
shared-key hazard note.
