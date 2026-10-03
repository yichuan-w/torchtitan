# `andy/proposer-drafts-checker` — training-side readout

Read-only readout of the TMAX task evolve/breeding pipeline as it stands on the
training branch, written so the static per-task audit rubric (v12) can delegate
whatever this pipeline already handles.

## 0. Provenance

| item | value |
|---|---|
| branch found at | `https://github.com/yichuan-w/torchtitan` (remote `origin`), ref `andy/proposer-drafts-checker` |
| head sha | `ac7604b7284a1e6636ac6b52269ec14c70cb3e36` |
| co-train base | `origin/yichuan/qwen35-port-cotrain` @ `16dc0a8df15bbcea85efd1ab542e261e6b586317` |
| merge-base | `324dc4466b686139b59b8d4190bf68b43ad6d07d` |
| commits base..branch | 7 (branch is 29 behind base) |

```
ac7604b7 tmax: a probe contract naming more cases than scripts is repaired, not fatal
dcf9bf03 tmax: network probe at sandbox boot; tasks may use the network; artifacts as hardening
66cbe539 tmax: the rewrite budget is one measured training epoch, not a fixed 6 h
4284544e tmax: repair in turns, author then verifier, up to three iterations per rewrite
0b506025 tmax: blind-mode test asserts the scratch-checker sentence
eb05127a tmax: the author drafts a scratch checker; the blind verifier starts from the previous revision's
f87f7498 fsdp: shard norm and lm_head as separate groups (mixed grad dtype under chunked loss on torch 2.12)
```

Diff against merge-base touches exactly one training file (`torchtitan/distributed/fsdp.py`)
and ten TMAX files; the branch's whole subject is the proposer/verifier split
and its repair loop.

All `<path>:<line>` citations below are on the branch ref. Paths are relative to
`torchtitan/experiments/rl/examples/tmax/` unless written out in full.

---

## a. Architecture

### The four roles

There is no component literally named "proposer" in the code; the commit
messages use that word for the **author session**. The cast is:

| role | code | prompt (AGENTS.md installed into the package) | sees |
|---|---|---|---|
| **proposer / author** | `evolve_codex.evolve_agentic` — `evolution/evolve_codex.py:1891` | `evolution/agents/task_evolution.md` (installed at `evolution/evolve_codex.py:586`) | instruction, Dockerfile, `solution/solve.sh`, current `tests/`, **`traces/` = real rollout records**, `run/resources.json`, `run/pretest.json` |
| **draft / scratch checker** | the author's edits to `tests/`, discarded at `evolution/evolve_codex.py:1476` (`_restore_seed_tests`) | `evolution/agents/task_evolution.md:139-143` | — |
| **verifier agent (blind)** | `evolve_codex._blind_verifier` — `evolution/evolve_codex.py:1709` | `evolution/agents/verifier_author.md` (installed at `evolution/evolve_codex.py:1462`) | instruction, `environment/`, **previous revision's** `tests/`, `run/seed_size.json`, `run/resources.json`. **Not** the solution, **not** the author's draft, **not** traces |
| **checker / independent probe author** | `evolve_codex._independent_verifier` — `evolution/evolve_codex.py:1592` | `evolution/agents/independent_verifier_probes.md` | instruction + environment only; `tests/` replaced by a stub that always exits 2 (`evolution/evolve_codex.py:1606-1610`) |

Defaults: `VERIFIER_AUTHOR = os.environ.get("SWE_VERIFIER_AUTHOR", "blind")`
(`evolution/evolve_codex.py:943`) — the blind split is **on** by default.
`harder_uses_operators()` defaults to `0` (`evolution/synth_operators.py:25-30`),
so hardening is **student-guided from traces**, not from the fixed operator menu.

One caveat on "by default": this whole agentic cast runs only when the arm is
`codex`. `SWE_RETUNE_AGENT` defaults to `chat` — one gpt-5.6 call with a trimmed
trace in the prompt, no container, no blind split (`evolution/feedback_loop.py:715,
788-796`). The deployed loop sets `codex` explicitly
(`evolution/della/evolveloop_env.sh:51`, `evolution/RUNBOOK.md:246`,
`runbook/RUNBOOK-yichuan-B300.md:961, 1092`). `SWE_EVOLVE_SIMPLIFY` defaults to
`0` (`evolution/evolve_ondella.py:89`), so the easier direction is off unless
switched on; the deployed config keeps it off.

### Control flow of one evolve round

Trainer side (`rollouter.py`), one rollout group of k=16:

1. `TMaxRollouter._maybe_emit_evolution_signal` — `rollouter.py:1219`. A group
   with ≥2 scored siblings and (sparse mode) either all-fail or
   `solved/total >= evolution_harder_ratio` (default 1.0, i.e. k/k —
   `config_registry.py:183`) writes `runs/<run>/signals/<task>--g<group>.json`
   with `direction`, `solved`, `total` and the **paths** of the siblings'
   rollout records (`rollouter.py:1293-1314`). The transcript is referenced,
   never copied.
2. An all-fail group in which *no attempt took a turn* is not a signal but an
   `infra_quarantine` advisory — `rollouter.py:1264-1289`.
3. New on this branch: `_net_probe` (`rollouter.py:316`) runs inside the sandbox
   right after boot and before the agent's clock starts (`rollouter.py:1471`),
   and lands in the rollout record as `net_probe` (`rollouter.py:731`; field at
   `rollouter.py:280`, targets/budget at `rollouter.py:289-292`).

Loop side (`evolution/evolve_ondella.py`), asynchronous, on the data host:

4. `discover` / `choose` → one signal per task per round; `handle` —
   `evolution/evolve_ondella.py:487` — creates `tasks/<task>/rewrites/<stamp>--<job>/`,
   copies `r<rev>` into `package/`, hardlinks the signal's rollout records into
   `package/traces/attempt-NN.jsonl` (`:553-557`), and snapshots the row's pin
   hook + protected lists into `rewrite/pretest.json` (`:540-551`).
5. `feedback_loop.process_one` — `evolution/feedback_loop.py:681` — runs the arm
   (`SWE_RETUNE_AGENT`; `codex` = agentic).
   * `easier` → `ec.simplify_codex` (`evolution/feedback_loop.py:775`)
   * `harder` → `_evolve_retrying_the_filter` → `ec.evolve_agentic`
     (`evolution/feedback_loop.py:619-645`, `844`)
6. `evolve_agentic` (`evolution/evolve_codex.py:1891`):
   a. `_prepare_package` writes `run/seed_literals.json`, `run/seed_size.json`,
      `run/resources.json`, `run/pretest.json`, installs `AGENTS.md` +
      `sandbox` (`:572-589`).
   b. one author session; the author must finish with `./sandbox check`
      = `VERDICT: pass` or the rewrite is discarded (`_require_checked`, `:654`).
      `BLOCKED:`/`GIVE UP:` in `run/verdict.txt` raises `Blocked` (`:667-677`).
   c. blind mode: `_blind_verifier` (`:1709`) lays the package out again minus
      `solution/` (`_blind_layout`, `:1439`), puts the **previous revision's**
      `tests/` back over the author's draft (`_restore_seed_tests`, `:1476`),
      runs the verifier session, then `_verify_original_probes` (`:1565`) and
      `_independent_verifier` (`:1592`), then `_take_verifier` copies the new
      `tests/` tree into the author's package and **deletes
      `run/checks.jsonl`** so the author's own pass cannot count (`:1514-1518`).
   d. `_reconcile_blind` (`:1872`) runs `./sandbox check` in the author's
      package — the first time the hidden solution meets the blind verifier —
      and on failure `_repair_iterations` (`:1821`) alternates author-repair
      (`_resume_author_blind`, `:2086`, with the previous revision's checker
      back in `tests/`) and verifier-repair (`_blind_repair`, `:1765`) for up to
      `EVOLVE_REPAIR_ROUNDS` = 3 (`:959`), bounded by `rewrite_budget_sec()`
      = one measured training epoch (`:990-1017`).
7. `feedback_loop.revalidate` — `evolution/feedback_loop.py:320` — the caller's
   own validation, in a container the session cannot reach (see §c).
8. Up to 3 more repair+revalidate iterations inside `process_one`
   (`evolution/feedback_loop.py:926-1013`), same budget.
9. `evolve_ondella.fold` — `evolution/evolve_ondella.py:820` — rebuilds the row
   with `pack.to_row`, replaces the row **in place** (pool size fixed), renames
   `package/` → `r<N+1>/`, publishes `data/mix/history/v<N>--<stamp>.jsonl` and
   relinks `live.jsonl`; the trainer hot-reloads it (`TASK_EVOLUTION.md:60-73`).

Ledger outcome per signal: `handled | deferred | junk | superseded`
(`LAYOUT.md`, Ledger section). Rewrite status: `running | accepted | rejected |
blocked | failed | interrupted | kept` (`LAYOUT.md`, Rewrite section).

---

## b. Visibility — who sees traces, who may edit what

**The verifier agent does NOT see traces.** `_blind_layout`
(`evolution/evolve_codex.py:1439-1457`) copies the author's package with
`HIDDEN_FROM_VERIFIER` removed and, for `run/`, keeps only
`seed_size.json`, `resources.json`, `seed_literals.json`, `pretest.json`
(`:1447-1452`) — `traces/` and `solution/` never reach it. The independent
probe author sees even less (`:1605-1616`).

**The proposer/author DOES see traces**, as files, in the one rollout-record
format the trainer wrote: `traces/attempt-NN.jsonl`, hardlinked by the loop
(`evolution/evolve_ondella.py:553-557`; `TASK_EVOLUTION.md:74-78`). Its prompt
is built around them:

> "Choose one change from the student's actual attempts in `traces/`. Start by
> identifying the successful strategy and a task-relevant judgment it currently
> bypasses." — `evolution/agents/task_evolution.md` via
> `_STUDENT_HARDER_GUIDANCE`, `evolution/evolve_codex.py:1047-1049`

> "Before editing, compare two candidate changes in `run/hardening.md`, then
> implement only one. For each candidate, cite attempt filenames and concrete
> actions or observations" — `evolution/evolve_codex.py:1057-1059`

Editing rights:

| file | proposer/author | blind verifier agent | independent probe author |
|---|---|---|---|
| `instruction.md` | yes — "**`instruction.md`** is a fair public request from someone who wants the work done" (`agents/task_evolution.md:149`) | **no** — "Do not change other task files: the task is fixed, and a check that only passes because you changed the task is a check on nothing" (`agents/verifier_author.md:38-40`) | no |
| `solution/solve.sh` | yes (`agents/task_evolution.md:69-76`) | never even sees it (`agents/verifier_author.md:18`) | no |
| `tests/…` | **yes, as scratch** — "In blind-verifier mode, `tests/` is your scratch checker: edit it so `./sandbox check` exercises the new requirement, and know that it is discarded" (`agents/task_evolution.md:139-143`) | **yes — this is its output** (`agents/verifier_author.md:34`) | no — stub only |
| `environment/Dockerfile`, fixtures | yes (`_HARDER_JOB_BLIND` step 3, `evolution/evolve_codex.py:1239`) | no | no |
| `run/verifier-probes/` | no | yes (`agents/verifier_author.md:206-240`) | yes, and **only** that (`agents/independent_verifier_probes.md:100`) |
| `sandbox` tool | forbidden — "Editing `sandbox`, or shaping the task around it, costs you the whole session and gains nothing" (`agents/task_evolution.md:251`) | — | — |

The blind rationale is stated in the prompt itself:

> "**You are not shown the reference solution, and that is the point.** … Of
> eight hardened tasks reviewed that a policy failed 16 of 16 times, five failed
> on exactly this, three with all the work done." — `agents/verifier_author.md:11-15`

And the new handoff sentence the author is given:

> "The verifier that ships with this rung is written afterwards by a second
> session that is shown the instruction, the environment and the previous
> revision's verifier -- not your draft, not your solution … So the seed's
> checks are not a floor you must pass: a requirement you change is checked as
> you state it. And a name only your solution knows will not be checked: state
> it in the instruction, or make the result checkable by value."
> — `evolution/evolve_codex.py:1244-1252`

Two consequences the branch calls out explicitly (commit `eb05127a`): a wrong
seed verifier no longer propagates as a requirement the harder task must still
satisfy, and a rung may now *change* what the seed asserted instead of only
adding to it.

---

## c. Oracle / gold and empty run

### In the evolve loop (per rewrite, before admission)

Two independent layers, both binary reward from the real grading contract:

1. **The agent's own `./sandbox check`** — `evolution/agent_sandbox.py:464`
   (`cmd_check`). Order: rebuild → **null probe** (grade the untouched
   workspace; `null_reward >= 1.0` ⇒ `VERDICT: fail stage=null_probe`,
   `:495-513`) → **oracle** (`solve.sh` then grade; `oracle_ok = reward >= 1.0
   and solve_exit == 0`, `:518-519`) → names audit (advice) → step-size rule
   (gate). Result appended to `run/checks.jsonl`; `_require_checked`
   (`evolution/evolve_codex.py:654`) discards any rewrite without a pass.

2. **The caller's revalidation**, which the session cannot reach —
   `evolution/feedback_loop.revalidate`, `evolution/feedback_loop.py:320`:
   * oracle: `daytona_probe(work, …)` → `daytona_revalidate.probe` with
     `shortcut=None`; verdict `ok = reward >= 1.0 and code == 0 and
     execution["submitted"]` — `evolution/daytona_revalidate.py:352`.
   * **empty/null run**: `daytona_probe(work, shortcut=":", …)` —
     `evolution/feedback_loop.py:468-473`. If it did not complete →
     `stage="null_check"` (`:474-481`); if it **passed** →
     `stage="null_pass"`, `"verifier passes on the untouched workspace"`
     (`:482-487`). Docker path: same two, `sl.oracle_check` /
     `sl.shortcut_check(..., ":")` at `:505-518`.
   * A package with no `solution/solve.sh` is rejected before any of this:
     `{"ok": False, "stage": "no_solution", "why": "package ships no
     solution/solve.sh"}` — `evolution/daytona_revalidate.py:268-273`.

**The hole to know about: the instruction-only fast path.** When the only
changed file is `instruction.md` and the job is not a simplify / spec-repair /
calibration, `revalidate` **skips both the oracle and the null probe** and
returns `{"ok": True, "fast_path": "instruction_only"}` after only a static
leak/dark-path diff — `evolution/feedback_loop.py:354-372`. `verdicts_of`
records `oracle: "skipped"` for it (`:529`). So a re-worded instruction is
admitted without ever running gold or empty again. The rationale is in the
docstring at `:344-353`.

Also note the non-docker host case: without docker **and** without Daytona
credentials, structural retunes are rejected with `stage="no_docker"`
(`:412-418`) — they are never silently admitted.

### For seeds

Seeds enter from the corpus, not from this loop. Oracle validation of the seed
corpus is `evolution/oracle_validate_seeds.py` (build → upload solution+tests →
run solve.sh → run verifier; "A task passes when test.sh exits 0", header lines
3-4) and the four-gate synthesis loop `evolution/synth_loop.py:8-19`
(build / oracle reward 1 / dark-check+leak audit / demonstrated-shortcut proof).
`evolution/build_clean_dataset.py:46-56` additionally records tasks whose
verdict *flipped* across repeats as `flaky`. The on-line loop trusts that: the
first signal for a task copies the corpus package into `r0` unchecked
(`materialize_r0`, `evolution/evolve_ondella.py:416`).

---

## d. Pins — who creates them, how they propagate

Three distinct mechanisms; only one of them is the author's.

**1. `protected_paths` / `protected_cmds` (digest pins).**
Runtime contract in `integrity_baseline.py:7-55`: the harness digests every
entry in the sandbox after setup and **before the agent's first action**, keeps
the digests harness-side, re-digests before the verifier, and "any difference
scores 0" (`:14-17`). Capture at the rollouter seam
(`capture_integrity_baseline`, `integrity_baseline.py:279`); comparison in
`grading.py:303-323`. A row with protected entries and **no** baseline raises
`IntegrityHarnessError` — the episode is void, never scored
(`integrity_baseline.py:302-309`).

Who writes them:
* **The proposer is expected to, and can.** "If the public instruction requires
  files or command outputs to remain unchanged, list them in
  `tests/protected_paths.json` as `{"paths": [...], "cmds": [...]}`; the harness
  digests them before and after the episode and any change scores 0."
  — `agents/task_evolution.md:37-39`.
* Resolution order is **package overrides inherited**:
  `pack_to_dataset.effective_protected` — `evolution/pack_to_dataset.py:171-181`
  — "the package's own `tests/protected_paths.json` when it ships one (the
  authoring agent's lists override), else the lists the variant INHERITS from
  the row it descends from". Validation of the file's shape:
  `package_protected`, `:199-222`. Row fields are built in one place,
  `integrity_baseline.tmax_protected_fields` (`integrity_baseline.py:262-276`),
  so a folded row and a prepared row cannot disagree.
* Propagation through breeding: loop snapshots the *row's* lists into
  `rewrite/pretest.json` (`evolution/evolve_ondella.py:540-551`); the author's
  copy lands at `package/run/pretest.json` (`evolution/evolve_codex.py:549-569`);
  the loop's probe grades with the snapshot the session cannot reach
  (`evolution/feedback_loop.py:749-760`, `--pretest-file`); the fold passes the
  replaced row's lists to `pack.to_row` (`evolution/evolve_ondella.py:873-878`),
  where the package file may still override.

**2. `pre_test_sh` + `pretest_env_identity` (the reaudit pin hook).**
Not author-generated at all. It rides on the data row
(`metadata.tmax.pre_test_sh`), is produced by dataset prep
(`prepare_tmax_data._pretest_tmax_fields`, `prepare_tmax_data.py:131-158`;
reaudit split: `prepare_tmax_reaudit_data.py:29-37`), and is run by
`grading.py:325-360` as root before `test.sh`, nonzero rc ⇒ reward 0 without
running the verifier. **Drift guard:** if the episode's environment identity
differs from the stamped one, the check is **SKIPPED** and one line is logged
(`grading.py:352-357`); the corpus-wide version of that failure is caught at
prep time by `prepare_tmax_data.selfcheck_env_identities` (`:370-400`).
At the fold the hook comes back from the row it replaces, and the row builder
re-derives the new package's identity — "a rewrite that kept the environment
keeps the check, one that rebuilt it is skipped by grading"
(`evolution/evolve_ondella.py:831-835`, `LAYOUT.md` Pretest section). So **a
rewrite that touches the Dockerfile silently loses its pre_test pin**.
`tests/reference_pins.sha256` is shipped as a fixture for those rows
(`prepare_tmax_reaudit_data.py:19`) but nothing on the training side regenerates
it for a bred child.

**3. Artifact/fetch pins inside the task.** New on this branch, and the author's
job:

> "**The network is available and may be part of a task.** Pin anything fetched
> (URL plus checksum or version) so the reference solution is reproducible, and
> make a failed fetch fail loudly: non-zero exit, the error on stderr, no silent
> fallback. No credentials, nothing that only works through a proxy."
> — `agents/task_evolution.md:324-327` (added in `dcf9bf03`)

This is a prompt instruction only — no gate checks it. The observability that
was added instead is the boot-time network probe (`rollouter.py:316`,
called at `:1471`, recorded as `net_probe` at `:731` and as one
`[tmax_net_probe]` log line per rollout at `:1472-1486`).

---

## e. "Verifier too weak" and similar reports

**There is no `verifier_too_weak` report channel, no severity ladder, and no
repair path keyed on such a report.** What exists:

* Machine-decided weakness, and it **rejects, does not repair**:
  `stage="null_pass"` — "verifier passes on the untouched workspace"
  (`evolution/feedback_loop.py:482-487`), and its in-session twin
  `stage=null_probe` with the message "The verifier passes on the untouched
  workspace: it pays for nothing." (`evolution/agent_sandbox.py:508-512`).
* Semantic weakness, and this one **does repair**: the independent controls.
  If a `wrong-N.sh` is graded *pass* (or `correct.sh` fails), `verify_probes`
  collects it as a `SemanticProbeMisses` (`evolution/verifier_probes.py:199-208,
  297-300`); `_independent_verifier` then resumes the verifier session once
  with `run/independent-failures.jsonl` and re-replays, including the
  session's *original* pre-repair controls as a regression check
  (`evolution/evolve_codex.py:1663-1704`). One repair attempt only
  (`for attempt in range(2 if allow_repair else 1)`, `:1655`), and after a
  `_blind_repair` the controls are replayed with `allow_repair=False` (`:1794`).
  A second miss raises and the whole rewrite is discarded.
* Contract mismatch (cases declared ≠ scripts written) is the branch's newest
  fix: was a fatal `FileNotFoundError` ("8 of the first 17 failures on hip"),
  is now `SemanticProbeContract` and one resume of the same session
  (`evolution/verifier_probes.py:29-33, 61-67`;
  `evolution/evolve_codex.py:1543-1589`).
* Agent-declared weakness is an **exit, not a report**: `GIVE UP:` /
  `BLOCKED:` in `run/verdict.txt` raises `Blocked`
  (`evolution/evolve_codex.py:667-677`) and `process_one` records
  `status="kept"` — "neither a success nor a failure of the pipeline, and left
  out of acceptance rates" (`LAYOUT.md`, Rewrite section;
  `evolution/feedback_loop.py:780, 855`). The task returns to training
  **unchanged**. The prompt makes this the preferred outcome over weakening:
  "For harder jobs, if your only route to `VERDICT: pass` removes a check for a
  required public behavior, take the give-up instead"
  (`agents/task_evolution.md:301-303`).
* The one spec-side report channel: a simplify session may report a seed defect
  as `BLOCKED: repair_required:`, which becomes `status="kept",
  stage="repair_required"` (`evolution/feedback_loop.py:779-783`) and is routed
  to the `repair_spec` job (`_SPEC_REPAIR_JOB`,
  `evolution/evolve_codex.py:1327-1357`) — which requires a
  `run/repair.json` with nonempty `diagnosis / evidence / change /
  retained_skill / validation` before it will edit anything
  (`evolution/evolve_codex.py:2054-2059`).

So, to the user's question 4 ("this bug won't cause the task to be thrown away?
only reported as verifier too weak?"): **on this branch a demonstrated
verifier weakness throws the rewrite away** (null_pass, second probe miss) or
returns the task unchanged (`kept`). Nothing carries a weakness verdict
forward as metadata on the row.

The rejected statuses that *are* in the ledger, none of them a weakness ladder:
`build`, `oracle`, `daytona_oracle`, `null_pass`, `null_check`, `step_size`,
`audit`, `empty`, `no_docker`, `daytona_error`, `simplify_scope`,
`simplify_context`, `not_in_mix`, `fold`, `ungraded`, `error`
(`evolution/feedback_loop.py:362-519, 729-735, 899-904, 1016-1025`;
`evolution/evolve_ondella.py:649-653, 858-863`).

---

## f. Timing / fragility

**Wall-clock-based scoring:** the only detector is the offline static scan
`evolution/audit_time_bombs.py`. Its `clock` category is
`CLOCK = {"shell_date": …, "python": datetime.now|utcnow|today|time.time|…,
"year_literal": 202[5-9]|203\d}` (`:122-129`), applied per-file at `:236-241`,
and summarised specifically as `clock_in_grade` at `:390-393`. It reads
Dockerfiles, solutions, tests **and** `tmax["pre_test_sh"]` (`:340-341`).
Nothing in the online evolve loop or in `./sandbox check` runs it, and no gate
consumes it.

**Flaky verifiers:** `evolution/build_clean_dataset.py:46-56, 105-114` marks a
seed task `flaky` when its oracle verdict flipped across repeats, and records
`pass_with_flipped_verdict` in the dataset. That is corpus-build time only.
Inside the loop, every probe is run **once**; the only retry is infra-shaped
(`_INFRA_RE`, `evolution/feedback_loop.py:82-85, 158-192`) and a Daytona
BadGateway retry in the probe replay (`evolution/verifier_probes.py:154-165,
231-280`) — both explicitly *not* re-runs for flakiness, and the replay refuses
to recover if the package or controls changed in between (`:261-272`).

**Verifier fragility, indirectly:** the blind split is the structural answer
(`agents/verifier_author.md:11-16`), and the prompts carry a long list of
fragility rules the verifier author must follow — accept permitted
representations, compare numeric values not stringified forms, derive tolerance
from the public requirement, use byte equality only where the task demands it
(`agents/verifier_author.md:88-153`). `./sandbox check` prints a **names audit**
of literals the verifier depends on that nothing an agent can read states
(`evolution/agent_sandbox.py:438-446, 524`), and the loop computes the same
thing (`new_dark_literals`, `evolution/feedback_loop.py:299-313`) — but both are
**advice, not a gate**, with the precision measurement recorded in the code:
"measured on wd-20260904a over 464 rewrites, the names audit flagged 130
literals without its seed baseline and none with it, and every one of the 130
was a false positive … the paths audit rejected nothing across 621 signals. A
gate with no measured precision has no business discarding a session"
(`evolution/feedback_loop.py:447-455`).

**Trace-driven hardening loop:** yes, and it is the default path.
`_STUDENT_HARDER_GUIDANCE` (`evolution/evolve_codex.py:1047-1119`) is exactly
"insights from traces → what to harden": identify the successful strategy, name
the assumption it relied on without checking, remove that assumption, and
justify it with cited attempt filenames in `run/hardening.md`. The branch's
`dcf9bf03` adds the generalisation of what a hardening *is*:

> "What every hardening removes is an assumption the successful strategy relied
> on without checking: that the only CSV in the directory is the input, that the
> first match is the target, that a re-run reproduces what was recorded, that
> two records of different types are never equal. Adding artifacts to the
> workspace -- fixtures, decoy files, a second candidate, a stale copy, a log
> that contradicts a config -- is a legitimate way to remove such an assumption
> … it counts only when the added material forces a decision judged by content"
> — `evolution/evolve_codex.py:1090-1098`

Closing the measurement loop is `evolution/measure_difficulty.py:8-27` (re-solve the
folded revision pass@k and compare to the signal's before-rate), and its own
header is careful that neither metric "establishes task validity or stable
improvement without independent evidence" (`:18-27`). Student feedback is fed
back into the next author session as `run/student_feedback.json`
(`evolution/evolve_codex.py:1990-2007`; assembled at
`evolution/evolve_ondella.py:611-628`).

---

## g. Training-side filters that make a static audit finding redundant

| filter | where | what it removes |
|---|---|---|
| null / empty-submission probe (loop) | `evolution/feedback_loop.py:468-487`; `evolution/daytona_revalidate.py:379-392` | any rewrite whose verifier passes on the untouched workspace |
| null probe (author's own tool) | `evolution/agent_sandbox.py:491-513` | same, before the session can finish |
| oracle gate (loop) | `evolution/feedback_loop.py:409-445`; `evolution/daytona_revalidate.py:349-378` | rewrite whose reference solution does not score 1 in the row's own box |
| no-solution gate | `evolution/daytona_revalidate.py:268-273` | package shipping no `solution/solve.sh` |
| independent semantic controls | `evolution/evolve_codex.py:1592`; `evolution/verifier_probes.py:40` | verifier that accepts a demonstrated wrong deliverable or rejects a demonstrated legal one (one repair, then discard) |
| blind-verifier construction | `evolution/evolve_codex.py:943, 1709` | hidden-vocabulary verifiers by construction (the "5 of 8 0/16 tasks" class) |
| step-size rule | `evolution/agent_sandbox.py:449-461, 530`; `evolution/feedback_loop.py:399-408, 457-467`; `task_size.violations` | rewrites more than one rung above the seed (solution line growth, ≤5 added assertions) |
| integrity baseline | `integrity_baseline.py:279-327`; `grading.py:303-323` | episodes that mutated a protected path or a protected command's output → reward 0 (and void, not 0, on harness failure) |
| pre_test hook | `grading.py:325-360` | episodes that broke a pinned reference → reward 0 without running the verifier |
| environment drift guard | `grading.py:352-357`; `prepare_tmax_data.py:370-400` | skips the pin check when the environment moved; refuses the whole corpus prep if *no* stamped row matches |
| infra-quarantine advisory | `rollouter.py:1264-1289` | flags tasks whose every attempt died at zero turns — no evolve signal is drawn from them |
| zero-std handling | `config_registry.py:556`, `torchtitan/experiments/rl/components/training_sample_builder.py:175`, `data.py:306-321` | `SWE_DROP_ZERO_STD=1` drops the group; `skip_ids_path` removes a task from sampling entirely |
| solve-rate-driven re-tune | `rollouter.py:1219-1262` (`evolution_harder_ratio`) | not a drop: 0/k → simplify, k/k → harden, pool size fixed |
| simplify scope guard | `evolution/feedback_loop.py:876-904` | `add_scaffold` touching anything but `instruction.md`; `provide_initial_state` touching verifier files |
| lineage tamper guard | `evolution/evolve_ondella.py:637-653` | rewrite that modified the reference copy of the parent revision |
| author-check enforcement | `evolution/evolve_codex.py:654-664` | rewrite that never got a passing `./sandbox check` |
| corpus-build gates (offline) | `evolution/synth_loop.py:8-19`; `evolution/oracle_validate_seeds.py`; `evolution/build_clean_dataset.py:46-56` | seeds that do not build, do not pass their oracle, have dark checks or leaks, have a demonstrated shortcut, or flip verdicts |

---

## h. Where a v12 static audit row could plug in

The v12 `initial_row` carries, among others: `oracle_reachable`,
`expectation_movable`, `expectation_revealed`, `core_clause_unenforced`,
`secondary_clauses_unenforced`, `overspecific_check`, `unstable_reward`,
`untouched_image_passes`, `trivial`, `weak_verifier_exploit`, `value_derivable`,
`env_mismatch`, `ambiguity`, `reject_shape`, `verifier_fix`, `tier`, `verdict`.

Concrete consumers that exist today:

1. **`tests/protected_paths.json` in the package** — the *only* per-task audit
   artefact the pipeline already reads back and honours, and it overrides
   inherited lists (`evolution/pack_to_dataset.py:171-181`). An audit row whose
   remedy is "pin this file / pin this command's output" can be shipped as this
   file and it will be picked up by the loop's probe, the agent's sandbox tool
   and the fold, unchanged.
2. **The reaudit parquet columns** `pre_test_sh`, `pre_test_env_identity`,
   `protected_paths`, `protected_cmds` — consumed by
   `prepare_tmax_reaudit_data.py:29-37` and `evolution/build_mix_v2.py:176-190`.
   Same remedy, shipped at dataset level instead of package level.
3. **`verifier_fix`** has no consumer. The nearest thing is the `repair_spec`
   job, but it is reachable only from a simplify session's own
   `BLOCKED: repair_required:` report (`evolution/feedback_loop.py:779-783`),
   not from an external row, and it deliberately refuses to trust a claim it
   cannot demonstrate ("it is a claim to check, not an established fact",
   `evolution/evolve_codex.py:1327-1330`). Feeding a static `verifier_fix` in
   would need a new entry point; the file it would most naturally write is
   `run/failure.txt` before a `repair_spec` session.
4. **`tier` / `verdict` as a drop list** — `data.py:306-321`
   (`skip_ids_path` / `SWE_SKIP_PROMPTS`) takes a flat id list and never samples
   those rows again; `evolution/drop_from_mix.py` removes a row from the live
   mix with a mandatory `--why` recorded beside it. Both are the existing,
   already-plumbed sink for "this task should not train".
5. **`unstable_reward` with a clock cause** — `evolution/audit_time_bombs.py`
   already produces the same finding statically over the whole corpus
   (`clock_in_grade`, `:390-393`); a static audit row adds nothing new unless it
   is an *executed* demonstration, which nothing in the loop is asked for.

### Table: audit concern → is it handled on the training side?

| audit concern (v12 field) | handled by training/evolve? | how, where |
|---|---|---|
| `untouched_image_passes` (verifier green on untouched workspace) | **handled** | null probe both sides: `evolution/agent_sandbox.py:491-513`, `evolution/feedback_loop.py:468-487`. Rejects. |
| `oracle_reachable` (gold run scores 1) | **handled for structural rewrites; partial for instruction-only** | `evolution/daytona_revalidate.py:349-378`; **skipped** on the instruction-only fast path, `evolution/feedback_loop.py:354-372` |
| task has no oracle at all | **handled** | `evolution/daytona_revalidate.py:268-273` (`stage="no_solution"`) |
| `trivial` / `weak_verifier_exploit` (a cheap command satisfies the grader) | **partially** | only what the independent `wrong-N.sh` controls happen to cover (`evolution/verifier_probes.py:40`); the generic detector `evolution/scan_degenerate_graders.py` is offline and its own header says the null probe "is too weak" (`:5`) |
| `core_clause_unenforced` (instruction asks X, verifier never checks X) | **partially** | the blind author is told to map every retained/added requirement to a check (`agents/verifier_author.md:49-94`), and `run/verifier-changes.md` records the mapping — but nothing verifies the mapping is complete. No gate. |
| `overspecific_check` (verifier requires a name/format the task leaves open) | **partially** | blind construction removes the commonest cause (`agents/verifier_author.md:11-16, 49-56`); the names audit measures it but is **advice only**, precision measured at 0/130 (`evolution/feedback_loop.py:447-455`); the `correct.sh` control must pass, which catches the case the probe author happened to exercise |
| `expectation_movable` (solver can rewrite the expected value / fixture) | **partially** | prompt rules forbid it ("Never use solver-writable replacement inputs as the authority for correctness", `agents/verifier_author.md:88-91`) and the replay controls include an input-replacement control (`agents/verifier_author.md:181-185`); enforcement only where `protected_paths` pins the fixture (`integrity_baseline.py:14-17`) |
| `expectation_revealed` (instruction leaks the verifier) | **handled** | static leak diff on every instruction edit, `evolution/feedback_loop.py:363-371`; prompt rule `agents/task_evolution.md:306-310`; `evolution/synth_loop.py:13-16` gate 3 at corpus build |
| dark check (verifier needs a path nothing reveals) | **partially** | `new_dark_paths` + container probe `--require-path` (`evolution/feedback_loop.py:265-284`, `evolution/daytona_revalidate.py:302-315`), but recorded as advice, not a rejection (`evolution/feedback_loop.py:446-456`); it **is** a rejection on the instruction-only fast path (`:363-371`) |
| `unstable_reward` — clock / date dependence | **not handled online** | static offline scan only, `evolution/audit_time_bombs.py:122-129, 390-393`; no gate consumes it |
| `unstable_reward` — flaky verifier | **not handled online** | one probe run per rewrite; only corpus-build repeats mark `flaky`, `evolution/build_clean_dataset.py:46-56` |
| `unstable_reward` — network dependence of the verifier | **not handled; now observable** | `scan_degenerate_graders.py` `network` pattern is offline; the new boot probe records reachability per rollout (`rollouter.py:316, 731, 1471`) but nothing gates on it |
| `env_mismatch` / task never boots | **handled** | `stage="boot"` (`evolution/agent_sandbox.py:483-488`), `stage="build"` (`evolution/feedback_loop.py:501-504`), infra-quarantine advisory (`rollouter.py:1264-1289`) |
| resource starvation read as difficulty | **handled** | `starved()` (`evolution/daytona_revalidate.py:203`, `evolution/agent_sandbox.py:414-427`) and provisioning from a measurement, never below the seed (`evolution/feedback_loop.py:546-593`) |
| pins: protected files / command outputs | **handled, and the proposer can author them** | `agents/task_evolution.md:37-39` → `evolution/pack_to_dataset.py:171-181` → `integrity_baseline.py:279-327` → `grading.py:303-323` |
| pins: artifact sha / fetched URL | **partially** | prompt instruction only (`agents/task_evolution.md:324-327`); no gate, and `tests/reference_pins.sha256` is not regenerated for a bred child |
| pins: `pre_test_sh` survives breeding | **partially / a known hole** | carried from the replaced row at the fold (`evolution/evolve_ondella.py:831-835`), but **silently skipped** when the rewrite rebuilt the environment (`grading.py:352-357`) |
| "verifier too weak" as a report with a severity ladder | **not handled** | no such channel; weakness either rejects the rewrite (`null_pass`, second probe miss) or is an agent give-up recorded as `kept` (`LAYOUT.md`, Rewrite section) |
| `verifier_fix` (a proposed patch to the verifier) | **not handled** | no external entry point; `repair_spec` is reachable only from a simplify session's own report (`evolution/feedback_loop.py:779-783`) |
| `reject_shape` / negative-control design | **handled inside the loop's own authoring** | `run/verifier-probes/contract.json` + `wrong-N.sh`, replayed in fresh sandboxes by the caller (`agents/verifier_author.md:206-240`, `evolution/verifier_probes.py:40-303`). A static audit's reject shape duplicates this for bred tasks; it is still the only source for *seed* tasks that never enter the loop. |
| `ambiguity` (spec cannot decide) | **handled as an exit** | `BLOCKED: <what, precisely>` from either agent (`agents/verifier_author.md:38-45`, `agents/independent_verifier_probes.md:104-106`) → `Blocked` → `status="kept"` |
| `value_derivable` (answer computable without the work) | **partially** | covered only if a `wrong-N.sh` control demonstrates it; the shortcut-claim machinery that used to do this (`evolution/verify_shortcuts.py`, `evolution/synth_loop.py:17-19`) is corpus-build only. The loop deliberately dropped its LLM-guessed hackability gate — 148 rewrites rejected in a week, 100 of them wrongly (`evolution/feedback_loop.py:334-342`). |
| difficulty actually moved | **partially** | `evolution/measure_difficulty.py`, run out of band, and its own header disclaims that either metric establishes validity (`:18-27`) |
