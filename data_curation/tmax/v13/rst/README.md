# v13-rst — the v13 seed audit, adapted to the RST corpus

`../v13_prompt.md` is frozen (sha `96fbc03b…`) and is still what judges TMAX. This directory is a separate variant for
RST (Harbor-format tasks). The rules A1–A7, B1 and B3 mean what they mean in v13; `DIFF_v13_to_v13rst.patch` is the
exact text difference.

## Why RST needs its own prompt

| | TMAX | RST |
|---|---|---|
| what the image bakes in | `setup.sh` | `environment/Dockerfile` plus the build-context files its COPY/ADD lines bring in |
| the verifier | `tests/test.sh` | `tests/test_state.py` (pytest); `tests/test.sh` is only a harness bootstrap that installs tools and runs it |
| grade-time network | rare, a per-task defect (B3) | **174 of 181 staged tasks**: `apt-get`, the uv installer from astral.sh, PyPI |
| reference solution | often absent | `solution/solve.sh` on every task; the oracle/empty runtime gate has already been run |
| security label | `domain` column | `security_union` column + an instruction screen (below) |

## The bootstrap: kept as a finding, in one place, on every row

The owner's decision: the bootstrap's grade-time fetches stay in the results. An earlier smoke reported them under B3
on one row of three, which is worse than always or never. So:

- new field **`bootstrap_fetch`** (Step 4b), written on every task: `tests/test.sh:<from>-<to>` + each fetch as
  `<command> -> <host>: <what>`. Null only when the bootstrap reaches no host but loopback (7 of 181).
- it has **no tier or verdict effect** and sits in no cross-field rule. B3 keeps meaning "this task's own verifier is
  unstable". Treating it as B3 would flag 96 % of the corpus for a property of the harness.
- it is cross-checked: `stage_rst.py` extracts the same facts mechanically into `sidecar/<id>.json`, and lints
  LR1–LR4 compare the judge's field with them. `score_run_rst.py` prints the tier **both ways** — as written, and
  strict (a fetching bootstrap counts as held back) — so the policy can be flipped without re-judging.
- the one bootstrap matter that does block is a new B1 shape: a state the *instruction* asks for that stops the
  bootstrap before pytest starts (outbound traffic blocked, apt sources or a proxy re-pointed, `curl` or the system
  Python removed), so that every obedient submission scores 0.

## What else differs, and where each difference is handled

| finding | handled in |
|---|---|
| **RST does carry a security label.** `security_union` (all 37,484 tasks) marks 16 of the 214 requested ids; the earlier staging said "no labelling found" and one of them went to the third-party API in the first smoke. The tag union is also known to miss about 14 % of security-shaped tasks, so an instruction screen (Opus, labels only) was run over the remaining 196: **15 more**. 33 excluded, 181 staged. | `stage_rst.py` (refuses to stage without `security_screen.tsv`; excludes anything the screen did not cover) |
| 43 of the 44 ids "not found" are under `tasks_nonEasy/` (the first stager looked under `tasks/`, `tasks_hard/`). | `stage_rst.py` |
| Build-context files reach the image only through COPY/ADD (30 tasks); 25 tasks ship context files that are never copied and are therefore not in the image. | stager appends the copied files to the staged `setup.sh`, each under a header naming the COPY line; never-copied files, `solution/`, the generator's metadata JSONs and compose files are not staged. Prompt fact 1. |
| The bootstrap is not one text: about 70 normalised shapes. Besides the usual `apt + uv + uvx`, there are `-p 3.13 -w` (59), `pip install` + the image's Python (7), a virtualenv the image ships, and `python3 /tests/test_state.py` with no pytest. 44 run under `set -e`; 12 have no fallback trap. | prompt fact 11 names the variants; the sidecar records which one a task has |
| Reward is 1 exactly when the test run exits 0: a skipped or xfailed test passes; an exception escaping a test, a fixture or the module import fails. Under `uvx` the verifier runs in uv's own interpreter with only the `--with` packages. | glossary "assertion", fact 11 (a)(b), one B1 shape (import of the agent's module), one A6 shape (tests that skip when the artefact is missing) |
| cwd at grade time is the Dockerfile's last `WORKDIR`, where the task's files live, so a cwd-relative path is not the defect it is in TMAX. | fact 4; the B1 cwd clause narrowed |
| The harness deletes the reward file and plants a sentinel before the bootstrap, so the bootstrap's "write 0 if missing" trap and a pre-written reward are not findings. `reward.json` is never read. | fact 4 |
| 1 vCPU / 2 GiB is what `task.toml` declares on every staged task; verifier timeout 600–1,800 s includes the installs. | fact 9 |
| Bug-injection tasks: the Dockerfile commits a good file, then breaks it without committing, so `git checkout` returns the answer. 30 staged Dockerfiles make a git commit and then change a file without committing. | one marked A3 example |
| The oracle/empty gate already ran: 168 pass, 7 `oracle_zero`, 1 `empty_one`, 5 not in the gate output. | sidecar `gate`; the scorer crosses tier with it. The judge is not shown it. |
| Two harness-compatibility classes of the RST campaign: tmux older than 2.3 (4 staged tasks), alpine exec hang (1). | sidecar `harness_compat`; not the judge's business |
| The v12 candidate extractor only looks under `/app /home /opt …`; RST workdirs include `/repo`, `/massdns`, `/plume`. | stager adds the task's own WORKDIR root to the path pattern |

Not handled, on purpose: an upstream fix reachable over the open network for a repository cloned at build time
(cannot be judged statically without network); the RST campaign's static flags (`L1_test_literal_in_dockerfile` 61,
`S3_pipeline_leftover_json` 72, …) are carried in the sidecar for cross-tabs and are not shown to the judge.

## Review and smoke (2026-09-20)

**Independent review before any run** (`REVIEW_rst_prompt.md`, Opus, read the staged files and the training code):
1 blocker, 5 major, 7 minor, all applied. The blocker: the first draft said an image's `CMD` is started; the rollouter
starts only an `ENTRYPOINT` (`prepare_rts_data.py:_entrypoint_command` returns `None` without one, checked at
`2c2f5b7e`), so a CMD-only service never runs in the training sandbox. Majors: a `-w '<name> @ file:///<path>'` spec
installs in-image, agent-writable code and is rollout material, not `grade_time_generated`; an A6 sentence
contradicted the clause added two lines above it; the inherited lint L7 misfired on the new bootstrap-anchored B1; LR4
missed quoted `--with` specs and the `-p` pin; the "import of the agent's module" B1 shape was a possibility, not a
predicate, and is now the decidable case only.

**Smoke**: 12 tasks, one per bootstrap variant or corpus feature (`../tb21_top10_rst/smoke_ids.tsv`), GLM-5.3 at low
effort, 12/12 rows on the first attempt, 0 schema-invalid, median 188 s and 13.5 k output tokens per task.

| check | result |
|---|---|
| bootstrap record written where the bootstrap fetches | **10 of 10**, same form, correct span, packages and `-p` pin (the first smoke, under plain v13: 1 of 3) |
| record null where the bootstrap does not fetch | 2 of 2 |
| a bootstrap fetch reported under B3, or a leak anchored in harness code (LR3) | 0 |
| LR1-LR5 | none fired |
| tiers | SEED 3, FLAGGED 9 (B1 on 9, A3 on 1); strict reading (a fetching bootstrap blocks): SEED 0 |
| against real rollouts, where the RST campaign has them (3 of 12) | SEED 4/5, SEED 5/5, FLAGGED-B1 0/5: all three agree |
| rows with a v13 lint | 9 of 12: L14 3 (a `def test_` line left outside an entry), L15 3, L3/L16 2 each (pins to look at), L11 2, L7 1; none changes a tier |

B1 fires on 9 of 12. The findings are concrete (a literal report header, a source grep for `fork`/`execvp`, an output
file named nowhere the agent can read, a persistent `nvm` default the instruction never asks for) and they are the
defect the RST campaign measured independently: the verifier encodes one particular solution, and agents pass about
13 % of attempts. Eight of the nine passed the oracle gate, which is consistent: the oracle is that one solution.

## Mechanical facts the sidecars add (all 181 staged, no judge involved)

- **Compose**: all 12 tasks that ship a `docker-compose.yaml` failed or never finished the runtime gate (7
  `oracle_zero`, 5 not in its output); of the 169 without one, 168 pass and 1 is `empty_one`.
- **CMD**: 96 idle (`bash`, `sleep`), 82 none, 2 run through an ENTRYPOINT, 1 is a service the sandbox never starts.
- **Instruction points at a generator JSON** (`rewrite_contract.json`, `rewrite_step_verifier.json`, ...): 16 tasks; in
  15 the file is not in the image, so whatever it would have listed is unstated.
- **Bootstrap**: 174 fetch at grade time (apt 157, astral.sh 166, PyPI via uvx 167, PyPI via pip 7); 44 run under
  `set -e`, 12 have no fallback trap, 60 pin an interpreter uv may have to download.

## Files

| file | what |
|---|---|
| `v13_rst_prompt.md` | the judge prompt |
| `DIFF_v13_to_v13rst.patch` | exact diff against `../v13_prompt.md` |
| `build_schema_v13_rst.py` → `output_schema_v13_rst.json` | imports the frozen v13 builder; adds `bootstrap_fetch`, RST ids, `rubric_version: v13-rst`; the 23 cross-field rules are unchanged |
| `schema_selftest_rst.py` | what must and must not validate (140 + 12 cases), built from the v13-valid rows of the GLM low arm |
| `lint_selftest_rst.py` | the stager's bootstrap reader (8 cases) and LR1–LR5 (13 cases) on synthetic tasks built from the public bootstrap text; needs no corpus |
| `stage_rst.py` | staging + security gate + sidecars + candidate lists. Reads the RST campaign directory named by `RST_DATA_ROOT` (task trees, `security_union` table, static flags, runtime-gate results) |
| `lint_v13_rst.py` | every v13 lint + LR1–LR5 |
| `render_prompts_rst.py` | one prompt per task; digests computed at render time; `JUDGE_PYTHON` names the interpreter the judge validates its row with (default: the one running the renderer) |
| `score_run_rst.py` | coverage, schema, lints, tiers both ways, tier × runtime gate |
| `REVIEW_rst_prompt.md` | independent review of the first draft |

The run directory (staged tasks, sidecars, candidate lists, prompts, rows) is not part of any repository: it holds
task files. One run = `stage_rst.py <run>` (needs `requested_ids.txt` and `security_screen.tsv` in it), then
`render_prompts_rst.py <run>`, the headless-worker launcher of the v13 kit (`run_glm_judges.py`, one task per worker,
`--rows-root <run>`), then `score_run_rst.py <run>`. `smoke12.score.json` and `smoke12.rows.jsonl` are the smoke above.
