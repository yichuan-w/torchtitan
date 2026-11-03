# v13-harbor — the v13 seed audit for native harbor packages (TerminalWorld)

`../v13_prompt.md` judges TMAX and `../rst/v13_rst_prompt.md` judges RST's *staged* files. This directory is the
variant for harbor-format packages read as they ship: `instruction.md`, `environment/Dockerfile` with the
build-context files its COPY/ADD lines bring in, a harness bootstrap `tests/test.sh`, and the verifier
`tests/test_state.py` (pytest). It was cut for TerminalWorld (andylizf/TerminalWorld-Seeds-Clean, the 99 tasks of the
tb21 top-10). The rules A1–A7, B1 and B3 mean what they mean in v13.

## Lineage

| cut | prompt | sha256 | schema | sha256 | derived by | from |
|---|---|---|---|---|---|---|
| v1 (superseded) | `v13_harbor_prompt.md` | `4e9acb39…` | `output_schema_v13_harbor.json` | `18a68b9f…` | `derive_v13_harbor.py` | `../v13_prompt.md` `96fbc03b…` |
| v2 | `v13_harbor2_prompt.md` | `e6f20b43…` | `output_schema_v13_harbor2.json` | `013cc89d…` | `derive_v13_harbor2.py` | `../rst/v13_rst_prompt.md` `d905473d…` |
| v2.1 | `v13_harbor21_prompt.md` | `74666a72…` | `output_schema_v13_harbor21.json` | `0d37ee14…` | `derive_v13_harbor21.py` | v2 |
| **v2.1.1 (current)** | `v13_harbor211_prompt.md` | `ac3dde0e…` | `output_schema_v13_harbor21.json` | `0d37ee14…` | `derive_v13_harbor21.py` | v2.1 |

Rubric label: `v13` for v1 (its schema const), `v13-harbor2` for v2.1 and v2.1.1. Every row the v2.1.1 run wrote also
carries the prompt's sha in its agent label (`judge:harbor:tw:v21-ac3dde0e:bNNNN`).

- **v1** changed only the file contract of the TMAX base. That was the wrong cut. TerminalWorld runs the same grading
  path as RST (`grade_tmax` uploads `tests/*`, runs `bash /tests/test.sh`, reads `/logs/verifier/reward.txt` behind a
  sentinel), and all 99 bootstraps run `uvx … pytest /tests/test_state.py`. So v1 lacked the RST harness facts: the
  bootstrap, pytest's reward semantics, and that `--with` packages come from PyPI at grade time. Its judges therefore
  fired B3 on the bootstrap's own fetches.
- **v2** takes v13-rst's harness facts and replaces only its staged-file contract.
- **v2.1** deletes the passages v2 still carried for RST's staged file, restates the TerminalWorld facts, and adds the
  build-context files to the read list (38 of 99 TW images hold some; v2 told the judge to read four files "and
  nothing else"). The verifier's file name replaces `test.sh` wherever the verifier is meant: 6 field descriptions and
  33 bare mentions, 13 of them inside rules. Only file names change; no rule's meaning does.
- **v2.1.1** applies the ten minor findings of the v2.1 review. It changes no rule section.

## Rules: what is frozen, and how that is checked

`derive_v13_harbor21.py` asserts it:
- **A1, A2, B1, A4, A6, A7:** identical to v13-rst apart from the verifier's file name.
- **A3:** v13-rst's minus its RST-only example, which makes it the base v13 A3 with the same rename.
- **B3:** v13-rst's minus a phrase that had become "the lines below the file".
- **v2.1.1:** every rule section byte-identical to v2.1.

## Re-deriving

Each script checks its source shas and then rebuilds its output. With `--check` it compares against the files here
instead of writing them. The scripts resolve paths from their own directory: `../v13_prompt.md`,
`../output_schema_v13.json` and `../rst/` are the files this PR already carries.

    python3 derive_v13_harbor.py --check      # v1
    python3 derive_v13_harbor2.py --check     # v2
    python3 derive_v13_harbor21.py --check    # v2.1 and v2.1.1, 134 assertions

`derive_v13_harbor21.py` makes these assertions:
- every op's markers occur exactly once, and no two spans overlap;
- the rule-section checks above;
- no leftover staging or RST wording;
- an allow-list of the lines that may name `tests/test.sh`;
- the only placeholders are `{TASK_ROOT}`, `{CANDIDATES_PATH}` and `{OUTPUT_PATH}`.

## Kit

- `harbor_files.py`: the judge's read list, in the prompt's §1 order. Build-context files are resolved as the harness's
  `_build_context` resolves them; `--crosscheck` runs the harness's own function (99 of 99 equal on TW).
- `candidates_harbor.py`: the `{CANDIDATES_PATH}` inventory. Failing statements come from `tests/test_state.py`,
  programs from every file the judge reads. The v12 extractor reads `setup.sh` and `tests/test.sh`, and on native
  packages found failing statements in 4 of 99.
- `lint_v13_harbor2.py`: the row lint for these rows. It needs no sidecar and no separator. Its ids are H0–H16, plus:
  - `HB`: the bootstrap record against the bootstrap itself;
  - `HL`: a leak, a B3 or an `uncertain_fact` anchored in the bootstrap.

  It imports `../tools/lint_v13.py` and `../tools/candidates_extract.py`. `lint_selftest_harbor2.py` builds its
  fixtures from an extracted package and v21 candidate list under `../tb21_agentic_top10/harbor/`, which this PR does
  not carry.
- `stage_harbor_197.py`: stages TW judge inputs. It needs `tb21_agentic_top10.source.csv` and the packages extracted
  from the dataset tar. Its `--restage` writes a fresh run directory `<corpus>_v21/` with `candidates/`, `prompts/`,
  `batches/`, `rows/` and `scratch/`. The wrapper pins the prompt and schema by sha and lists each task's
  build-context files. It refuses SWE-Smith and SWE-Rebench, whose verifier is not `tests/test_state.py`.

## Reviews

- `CHANGELOG_v13_harbor.md` records each cut, and why.
- A review of v2 (2026-09-23) found a blocker: the unread build-context files. It also found the staged-file residue.
  That review produced v2.1, and the CHANGELOG summarises it.
- `REVIEW_v13_harbor21_prompt.md` is an independent review of v2.1. Its verdict is **fit to judge TW**, with 0
  blockers, 1 major (the lint lacked the bootstrap-boundary check, now `HL`) and 10 minors; all 11 are fixed in v2.1.1.
  - Harness claims: 29 of 31 hold and 2 are unverifiable.
  - Corpus claims: 11 of 11 hold. Author claims: 12 of 12.
  - The review also reports, outside the prompt, a grading-path defect for the harness owner: `/tests` is not cleared
    before the uploads.

## Runs

TerminalWorld: the 99 harbor packages of the tb21 top-10. Judge z-ai/glm-5.3 in headless Claude Code, through a
provider-pin shim to OpenRouter.

| | v1 | v2.1.1 |
|---|---|---|
| rows | 99 (99 valid under the v1 schema) | 99 (99 valid under `0d37ee14`) |
| tiers | FLAGGED 84, SEED 15 | SEED 67, FLAGGED 29, UNSURE 3 |
| verdicts | UNSTABLE-REWARD 57, ANSWER-LEAK 24, CLEAN 15, INSTR-VERIFIER-MISMATCH 3 | CLEAN 69, ANSWER-LEAK 26, INSTR-VERIFIER-MISMATCH 4 |
| `HL` (lint_v13_harbor2) | 81 | 0 |
| spend | $45.93, five provider phases | $24.53 ($0.248 per row), one phase (baidu/fp8, 4-wide) |

v2.1.1 tier × verdict: SEED/CLEAN 67, FLAGGED/ANSWER-LEAK 25, FLAGGED/INSTR-VERIFIER-MISMATCH 4,
UNSURE/ANSWER-LEAK 1, UNSURE/CLEAN 2.

The v2.1.1 run itself: 0 mid-stream aborts; 139 × 429, all absorbed by the shim's retries; 0 watchdog abandons; every
task done on its first attempt; 0 capped turns (counted from the transcripts).

`results/`:
- `tw_v21.rows.jsonl`: the 75 published v2.1.1 rows.
- `tw_v21.score.json`: counts, tier × verdict, fired fields, the lint distribution and the run's counters.
- `tw_v1.summary.json`: the v1 counts, and the v1 rows measured against the v2.1 contract, with no rows.

The 24 security-screened tasks were judged but are not published: their rows and ids are out of this PR, as in every
earlier one. The counts above include them.
