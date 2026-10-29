# Independent review — v13-rst judge prompt and kit

Reviewed (sha256, 2026-09-20): `v13_rst_prompt.md` 1b8368a5e8cc3a11, `stage_rst.py` 9f821e625ae9c495,
`lint_v13_rst.py` ea7f54fa94a7d9e4, `score_run_rst.py` d1c4a0e4ae5d2e63, `render_prompts_rst.py` 085664428b234b26,
`output_schema_v13_rst.json` 1cf0bd9b7a50c05d, `build_schema_v13_rst.py`, `DIFF_v13_to_v13rst.patch`, `README.md`,
`../v13_prompt.md` 96fbc03bc81ab876, `../validation/lint_v13.py`, `../build_schema_v13.py`,
`../../v12/candidates_extract.py`, the rollout contract, and the 181 staged tasks under `../tb21_top10_rst/`
(sidecars + candidate lists in bulk; `020ad168…`, `8b10590c…`, `6580a3ee…`, `7ac455a6…`, `3a84f68e…` and my two
own picks `67c42731…` and `6e710690…` in detail). Harness claims were checked against
`external/torchtitan-cotrain/torchtitan/experiments/rl/` (paths below are relative to that directory).
Note: `stage_rst.py` was rewritten at 19:04:54 while this review was running (`MAX_SHOW_*` 32768/400 → 65536/1500);
the digest above is what I read.

Counts: **1 blocker, 5 major, 7 minor.**

---

## F1 — blocker — fact 1, the glossary and A1 say a CMD's state is in the container; a CMD-only image's CMD never runs (97 of 181 staged tasks)

**Where.** `v13_rst_prompt.md:110` ("together with whatever the image's CMD/ENTRYPOINT creates (the rollouter starts it
detached…)"), `:103` ("as the Dockerfile and the started CMD/ENTRYPOINT leave it"), `:75` ("an in-image or
CMD/ENTRYPOINT-created executable…"), `:256` ("P in the image or the CMD/ENTRYPOINT state"). v13 said ENTRYPOINT in all
four places; the variant broadened it to CMD.

**What is wrong.** The rollouter starts only an ENTRYPOINT. `examples/tmax/prepare_rts_data.py:220-249`
(`_entrypoint_command`) builds the command from ENTRYPOINT and *appends* CMD as its arguments, and returns `None` when
there is no ENTRYPOINT; `:610-612` writes `metadata["entrypoint"]` only when that is non-None;
`examples/tmax/rollouter.py:1261-1264` starts it only `if sample.entrypoint`. Daytona's
`harness/sandbox/daytona.py:712-759` (`_declarative_image`) builds the image and never runs its CMD. So for a
Dockerfile with a CMD and no ENTRYPOINT **nothing is started**: no service, no daemon, no file that CMD would have
created exists during rollout. Measured on the staged set: 97 tasks are CMD-only, 2 have an ENTRYPOINT.

**Evidence of harm.** `tb21_top10_rst/tasks/rts_task_a4d77e401ddc2927ecc1f508/instruction.md:2` opens with a claim that
an Apache2 server "is installed and running on this system", while its Dockerfile's only starter is
`CMD ["/usr/sbin/httpd", "-DFOREGROUND"]` (staged `setup.sh`), which never runs. Under the prompt as written the judge
must accept the premise ("you do not speculate about whether it came up"); under the harness the premise is false in
the shipped state, which is a live candidate for the B1 shape already in the prompt ("a behaviour the task's premise
promises … that the shipped data does not produce"). The same error invents rollout-present material for A1/A3 on any
CMD that looks like a producer.

**Fix.** Replace `:110-111` (the second sentence of fact 1) with:

> is in the container during rollout. **A `CMD` alone never runs**: the rollouter starts only an `ENTRYPOINT` (with
> CMD appended as its arguments), detached, so an image whose Dockerfile has a CMD and no ENTRYPOINT starts nothing —
> a service, daemon or file that CMD would have created is absent during rollout, whatever the instruction assumes
> about it. Where there is an ENTRYPOINT you do not speculate about whether it came up.

and drop "CMD/" from `:75`, `:103` and `:256` (`an in-image or ENTRYPOINT-created executable…`; `as the Dockerfile and
the started ENTRYPOINT leave it`; `P in the image or the ENTRYPOINT state`).

---

## F2 — major — Step 2 disposes of every `--with` package as `grade_time_generated`; a `-w '<pkg> @ file:///<path>'` spec installs rollout-writable in-image code into the verifier

**Where.** `v13_rst_prompt.md:229` (`grade_time_generated` … "and everything the bootstrap installs or writes (uv,
pytest, the `--with` packages, `/logs/verifier/*`) — none of it exists during rollout"), reinforced by `:216-217`
("without further thought") and by the glossary redirect `:53-54` ("Where a rule says 'test.sh reads … ', in this
corpus that is the verifier"), which puts the bootstrap's own reads out of scope.

**What is wrong.** Two staged tasks install a package **from a path inside the image**, not from PyPI:
`tb21_top10_rst/tasks/rts_task_67c427311a79494dfe4d81df/tests/test.sh:21` is a `-w` spec of the form
`'<name> @ file:///tmp/<dir>'`, and that directory is created at build time by the Dockerfile
(same task, `setup.sh:14`, a `COPY … /tmp/…`). The verifier then imports the package
(`…/tests/test.sh:158`, `from earthsource import EarthSource`) and grades against it. The agent is root and can rewrite
that directory during rollout, so it is exactly the material A4 is about (the expectation is computed at grade time
from rollout-present material that is neither pinned nor regenerated) — and A1/A2/A3 must be asked of it too. The
prompt instead tells the judge to file it as `grade_time_generated`, "without further thought", and the redirect says
the bootstrap is not where rules look. `rts_task_f5340334eeced3161593edcb` is the same task shape (same `COPY … /tmp/earthsource_pkg/`, same `-w` spec, six
references in its verifier). Both pass the oracle gate, so both are live seeds today.

**Fix.** Replace the `grade_time_generated` cell at `:229` with:

> `grade_time_generated` | inputs, fixtures or helper files the verifier creates itself at grade time; and what the
> bootstrap downloads or writes (uv, pytest, the `--with` packages it resolves from PyPI, `/logs/verifier/*`) — none of
> that exists during rollout. **Exception: a `--with`/`-w` spec of the form `<name> @ file:///<path>` installs code from
> the image itself**; that path exists during rollout and the agent can rewrite it, so it is rollout material (A4 when
> the verifier's expectation depends on it, and A1/A2/A3 by their own tests), never `grade_time_generated`.

and add one sentence to fact 11(b) after `:160`: "A `-w '<name> @ file:///<path>'` spec loads code from the image, not
from PyPI: whatever the agent left at that path is what the verifier imports."

---

## F3 — major — the new A6 clause contradicts the sentence two lines below it

**Where.** `v13_rst_prompt.md:415-416` adds "in this corpus also a submission that omits an artefact whose tests
`pytest.skip`, or return inside `except`, when it is missing"; `:419` still reads "A submission that crashes or never
produces the requested artefact demonstrates nothing."

**What is wrong.** The new shape *is* a submission that never produces the requested artefact. A low-effort judge that
reads both sentences writes neither the note nor anything in its place; because a SEED row needs `pass_probe` **or** an
A6/A7 note (§5 R1, `build_schema_v13.py:102-106`), the row then fails the schema or is downgraded — the over-filtering
the variant exists to avoid. Surface: 1 staged verifier uses `pytest.skip`, 10 use `except …: return`.

**Fix.** Replace `:419` with:

> A submission that crashes demonstrates nothing, and neither does one that omits an artefact some assertion still
> checks: omission counts only where you name the test that skips, or returns inside `except`, instead of failing.

---

## F4 — major — a B1 anchored in the bootstrap (the variant's own new shape) trips inherited lint L7

**Where.** `v13_rst_prompt.md:336-338` tells the judge to "anchor the bootstrap line that fails"; Step 6 `:479-482`
makes `evidence`/`evidence_line` the decisive line; `../validation/lint_v13.py:169-179` (L7) requires that line to
contain one of `assert`, `exit 1`, `fail`, `raise`, `==`, `!=`, `<`, `>`, ` in `. `lint_v13_rst.py:34` runs the whole
v13 lint set unchanged.

**Evidence.** I built a row for `rts_task_3a84f68efb848c2f9199a04a` that uses the shape exactly as written (B1 anchored
at `tests/test.sh:4`, the bootstrap's apt line, every candidate disposed of, every failing statement covered,
`bootstrap_fetch` correct). It validates against `output_schema_v13_rst.json` with zero errors and lints as
`['L7']` — the only lint, and a false positive. The evidence line
(`tests/test.sh:4`) is an `apt-get`/`install` command and contains no assert token.

**Fix (code, `lint_v13_rst.py`, after the LR3 loop at line 56).** Add:

```python
    # L7 (v13) wants the evidence line to look like an assertion. The one B1 shape that anchors in the
    # bootstrap -- a state the instruction asks for that stops it -- points at a harness command, so L7
    # does not apply there; its range half is re-checked here.
    ev, ef = row.get("evidence_line"), row.get("evidence_file")
    if ("L7" in out and ef == "tests/test.sh" and isinstance(ev, int) and sep and ev < sep
            and row.get("overspecific_check") and not any(row.get(k) for k in BLOCKING_NOT_B1)
            and 1 <= ev <= len((task_files.get("tests/test.sh") or "").splitlines())):
        out.discard("L7")
```

and add to the module docstring: "L7 is suppressed for a B1 whose evidence is a bootstrap line (the counterpart of
LR3)."

---

## F5 — major — LR4 claims to catch a missing `--with` package and misses every quoted one, and never checks the `-p <version>` download

**Where.** `lint_v13_rst.py:9` ("LR4 bootstrap_fetch leaves out a kind of fetch, **or a --with package**, that the
bootstrap contains"), `:51-52`; the facts it compares against come from `stage_rst.py:227`.

**What is wrong.** The extraction regex at `stage_rst.py:227` requires the spec to start with `[A-Za-z0-9_.\-\[\]]`, so
a quoted spec is skipped entirely. Four staged bootstraps quote it: two with `'earthsource @ file:///…'`
(`rts_task_67c42731…`, `rts_task_f5340334…`), one with `"pypdf==5.1.0"` (`rts_task_db13bc15…`), one with
`"eth-typing<4"` (`rts_task_ff2d2fbc…`). Probe: a row for `rts_task_67c42731…` whose `bootstrap_fetch` names only
pytest, pytest-json-ctrf and jsonschema — omitting the fourth `-w` package **and** the `-p 3.13` interpreter download —
lints clean (no LR). Step 4b `:375-376` asks for both ("Name every `--with` / `-w` package"; "with `-p <version>` also a
Python build, from GitHub"), so the lint under-enforces its own rule. 60 staged bootstraps carry a `-p` pin.

**Fix (two places).** `stage_rst.py:227` →

```python
    specs = re.findall(r"(?:--with|-w)[ =]+('[^']*'|\"[^\"]*\"|[^\s\\]+)", joined)
    pk = [re.split(r"[=<>!~@ ]", s.strip("'\""), 1)[0] for s in specs]
```

and in `lint_v13_rst.py`, inside the `if bf:` block after `:52`:

```python
        if boot.get("python_pin") and boot["python_pin"] not in low:
            out.add("LR4")
```

---

## F6 — major — the new B1 "import of the agent's module" shape is speculative as written and invites over-flagging; fact 11(b) is incomplete about `python -m pytest`

**Where.** `v13_rst_prompt.md:334-336`: "an `import` of the agent's own module inside test_state.py when a faithful
solution **may** depend on a package the bootstrap's `--with` list lacks (fact 11b)".

**What is wrong.** (a) The condition is a possibility, not a predicate the three files decide, and B1's own test at
`:322-324` is generative ("write the deliverable you would write, and ask which assertion rejects it"). "May depend on
some package" is satisfiable for nearly any Python deliverable, so a low-effort judge can fire B1 on any verifier that
imports the agent's code. (b) The real, decidable defect is the opposite and stronger one: under `uvx` nothing puts the
agent's directory on the verifier's import path at all (measured: **0 of 181** verifiers touch `sys.path` or
`importlib`), so an unqualified import of agent code under `uvx` fails whatever the agent writes. (c) fact 11(b)
(`:157-160`) says the separation "does not exist" in the image-Python variants, which is right but incomplete: the 10
`pip` variants invoke `python -m pytest`, which puts the current directory on `sys.path`, while the `uvx` `pytest`
console script does not — that difference, not the interpreter, is what makes an agent module importable. Population:
12 tasks could import agent code (2 via the `file://` spec of F2, 10 via `python -m pytest`), and all 12 pass the
oracle gate today.

**Fix.** Replace the clause at `:334-336` with:

> an `import` of the agent's own module inside test_state.py under `uvx`, where nothing puts the agent's directory on
> the verifier's path and the module is not one of the bootstrap's `--with` packages, so the import fails whatever the
> agent writes — name the import line and the missing package; that some faithful solution *might* use a library the
> `--with` list lacks is not B1;

and append to fact 11(b) at `:160`: "`python -m pytest` also puts the current directory on the verifier's import path;
the `pytest` console script `uvx` runs does not."

---

## F7 — minor — fact 4's "`/app` when it has none" is false, and the narrowed B1 cwd clause ignores the bootstraps that `cd`

**Where.** `v13_rst_prompt.md:112-113` ("The image's default directory is the Dockerfile's last `WORKDIR` (`/app` when
it has none)"), `:122-123` (fact 4) and the B1 clause `:333-334` ("a cwd-relative path in the verifier when the
instruction puts the deliverable somewhere other than the last `WORKDIR`").

**What is wrong.** The WORKDIR part is right and measured (`rollouter.py:617-622`: "Docker starts ENTRYPOINT in the
image's own WORKDIR, and the container already puts every exec there — measured on four tasks"). The `/app` fallback is
not: it is `metadata.workdir`'s *data-prep* default (contract line 21), used only for `mkdir -p`
(`examples/tmax/grading.py:242-248`), never for a `cd`; the same source note says the guess "is wrong … exactly where
no Dockerfile WORKDIR exists". The corpus proves it: a staged bootstrap refuses to run when `"$PWD" = "/"` and tells the
author to set a WORKDIR (`tb21_top10_rst/tasks/rts_task_1112124465d5ae29c8a41169/tests/test.sh:21-23`). Separately, 4
staged bootstraps `cd` before running pytest — 2 into `/repo`, and 2 into `/tests` while the WORKDIR is `/app`
(`rts_task_22df3891…`, `rts_task_9b11e9cb…`, sidecar `bootstrap.cd`) — so the B1 clause names the wrong reference
directory for them even though fact 4 carries the "unless the bootstrap changes it" carve-out. Impact today is nil
(every staged Dockerfile sets a WORKDIR; 0 verifiers open a cwd-relative file), so this is a correctness fix, not a
behaviour change.

**Fix.** `:122-123` →

> Grading runs in place, as root, in the same container; the harness issues no `cd`, so cwd is the image's own default —
> the Dockerfile's last `WORKDIR`, and whatever the base image uses when the Dockerfile sets none (`/` on these images,
> which is why one bootstrap refuses to run there) — unless the bootstrap itself `cd`s, which a few do: read its lines
> before you rely on the directory.

`:112-113` → "The image's default directory is the Dockerfile's last `WORKDIR`." (delete the parenthesis.)
`:333-334` → "a cwd-relative path in the verifier when the deliverable's directory is not the one the verifier runs in —
the last `WORKDIR`, or the bootstrap's `cd` target when it has one (fact 4);"

---

## F8 — minor — Step 4b's example matches no bootstrap in the corpus, and LR2 cannot catch a judge who copies it

**Where.** `v13_rst_prompt.md:378-381` (`tests/test.sh:12-18 apt-get -> … uvx -> pypi.org: pytest, pytest-json-ctrf,
requests`).

**What is wrong.** In the usual bootstrap (26 lines, e.g. `rts_task_020ad1689cad3626497b56fe`) the fetches are at lines
11, 13 and 17-20, i.e. the span is 11-20, and `requests` is a `--with` package of only 3 tasks. LR2
(`lint_v13_rst.py:44-47`) only requires the judge's span to *overlap* the stager's, so `12-18` copied verbatim passes on
the standard bootstrap and is never flagged. Since the owner's requirement is a record written the same way on every
row, the example should be the corpus's real one and the invariant part should be copyable.

**Fix.** Replace `:379-381` with:

> `; ` — for the usual bootstrap (apt on one line, the uv installer `curl` on another, a `uvx` call continued over
> several lines) the record reads `tests/test.sh:11-20 apt-get -> distro mirrors: curl; curl -> astral.sh: uv 0.9.7;
> uvx -> pypi.org: pytest, pytest-json-ctrf`. Write that form verbatim, changing only this task's line numbers, its
> package list and any fetch it adds; never copy the example's numbers.

---

## F9 — minor — §1 promises each COPY'd context file verbatim; three staged tasks carry a truncated or stubbed one and the prompt never says what the marker means

**Where.** `v13_rst_prompt.md:14-17` ("each build-context file that a COPY/ADD line … brings into the image, under a
header line naming its source path") against `stage_rst.py:46,171-184`, which stubs a binary file and truncates a large
one to 65536 bytes / 1500 lines.

**Evidence.** `tb21_top10_rst/tasks/rts_task_6e7106906920194ed82eddd3/setup.sh:1016` is a header ending
"[first 1500 of 4054 lines shown, 111128 bytes in all]"; `rts_task_3496f3d0…` has a binary stub and `rts_task_2c112ac4…`
a truncated script (sidecar `dockerfile.context.copied_stubbed_or_truncated`). A judge told it read the file "IN FULL"
can rule out an oracle in a source file of which it saw 1500 of 4054 lines.

**Fix.** Append to the `setup.sh` line at `:17`: "; a file too large or not text appears as its header alone, or as its
first lines, and the header says so — what the header hides is material you have not read, so do not assert its
contents either way."

---

## F10 — minor — "The Dockerfile itself is not shown to the agent" is false for one staged task

**Where.** `v13_rst_prompt.md:113-114`.

**Evidence.** `tb21_top10_rst/tasks/rts_task_2fe3dafcc4e9992f4181115a/setup.sh:19` is `COPY Dockerfile /app/Dockerfile`
(its own comment says it is there to be inspected by the agent). The Dockerfile's text is then rollout-readable, which
changes any A3 question of the form "V is byte-present only in the Dockerfile".

**Fix.** `:113-114` → "The Dockerfile itself is not shown to the agent — unless one of its own COPY lines puts it in the
image, so read them — but the files it writes are."

---

## F11 — minor — the rendered prompt forbids opening `rows*` and then tells the judge to write its row there

**Where.** `render_prompts_rst.py:26` ("do not open any directory named rows*, results*, sidecar or validation*")
against `:29,36` (`{OUTPUT_PATH}` is `{run}/rows/{tid}.jsonl`, default `rows` dir at `:46`).

**Fix.** `:26` → "…you do not look for one: do not read any other judge's row, and do not open any directory named
results*, sidecar or validation*. The only file you write is your own row." (and keep the rest).

---

## F12 — minor — `score_run_rst.py` dies on a malformed row it has already classified as schema-invalid; one output key is mislabelled

**Where.** `score_run_rst.py:63-65,86` use `r["tier"]`, `r["verdict"]`, `r["expectation_source"]` on every parseable
row, including rows already listed in `bad_schema` (`:46`). A judge row missing one key (the schema requires all 31, so
this is exactly the failure mode being measured) raises `KeyError` and no `score.json` is written at all. Separately
`:78` names the key `b3_anchored_in_bootstrap` while LR3 fires for A1/A2/A3 as well as B3.

**Fix.** Use `r.get("tier", "MISSING")`, `r.get("verdict", "MISSING")`, `r.get("expectation_source", "MISSING")` at
`:63-65,86` and `strict()` at `:59`; rename the key (and the print at `:97`) to
`leak_or_b3_anchored_in_bootstrap`.

---

## F13 — minor — two latent stager hazards (0 tasks affected today), plus one cross-tab worth printing

**Where.** `stage_rst.py:96-113` (`instructions`) and `:163` (`rel.startswith("docker-compose")`).

(a) The heredoc branch fires on any line containing `<<WORD` and then skips forward to a line equal to `WORD`; if no
such line exists (a `<<` inside a quoted string, a here-string variant), the loop consumes the **rest of the
Dockerfile**, silently losing its COPY lines, WORKDIR and CMD. I checked all 181: no COPY line is currently lost.
Fix at `:101-106`:

```python
        if m:
            end = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j].strip() != end:
                j += 1
            if j < len(lines):          # only skip when the terminator really exists
                i = j
```

(b) `:163` drops any context file whose relpath starts with `docker-compose`, even when a COPY line brings it into the
image — the one case where the file *is* shipped state. No staged task COPYs one today (I checked all 12 that ship a
compose file). Fix: drop the compose test from the staging guard at `:163` (`if rel in used or not any(matched(rel, s)
for s in srcs): continue`) and leave it where it belongs, in the `present_but_never_copied` count at `:196-197`.

(c) Observation for the scorer, not a bug: all 7 `oracle_zero` tasks are among the 12 that ship a `docker-compose.yaml`
(and 3 of the 5 `not_in_gate_output` ones). The sidecar already records `dockerfile.context.compose_file`; printing it
against `gate` in `score_run_rst.py` costs one line and would tell the owner whether the gate failures are a
single-container artefact rather than task defects. I did not establish the mechanism — none of the three instructions
I checked mentions a sibling service.

---

## Checked and found correct

- **Staging shape.** setup.sh = 2 header lines + Dockerfile from line 3 (`stage_rst.py:43-44,157`); the context-file
  header's `setup.sh:<n+2>` is right (verified against the staged files: the header of `rts_task_3b6b5f1b…` says
  `setup.sh:38` and the COPY is staged line 38). Exactly one separator line in all 181 staged `tests/test.sh`, and none
  in any `setup.sh`; `separator_line`/`total` in the rendered prompt match the files
  (`render_prompts_rst.py:66-67`). No COPY/ADD source is dropped by the parser in any of the 181 (only
  `COPY Dockerfile`, which the judge reads anyway — F10).
- **Reward path.** Reward = first line of `/logs/verifier/reward.txt`, clamped [0,1] (`grading.py:151-157,349-359`);
  the harness deletes reward.txt/ctrf.json, clears immutable bits and writes a nonce **into reward.txt**
  (`:70-90,256-262`), and a surviving sentinel scores 0 (`:349-358`) — so the bootstrap's
  `[ ! -f reward.txt ] && echo 0` trap and a pre-written reward really do change nothing, exactly as fact 4 says.
  `reward.json` is never read; `ctrf.json` is diagnostics only (`:372-380`).
- **`set -e` variants** (44 staged) still resolve to "reward 1 iff the test run exits 0": a non-zero pytest aborts the
  script before the reward write, the trap finds the sentinel file present, and the sentinel scores 0.
- **Uploads.** `/tests/test.sh` and `/tests/test_state.py` arrive only at grade time (`grading.py:105-115,249-254`);
  `solution/` never reaches the sandbox (contract §5). No staged task has `environment/seeds/` or any extra `tests/`
  fixture, so dropping v13's seed language and the `needs: seeds` prefix is right.
- **Resources and budget.** All 181 task.tomls declare 1 cpu / 2048 MB (fact 9 understates: it is every task, not
  "almost every"); declared verifier timeouts are 600/900/1800 s, effective `max(declared, 600)` — fact 9's range is
  correct, and the installs do come out of it.
- **fact 11 variants** match the corpus: 167 `uvx`, 10 `pip` + image Python, 1 bare `pytest`, 3
  `python3 /tests/test_state.py`; 174 of 181 fetch, 7 do not; the no-fetch cases (venv activate, direct run) are the
  ones that legitimately get `bootstrap_fetch: null`.
- **Step 0's bootstrap carve-out** is consistent with the lints: `candidates_extract.FAIL_RE` lists a bootstrap
  `exit 1` in 10 tasks (all the "no WORKDIR" guard), L14 requires those lines to be covered, and Step 0 tells the judge
  to cover exactly them; `lint_v13.inventory()` starts at the first `def test_` so L2/L2b do not double-count.
  `/logs/...` and `/tests/...` never appear in any `programs` list, so the extra Step 2 entries are noise, not conflict.
- **Glossary pytest semantics** are right for this corpus: an escaping exception in a test or fixture, a module-level
  raise (collection error, exit 2) and `pytest.fail` all fail the run; skip/xfail do not. `check=True` appears in 28
  verifiers, `except …: return` in 10, `pytest.skip` in 1, `xfail` in 0 — the added text is well aimed, and nothing in
  it conflicts with `FAIL_RE` (the inventory is a subset the judge may extend).
- **A3's new "this corpus" example** is a correct application of v13's A3: commit history is already listed as an `A`,
  `git checkout` is "running an in-image program" and therefore never a core step, the deliverable is static so A1
  correctly defers to A3, and the closing sentence is the `derivation_is_core_step` escape. The shape is real: 30 staged
  Dockerfiles make a git commit and all 30 break a file afterwards without committing.
- **Schema.** `output_schema_v13_rst.json` on disk is byte-identical to what `build_schema_v13_rst.py` produces; 31
  properties in prompt order with `bootstrap_fetch` between `unstable_reward` and `expectation_movable`,
  `required == properties`, `additionalProperties: false`, the 23 v13 cross-field rules unchanged, and
  `bootstrap_fetch` in none of them. The prompt's §5 key list and §5 consistency paragraph agree with it.
- **LR1/LR2/LR3/LR5** do what the docstring claims; `lint_selftest_rst.py` passes (10/10) and its cases are the right
  ones. LR3's B1 exemption is implemented as documented.
- **Security gate.** `security_signals` unions `security_union` (values `0`/`1`), `is_security_shaped` (values
  `True`/`False`) from all three label files — each file really has that column — and the instruction screen, refuses
  to run without the screen, and excludes anything the screen did not cover (213 requested, 196 screened, 33 excluded,
  181 staged). No canary line survives into any staged `instruction.md`.
- Staged files are small enough to be read in full (median 12 KB / 294 lines across the three; largest 84 KB).
