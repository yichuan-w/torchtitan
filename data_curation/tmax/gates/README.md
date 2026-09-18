# The 8 gates — what each checks, and its source script

Every task that reaches `train` has passed all eight. Gates 1 and 4–8 are Python
against sandboxes; gates 2–3 are the LLM judge workflow (see
[`../workflows/LAUNCH.md`](../workflows/LAUNCH.md)).

| # | gate | establishes | source (audit harness) |
|---|---|---|---|
| 1 | empty-submission | the verifier does not pay out for nothing (untouched container must reward 0) | `tmax_gate*.py` |
| 2 | rubric audit, judge A (Opus 5) | instruction / env / verifier are mutually consistent | `*_workflow.js` |
| 3 | rubric audit, judge B (Fable 5), blind | judge A's CLEAN is not a single-model artifact | `*_workflow.js` |
| 4 | solve gate, 3× (competent agent) | a competent agent actually solves it (keep ≥1/3) | gate-2 / gold-rollout driver |
| 5 | solution capture + same-sandbox grade | the captured artifact is the thing that scored | `capture_oracle.py` |
| 6 | oracle gate, fresh container | the artifact alone reproduces the reward | `rebuild_gold.py`, `oracle_census.py` |
| 7 | independent re-gate, separate harness | pass 6 was not a harness artifact (350/351) | re-gate driver |
| 8 | mutation test | the verifier reads the answer, not just its shape | `c2a_mutate.py`, `test_mutate.py` |

> **Status:** the gate scripts are now included in this directory **as-is from
> the audit harness** (`swe-rebench` / `tmax` recovery tree): `gold_rollout.py`
> (gate 4), `rebuild_gold.py` + `oracle_census.py` (gate 6), `c2a_mutate.py` +
> `test_mutate.py` (gate 8), `c2b_gate.py` / `c2b_envgate.py` + `tmax_gate2.py.bak`
> (gate 1), `capture_oracle.py.bak` (gate 5), and `archive_audits.py` (round
> archiver). Two of them survive **only as `.bak`** — that is the only copy that
> exists. They still carry harness-internal paths and are not yet a one-command
> pipeline; cleaning them into clean, parameterized, runnable form (+ a smoke
> test) is the remaining work — see the checklist below.

## Shared-Daytona-key hazard (read before running gates 1, 4–8)

The runtime gates drive **Daytona** sandboxes with a key that is **shared across
workloads** — a read-only list showed ~9,300 sandboxes from other users
(~2,000 running, ~6,900 build-failed). Consequences the extraction must preserve:

- The key is supplied via **environment only** and never committed.
- **Never** stop, delete, or otherwise touch a sandbox you did not create; match
  on the create-time id you recorded, not on a name/age heuristic.
- Quota is contended — start sandboxes small (1 vCPU / 1 GB / 1 GB) and step up
  only on OOM; cap concurrency (a wide wave lost sandboxes to a 10-min idle TTL).
- A harness hazard: `test.sh`'s reward-fallback only writes 0 if no reward file
  exists — an agent-pre-written `1` survives a verifier crash. Grade with an
  empty `/logs/verifier`.

## Extraction / sanitization checklist (before this goes in a PR)

- [ ] Extract each gate's runnable core from the harness into `gates/`, with a
      single documented entrypoint and its inputs/outputs.
- [ ] Parameterize all paths (no absolute `/data/users/...`); Daytona key via env.
- [ ] **Scan every `*_workflow.js` for embedded task content or private id lists.**
      The judge *prompt* (rubric) ships; **task instructions/gold/answers and the
      private task-id set must not.** Replace any embedded id batch with a
      placeholder + a note on how to supply your own.
- [ ] Confirm no credentials, tokens, or internal hostnames anywhere.
- [ ] A smoke run on a tiny public/synthetic task set that reproduces the gate
      behaviour end to end.
