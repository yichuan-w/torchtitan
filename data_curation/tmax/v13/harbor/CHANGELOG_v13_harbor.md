# v13-harbor — a package-layout adaptation of v13, not a rubric revision

| artefact | sha256 | derived from |
|---|---|---|
| `v13_harbor_prompt.md` | `4e9acb3915d09363392789b99d224d2d503c67f7b916ab79e54df941aebc3d6a` | `v13_prompt.md` `96fbc03bc81ab876…` |
| `output_schema_v13_harbor.json` | `18a68b9f50b5e4d65d671ac1c4901b0ff14cb6597a92b79e699eb83aaad2a9c3` | `output_schema_v13.json` `f8a47506d0d060c1…` |

Both are produced by `derive_v13_harbor.py`, which asserts the source shas before it starts and refuses if
either moved. `derive_v13_harbor.py --check` re-derives and compares, so the pair can be shown to be exactly
this adaptation of exactly that v13 rather than a hand-edited cousin.

## Why

SWE-Smith-Seeds-Clean, SWE-Rebench-Tasks-Clean and TerminalWorld-Seeds-Clean ship harbor-style packages, and
**none of them has a `setup.sh`**. The image is built by `environment/Dockerfile`, and part of the verifier
lives in a companion file beside `tests/test.sh` — `tests/test_state.py` (TerminalWorld), `tests/test.patch`
(SWE-Rebench), `environment/bug.patch` (SWE-Smith; byte-identical to `solution/bug.patch` in all 37, so
nothing is lost by excluding `solution/` from the judging package).

Run under frozen v13 the judge would open a file that does not exist and never read the file where most of
the assertions live. That is not a drift to absorb quietly: it changes what gate 1 measures.

## What changed — the whole of it

**Prompt: eight replacements, each asserted to match exactly once.** The file list and read order; the
"never execute" sentence; the definition of *shipped state*; harness fact 1 (what the build leaves behind);
the Step 2 inventory sentence; the `removed_before_rollout` exclusion; one A4 example; and the
`evidence_file` line of the output block. The phrase "the three files" becomes "the files", because the list
is now given at the top and is corpus-specific. `{COMPANION_LINE}` is filled by the renderer with the
corpus's own companion, so each judge is told exactly which files it may read.

**Schema: three edits, 14 leaf differences, 17 of 30 properties byte-identical.**

1. `task_id` — was `^(task_[0-9]{6}_[0-9a-f]{8}|rts_task_[0-9a-f]{8,32})$`, now a non-empty string. The old
   pattern matched **0 of 99** TerminalWorld ids, **0 of 61** Rebench and **0 of 37** Smith: not one row of
   this run could have validated.
2. The **twelve** anchor patterns — `oracle_reachable`, `expectation_revealed`, `value_derivable`,
   `overspecific_check`, `unstable_reward`, `expectation_movable`, `weak_verifier_exploit`,
   `core_clause_unenforced`, `ambiguity`, `uncertain_fact`, `pass_probe` — now admit the union list
   `instruction.md`, `tests/test.sh`, `tests/test_state.py`, `tests/test.patch`, `environment/bug.patch`,
   `environment/Dockerfile`. Union rather than per corpus: one row shape across three sources, and the prompt
   is what tells a judge which of them its task actually has.
3. `evidence_file`'s enum — the same union.

**Corrected 2026-09-23, after the TerminalWorld run.** The first derivation rewrote only the anchor patterns
reachable as `properties[*].anyOf` — **eleven** — and missed a **twelfth** nested in
`excluded_material.items.basis`. A judge hit it on `tw_287590` by anchoring into `environment/Dockerfile`,
exactly as the prompt told it to, and the row failed validation against a schema that still named `setup.sh`
there. **The row was right and the schema was wrong.** The derivation now walks the whole document rather
than the shape I happened to remember, and asserts twelve. Schema sha
`2b3d7317669e70ab…` → `18a68b9f50b5e4d6…`; the prompt is unchanged. The 99 TerminalWorld rows were produced
under the first schema and validate 99/99 under the corrected one, which is sound in this direction only
because the correction **widens** an anchor list that was wrong — no row was re-judged.

**Everything else is byte-identical**: every question, every field, the tier and verdict enums, the ordered
rules A1–A7 and B1–B3, and every `allOf` consistency rule. A v13-harbor verdict and a v13 verdict mean the
same thing; only the list of files the judge may open has moved.

## What this does not claim

It is not evidence that these corpora judge the same as TMAX. It is the smallest change that lets them be
judged at all, and any comparison of tier rates across corpora still has to account for them being different
task populations. Rows carry both shas so a reader can tell which pair produced them.


# v13-harbor **v2** — the same file contract, on the RST facts instead of the TMAX ones

| artefact | sha256 | derived from |
|---|---|---|
| `v13_harbor2_prompt.md` | `e6f20b43ed6efb889f7ccd2afab66e6f848d8ae21b10b00bb1625865ce73befd` | `rst/v13_rst_prompt.md` `d905473d86f51b3a…` |
| `output_schema_v13_harbor2.json` | `013cc89ddae6d0071f39fc9753f561a4ef630d59200fd41a00660022dacfcc3c` | `rst/output_schema_v13_rst.json` `1cf0bd9b7a50c05d…` |

**It supersedes `4e9acb3915d09363…` for any corpus whose verifier is pytest on `tests/test_state.py`.**
Produced by `derive_v13_harbor2.py`, which asserts both source shas and re-derives under `--check`.

**Why v1 was the wrong cut, in one line each.** v1 took the v13 TMAX base and changed the file contract. But
TerminalWorld runs the **same grading path as RST** — `grading.py:220 grade_tmax` uploads `tests/*`, runs
`bash /tests/test.sh` and reads `/logs/verifier/reward.txt`; `codex_session.py:61` picks the verifier file by
whether `tests/test_state.py` exists, **not by corpus**; and **99 of 99** TW `test.sh` run
`uvx … pytest /tests/test_state.py`. So the base's harness facts describe a path these tasks do not take:
fact 11 (reward is 1 iff pytest exits 0, skip/xfail ungraded, `--with` fetched from PyPI, import-time failure
scores 0) is **absent** from v1, fact 3 never mentions `test_state.py`, fact 4 has no reward.txt or sentinel,
and the pytest glossary is missing — v1 has 3 pytest mentions against v13-rst's 21. The reason v1 took the
base was real but wrong-headed: v13-rst's contract is written around the RST **staging** layout (a separator
line in a staged `tests/test.sh`, anchors by staging name), and **0 of 99** TW packages have that. The
separator is a staging artefact; the facts are harness facts, and the harness is shared. The correct move was
this one — take the RST facts, change the contract.

**What v2 changes against `v13_rst_prompt.md`**, all of it: the file block (native `instruction.md`,
`environment/Dockerfile`, `tests/test.sh` and the corpus companion, no staged `setup.sh`, no separator); the
anchor rule and the glossary's anchor entry (own paths, own line numbers); the glossary's `bootstrap` entry
and fact 11's opening (`tests/test.sh` is the bootstrap in its entirety, rather than the part above a
separator); the `assertions` field's file (`tests/test_state.py` rather than "below the separator"); the B3
scope sentence and Step 4b's opening, for the same reason; the second anchor list, which still named
`setup.sh`; the header sentence, so it no longer says "the RST corpus"; and `evidence_file` in the output
block. **Facts 1, 3, 4, 9, 11, Step 4b, `bootstrap_fetch` and the pytest glossary are carried over
unchanged.**

**The schema could not be reused.** `18a68b9f50b5e4d6…` was derived from the TMAX schema and has **30**
properties; v13-rst's has **31**, the extra one being `bootstrap_fetch`, which Step 4b requires. So v2's
schema is the **v13-rst** schema with the same three surgical edits (task_id a non-empty string, **12** anchor
patterns widened to the union, `evidence_file`'s enum widened). Consequence worth stating plainly: **all 99
rows written under v1 fail v2's schema, 99 of 99, on `'bootstrap_fetch' is a required property`** — they were
never asked for it. Under v2 there is no such thing as keeping the v1 rows and adding a field.

**On linting.** `lint_v13_rst.py` and `lint_v13.py` are **row** lints, not prompt lints: they take a run
directory plus row files and read a `sidecar/<id>.json` per task. Pointed at a prompt they lint nothing and
exit 0 — which is what happened the first time I ran one here, and is worth recording as a trap. The harbor
staging writes no sidecar, so the row lint cannot run on these rows at all without one. What was checked
instead: the derivation's own assertions (both source shas, each replacement matching exactly once, twelve
anchor patterns rewritten, no staging wording surviving outside the two sentences that say the corpus has
none), and a dry render of the prompt for one non-security TerminalWorld id — 592 lines, every placeholder
filled, all four files named and present on disk, no leftover staging or `setup.sh` wording.
# v13-harbor **v2.1** — the RST-only passages deleted, the TerminalWorld facts restated, the verifier named

| artefact | sha256 | derived from |
|---|---|---|
| `v13_harbor21_prompt.md` | `74666a729f075a0003ab92ea719aaf6511d5df24907464483c3672a8ab6815c5` | `v13_harbor2_prompt.md` `e6f20b43ed6efb88…` |
| `output_schema_v13_harbor21.json` | `0d37ee14631bc41531df0af8b15e29d387a33c95e80d713a73fd5cb3d2b439ad` | `output_schema_v13_harbor2.json` `013cc89ddae6d007…` |

Rubric label `v13-harbor2` (prompt title, output block, schema `rubric_version` const, `$id` and title). Produced by
`derive_v13_harbor21.py`; `--check` re-derives both files byte for byte.

**Superseded before any use:** commit 2db44a16 carried an earlier cut of this file (prompt `9c7e5894…`), written as a
whole-passage rewrite in answer to the first review. The lead's later ruling approved the second review's plan
instead (below); that cut was re-derived before anything was staged for judging or launched.

## Why (review of v2, 2026-09-23, delivered to the lead)

v2 carried v13-rst's harness facts over correctly, but it kept every passage written for RST's *staged* file — a
verifier "below the separator", "the three files", assertions and literals located "in tests/test.sh" — and a few
RST-only claims. Checked against the harness (`examples/tmax/grading.py`, `prepare_rts_data.py`, `rollouter.py` in
the training checkout) and the 99 TerminalWorld packages (pattern counts only):

- **Blocker.** 38 of 99 TW images hold 86 build-context files that COPY/ADD lines bring in (28 of the 38 tasks are
  not security-screened); v2 told the judge to read four files "and nothing else", so A1/A3 were undecidable there.
  v2 had also dropped v13-rst's sentence on unread material (review F9).
- **Location of assertions.** Step 0 said "line numbers being those of that tests/test.sh" right after naming
  tests/test_state.py; `last_assert_line`, `literal_derivation`, `evidence`, the SEED probe and two output comments
  located assertions "in tests/test.sh"; B3 read "the lines below the file."; the glossary called the verifier "the
  lines below it". The schema accepts both files, so a judge following those lines writes valid rows anchored in the
  wrong file.
- **RST-only claims.** Bootstrap variants (pip, virtualenv, `python -m pytest`, direct `python3`): 0 of 99. "A few
  bootstraps cd": 0 of 99. The "write 0 if missing" trap: 0 of 99. Fact 11's "creates /logs/verifier": 0 of 99 (the
  harness does, grading.py:242-247). "Pytest, almost always": 99 of 99. The time limit "600 to 1,800 s": TW declares
  none to the harness, which uses one run-wide value, 600 s by default (config_registry.py:163, rollouter.py:1036-1041).
  The A3 "this corpus" example: 0 of 99 fit it, and v13's base A3 never had it.
- **Kit.** The v1 wrappers never substituted `{COMPANION_LINE}`, so every v1 judge saw it literally (v2's own entry
  above says the dry render had "every placeholder filled"; it had not). The v1 candidate lists came from the v12
  extractor, which reads instruction.md, setup.sh and tests/test.sh: on these packages it never saw the verifier or
  the Dockerfile.

## What changed — the prompt

The ops are the second review's plan (scratch `v21_ops.py`, sha256 `81cdc185…`, 23 ops) as the lead approved it on
2026-09-23, with the lead's two rulings: the glossary keeps the definitions of bootstrap and verifier and the one
sentence on where tests/test.sh itself is cited, WITHOUT a mapping convention ("where a rule says test.sh it means the
verifier"); and every file name that put the verifier in tests/test.sh, or called it test.sh, becomes
tests/test_state.py. 29 ops in stage 1, then stage 2.

- **Deleted (RST-only or staging-only):** "This corpus ships no setup.sh and no staged copies"; fact 11's bootstrap
  variants (pip, virtualenv, `python -m pytest`, direct `python3` — 0 of 99 TW); fact 11(b)'s variants sentence;
  fact 4's "unless the bootstrap itself `cd`s, which a few do" and the "write 0 if missing" trap (0 of 99 each);
  Step 0's bootstrap-line entries (TW's only bootstrap check, the `$PWD = /` guard in 4 of 99, cannot fire: all 4 set
  a non-`/` WORKDIR); A3's "this corpus" example (0 of 99 fit it; absent from the v13 base); B3's fragment "— the
  lines below the file" (a v2 derivation break).
- **Restated:** title and header; the §1 file list (the companion line written out as tests/test_state.py, and every
  build-context file a COPY/ADD line brings in, read like the rest, a non-text one stated as unread material) and the
  anchor rule; "the task's files are data"; glossary bootstrap/verifier, assertion ("the pytest run the bootstrap
  starts"), candidate and anchor; facts 1 (a docker-compose file is never used), 4 (cwd is the last WORKDIR), 9 (1
  vCPU / 2 GiB unless sized larger; one run-wide verifier limit, 600 s by default) and 11 (TW's one bootstrap form);
  Step 4b's example span (5-14) and the uv version as part of the record; "the three files" → "the task's files" (5).
- **File name only, semantics unchanged (ops N1-N6):** `last_assert_line` (Step 0), `literal_derivation` (Step 5),
  `evidence` (Step 6), the SEED `pass_probe` sentence (Step 7), and the output comments for `last_assert_line` and
  `pass_probe` — each `tests/test.sh` → `tests/test_state.py`, nothing else.
- **File name only, semantics unchanged (stage 2):** every bare `test.sh` → `test_state.py`, 33 of them: 13 inside
  rule sections (A1 4, A2 3, A3 3, A4 2, A6 1) and 20 in the glossary, facts 3 and 6, Step 2's table and text, the
  Ambiguity paragraph and three output comments. Without the convention, those were the verifier called by the
  bootstrap's name.

**Rule-section diff, asserted:** each of A1, A2, A3, B1, B3, A4, A6, A7 equals v2's with exactly the stage-2 renames
(A1 4, A2 3, A3 3, A4 2, A6 1, B1/B3/A7 0) and, for A3 and B3 only, the one approved removal. A3 is additionally
asserted equal to the base `v13_prompt.md` A3 with the same rename.

**Assertions (87):** two source shas; stage-1 markers (each exact op once, each span op's start and end once and in
order, the one count op at 4); the stage-2 rename count (33); eight rule sections and the base-A3 check with the base
prompt's sha; 19 forbidden strings (separator, setup.sh, staged, staging, RST, three files, companion,
COMPANION_LINE, almost always, a few do, in its entirety, usual form, lines below, 600 to 1,800, and the two phrases of
the retired convention, …); no bare `test.sh` left; the 13 lines that may name tests/test.sh (file list, bootstrap,
Step 4b's record) listed and each present; placeholders exactly `{TASK_ROOT}`, `{CANDIDATES_PATH}`, `{OUTPUT_PATH}`;
three schema edits and no "v13-rst" left. Mutation-checked: dropping the B3, A3x, N1, N6 or F1 op, putting the
convention back into G1, editing A2, and adding one more bare `test.sh` each fail with a named assertion. The renames
lengthen some lines past 120 characters; they are not reflowed, because inside a rule that would change more than a
file name.

## What changed — the kit (before any v2.1 launch)

- `harbor_files.py`: the judge's read list, one definition for stager, extractor and lint. Build-context resolution
  mirrors the harness's `_build_context`; `--crosscheck` runs the harness's own function (lifted by `ast`, since the
  module needs torch) over every task: 99 of 99 identical file sets; dropping one COPY line's files makes 38 differ.
- `candidates_harbor.py`: the v12 regexes over the v2.1 read list; failing statements from tests/test_state.py.
  Coverage on the 99 TW packages, v1 → v21: tasks with failing statements 4 → 99 (v1's four were all a bootstrap
  `exit 1` guard); failing statements 4 → 1,161; programs 261 → 450, mentioned in instruction.md 192 → 182 (directory
  prefixes now dropped against longer paths from other files), tests/test.sh 97 → 97, environment/Dockerfile 0 → 388,
  tests/test_state.py 0 → 438, copied build-context files 0 → 10.
- `../tb21_agentic_top10/stage_harbor_197.py`: points at the v2.1 prompt and schema by sha; the wrapper says
  `rubric_version "v13-harbor2"`, lists each task's read list with every COPY'd file (86 across 99 wrappers, 1 marked
  not text; 0 list an uncopied `environment/solve.sh` or compose file), and no longer claims "these files and no
  others". `--restage` writes the fresh run directory `harbor/tw_v21/` and refuses if it exists; v1's candidates,
  prompts and rows under `harbor/tw/` are untouched. SWE-Smith and SWE-Rebench are refused: their verifier is not
  tests/test_state.py. The wrapper's validate command falls back to `Draft7Validator`: `/usr/bin/python3` has
  jsonschema 3.2.0 with no `Draft202012Validator`, so **v1's instructed validate command raised AttributeError as
  written**. The schema uses only keywords with the same meaning in draft 7 — measured, identical error sets under
  both validators on 396 rows (the 99 v1 rows and three variants of each).
- `lint_v13_harbor2.py` + `lint_selftest_harbor2.py`: the row lint for harbor rows, without sidecar or separator
  (ids H0-H16, HB; see its docstring). Selftest: a consistent SEED row validates and lints clean, 15 mutations each
  raise their id, and the one legitimate bootstrap-evidence shape (B1 on an instruction that stops the bootstrap)
  lints clean. On the 99 v1 rows, as a measure of what v1 did not ask for: H0 and HB 99 (no `bootstrap_fetch`, old
  label), H2b 74 and H2 68 (verifier failing statements uncovered), H7 56 (40 of the non-security ones cite a
  bootstrap line as evidence — v1's B3-on-bootstrap-fetch shape, which v2 moved to Step 4b).
- Dry render, tw_100135 (not security-screened), against prompt `74666a72…`: the wrapper's three substitutions leave no
  placeholder (586 lines); 6 files listed in §1's order, equal to the read list, all present (2 build-context files);
  prompt and schema shas in the wrapper match the files; the wrapper's rubric label equals the schema const;
  `solution/` absent; `rows/` empty. Nothing launched.

Three earlier `--restage` runs are kept, renamed, nothing deleted: `harbor/tw_v21.superseded-validator-command/` (the
crashing validate command), `.superseded-2db44a16-prompt/` (the earlier cut) and `.superseded-read-order/` (files
listed in a different order from §1). The read list now follows §1: instruction.md, environment/Dockerfile,
tests/test.sh, tests/test_state.py, then the build-context files.

# v13-harbor **v2.1.1** — the v2.1 review's findings (REVIEW_v13_harbor21_prompt.md, 3ac74fa0)

| artefact | sha256 | derived from |
|---|---|---|
| `v13_harbor211_prompt.md` | `ac3dde0e54c079bfa9ce81635cc0c45960b603f97dd6781c6180de5574db7739` | `v13_harbor21_prompt.md` `74666a729f075a00…` |
| `output_schema_v13_harbor21.json` | `0d37ee14631bc41531df0af8b15e29d387a33c95e80d713a73fd5cb3d2b439ad` | unchanged |

A third stage of `derive_v13_harbor21.py`: 12 exactly-once ops on the v2.1 text; `--check` now re-derives v2.1 (still
`74666a72`), v2.1.1 and the schema, 134 assertions in all. **Every rule section is byte-identical to v2.1** (asserted,
all eight), so the rule-section diff against v2 is still exactly the 13 file-name renames and the two approved
removals. Paragraphs an op touches are reflowed whole; no line the stage writes is over 118 characters. Leftover
assertions: v2.1's 19 forbidden strings plus "as a real", "bootstrap, whole", "unless the task was sized larger", "one
of the files named at the top" and "nothing changes directory"; no bare `test.sh`; a 15-line allow-list of lines that
may name tests/test.sh; the three placeholders. Mutation-checked: 16 across both stages (7 for this one), 0 missed.

- **m1** Step 6 gives the bootstrap-stop case its evidence again: "…the decisive assertion in tests/test_state.py (whose
  outcome the reproduction changes), or, for the B1 case of an instruction that stops the bootstrap, the tests/test.sh
  line that fails". The glossary adds the third permitted citation of tests/test.sh: "as the `basis` of an
  `excluded_material` entry for a path only the bootstrap names" (Step 2's bootstrap-only paths).
- **m2** fact 4: "neither the harness nor the bootstrap changes directory before the verifier runs: its cwd is the
  Dockerfile's last `WORKDIR` (a bootstrap that `cd`s would move it; read its lines)" — B1's frozen reference to "the
  bootstrap's `cd` target (fact 4)" points at something again.
- **m3** Step 4b: the interpreter download is recorded without `-p` as well, when the Dockerfile installs no Python and
  its base is not a Python image.
- **m4** "as a real Dockerfile" and "the harness bootstrap, whole" dropped; the header lists facts 1, 3, 4, 7, 9 and 11
  (measured against the base: 3 and 7 differ in substance, 6 only by the verifier's file name).
- **m5** §5 `evidence_file` lists the four files; the candidate glossary says a `seen_in` inside a build-context file is
  cited through its COPY/ADD line.
- **m6** fact 1: in a multi-stage Dockerfile, what the last stage holds, including what its `COPY --from` lines bring in.
- **m7** fact 9: "1 vCPU and 2 GiB for most tasks, and the files you read do not say which tasks get more, so judge a
  threshold against 1 vCPU and 2 GiB".
- **m8** (stager) the wrapper says "The only files you write are your own row and one scratch file, under
  `harbor/tw_v21/scratch/<task_id>/`", and the stager creates that directory; `harbor/*/scratch/` is ignored by shape.
- **m9** (stager) the agent label carries the prompt's first 8 hex digits: `judge:harbor:tw:v21-ac3dde0e:b0001`.
- **m10** (lint) HB also requires the record's form, `<command> ->` for each of apt-get, curl and uvx the bootstrap's
  fetch lines run. On a correctly formed record for each of the 99 bootstraps it fires 0 times.
- **M1** (lint) new check **HL**: a leak field, `unstable_reward` or `uncertain_fact` anchored in tests/test.sh. Three
  selftest mutations (the reviewer's probes) each raise HL; the selftest passes 19 of 19 mutations plus both clean rows.
  **On the 99 v1 rows HL fires on 81** (21 of them security-screened), the measure of v1's bootstrap-as-verifier defect.

Restaged: `harbor/tw_v21/` against `ac3dde0e`; the previous staging renamed to `tw_v21.superseded-74666a72/`, nothing
deleted. Dry render, tw_100135: 592 lines, no placeholder left, 6 files in §1's order (2 build-context), shas and label
match, scratch directory present, `solution/` absent, `rows/` empty. Nothing launched.

The review's five observations are frozen or harness-conditional text and are left as they are; its harness finding
(an agent-written `/tests/conftest.py` survives into grading) is for the harness owner, not the prompt.
