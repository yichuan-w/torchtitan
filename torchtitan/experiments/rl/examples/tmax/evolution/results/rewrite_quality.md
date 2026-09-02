# Rewrite quality: what six prompt versions changed, and how it was measured

The rewrite exists to fix one number. The seed corpus is 72% pass@5 = 1.0 for
GPT-5.6-sol — solved on every attempt, an advantage of exactly zero under GRPO,
no gradient — leaving about a fifth of 861 tasks doing any work. Rewriting is not
a data-volume exercise; it is difficulty manufacture, which is why RST runs
fifteen rounds rather than one.

So "the rewrite got better" has to be a claim about numbers.
`scripts/review_synth.py` scores a run on six, each with a target beside it, and
every version is archived under `baseline-v*/` with its records and its packages.

## What moved

| | v1 | v3 | v5 | v14 | v16 | v18 | v19 | target |
|---|---|---|---|---|---|---|---|---|
| accepted | 0% | 13% | 14% | 4% | 8% | 17% | **35%** | — |
| reached the rollouts | — | 43% | 46% | 59% | — | 40% | **53%** | — |
| too_hard | 0% | 43% | 46% | 26% | 17% | 8% | **5%** | low |
| too_easy | 100% | 27% | 23% | 30% | 14% | 12% | **6%** | low |
| oracle_failed | — | 26% | 32% | 19% | 33% | 31% | 27% | low |
| retune converted | — | — | — | — | 3% | 34% | **62%** | — |
| has a no-shortcut check* | 40% | 70% | 54% | — | — | **96%** | — | 100% (RST) |
| >= 4 checks* | 80% | 58% | 75% | — | — | **98%** | — | 100% (RST) |
| solve.sh lines, median | 101 | 156 | 180 | — | — | 163 | — | 67 (RST round-1) |

\* both undercounted by the detector — see the correction below.

The too-easy end came down from 100% to 23% against seeds at 72%, so the rewrite
does add difficulty. What it kept doing was overshooting into the other end.

## The four root causes, and how each was found

None of these came from reading the prompts. Each came from reading which checks
the failures failed on.

**Two of the four were caused by the fix before them.** That is worth stating
plainly: a prompt change that improves one number reliably breaks another, and
the only defence is measuring after every round rather than reasoning about it.

### 1. A mandatory check the seed could not support (v2 → v3)

v2 made `no_shortcut` mandatory and specified one implementation: perturb an
input, re-run the workflow, assert the output followed. Step 2 began returning
`blocked` when a seed had no re-runnable workflow — 11% of attempts, all of them
created by the previous commit. Now three forms in preference order: re-run,
recompute-and-compare inside the verifier, or an intermediate artifact that must
agree with the final one.

### 2. The check demanded a property nothing stated (v3 → v4)

Every `too_hard` task failed on the same family of check names —
`test_rerunnable_input_variation`, `test_clean_workflow_produces_current_artifacts`
— and failed on all four attempts. The check re-runs the workflow after changing
an input. The agent was never told the workflow had to be repeatable. It solves
the task correctly, once, and fails every attempt.

That is hidden difficulty, which is precisely what the audit gate exists to
reject. The audit missed it because it looks for undiscoverable **paths** and
this is an undiscoverable **property**. Step 3 now states it: fairness covers
properties of the deliverable, not only paths and formats.

### 3. The same thing from the oracle's side (v4 → v5)

Eight of nineteen oracle failures had a reference solution that exited 0 and a
verifier that scored it zero — the signature this corpus has produced six times
now. The failing checks were the same re-run family.

Step 1 had asked for a script "idempotent where reasonable", which is both too
weak and the wrong property. A script that writes a fixed answer is perfectly
idempotent and fails this check the moment an input changes, because the check
does not ask whether re-running is safe; it asks whether the output follows the
input. It now asks for recomputation from the inputs as they are at run time, and
says why, because the model reached for the weaker reading when given the weaker
word.

### 4. The consistency pass never checked that direction (v5 → v6)

Oracle failures sat at 26-32% through five versions and are the largest single
loss: a build spent, a task discarded. The cross-file consistency pass was
repairing three directions — artifacts the contract omits, checks without public
evidence, instruction requirements nothing satisfies — and not the one that
produces oracle failures, which is **verifier checks the solution does not
satisfy**. It now walks each check and names the lines of solve.sh behind it,
fixing whichever side is wrong rather than always the same side.

## Two harness faults, wrongly attributed to the rewrite

- **Generated packages were missing the seed's build context.** The seed
  Dockerfile `COPY`s files that live beside it; `materialize()` wrote only the
  Dockerfile. Preflight then rejected the result for a missing source that the
  packaging had dropped rather than the rewrite invented.
- **The audit flagged `/tmp` as undiscoverable**, which was two of every three
  tasks it turned down. A path every Unix has is not a hidden requirement.
  Whitelisted exactly, so `/tmp/scratch` stays flagged — a verifier asserting a
  specific file the instruction never mentions is still a dark check.

And one measurement fault worth recording: the first version of
`review_synth.py` looked for the *words* placeholder, hardcoded, stale, reported
0% anti-shortcut coverage, and nearly sent a working prompt back for repair. The
model was writing the strongest form of the check — mutate a fixture, re-run,
assert regeneration — which contains none of those words. Judging a working
pipeline broken is how a good one gets edited into a worse one.

## Does the output hold up independently?

The loop's difficulty gate samples k=4, once. All 22 tasks v19 accepted were then
re-measured at k=5 by `solve_eval.py` — the same harness, the same protocol used
on the seeds, and from v19 on the same solver as the gate, with no knowledge of
its verdict.

| pass@5 | tasks | |
|---|---|---|
| 1.00 | 3 | drifted to no gradient |
| 0.80 | 3 | |
| 0.75 | 2 | |
| 0.60 | 2 | |
| 0.40 | 2 | |
| 0.33 | 1 | |
| 0.20 | 5 | |
| 0.00 | 4 | drifted to no gradient |

**15 of 22 in the usable band, against the seed corpus's 20.3%** — the rewrite
moves difficulty where it is meant to go, by a factor of 3.3, and spreads it
across the band rather than piling it at one edge.

**7 of 22 still moved out of the band the gate put them in.** A single estimate
of difficulty is not a property of the task, which is why shipping is two stages:
`ship_dataset.sh` collects what the gates accepted, re-measures all of it, and
keeps only what held. `data/shipped-v19/tasks-00000.tar` is those 15.

## Correction: the coverage numbers above were the detector, not the output

Every anti-shortcut figure in the table — 40%, 54%, 70%, 54% — is an undercount,
and so is every `>= 4 checks` figure. Two faults in how they were measured, both
found by reading a rejected verifier instead of trusting the number:

- **A return annotation hid the function.** The model writes
  `def test_no_shortcut() -> None:`, and a pattern requiring `)` to be followed
  by `:` matches none of those. Four detectors were built on that pattern.
- **The re-run was in a helper.** A check that has to rebuild an image calls
  `_build(tag)` from a module-level function; searching the check's own body for
  a subprocess call finds nothing. The mutation belongs in the body — that is
  what makes it this check — but the re-run does not.

Re-measured with both fixed, over 81 packages from v13:

| | seeds | reported | actual | RST |
|---|---|---|---|---|
| >= 4 checks | 46% | 58-80% | **98%** | 100% |
| anti-shortcut check | 24% | 54-70% | **96%** | 100% |

So the metric that matters most against reward hacking was essentially at RST's
level and being reported as two thirds of it. It also means the preflight gate
was rejecting around 30% of each run over checks that were present — those tasks
had passed synthesis, consistency, an oracle repair and a coverage repair before
being thrown away by a regex.

## A measurement that does not count, and why it is recorded anyway

v18's accepted tasks re-measured at 62% in the usable band against the seed
corpus's 20.3%, and three of eight drifted from at most 2 of 4 at the gate to 5
of 5 on re-measurement. That jump is too large for sampling, and the timestamps
say what it actually was:

```
v18 synthesis started   15:58:23   gated with the solver as it was
agent self-check landed 16:00:06
v18 verification began  16:25:44   measured with the solver as it became
```

The gate ran against a solver that stopped after three turns without checking its
work; the verification ran against one told to read its artifacts back first. A
better solver solves more, so tasks move toward 1.0 — that is the solver
changing, not the task being easy.

**The gate and the verification have to use the same solver**, or the difference
between them is what gets measured. Both are aligned from v19 on. The number is
kept here because a plausible result from a broken comparison is worth more as a
warning than as a deletion.

## Still open
- `solve.sh` runs to a median of 156-180 lines against RST's round-1 median of
  67. Ours are more elaborate, which is not obviously better and has not been
  tested either way.
- Acceptance is 13-14%. Whether that is bad is unknown: RST reports a cost per
  accepted task and no yield, and the four questions in
  `docs/drafts-rst-authors-followup.md` include asking them for their blocked
  rate, which is what would calibrate it.
