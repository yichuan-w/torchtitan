# Independent review — v13-harbor2 judge prompt v2.1 and its kit, for TerminalWorld

Reviewer: claude-opus-5-5, read-only on the prompt and kit, nothing launched. 2026-09-23.

**Target.** The brief named `v13_harbor21_prompt.md` `9c7e5894…` at `2db44a16`. While this review was running, commit
`1d16136c` (23:36:31) replaced that cut with `74666a729f075a00…`. Its commit message says 2db44a16 "was never staged
for judging or launched", and `harbor/tw_v21/` is now staged against `74666a72`. I told the lead at the time and
reviewed **`74666a72` at `1d16136c`**, the version a judge will read. Every line number below is a line of that file.
The appendix says which findings also apply to `9c7e5894`.

Reviewed (sha256, first 16): prompt `74666a729f075a00`, schema `0d37ee14631bc415`, `derive_v13_harbor21.py`
`8297fc45a5370e7d`, `lint_v13_harbor2.py` `9662c6de80bf1062`, `lint_selftest_harbor2.py` `35dd8601176e44b8`,
`harbor_files.py` `a925e2f1dd0dbbb0`, `candidates_harbor.py` `62e65f99c6f5c3c6`,
`../tb21_agentic_top10/stage_harbor_197.py` `3793b6643b72f6f5`, the tw_100135 wrapper `e2821184dc62c526`,
`CHANGELOG_v13_harbor.md` `d68d3f24894c18ec`. Ancestors: v2 `e6f20b43…`, `../rst/v13_rst_prompt.md` `d905473d…` and
`../v13_prompt.md` `96fbc03b…`. Harness paths are relative to `external/torchtitan-cotrain/torchtitan/experiments/rl/`
(checkout `e22c7098`). Corpus counts cover the 99 packages under `../tb21_agentic_top10/harbor/tw/tasks`, by pattern
only. The 24 security-screened ids were only counted by pattern; I did not read them. No task text appears here.

**Counts: 0 blockers, 1 major, 10 minor.** Also: 5 observations on frozen or harness-conditional text, and one harness
defect outside the prompt that the harness owner should see (last section).

**Claims that hold:** 29 of 31 harness claims hold, 2 are unverifiable from the code and 0 are false; one claim is
false for 1 of 99 tasks (m6). 11 of 11 corpus claims hold. 12 of 12 author claims hold. 8 of 8 rule sections are
unchanged in meaning.

**Verdict: fit to judge TW.** The prompt contains no instruction a literal judge would follow to a wrong verdict. The
major finding is in the row lint, which runs after judging; fix it before its output is used to accept or reject any
row.

---

## M1 — major — the row lint does not enforce the bootstrap boundary, the defect v1's rows showed most

**Where.** `lint_v13_harbor2.py` (no check), against the prompt's own rule:
- `:364-365`: "The bootstrap's fetches are never B3: they go to Step 4b".
- `:392-394`: a bootstrap fetch is "never repeated under B3, in `uncertain_fact` or in `reason`".
- `:57-58`: "tests/test.sh itself is cited only by the bootstrap record (Step 4b) and by the B1 case of an
  instruction that stops the bootstrap".

**What is wrong.** The lint's only bootstrap check is H7 (`:156`). H7 fires when `evidence_file` is `tests/test.sh`
and nowhere else. But Step 6 (`:489`) now tells the judge that every A1–A3, B1 and B3 row takes its evidence from
`tests/test_state.py`. A judge who makes v1's mistake while following Step 6 therefore writes a row that H7 cannot see.

**Evidence.** I built each probe from the selftest's own clean row for `tw_100135`, under `/usr/bin/python3 -B`. The
clean row validated with 0 errors and linted `ok`, the positive control. Every probe below also gave 0 schema errors
and lint `ok`:
- `unstable_reward` anchored at `tests/test.sh:7` (the uv installer `curl`), FLAGGED, `external-fetch`.
- `value_derivable` anchored at `tests/test.sh:7`, naming a real candidate path, FLAGGED, `reward==1`.
- `uncertain_fact` anchored at `tests/test.sh:7` ("grade-time fetch from astral.sh may fail"), which turns a SEED row
  into UNSURE.

The HB control fired as expected: omitting `python 3.13` from `tw_17238`'s record gave `['HB']`. The CHANGELOG measures
the shape on the v1 rows: H7 fired on 56 of them, 40 non-security, "v1's B3-on-bootstrap-fetch shape". The third probe
is the over-filtering this variant exists to stop. The RST lint had a check for exactly this (LR3, "leak or B3 anchored
in the bootstrap"). The harbor lint dropped it.

**Fix.** In `lint_v13_harbor2.py`, after the H7 block, add:

```python
    # HL -- the bootstrap is harness code: no leak, no B3 and no uncertain_fact is anchored in it (Step 4b (iii),
    # B3's scope sentence, the glossary's two permitted citations of tests/test.sh)
    for k in tuple(base.LEAK) + ("unstable_reward", "uncertain_fact"):
        a = anchor_of(r.get(k))
        if a and a[0] == hf.BOOTSTRAP:
            v.add("HL")
```

Also add one line to the docstring and three mutations to `lint_selftest_harbor2.py`, the three probes above, each
expecting `HL`.

---

## m1 — minor — the glossary, Step 2 and Step 6 disagree about where tests/test.sh may be cited

**Where.**
- `:57-58`: tests/test.sh "is cited only by the bootstrap record (Step 4b) and by the B1 case of an instruction that
  stops the bootstrap"; B1 `:343` adds "(anchor the bootstrap line that fails)".
- Step 6 `:489-490`: "`evidence`, `evidence_file`, `evidence_line`: for A1–A3, B1 and B3 the decisive assertion in
  tests/test_state.py (whose outcome the reproduction changes)".
- Step 2 `:222-224`: bootstrap-only paths "are `toolchain` or `grade_time_generated`, one entry each". Each entry needs
  a `basis` anchor.

**What is wrong.** There are two separate breaks.
1. Step 6 leaves no place for the evidence of the one B1 case the glossary allows in the bootstrap. When the bootstrap
   stops before pytest starts, no tests/test_state.py assertion changes its outcome. This break came from a rename.
   Op N3 is described as "file name only", but in v13-rst the same sentence said `tests/test.sh`, and that file then
   held the bootstrap lines, so the bootstrap case fell inside it. The rename moved the meaning. (The lint's selftest
   still calls a bootstrap evidence line "the one legitimate bootstrap-evidence shape".)
2. Step 2 requires entries whose only anchor is in tests/test.sh, which the glossary's "only" forbids. The `tw_v21`
   inventories carry such a path in 11 of 99 tasks (12 entries, mostly `/var/lib/apt/lists`).

The verdict is unaffected either way. At most 2 of 99 instructions mention a firewall or `/etc/hosts`.

**Fix.**
- Step 6 `:489-490`: after "(whose outcome the reproduction changes)" add "or, for the B1 case of an instruction that
  stops the bootstrap, the tests/test.sh line that fails".
- Glossary `:57-58`: end the sentence "…and by the B1 case of an instruction that stops the bootstrap, and it is the
  `basis` of an `excluded_material` entry for a path only the bootstrap names."

## m2 — minor — B1 cites "the bootstrap's `cd` target (fact 4)", but fact 4 no longer mentions a `cd`

**Where.** Fact 4 `:131-132` says "nothing changes directory before the verifier runs: its cwd is the Dockerfile's last
`WORKDIR`". B1 `:335-337` is frozen text and says "the last `WORKDIR`, or the bootstrap's `cd` target when it has one
(fact 4)".

**What is wrong.** The cross-reference now points at nothing. The claim itself holds: 0 of 99 bootstraps `cd`, and the
harness opens a fresh Daytona session for every exec (`harness/sandbox/daytona.py:1111-1116`), so an agent's `cd`
cannot leak into grading. The fix belongs in fact 4, because B1 is frozen.

**Fix.** Replace `:131-132` with: "Grading runs in place, as root, in the same container, and neither the harness nor
the bootstrap changes directory before the verifier runs: its cwd is the Dockerfile's last `WORKDIR` (a bootstrap
that `cd`s would move it; read its lines)."

## m3 — minor — Step 4b records the interpreter download only for a `-p` pin

**Where.** `:380-381`: "with `-p <version>` also that Python build, from GitHub, unless the image has it". Also
`:389-390`.

**What is wrong.** When there is no `-p`, uv uses the image's Python if it finds one and otherwise downloads a build
from GitHub. 69 of 99 bootstraps run `uvx` without `-p`. In 36 of them the base image is not a Python image and the
Dockerfile never mentions `python`, which is the typical case of FROM `ubuntu` (64 of 99 bases). There the record
misses a fetch. The field has no tier effect, and HB cannot check this case because the image's Python is not
decidable from the files.

**Fix.** At `:381` add: "…unless the image has it; without `-p`, the same download happens when the image has no
Python, so add `python -> github.com (unless the image has one)` when the Dockerfile installs none and its base is
not a Python image".

## m4 — minor — leftover staging wording, and the header lists too few changed facts

**Where.**
- `:15`: "what the image bakes in before the agent starts, as a real Dockerfile". "Real" only makes sense in contrast
  with RST's staged setup.sh.
- `:18`: "the harness bootstrap, whole (fact 11)". "Whole" is left over from the "part above the separator".
- `:5-6`: "what differs is … the harness (§3, facts 1, 4, 9 and 11)". Against the base, facts 3, 6 and 7 also differ.
  Fact 3 now covers the upload of test_state.py and the reference solution, and fact 7 now covers the bootstrap's
  install and "a test file that collects no tests".

**Fix.** `:15` → "what the image bakes in before the agent starts: read its RUN, COPY/ADD, WORKDIR, ENTRYPOINT and CMD
lines as fact 1 describes them". `:18` → "the harness bootstrap (fact 11): …". `:6` → "(§3, facts 1, 3, 4, 7, 9 and
11)".

## m5 — minor — the inventory, the anchor rule, §5 and the schema cite build-context files four different ways

**Where.**
- The inventory writes `seen_in` as `environment/<src>:<line>`: 6 tasks, 1 of them with a program seen only there.
- §1 `:26-28` says a copied file is cited "by the COPY/ADD line that brings it in, followed by its path in the image".
- §5 `:551` says `evidence_file` is "one of the files named at the top of this prompt", and that list includes
  `environment/<source>`.
- The schema's anchor patterns and `evidence_file` enum refuse `environment/<src>`, and they admit `tests/test.patch`
  and `environment/bug.patch`, which no TW task has.

A judge who copies a `seen_in` into a `basis` fails validation. That is recoverable, but the prompt itself caused it.

**Fix.**
- §5 `:551` → `"evidence_file": "instruction.md | environment/Dockerfile | tests/test.sh | tests/test_state.py"`.
- Glossary `candidate` `:87-90`: add "a `seen_in` inside a build-context file is cited through its COPY/ADD line (§1)".

## m6 — minor — fact 1 is false for a multi-stage build

**Where.** `:113-115`: "everything its `RUN` lines, heredocs and COPY/ADD lines create … is in the container during
rollout".

**Evidence.** 1 of 99 Dockerfiles (security-screened) has two `FROM` lines and a `COPY --from`. What an earlier stage
creates is in the image only where a `COPY --from` brings it. The §1 file list correctly leaves `--from` sources out
(`harbor_files.py:21` mirrors `prepare_rts_data.py:80`), but fact 1 still asserts presence.

**Fix.** After "during rollout" add: "(in a multi-stage Dockerfile, only the last stage, plus what its `COPY --from`
lines bring into it)".

## m7 — minor — fact 9's "unless the task was sized larger" asks the judge for something it cannot see

**Where.** `:147`.

**What is wrong.** Sizing comes from task.toml and a dataset parquet (`examples/tmax/prepare_rts_data.py:411-441` and
`:541-550`), and neither is in the read list. Measured in task.toml: 5 tasks declare 4 CPUs and 9 declare 8 GiB, and
the harness honours both. For those tasks a B3 threshold is judged against a smaller box than the one the task gets.
Exposure is small: 1 verifier (security-screened) compares wall-clock time.

**Fix.** "The training sandbox is 1 vCPU and 2 GiB for most tasks; the files you read do not say which tasks get more,
so judge a threshold against 1 vCPU and 2 GiB."

## m8 — minor — the wrapper forbids every write except the row, then asks for a scratch file

**Where.** `tw_v21/prompts/tw__tw_100135.txt:1` says "The only file you write is your own row". `:16` says
"/usr/bin/python3 in a scratch file in your current working directory". Prompt §1 `:46` says "a scratch file of your
own". The judge's working directory is not stated, so a scratch file can land wherever the workflow starts the agent.

**Fix.** `stage_harbor_197.py`, wrapper line 1 → "The only files you write are your own row and one scratch file,
under a directory the workflow names."

## m9 — minor — one rubric label now names two prompt texts, and a row records neither sha

**Where.** The schema const is `v13-harbor2` (`0d37ee14…`) for both `9c7e5894` and `74666a72`. The wrapper checks the
prompt sha when the judge starts, but the row carries only the label. The same happened one step earlier: the v2 schema
kept the const `v13-rst` (`derive_v13_harbor21.py:236`).

**Impact.** Nothing at present: all three superseded stagings (`tw_v21.superseded-*/rows`) are empty. It becomes a
problem the first time two cuts are ever launched.

**Fix.** Put the prompt's first 8 hex digits into the agent label the stager writes (`judge:harbor:tw:v21-74666a72:b0001`),
or have the assembler stamp the sha from the wrapper.

## m10 — minor — HB checks the span, packages, pin and uv version, but not the record's form

**Where.** `lint_v13_harbor2.py:236-253`.

**Evidence.** A record that keeps the span and lists only package names and the version, with no `->`, no command and
no host, lints `ok` (my probe on `tw_100135`). Step 4b `:384-388` fixes the form as `<command> -> <host>: <what>`.

**Fix.** Inside HB's `else:` add: for each of `apt-get`, `curl` and `uvx` present in the bootstrap's fetch lines,
require `f"{cmd} ->"` in the record.

---

## Observations — frozen or conditional text, measured, no change proposed

- **B1's bootstrap-stop list includes "the system Python removed" (`:342`).** Under `uvx` (99 of 99), removing the
  system Python does not stop the bootstrap, because uv fetches an interpreter. A literal judge could therefore fire a
  false B1. Exposure: 0 of 99 instructions ask to remove Python or curl, or to work offline. The text is frozen rule
  text, so I only record it.
- **A6's "in this corpus also … `pytest.skip`, or return inside `except`" (`:424-425`) is frozen and true.** TW
  verifiers contain `pytest.skip`/`skipif`/`xfail` 0 times and `except …: return` once (security-screened).
- **The `-w '<name> @ file:///<path>'` sentences fit 0 of 99 TW bootstraps.** They appear in fact 11(b) `:167-169`,
  Step 2 `:236` and Step 4b `:382`. They are conditional and describe uv, not a corpus, so keeping them does not break
  the deletion rule.
- **"Reward 1 exactly when pytest exits 0" (`:62-63`, `:159`) holds under `reward_mode` sparse.** Sparse is the config
  default (`examples/tmax/config_registry.py:171-173`) and the live run leaves `SWE_REWARD_DENSE` unset
  (`examples/tmax/runbook/RUNBOOK.md:658`). A dense run would take the CTRF pass fraction as the reward. Under dense, a
  skipped test lowers the reward, and the glossary's definition of an assertion would no longer describe the reward.
- **Fact 10's "never rewrites a task whose groups do not reach that solve rate" holds only with
  `SWE_EVOLVE_SIMPLIFY=0`.** That is the live setting (`RUNBOOK.md:821`). The code default (`evolution/evolve_ondella.py:79`,
  `rollouter.py:1043-1050`) simplifies 0/k groups, and under it an unfair task can be rewritten too.

---

## Harness claims, checked (item 1)

| # | claim (prompt line) | verdict | where |
|---|---|---|---|
| 1 | the image is built from the Dockerfile before the agent starts (113) | holds | `rollouter.py:1238-1246`, `harness/sandbox/daytona.py:712-759,836-842` |
| 2 | everything the RUN/COPY lines create is in the container (113-115) | holds, false for 1 multi-stage task (m6) | Docker semantics; corpus |
| 3 | a CMD alone never runs; ENTRYPOINT runs with CMD appended, detached (115-118) | holds | `prepare_rts_data.py:220-249,610-612`, `rollouter.py:609-633,1261-1264`; 58 of 99 are CMD-only, 4 have an ENTRYPOINT |
| 4 | a build-context file is in the image only where a COPY/ADD puts it (118-119) | holds | `prepare_rts_data.py:252-290`, `daytona.py:733-741`; `harbor_files.py --crosscheck` gives 99 of 99 identical file sets (rerun) |
| 5 | a docker-compose file is never used, and no service it declares exists (119-120) | holds | no compose handling anywhere under `rl/`; 9 packages ship one, 0 COPY it |
| 6 | the default directory is the last WORKDIR (120-121) | holds | `rollouter.py:617-622` (measured); 99 of 99 set a WORKDIR |
| 7 | the network is open during rollout (121) | holds | `daytona.py:836-842` passes no network restriction; 4 task.tomls declare `allow_internet = false`, and nothing reads that |
| 8 | the Dockerfile is not shown unless COPY'd (121-123) | holds | 0 of 99 COPY their own Dockerfile |
| 9 | the agent is root (124) | holds | `rollouter.py:383-407` |
| 10 | tests/ is absent during rollout; test.sh and test_state.py are uploaded after submission (126) | holds (see the last section) | `grading.py:122-136,242-254`, `rollouter.py:1330-1337`; 0 of 99 Dockerfiles mention /tests |
| 11 | the reference solution never reaches the container (127) | holds | `prepare_rts_data.py:554-559` reads solve.sh only to count commands; 2 packages hold an uncopied `environment/solve.sh` |
| 12 | grading runs in place, as root, in the same container (131) | holds | `grading.py:338-343`, `rollouter.py:1331-1337` |
| 13 | nothing changes directory before the verifier runs (131-132) | holds | `daytona.py:144-182` (no cd), `:1111-1116` (fresh session per exec); 0 of 99 bootstraps cd |
| 14 | the reward is the first line of reward.txt (132) | holds | `grading.py:151-157,359` (clamped to [0,1]) |
| 15 | the sentinel is planted and a surviving one scores 0, so a pre-written reward scores 0 (133-134) | holds | `grading.py:70-90,256-269,349-358` |
| 16 | reward.json is never read (134) | holds | only a presence filter reads it, `prepare_rts_data.py:518` |
| 17 | verifier stdout is discarded (134) | holds for the reward | kept only as a 400-character log tail, `grading.py:348-367` |
| 18 | services left running are reachable on loopback at grade time (135) | holds | same sandbox, `rollouter.py:1331` |
| 19 | nothing reaps processes (no init) (135-136) | unverifiable | the ENTRYPOINT is started with `setsid nohup`, not as PID 1 (`rollouter.py:609-633`); what PID 1 does in a Daytona sandbox is not in this code |
| 20 | runtime gates: empty run and reference solution (139-143) | unverifiable here | outside the harness code |
| 21 | listed paths are digested before the first action and before grading; any difference scores 0 (144-146) | holds | `rollouter.py:1270-1279`, `grading.py:283-299` |
| 22 | the sandbox is 1 vCPU / 2 GiB unless sized larger (147) | holds for the live fleet | `RUNBOOK.md:552-556,569-573`, `README_TERMINALWORLD.md:149-151`; code defaults are 2/4 (`daytona.py:772-781`); see m7 |
| 23 | one run-wide verifier limit, 600 s by default, which also covers the installs (148-149) | holds | `config_registry.py:163`, `rollouter.py:743-744,1036-1041`; the TW prep emits no `verifier_timeout_sec` (task.tomls declare 600–3600 s, ignored); one exec, `grading.py:338-343` |
| 24 | the evolve loop never rewrites a task below the solve rate (150-155) | holds for the live run | `RUNBOOK.md:821`; see observations |
| 25 | the bootstrap's form: apt/curl, uv installer, `uvx [-p] --with pytest==… --with pytest-json-ctrf==… pytest … /tests/test_state.py`, reward 1 iff exit 0 (156-160) | holds | 99 of 99 uvx and test_state.py; 98 apt; uv 0.9.7 ×95, 0.9.5 ×2, unversioned ×2; `-p 3.13` ×30; extra `--with` ×16; guard ×4, PATH export ×1; the reward comes from pytest's exit code in 99 of 99 |
| 26 | reward 1 exactly when the pytest run exits 0 (62-63) | holds (sparse) | `config_registry.py:171-173`, `RUNBOOK.md:658` |
| 27 | skip and xfail do not fail the run (66-67, 162-163) | holds | local `uvx --offline --with pytest==8.4.1`: skip exit 0, xfail 0, positive control 0 |
| 28 | a module-level raise at import fails the run (65) | holds | same probe: exit 2 |
| 29 | uvx: uv's own environment, image-pip packages not importable, cwd not on the import path (164-167) | holds | same probe: a module in cwd, imported by the test, gives exit 1; uvx runs in an ephemeral venv |
| 30 | `--with`/`-w` packages come from PyPI; a `file:///` spec loads from the image (158-159, 167-169) | holds (0 of 99 file:// specs) | uv semantics |
| 31 | with `-p`, uv downloads that Python from GitHub unless the image has it (380-381) | holds | uv semantics; the unpinned case is missing (m3) |

## Corpus claims, checked (item 2) — 11 of 11 hold

| # | claim (prompt line) | count over the 99 packages |
|---|---|---|
| C1 | the bootstrap is harness code the whole corpus shares, with small variations (56) | 92 are install/run/reward only; the other 7 add a `$PWD` guard (4), an `export PATH` (1), or a variant of the apt/curl line |
| C2 | it runs `apt-get update` and `apt-get install -y curl` (156) | 98; 1 (security-screened) uses the image's curl — covered by "read this task's lines" |
| C3 | it downloads the uv installer from astral.sh (157) | 99 |
| C4 | `uvx [-p] --with pytest==… --with pytest-json-ctrf==… … pytest … /tests/test_state.py` (157-158) | 99 (70 spell it `--with`, 29 `-w`) |
| C5 | it writes `1` when pytest exits 0, otherwise `0` (159) | 99 (98 test `$?` directly, 1 through `EXIT_CODE`) |
| C6 | tasks differ in the uv version, a `-p` pin, the extra packages, a guard line (159-160) | uv 0.9.7 ×95, 0.9.5 ×2, unversioned ×2; `-p 3.13` ×30; extra `--with` ×16; guard ×4 |
| C7 | the usual bootstrap's span is `5-14` (387) | exactly 5-14 in 50 (the plurality); 5-15 in 24, 3-12 in 10 |
| C8 | COPY/ADD lines bring build-context files into the image (19-24, 118-119) | 38 tasks (28 non-security), 86 files, 1 not text, largest 82,684 bytes; 0 ADD from a URL |
| C9 | a docker-compose file may sit beside the Dockerfile (119-120) | 9 ship one; 0 COPY it |
| C10 | `tests/` holds test.sh and test_state.py (126) | 99 have exactly these two files; 0 Dockerfiles mention `/tests` |
| C11 | "in this corpus also" a test that `pytest.skip`s or returns inside `except` (424-425) | 0 skip/skipif/xfail; 1 `except …: return` (security-screened) |

Negative checks behind the deletions: 0 of 99 bootstraps `cd`, use `set -e`, `pip`, `python -m pytest`, reward.json or
`mkdir /logs`. 99 of 99 Dockerfiles set a WORKDIR, and none of the 4 guard tasks sets it to `/`. 0 COPY their own
Dockerfile or `solve.sh`.

## Rule semantics (item 3) — unchanged in meaning

I diffed word by word myself (`scratchpad/rulediff.py`), section by section.
- **Against v13-rst:** A1 (4), A2 (3), A3 (3), A4 (2) and A6 (1) differ only by `test.sh` → `test_state.py`. B1 and
  A7 are word-identical. A3 also drops the "(marked, this corpus)" example. B3 drops "— the lines below the
  separator".
- **Against the base:** A3 equals the base A3 with the same three renames.

In v13-rst the glossary already defined the rules' "test.sh" as the verifier, and in the base it was the verifier, so
each rename keeps the rule's meaning. I also checked that nothing a rule attributes to "test.sh" is done by the TW
bootstrap. Across 99 of 99 bootstraps the only lines outside install/run/reward are the 4 `$PWD` guards, 1
`export PATH`, and the apt/curl line variants.

The six field descriptions (ops N1-N6) are file-name changes. One of them moved meaning outside the rule sections:
Step 6's evidence sentence (m1).

**The lead's scope ruling, verified against v2 `e6f20b43` byte for byte, with my own script (not the author's
derivation).**
- v2 has 35 bare `test.sh`, and 74666a72 has 0.
- 33 are renamed in place, found as single-token `test.sh` → `test_state.py` substitutions in a word diff. The other
  2 went with stage-1 ops: the retired mapping convention (v2:56) and the restated `candidate` entry (v2:86).
- Inside rule text, I applied only the rename (plus the two named removals) to each v2 section:
  - A1 (4), A2 (3), A3 (3), A4 (2) and A6 (1) come out byte-equal to 74666a72, so the rule-text renames total 13.
  - B1 and A7 have 0 renames and are byte-identical.
- A3 also drops the "(marked, this corpus)" example. A3 equals the base v13 A3 with the same 3 renames, byte for byte.
- B3 differs from v2 only by the removal of " — the lines below the file". The space after that sentence is now a
  line break, which is whitespace only.

Nothing else in rule text moved. **No rule-text or rule-meaning change beyond the ruling, so no blocker under item 3.**

## Kit (item 5)

- **Wrapper, tw_100135.** The three placeholders are filled and the render is 586 lines with no placeholder left. The
  wrapper lists 6 files in §1's order, which equals `harbor_files.read_list`. I checked all 99 wrappers the same way:
  0 differ, 86 copied files are listed and 1 is marked not text. Each names prompt `74666a72` and schema `0d37ee14`,
  and has a distinct output row and a single-id batch. `rows/` is empty.
- **Validate command.** Run exactly as written on `/usr/bin/python3` (jsonschema 3.2.0, which has no
  `Draft202012Validator`, so it falls back to `Draft7Validator`): a valid row prints `valid`, and two invalid rows
  (a leak on a SEED row; a glob in `protected_paths`) raise.
- **Draft-7 safety.** The schema uses only `$comment $id $schema additionalProperties allOf anyOf const enum if then
  items(object) maxLength minItems minLength minimum not pattern properties required type uniqueItems`. There is no
  2019/2020-only keyword. With jsonschema 4.26, `Draft7Validator` and `Draft202012Validator` gave identical error sets
  on my probe rows.
- **Author's checks, rerun.** `derive_v13_harbor21.py --check` prints "29 ops, 33 bare test.sh renamed, 87 assertions
  passed; … matches". `lint_selftest_harbor2.py` on `/usr/bin/python3` passes 0 misses: clean row ok, 15 mutations,
  and the B1-bootstrap row.
- **What each lint id tests against what the prompt demands.**
  - These match the prompt's demands: H0 (schema), H1 (material named in the files), H2/H2b/H12 (Step 0 census),
    H3 (Step 2 exclusivity and toolchain), H5 (Step 6 heredoc), H9 (outcomes, repro_expected, SEED probe), H10/H13
    (Step 2 inventory), H11 (anchor range and buggy_premise), H15, H16.
  - H7 matches 2db44a16's Step 6, not 74666a72's (m1).
  - HB covers the span, packages, pin and uv version, not the form (m10).
  - Nothing enforces the bootstrap boundary (M1).
- **Author claims (CHANGELOG, v2.1 entry), each checked.** All 12 hold:
  - 38 of 99 images and 86 files, 28 of the 38 non-security.
  - Failing statements go from 4 to 99 tasks and to 1,161 statements.
  - 0 of 99 for the bootstrap variants, `cd`, the trap and `mkdir /logs`.
  - 99 of 99 run pytest.
  - The run-wide 600 s limit.
  - 29 ops and 33 renames (13 in rules: A1 4, A2 3, A3 3, A4 2, A6 1).
  - A3 equals the base A3 renamed; B3 differs by the one removed phrase.
  - 87 assertions.
  - `$PWD` guard in 4, none of which can fire.
  - 99 of 99 crosscheck.
  - 86 files and 1 not text in the wrappers.
  - The 586-line render; all three superseded stagings are empty.

---

## Outside the prompt — a grading-path defect for the harness owner (not a judge matter)

`grade_tmax` creates `/tests` with `mkdir -p` and writes `test.sh` and the fixtures into it (`grading.py:242-254`).
Nothing clears the directory first, the pre-grade reset touches only reward.txt and ctrf.json (`:70-90`), and the
integrity baseline covers only a row's listed paths. pytest loads a `conftest.py` that sits beside the test file.

Local reproduction, `uvx --offline --with pytest==8.4.1`, cwd elsewhere:
- A `test_state.py` whose only test fails, with no conftest (the control), exits 1.
- The same file with a `conftest.py` beside it that calls `os._exit(0)` exits **0**. It still exits 0 with the
  bootstrap's own flags (`--with pytest-json-ctrf==0.3.5 … pytest --ctrf <file> … -rA`).

So a rollout that writes `/tests/conftest.py` before submitting would score 1 on every task on this grading path
(TerminalWorld, RST, TMAX), whatever the verifier checks. I did not reproduce this in a Daytona sandbox.

Fix: in `_pre_grade_command`, or before the uploads in `grade_tmax`, run `rm -rf /tests` and then `mkdir -p /tests`.
Do the same in `grade_tmax_daytona`.

The judge is right not to report this: it is corpus-wide, and fact 2 already rules out "the agent could overwrite X".

---

## Appendix — the briefed cut `9c7e5894…` (2db44a16), superseded and never staged

The brief's author claims for that cut hold:
- A1, A2, B1, A4, A6 and A7 are byte-identical to v2, and therefore word-identical to v13-rst.
- A3 is byte-identical to the base A3.
- B3 differs from v2 by the removal of "— the lines below the file" only.

The ruling behind 1d16136c (no mapping convention) removed its glossary convention, which glossed the rules' "test.sh"
as the verifier. Of the findings above:
- M1, m3, m6, m7, m8, m9 and m10 apply to it unchanged.
- m5 applies (its §1 names the copied files "for each task", but its §5 comment and the schema disagree the same way).
- m1 applies to Step 2 only, because its Step 6 kept the bootstrap-evidence clause.
- m2 does not apply (its fact 4 keeps the `cd` sentence).
- m4 applies to the header only.

---

## Delta check — v2.1.1 (`v13_harbor211_prompt.md` `ac3dde0e54c079bf…` at `f21db81f`), 2026-09-24

The same reviewer checked this against the findings above, read-only. The re-judge was launching in parallel; I did
not open `tw_v21/rows/`.

**Verdict: fit to judge TW. 0 blockers. All 11 findings are fixed as asked. 3 new minor residuals, none of which
changes a verdict.**

- **Rule sections.** A1, A2, A3, B1, B3, A4, A6 and A7 are byte-identical to v2.1 `74666a72` (my `rulediff.py`). A
  full `diff` of the prompt shows 12 hunks, one per op, and none of them is inside a rule section. The schema is
  unchanged (`0d37ee14`).
- **Text checks on the new file.**
  - Leftover wording: 0 hits, over the derivation's 19 strings plus "as a real", "bootstrap, whole" and "one of the
    files named at the top".
  - 0 bare `test.sh`.
  - `tests/test.sh` appears on 15 lines, which equal the derivation's allow-list.
  - The placeholders are exactly the three expected.
  - The render is 592 lines with no placeholder left.
  - `derive_v13_harbor21.py --check`: 134 assertions pass, and it compares the on-disk v2.1.1 file as well. Its
    summary line still says "v13-harbor2.1".
- **Kit.** All 99 wrappers name `ac3dde0e` and `0d37ee14`, list the read list unchanged, and each has its own label
  ending `v21-ac3dde0e:b####`. Each has an empty `scratch/<task_id>/` that the wrapper names, and a distinct output
  row. The schema's `agent` field is a free string, and the only other reader of `agent` copies it opaquely
  (`tb_training_mix/build/columns/make_agentic195_columns.py:336`). `lint_selftest_harbor2.py` passes with 0 misses,
  including the new HL and HB cases.

| finding | fixed by | checked |
|---|---|---|
| M1 | HL: a leak field, `unstable_reward` or `uncertain_fact` anchored in tests/test.sh | my three probes now give `['HL']`; the clean rows for all 75 non-security tasks lint clean |
| m1 | Step 6 `:494-497` restores the bootstrap-stop evidence; the glossary `:57-59` adds the exclusion basis | text as asked; H7 still allows bootstrap evidence only for that B1 case |
| m2 | fact 4 `:133-135` | as asked |
| m3 | Step 4b (i) `:384-387`, (ii) `:394-396` | as asked; consistent with the `-p` record line |
| m4 | header facts 1, 3, 4, 7, 9, 11; "as a real" and ", whole" gone | as asked |
| m5 | `evidence_file` comment `:557`; the candidate glossary line `:92` | as asked (the schema still also admits test.patch and bug.patch, which is harmless) |
| m6 | fact 1 `:117-118`, the multi-stage clause | as asked |
| m7 | fact 9 `:150-153` | as asked; B3's "1 vCPU and 2 GiB" reads consistently |
| m8 | a per-task `scratch/` in the wrapper and the stager | as asked |
| m9 | label `judge:harbor:tw:v21-ac3dde0e:b####` | as asked |
| m10 | HB requires `<command> ->` for each of apt-get, curl and uvx on the fetch lines | a names-only record gives `['HB']`; a prompt-form record gives 0 HB on all 75 non-security bootstraps (false-positive sweep) |

**New minor residuals.**
- **r1.** HL also fires on an `uncertain_fact` about the B1 bootstrap-stop case when it is anchored at the bootstrap
  line it names (probe on `tw_100135`). The prompt does not say where such a doubt is anchored. This is my own
  proposal's edge. At most 2 of 99 instructions could reach it.
  **Fix:** add to `uncertain_fact` (`:477`) "a doubt about the B1 bootstrap-stop case is anchored at the instruction
  line".
- **r2.** The lint does not require the restored bootstrap-stop evidence to be in tests/test.sh. A row that puts that
  evidence in tests/test_state.py lints clean. This is cosmetic and changes no verdict.
- **r3.** The `--check` summary line names only v2.1 while it also checks v2.1.1. This is cosmetic.

I did not re-measure the author's "HL fires on 81 of 99 v1 rows", because that would mean reading the v1 rows, which
include the security-screened ones.
