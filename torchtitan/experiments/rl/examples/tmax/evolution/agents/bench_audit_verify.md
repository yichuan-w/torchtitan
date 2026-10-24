# EvolveBench audit: check one auditor's findings

An auditor has read a rewritten task and reported findings in
`<task>/<V>/verdict.json` (the rubric it used is `INSTRUCTIONS.md`). Your job
is to try to refute each finding of class LEAKY, UNFAIR, BROKEN or
UNSTATED-ENVIRONMENT-TRAP. Assume the auditor may be wrong.

For each such finding, read the file and line it names, the instruction,
the environment and the reference solution, and decide:

- **confirmed**: the evidence is there and it does what the finding says: a
  plausible solution that follows the instruction fails (or a solution that
  skips the work passes), and the instruction does not cover it.
- **partly**: the defect is there but narrower than claimed (only an
  unusual solution trips it, or the instruction half-states it).
- **refuted**: the instruction states it, the check does not do what the
  finding claims, the defect was already in the seed (`original/`), or the
  "plausible solution" is not one.

Write `<task>/<V>/verify.json`, one entry per finding id:

```json
{"f1": "confirmed", "f2": "refuted"}
```

and `<task>/<V>/verify_notes.md` with one sentence of evidence per
decision. You do not know which rewriter produced the variant. Do not run
anything that needs network access, and do not change `verdict.json`.
