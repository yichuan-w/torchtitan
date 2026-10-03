#!/usr/bin/env python3
"""Derive v13-harbor2.1 from v13-harbor2: delete what was written for RST's staged files, restate what TerminalWorld needs.

v2 (prompt e6f20b43…) carried v13-rst's harness facts over correctly but kept passages written for RST's STAGED
file -- a verifier "below the separator", "the three files", assertions and literals located in tests/test.sh --
and RST-only claims (bootstrap variants 0 of 99 TW tasks use, an A3 example no TW task fits, "600 to 1,800 s"). It
also never told the judge about the build-context files a Dockerfile COPYs into the image (38 of 99 TW images hold
some). The review went to the lead on 2026-09-23; the ops below are the ones it proposed (scratch v21_ops.py,
sha256 81cdc185…) as the lead approved them, with the lead's two rulings applied:

  * G1 keeps the definitions of bootstrap and verifier and the one sentence on where tests/test.sh itself is cited,
    and drops the mapping convention ("where a rule says test.sh it means the verifier") -- the document should not
    need one;
  * so every file name that located the verifier in tests/test.sh, or called it test.sh, now names
    tests/test_state.py: six field descriptions (ops N1-N6) and every bare `test.sh` (stage 2). FILE NAMES ONLY --
    the rules' semantics are frozen, and the rule-section check below asserts that each rule section equals v2's
    with exactly those substitutions and the two named removals (A3's RST-only example, B3's broken phrase).

Stage 1 applies the ops in order; each marker must occur exactly once in the text at the moment it applies ("count"
ops: exactly the stated number). Stage 2 renames every bare `test.sh` (not preceded by `/` or a word character) to
`test_state.py`, and asserts the count. Then the checks: rule sections, the base-v13 A3, forbidden wording, the
allow-list of lines that may name tests/test.sh (the bootstrap itself), the placeholders.

    python3 derive_v13_harbor21.py [--check]
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__)); V13 = f"{HERE}/.."
SRC_PROMPT, SRC_SCHEMA = f"{HERE}/v13_harbor2_prompt.md", f"{HERE}/output_schema_v13_harbor2.json"
BASE_PROMPT = f"{V13}/v13_prompt.md"
OUT_PROMPT, OUT_SCHEMA = f"{HERE}/v13_harbor21_prompt.md", f"{HERE}/output_schema_v13_harbor21.json"
SRC_PROMPT_SHA = "e6f20b43ed6efb889f7ccd2afab66e6f848d8ae21b10b00bb1625865ce73befd"
SRC_SCHEMA_SHA = "013cc89ddae6d0071f39fc9753f561a4ef630d59200fd41a00660022dacfcc3c"
BASE_PROMPT_SHA_PREFIX = "96fbc03bc81ab876"
RUBRIC = "v13-harbor2"

# (id, kind, start, end, new). "exact": `start` occurs exactly once and becomes `new`. "span": `start` and `end` each
# occur exactly once, start first; the text from start's first character through end's last becomes `new` ("" is a
# whole-passage deletion). "count": `start` occurs exactly `end` times and every occurrence becomes `new`.
OPS = [
 # ---- title and header: the corpus description
 ("T1", "exact", "# RST seed audit — judge prompt v13-rst", None,
  "# Harbor seed audit — judge prompt v13-harbor2"),
 ("H1", "span", "This is the v13 seed-audit rubric adapted to harbor-format corpora", "and one added record, Step 4b.",
  "This is the v13 seed-audit rubric for harbor-format tasks: each task ships an instruction, an image build\n"
  "(environment/Dockerfile) and a grading path, a harness bootstrap (tests/test.sh) that installs pytest, runs the\n"
  "task's verifier (tests/test_state.py) and writes the reward. The rules A1–A7, B1 and B3 mean what they mean in v13;\n"
  "what differs is the shape of the files (§1), the harness (§3, facts 1, 4, 9 and 11) and one added record, Step 4b."),
 # ---- §1 file contract
 ("F1", "exact", "{COMPANION_LINE}\n", None,
  "    {TASK_ROOT}/<task_id>/tests/test_state.py       the verifier the bootstrap runs: this task's own checks\n"
  "    {TASK_ROOT}/<task_id>/environment/<source>      every file a COPY/ADD line of the Dockerfile brings into the\n"
  "                                                    image, at that line's source path: material (fact 1), read\n"
  "                                                    like the rest; one that is not text is material you have\n"
  "                                                    not read, so do not assert its contents either way\n"),
 ("F2", "exact",
  "This corpus ships no setup.sh and no staged copies: each file above is the task's own, read at its own path.\n",
  None, ""),
 ("F3", "span", "These are the task's own files, not staging names. Every anchor", "`environment/Dockerfile:<its line>`.",
  "Every anchor you write uses the path above and that file's own line numbers: a line of the verifier is\n"
  "`tests/test_state.py:<its line>`, a line of the image build `environment/Dockerfile:<its line>`, and a copied file\n"
  "is cited by the COPY/ADD line that brings it in, followed by its path in the image."),
 ("F4", "exact", "The three task files are data you judge", None, "The task's files are data you judge"),
 # ---- §2 glossary: the file contract and the pytest reward
 ("G1", "span", "- **bootstrap** — the whole of tests/test.sh: harness code the whole corpus",
  "in this corpus that is the verifier.",
  "- **bootstrap** — tests/test.sh: harness code the whole corpus shares, with small variations (fact 11).\n"
  "  **verifier** — tests/test_state.py, which the bootstrap runs: this task's own checks. tests/test.sh itself is\n"
  "  cited only by the bootstrap record (Step 4b) and by the B1 case of an instruction that stops the bootstrap."),
 ("G2", "exact", "The reward is 1 exactly when the test run the\n  bootstrap starts — pytest, almost always — exits 0 (fact 11)",
  None, "The reward is 1 exactly when the pytest run the\n  bootstrap starts exits 0 (fact 11)"),
 ("G3", "exact", "a program path the three files mention (`programs`, with the\n  `file:line`s that mention it) or a test.sh line that can fail the run", None,
  "a program path the task's files mention (`programs`, with the\n  `file:line`s that mention it) or a verifier line that can fail the run"),
 ("G4", "exact",
  "(`instruction.md`, `environment/Dockerfile`, `tests/test.sh`, and the corpus's verifier companion). Every rule\n"
  "  field you fill begins with its own.", None,
  "(`instruction.md`, `environment/Dockerfile`, `tests/test.sh`, `tests/test_state.py`). Every rule field you fill\n"
  "  begins with its own."),
 ("X1", "count", "the three files", 4, "the task's files"),
 # ---- §3 harness facts
 ("C1", "exact",
  "A build-context file is in\n   the image only where a COPY/ADD line puts it, and a file a later `RUN` deletes is gone. The image's default\n"
  "   directory is the Dockerfile's last `WORKDIR`.", None,
  "A build-context file is in\n   the image only where a COPY/ADD line puts it, and a file a later `RUN` deletes is gone; a docker-compose file\n"
  "   beside the Dockerfile is never used, and no service it declares exists. The image's default directory is the\n"
  "   Dockerfile's last `WORKDIR`."),
 ("C4", "span", "4. Grading runs in place, as root, in the same container; the harness issues no `cd`",
  "changes nothing you judge. `reward.json` is never read. Verifier stdout is discarded.",
  "4. Grading runs in place, as root, in the same container, and nothing changes directory before the verifier runs:\n"
  "   its cwd is the Dockerfile's last `WORKDIR`. The reward is the first line of `/logs/verifier/reward.txt`. The\n"
  "   harness deletes that file and plants a sentinel before it starts the bootstrap, and a sentinel still present\n"
  "   scores 0, so a reward the agent pre-wrote scores 0. `reward.json` is never read. Verifier stdout is discarded."),
 ("C9", "span", "9. The training sandbox is 1 vCPU and 2 GiB for almost every task", "also has to cover the bootstrap's installs.",
  "9. The training sandbox is 1 vCPU and 2 GiB unless the task was sized larger, so a wall-clock or throughput\n"
  "   threshold is not met reliably even by a correct solution. The verifier's time limit is one value for the whole\n"
  "   run, 600 s by default, and it also has to cover the bootstrap's installs."),
 ("C11", "span", "11. **The bootstrap.** tests/test.sh is harness code in its entirety.",
  "read which one this task has. Three consequences:",
  "11. **The bootstrap.** tests/test.sh is harness code. It runs `apt-get update` and `apt-get install -y curl`,\n"
  "    downloads the `uv` installer from astral.sh, and starts `uvx [-p <version>] --with pytest==… --with\n"
  "    pytest-json-ctrf==… [--with <package>] pytest … /tests/test_state.py`, which fetches pytest and every `--with` /\n"
  "    `-w` package from PyPI; then it writes the reward: `1` when pytest exits 0, otherwise `0`. Tasks differ in small\n"
  "    ways — the uv version, a `-p` pin, the extra packages, a guard line — so read this task's lines. Three\n"
  "    consequences:"),
 ("C11b", "span", "runs in the image as the agent left it. In\n        the variants that use the image's own Python",
  "whatever the agent left\n        at that path is what the verifier imports.",
  "runs in the image as the agent left it. The\n"
  "        `pytest` console script `uvx` runs does not put the current directory on the verifier's import path. A\n"
  "        `-w '<name> @ file:///<path>'` spec loads code from the image, not from PyPI: whatever the agent left at that\n"
  "        path is what the verifier imports."),
 # ---- §4: file-contract residue inside procedure text (no rule semantics change)
 ("S0a", "exact", ", the line numbers being those of that\n  tests/test.sh; consecutive", None, ";\n  consecutive"),
 ("S0b", "span", " A bootstrap\n  line gets an entry only when the inventory lists it,", "\"pytest exit code → reward\" lines get none.", ""),
 ("A3x", "span", "*Example (marked, this corpus):*", "that restoration is the core step and A3 does not fire.\n", ""),
 ("B3", "exact", "verifier — the lines below the\nfile. The bootstrap's", None, "verifier.\nThe bootstrap's"),
 ("4b", "exact", "`tests/test.sh:11-20 apt-get", None, "`tests/test.sh:5-14 apt-get"),
 ("4b2", "exact", "changing only this task's line numbers, its package list and any fetch it adds;", None,
  "changing only this task's line numbers, uv version, packages and any fetch it adds;"),
 # ---- §5 output label (schema const changes with it)
 ("O1", "exact", '"rubric_version": "v13-rst",', None, '"rubric_version": "v13-harbor2",'),
 # ---- the six field descriptions that located the verifier's lines in tests/test.sh: FILE NAME ONLY
 ("N1 last_assert_line", "exact", "- `last_assert_line`: the last statement in tests/test.sh that can fail the run", None,
  "- `last_assert_line`: the last statement in tests/test_state.py that can fail the run"),
 ("N2 literal_derivation", "exact", "string) against a constant written in tests/test.sh (a value", None,
  "string) against a constant written in tests/test_state.py (a value"),
 ("N3 evidence", "exact", "for A1–A3, B1 and B3 the decisive assertion in tests/test.sh (whose", None,
  "for A1–A3, B1 and B3 the decisive assertion in tests/test_state.py (whose"),
 ("N4 SEED pass_probe", "exact", "   tests/test.sh assertion (anchor first) that defeats it;", None,
  "   tests/test_state.py assertion (anchor first) that defeats it;"),
 ("N5 output last_assert_line", "exact", "line of the last statement in tests/test.sh that can fail the run>,", None,
  "line of the last statement in tests/test_state.py that can fail the run>,"),
 ("N6 output pass_probe", "exact", "anchor of the defeating tests/test.sh assertion", None,
  "anchor of the defeating tests/test_state.py assertion"),
]

ALLOWED_TEST_SH_LINES = [   # every line that may name tests/test.sh: the file list, the bootstrap, Step 4b's record
    '(environment/Dockerfile) and a grading path, a harness bootstrap (tests/test.sh) that installs pytest, runs the',
    '    {TASK_ROOT}/<task_id>/tests/test.sh             the harness bootstrap, whole (fact 11): it installs pytest and',
    '  `bash /tests/test.sh` after the agent submits: the bootstrap first, then pytest on `/tests/test_state.py`.',
    '- **bootstrap** — tests/test.sh: harness code the whole corpus shares, with small variations (fact 11).',
    "  **verifier** — tests/test_state.py, which the bootstrap runs: this task's own checks. tests/test.sh itself is",
    '  (`instruction.md`, `environment/Dockerfile`, `tests/test.sh`, `tests/test_state.py`). Every rule field you fill',
    '3. `tests/` is absent during rollout: `/tests/test.sh` and `/tests/test_state.py` are uploaded only after submission,',
    '11. **The bootstrap.** tests/test.sh is harness code. It runs `apt-get update` and `apt-get install -y curl`,',
    '(i) Read the bootstrap (tests/test.sh) and list, in file order, every command in it that reaches',
    '(ii) `bootstrap_fetch` = `tests/test.sh:<from>-<to>`, the span of those commands, then each fetch as',
    '`tests/test.sh:5-14 apt-get -> distro mirrors: curl; curl -> astral.sh: uv 0.9.7; uvx -> pypi.org: pytest,',
    '  "bootstrap_fetch": "<Step 4b, every task, no tier effect: \'tests/test.sh:<from>-<to> \' + each bootstrap fetch as \'<command> -> <host>: <what>\' joined by \'; \'; null only when the bootstrap reaches no host but loopback>",',
    'normally carries it. Its anchor must lie in tests/test.sh. Validate before writing.',
]

BARE_TEST_SH = re.compile(r"(?<![/\w])test\.sh\b")
BARE_TEST_SH_COUNT = 33   # every bare test.sh v2 has after stage 1: 13 in rules A1 A2 A3 A4 A6, 20 elsewhere

RULES = [("A1", "**A1 — deliverable-side oracle**", "**A2 — "), ("A2", "**A2 — verifier-side taint", "**A3 — "),
         ("A3", "**A3 — derived value**", "### Step 4 "), ("B1", "**B1 — over-specific check**", "**B3 — "),
         ("B3", "**B3 — unstable reward**", "### Step 4b"), ("A4", "**A4 — movable expectation**", "**A6 — "),
         ("A6", "**A6 — a wrong submission passes**", "**A7 — "), ("A7", "**A7 — unenforced core clause**", "**Ambiguity**")]
RULE_REMOVALS = {"A3": "A3x", "B3": "B3"}   # the only ops allowed to touch a rule section besides the file name

FORBIDDEN = ["separator", "setup.sh", "staged", "staging", "RST", "three files", "three task files", "companion",
             "COMPANION_LINE", "almost always", "a few do", "in its entirety", "usual form", "lines below",
             "(marked, this corpus)", "600 to 1,800", "in this corpus that is", "it means the verifier",
             "Where a rule or a field below"]   # the last two: the retired mapping convention
PLACEHOLDERS = {"{TASK_ROOT}", "{CANDIDATES_PATH}", "{OUTPUT_PATH}"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def section(text, start, stop):
    i = text.index(start)
    return text[i:text.index(stop, i + 1)]


def apply_ops(t, ops):
    n = 0
    for oid, kind, a, b, new in ops:
        if kind == "count":
            assert t.count(a) == b, f"{oid}: {a!r} occurs {t.count(a)} times, expected {b}"
            t = t.replace(a, new); n += 1; continue
        assert t.count(a) == 1, f"{oid}: start marker occurs {t.count(a)} times"
        if kind == "exact":
            t = t.replace(a, new, 1); n += 1; continue
        assert t.count(b) == 1, f"{oid}: end marker occurs {t.count(b)} times"
        i, j = t.index(a), t.index(b) + len(b)
        assert j > i, f"{oid}: end before start"
        t = t[:i] + new + t[j:]; n += 3
    return t, n


def rename(t):
    return BARE_TEST_SH.subn("test_state.py", t)


def derive_prompt(src: str):
    text, n = apply_ops(src, OPS)
    text, k = rename(text)
    if BARE_TEST_SH_COUNT is not None:
        assert k == BARE_TEST_SH_COUNT, f"stage 2 renamed {k} bare test.sh, expected {BARE_TEST_SH_COUNT}"; n += 1
    # rule sections: v2's, with the named removal (if any) and the file-name rename -- nothing else
    by_id = {o[0]: o for o in OPS}
    diff = {}
    for rid, start, stop in RULES:
        a, b = section(src, start, stop), section(text, start, stop)
        if rid in RULE_REMOVALS:
            assert RULE_REMOVALS[rid] in by_id, f"{rid}: the op removing its residue is missing"
            a, _ = apply_ops(a, [by_id[RULE_REMOVALS[rid]]])
        expect, renamed = rename(a)
        assert expect == b, f"{rid}: changed beyond its file names" + (" and its named removal" if rid in RULE_REMOVALS else "")
        diff[rid] = (renamed, rid in RULE_REMOVALS); n += 1
    base = open(BASE_PROMPT, "rb").read()
    assert sha(base).startswith(BASE_PROMPT_SHA_PREFIX), "v13_prompt.md moved"
    assert rename(section(base.decode(), *RULES[2][1:]))[0] == section(text, *RULES[2][1:]), \
        "A3 is not the v13 base A3 with the file name renamed"
    n += 2
    for w in FORBIDDEN:
        assert w not in text, f"leftover wording: {w!r}"; n += 1
    assert not BARE_TEST_SH.search(text), "a bare test.sh survives"; n += 1
    named = [l for l in text.splitlines() if "tests/test.sh" in l]
    extra = [l for l in named if l not in ALLOWED_TEST_SH_LINES]
    assert not extra, f"tests/test.sh named outside the allowed bootstrap mentions: {extra}"
    missing = [l for l in ALLOWED_TEST_SH_LINES if l not in named]
    assert not missing, f"allowed mention absent (stale allow-list): {missing}"
    n += 2
    ph = set(re.findall(r"\{[A-Z_]+\}", text))
    assert ph == PLACEHOLDERS, f"placeholders {sorted(ph)}"; n += 1
    return text, n, diff, k


def derive_schema(src: dict):
    s = json.loads(json.dumps(src)); n = 0
    assert s["$id"] == "rst-seed-audit-row-v13-rst"; s["$id"] = f"harbor-seed-audit-row-{RUBRIC}"; n += 1
    assert s["title"] == "RST seed audit row, rubric v13-rst"; s["title"] = f"Harbor seed audit row, rubric {RUBRIC}"; n += 1
    assert s["properties"]["rubric_version"] == {"const": "v13-rst"}
    s["properties"]["rubric_version"] = {"const": RUBRIC}; n += 1
    assert "v13-rst" not in json.dumps(s), "the schema still names v13-rst"; n += 1
    return s, n



# ---------------------------------------------------------------------------------------------------------------------
# v2.1.1: the ten minor findings of REVIEW_v13_harbor21_prompt.md (3ac74fa0), applied to the v2.1 text. None touches a
# rule section; the check below asserts all eight byte-identical to v2.1.
OUT_PROMPT_211 = f"{HERE}/v13_harbor211_prompt.md"
OPS_211 = [
 ('m4a header facts', "exact",
  "This is the v13 seed-audit rubric for harbor-format tasks: each task ships an instruction, an image build\n(environment/Dockerfile) and a grading path, a harness bootstrap (tests/test.sh) that installs pytest, runs the\ntask's verifier (tests/test_state.py) and writes the reward. The rules A1–A7, B1 and B3 mean what they mean in v13;\nwhat differs is the shape of the files (§1), the harness (§3, facts 1, 4, 9 and 11) and one added record, Step 4b.", None,
  "This is the v13 seed-audit rubric for harbor-format tasks: each task ships an instruction, an image build\n(environment/Dockerfile) and a grading path, a harness bootstrap (tests/test.sh) that installs pytest, runs the task's\nverifier (tests/test_state.py) and writes the reward. The rules A1–A7, B1 and B3 mean what they mean in v13; what\ndiffers is the shape of the files (§1), the harness (§3, facts 1, 3, 4, 7, 9 and 11) and one added record, Step 4b."),
 ("m4b 'as a real Dockerfile'", "exact",
  "what the image bakes in before the agent starts, as a real\n"
  "                                                    Dockerfile: read its RUN, COPY/ADD, WORKDIR, ENTRYPOINT and\n"
  "                                                    CMD lines as fact 1 describes them", None,
  "what the image bakes in before the agent starts: read its RUN,\n"
  "                                                    COPY/ADD, WORKDIR, ENTRYPOINT and CMD lines as fact 1\n"
  "                                                    describes them"),
 ("m4c 'bootstrap, whole'", "exact",
  "the harness bootstrap, whole (fact 11): it installs pytest and\n"
  "                                                    runs the verifier, and writes the reward", None,
  "the harness bootstrap (fact 11): it installs pytest, runs the\n"
  "                                                    verifier and writes the reward"),
 ("m1b glossary: where tests/test.sh is cited", "exact",
  "tests/test.sh itself is\n  cited only by the bootstrap record (Step 4b) and by the B1 case of an instruction that stops the bootstrap.", None,
  "tests/test.sh itself is\n  cited only by the bootstrap record (Step 4b), by the B1 case of an instruction that stops the bootstrap, and as the\n"
  "  `basis` of an `excluded_material` entry for a path only the bootstrap names."),
 ("m5b candidate: seen_in in a build-context file", "exact",
  "(The list also carries `imports`; you do not use it.)", None,
  "(The list also carries `imports`; you do not use it.)\n"
  "  A `seen_in` inside a build-context file is cited, in your fields, through the COPY/ADD line that brings it in (§1)."),
 ('m6 fact 1: multi-stage', "exact",
  "1. The image is built from the Dockerfile before the agent starts; everything its `RUN` lines, heredocs and COPY/ADD\n   lines create — compiled programs, generators, corpora, golden files, repositories with their history — is in the\n   container during rollout. **A `CMD` alone never runs**: the rollouter starts only an `ENTRYPOINT` (with CMD\n   appended as its arguments), detached, so an image whose Dockerfile has a CMD and no ENTRYPOINT starts nothing — a\n   service, daemon or file that CMD would have created is absent during rollout, whatever the instruction assumes\n   about it. Where there is an ENTRYPOINT you do not speculate about whether it came up. A build-context file is in\n   the image only where a COPY/ADD line puts it, and a file a later `RUN` deletes is gone; a docker-compose file\n   beside the Dockerfile is never used, and no service it declares exists. The image's default directory is the\n   Dockerfile's last `WORKDIR`. The network is open during rollout. The Dockerfile itself is not\n   shown to the agent — unless one of its own COPY lines puts it in the image, so read them — but the files it\n   writes are.", None,
  "1. The image is built from the Dockerfile before the agent starts; everything its `RUN` lines, heredocs and COPY/ADD\n   lines create — compiled programs, generators, corpora, golden files, repositories with their history — is in the\n   container during rollout (in a multi-stage Dockerfile, what the last stage holds, including what its `COPY --from`\n   lines bring into it). **A `CMD` alone never runs**: the rollouter starts only an `ENTRYPOINT` (with CMD appended as\n   its arguments), detached, so an image whose Dockerfile has a CMD and no ENTRYPOINT starts nothing — a service,\n   daemon or file that CMD would have created is absent during rollout, whatever the instruction assumes about it.\n   Where there is an ENTRYPOINT you do not speculate about whether it came up. A build-context file is in the image\n   only where a COPY/ADD line puts it, and a file a later `RUN` deletes is gone; a docker-compose file beside the\n   Dockerfile is never used, and no service it declares exists. The image's default directory is the Dockerfile's last\n   `WORKDIR`. The network is open during rollout. The Dockerfile itself is not shown to the agent — unless one of its\n   own COPY lines puts it in the image, so read them — but the files it writes are."),
 ('m2 fact 4: the cd', "exact",
  "4. Grading runs in place, as root, in the same container, and nothing changes directory before the verifier runs:\n   its cwd is the Dockerfile's last `WORKDIR`. The reward is the first line of `/logs/verifier/reward.txt`. The\n   harness deletes that file and plants a sentinel before it starts the bootstrap, and a sentinel still present\n   scores 0, so a reward the agent pre-wrote scores 0. `reward.json` is never read. Verifier stdout is discarded.", None,
  "4. Grading runs in place, as root, in the same container, and neither the harness nor the bootstrap changes directory\n   before the verifier runs: its cwd is the Dockerfile's last `WORKDIR` (a bootstrap that `cd`s would move it; read\n   its lines). The reward is the first line of `/logs/verifier/reward.txt`. The harness deletes that file and plants a\n   sentinel before it starts the bootstrap, and a sentinel still present scores 0, so a reward the agent pre-wrote\n   scores 0. `reward.json` is never read. Verifier stdout is discarded."),
 ('m7 fact 9: sizing the judge cannot see', "exact",
  "9. The training sandbox is 1 vCPU and 2 GiB unless the task was sized larger, so a wall-clock or throughput\n   threshold is not met reliably even by a correct solution. The verifier's time limit is one value for the whole\n   run, 600 s by default, and it also has to cover the bootstrap's installs.", None,
  "9. The training sandbox is 1 vCPU and 2 GiB for most tasks, and the files you read do not say which tasks get more, so\n   judge a threshold against 1 vCPU and 2 GiB, where a wall-clock or throughput threshold is not met reliably even by\n   a correct solution. The verifier's time limit is one value for the whole run, 600 s by default, and it also has to\n   cover the bootstrap's installs."),
 ('m3a Step 4b (i): the interpreter download', "exact",
  "(i) Read the bootstrap (tests/test.sh) and list, in file order, every command in it that reaches\na host other than loopback at grade time: `apt-get update` / `apt-get install` (the distribution's mirrors), the\n`curl` of the uv installer (astral.sh), `uvx` / `uv` resolving pytest and each `--with` / `-w` package (PyPI; with\n`-p <version>` also that Python build, from GitHub, unless the image has it), a `pip install` (PyPI), a\n`git clone`, any other download. A `-w '<name> @ file:///<path>'` spec is not a fetch (it is in-image material,\nStep 2).", None,
  "(i) Read the bootstrap (tests/test.sh) and list, in file order, every command in it that reaches\na host other than loopback at grade time: `apt-get update` / `apt-get install` (the distribution's mirrors), the\n`curl` of the uv installer (astral.sh), `uvx` / `uv` resolving pytest and each `--with` / `-w` package (PyPI; and a\nPython build from GitHub when `-p <version>` names one the image lacks, or when the image has no Python at all), a\n`pip install` (PyPI), a `git clone`, any other download. A `-w '<name> @ file:///<path>'` spec is not a fetch (it is\nin-image material, Step 2)."),
 ('m3b Step 4b (ii): the interpreter download without -p', "exact",
  "(ii) `bootstrap_fetch` = `tests/test.sh:<from>-<to>`, the span of those commands, then each fetch as\n`<command> -> <host>: <what it installs>`, joined by `; ` — for the usual bootstrap (apt on one line, the uv\ninstaller `curl` on another, a `uvx` call continued over several lines) the record reads\n`tests/test.sh:5-14 apt-get -> distro mirrors: curl; curl -> astral.sh: uv 0.9.7; uvx -> pypi.org: pytest,\npytest-json-ctrf`. Write that form, changing only this task's line numbers, uv version, packages and any fetch it adds;\nnever copy the example's numbers. Name every `--with` / `-w` package, and when the bootstrap pins an interpreter\nwith `-p <version>` add `python <version> -> github.com (uv downloads it unless the image has it)`. The field is\nnull only when the bootstrap reaches no such host (a pytest already in the image, run directly).", None,
  "(ii) `bootstrap_fetch` = `tests/test.sh:<from>-<to>`, the span of those commands, then each fetch as\n`<command> -> <host>: <what it installs>`, joined by `; ` — for the usual bootstrap (apt on one line, the uv\ninstaller `curl` on another, a `uvx` call continued over several lines) the record reads\n`tests/test.sh:5-14 apt-get -> distro mirrors: curl; curl -> astral.sh: uv 0.9.7; uvx -> pypi.org: pytest,\npytest-json-ctrf`. Write that form, changing only this task's line numbers, uv version, packages and any fetch it adds;\nnever copy the example's numbers. Name every `--with` / `-w` package, and when the bootstrap pins an interpreter\nwith `-p <version>` add `python <version> -> github.com (uv downloads it unless the image has it)`; without `-p`, add\n`python -> github.com (unless the image has one)` when the Dockerfile installs no Python and its base is not a Python\nimage. The field is null only when the bootstrap reaches no such host (a pytest already in the image, run directly)."),
 ('m1a Step 6: the bootstrap-stop evidence', "exact",
  '- `evidence`, `evidence_file`, `evidence_line`: for A1–A3, B1 and B3 the decisive assertion in tests/test_state.py (whose\n  outcome the reproduction changes); on a SEED row the assertion that defeats the `pass_probe`, or, when a note stands\n  in for it, the line that note anchors (an assertion for A6, the instruction clause for A7); for ambiguity the\n  instruction line.', None,
  '- `evidence`, `evidence_file`, `evidence_line`: for A1–A3, B1 and B3 the decisive assertion in tests/test_state.py\n  (whose outcome the reproduction changes), or, for the B1 case of an instruction that stops the bootstrap, the\n  tests/test.sh line that fails; on a SEED row the assertion that defeats the `pass_probe`, or, when a note stands in\n  for it, the line that note anchors (an assertion for A6, the instruction clause for A7); for ambiguity the\n  instruction line.'),
 ("m5a output: evidence_file", "exact",
  '"evidence_file": "one of the files named at the top of this prompt",', None,
  '"evidence_file": "instruction.md | environment/Dockerfile | tests/test.sh | tests/test_state.py",'),
]
FORBIDDEN_211 = FORBIDDEN + ["as a real", "bootstrap, whole", "unless the task was sized larger",
                             "one of the files named at the top", "nothing changes directory"]
ALLOWED_TEST_SH_LINES_211 = [   # v2.1's list, plus Step 6's bootstrap-stop evidence and the evidence_file enum
    "(environment/Dockerfile) and a grading path, a harness bootstrap (tests/test.sh) that installs pytest, runs the task's",
    '    {TASK_ROOT}/<task_id>/tests/test.sh             the harness bootstrap (fact 11): it installs pytest, runs the',
    '  `bash /tests/test.sh` after the agent submits: the bootstrap first, then pytest on `/tests/test_state.py`.',
    '- **bootstrap** — tests/test.sh: harness code the whole corpus shares, with small variations (fact 11).',
    "  **verifier** — tests/test_state.py, which the bootstrap runs: this task's own checks. tests/test.sh itself is",
    '  (`instruction.md`, `environment/Dockerfile`, `tests/test.sh`, `tests/test_state.py`). Every rule field you fill',
    '3. `tests/` is absent during rollout: `/tests/test.sh` and `/tests/test_state.py` are uploaded only after submission,',
    '11. **The bootstrap.** tests/test.sh is harness code. It runs `apt-get update` and `apt-get install -y curl`,',
    '(i) Read the bootstrap (tests/test.sh) and list, in file order, every command in it that reaches',
    '(ii) `bootstrap_fetch` = `tests/test.sh:<from>-<to>`, the span of those commands, then each fetch as',
    '`tests/test.sh:5-14 apt-get -> distro mirrors: curl; curl -> astral.sh: uv 0.9.7; uvx -> pypi.org: pytest,',
    '  tests/test.sh line that fails; on a SEED row the assertion that defeats the `pass_probe`, or, when a note stands in',
    '  "bootstrap_fetch": "<Step 4b, every task, no tier effect: \'tests/test.sh:<from>-<to> \' + each bootstrap fetch as \'<command> -> <host>: <what>\' joined by \'; \'; null only when the bootstrap reaches no host but loopback>",',
    '  "evidence_file": "instruction.md | environment/Dockerfile | tests/test.sh | tests/test_state.py",',
    'normally carries it. Its anchor must lie in tests/test.sh. Validate before writing.',
]


def derive_prompt_211(v21: str):
    text, n = apply_ops(v21, OPS_211)
    for rid, start, stop in RULES:
        assert section(v21, start, stop) == section(text, start, stop), f"v2.1.1 changed rule section {rid}"; n += 1
    for w in FORBIDDEN_211:
        assert w not in text, f"v2.1.1 leftover wording: {w!r}"; n += 1
    assert not BARE_TEST_SH.search(text), "v2.1.1: a bare test.sh"; n += 1
    named = [l for l in text.splitlines() if "tests/test.sh" in l]
    assert sorted(named) == sorted(ALLOWED_TEST_SH_LINES_211), \
        f"v2.1.1 tests/test.sh lines differ from the allow-list: {sorted(set(named) ^ set(ALLOWED_TEST_SH_LINES_211))}"
    n += 1
    assert set(re.findall(r"\{[A-Z_]+\}", text)) == PLACEHOLDERS, "v2.1.1 placeholders"; n += 1
    return text, n

def main() -> int:
    ps, ss = open(SRC_PROMPT, "rb").read(), open(SRC_SCHEMA, "rb").read()
    assert sha(ps) == SRC_PROMPT_SHA, "v13_harbor2_prompt.md moved"
    assert sha(ss) == SRC_SCHEMA_SHA, "output_schema_v13_harbor2.json moved"
    prompt, n_p, diff, k = derive_prompt(ps.decode())
    schema, n_s = derive_schema(json.loads(ss))
    stext = json.dumps(schema, indent=1, sort_keys=True) + "\n"
    n = 2 + n_p + n_s
    p211, n211 = derive_prompt_211(prompt)
    n += n211
    if "--check" in sys.argv:
        ok = open(OUT_PROMPT).read() == prompt and open(OUT_SCHEMA).read() == stext \
            and open(OUT_PROMPT_211).read() == p211
        print(f"{len(OPS)} ops, {k} bare test.sh renamed, {n} assertions passed; on-disk v13-harbor2.1 "
              + ("matches this derivation" if ok else "MISMATCH"))
        return 0 if ok else 1
    open(OUT_PROMPT, "w").write(prompt); open(OUT_SCHEMA, "w").write(stext); open(OUT_PROMPT_211, "w").write(p211)
    print(f"{len(OPS)} ops applied, {k} bare test.sh renamed to test_state.py, {n} assertions passed")
    for rid, (renamed, removal) in diff.items():
        print(f"   rule {rid}: {renamed} file-name substitutions" + ("  + its named removal" if removal else ""))
    print(f"   v2.1.1: {len(OPS_211)} ops on v2.1, every rule section byte-identical to v2.1")
    for p in (OUT_PROMPT, OUT_PROMPT_211, OUT_SCHEMA):
        print(f"  {os.path.basename(p)}  sha256 {sha(open(p, 'rb').read())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
