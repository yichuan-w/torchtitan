# Provenance — where the TMAX task set came from

## Source

- **Raw pool:** 14,601 TMAX / Terminal-Bench-family tasks, each a directory of
  `instruction.md` / `setup.sh` / `tests/test.sh` plus a Docker image. The tasks
  ship **no reference solution** — there is no oracle to validate a verifier
  against by construction.
- **Published clean set:** HuggingFace dataset `Fzz1/Tmax-Tasks-Clean`, with the
  method write-up in `README.md` / `PROCESS.md` / `AUDIT_PROMPT.md` (the
  authoritative funnel + rubric text).

## Rubric basis — the River paper

The audit rubric's 8-category taxonomy is modelled on **River**:

> *Learning Generalizable Behaviors for Terminal Agents* — Yao, Pang, Nguyen,
> Zhao, Joty & Yavuz (Salesforce AI Research), arXiv 2608.22631.

V1 implemented the River rubric (App. C) faithfully; V2→V7 refined the wording,
added a soundness×exploitability axis, and added a reproduction gate. The
categories (wording ours, categories the paper's):

`CLEAN`, `VERIFIER-TOO-WEAK`, `INSTR-VERIFIER-MISMATCH`, `INSTR-ENV-MISMATCH`,
`ANSWER-LEAK`, `INSTR-AMBIGUOUS`, `TASK-TRIVIAL`, `OTHER`.

River reports (§3.2) that >60% of environments exhibit ≥1 quality issue, leaving
<40% suitable for RL training; the abstract trains on <30%. Our independent audit
landed at 40.1% usable first-pass, then filtered harder for hack-freeness.

## The three splits produced

| split | rows | what it is | trust |
|---|---:|---|---|
| `train` | 398 | passed the rubric audit AND was solved by a competent agent (≥1/3) in a clean image | usable |
| `checking` | 207 | held back: 180 where two judges disagreed (re-judged → only 2.8% clean) + 27 withdrawn on adversarial review | inspect, don't train |
| `legacy` | 641 | earlier build selected by grader-strength heuristics; a later audit found 39.5% answer-leak / 10.8% weak-verifier | superseded |

## Why "no oracle" forces a stricter standard

Because no reference solution exists, "is this task correct?" cannot be answered
positively. The pipeline therefore:

1. **inverts the burden** — a task is CLEAN only when no defect is found (not
   "proven correct"); and
2. **manufactures oracle substitutes** — a solve gate (a competent agent must
   solve it), a capture+replay oracle (the produced solution must reproduce
   reward 1 in a clean container), and a mutation test (the verifier must read
   the answer, not just its shape).

See [`FUNNEL.md`](FUNNEL.md) for the counts and `../gates/README.md` for the
per-gate mechanics.
