#!/usr/bin/env python3
"""Derive v13-harbor-v2 from v13-rst by replacing ONLY its file contract.

v13-harbor v1 (prompt 4e9acb39…) was cut from the v13 TMAX base. That was the wrong cut: the base's harness
facts describe the TMAX path, while TerminalWorld runs the SAME grading path as RST -- `grade_tmax` uploads
tests/*, runs `bash /tests/test.sh` and reads /logs/verifier/reward.txt, and the verifier file is chosen by
whether tests/test_state.py exists, not by corpus. All 99 TW packages run `uvx … pytest /tests/test_state.py`
and write reward.txt. So v2 takes v13-rst's facts (1, 3, 4, 9, 11), its pytest glossary and Step 4b unchanged,
and changes only what v13-rst had to say about STAGED files: harbor corpora are read as native files.

    python3 derive_v13_harbor2.py [--check]
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__)); V13 = f"{HERE}/.."
SRC_PROMPT, SRC_SCHEMA = f"{V13}/rst/v13_rst_prompt.md", f"{V13}/rst/output_schema_v13_rst.json"
OUT_PROMPT, OUT_SCHEMA = f"{HERE}/v13_harbor2_prompt.md", f"{HERE}/output_schema_v13_harbor2.json"
SRC_PROMPT_SHA = "d905473d86f51b3ad4d3237154ea462ce37626d64a7617fa9f9391381494f186"
SRC_SCHEMA_SHA = "1cf0bd9b7a50c05d7b77ba94cfa0a834cdfbdd7a73b917b5a019c3e198fb1ef2"
ANCHOR_FILES = ["instruction.md", "tests/test.sh", "tests/test_state.py", "tests/test.patch",
                "environment/bug.patch", "environment/Dockerfile"]

EDITS = [
 ("the file contract: native files, no staging",
  """online evolve loop (§3 fact 10). For each task id in your batch you read three staged files IN FULL, in this order,
and nothing else about the task:""",
  """online evolve loop (§3 fact 10). For each task id in your batch you read the task's own files IN FULL, in this
order, and nothing else about the task:"""),
 ("the three staged paths become four native ones", None, None),   # filled below, it spans many lines
 ("the anchor rule",
  """`setup.sh` and `tests/test.sh` are staging names. Every anchor you write uses these three names and the line numbers
of the staged files, so a line of test_state.py is cited as `tests/test.sh:<its line in the staged file>`.""",
  """These are the task's own files, not staging names. Every anchor you write uses the path above and that file's own
line numbers, so a line of the verifier is cited as `tests/test_state.py:<its line>` and a line of the image build as
`environment/Dockerfile:<its line>`."""),
 ("glossary: bootstrap",
  "- **bootstrap** — the lines of the staged tests/test.sh above the separator line: harness code the whole corpus",
  "- **bootstrap** — the whole of tests/test.sh: harness code the whole corpus"),
 ("glossary: anchor",
  "- **anchor** — `file:line` or `file:from-to` in one of the three staged files, by its staging name",
  "- **anchor** — `file:line` or `file:from-to` in one of the files listed at the top, by its own path"),
 ("fact 11 opening",
  "11. **The bootstrap.** The lines of tests/test.sh above the separator are harness code. In its usual form it creates",
  "11. **The bootstrap.** tests/test.sh is harness code in its entirety. In its usual form it creates"),
 ("assertions field",
  """- `assertions`: one entry per statement of the verifier (tests/test.sh below the separator) that can fail the run —
  the glossary's list — in file order, as `L<line>: <what it grades>`, the line numbers being those of the staged""",
  """- `assertions`: one entry per statement of the verifier (tests/test_state.py) that can fail the run —
  the glossary's list — in file order, as `L<line>: <what it grades>`, the line numbers being those of that"""),
 ("B3 scope sentence",
  "separator. The bootstrap's fetches are never B3: they go to Step 4b, so that B3 keeps meaning \"this task's verifier",
  "file. The bootstrap's fetches are never B3: they go to Step 4b, so that B3 keeps meaning \"this task's verifier"),
 ("Step 4b opening",
  "(i) Read the bootstrap (tests/test.sh above the separator) and list, in file order, every command in it that reaches",
  "(i) Read the bootstrap (tests/test.sh) and list, in file order, every command in it that reaches"),
 ("the header sentence: this is no longer RST-only",
  "This is the v13 seed-audit rubric adapted to the RST corpus (Harbor-format tasks: a Dockerfile instead of a setup.sh,",
  "This is the v13 seed-audit rubric adapted to harbor-format corpora -- RST, TerminalWorld, and any corpus whose\ntask ships a Dockerfile instead of a setup.sh and whose verifier is pytest on tests/test_state.py (they share one\ngrading path: grade_tmax uploads tests/*, runs bash /tests/test.sh and reads /logs/verifier/reward.txt),"),
 ("the second anchor list, which still named setup.sh",
  "  (`instruction.md`, `setup.sh`, `tests/test.sh`). Every rule field you fill begins with its own.",
  "  (`instruction.md`, `environment/Dockerfile`, `tests/test.sh`, and the corpus's verifier companion). Every rule\n  field you fill begins with its own."),
 ("evidence_file in the output block",
  '"evidence_file": "instruction.md | setup.sh | tests/test.sh",',
  '"evidence_file": "one of the files named at the top of this prompt",'),
]
NEW_CONTRACT = """    {TASK_ROOT}/<task_id>/instruction.md            the specification the graded agent is shown
    {TASK_ROOT}/<task_id>/environment/Dockerfile    what the image bakes in before the agent starts, as a real
                                                    Dockerfile: read its RUN, COPY/ADD, WORKDIR, ENTRYPOINT and
                                                    CMD lines as fact 1 describes them
    {TASK_ROOT}/<task_id>/tests/test.sh             the harness bootstrap, whole (fact 11): it installs pytest and
                                                    runs the verifier, and writes the reward
{COMPANION_LINE}
This corpus ships no setup.sh and no staged copies: each file above is the task's own, read at its own path."""


def derive_prompt(src: str) -> str:
    out = src
    # the staged three-path block, from the first path line to the separator sentence, replaced wholesale
    start = out.index("    {TASK_ROOT}/<task_id>/instruction.md")
    end = out.index("(the task's tests/test_state.py, verbatim)") + len("(the task's tests/test_state.py, verbatim)")
    out = out[:start] + NEW_CONTRACT + out[end:]
    for what, a, b in EDITS:
        if a is None:
            continue
        assert out.count(a) == 1, f"{what}: matched {out.count(a)}"
        out = out.replace(a, b, 1)
    out = out.replace("three staged files", "the files").replace("the staged files", "the files")
    # the only staging words left may be MY OWN two sentences saying this corpus has no staging -- an
    # assertion that cannot tell the thing from the statement that the thing is absent is not an assertion
    allowed = {"This corpus ships no setup.sh and no staged copies: each file above is the task's own, read at "
               "its own path.",
               "These are the task's own files, not staging names. Every anchor you write uses the path above and "
               "that file's own",
               "task ships a Dockerfile instead of a setup.sh and whose verifier is pytest on tests/test_state.py "
               "(they share one"}
    left = [l for l in out.splitlines() if re.search(r"separator|staging|staged|setup\.sh", l) and l.strip() not in
            {x.strip() for x in allowed}]
    assert not left, f"staging wording survives: {left[:3]}"
    return out


def derive_schema(src: dict) -> dict:
    s = json.loads(json.dumps(src))
    s["properties"]["task_id"] = {"type": "string", "minLength": 1}
    pat = "^(" + "|".join(re.escape(f) for f in ANCHOR_FILES) + r"):[0-9]+(-[0-9]+)?\b"
    n = 0

    def rewrite(node):
        nonlocal n
        if isinstance(node, dict):
            if str(node.get("pattern", "")).startswith("^(instruction"):
                node["pattern"] = pat; n += 1
            for v in node.values():
                rewrite(v)
        elif isinstance(node, list):
            for v in node:
                rewrite(v)
    rewrite(s)
    assert n >= 11, f"rewrote only {n} anchor patterns"
    s["properties"]["evidence_file"]["enum"] = list(ANCHOR_FILES)
    print(f"   anchor patterns rewritten: {n}")
    return s


def main() -> int:
    assert hashlib.sha256(open(SRC_PROMPT, "rb").read()).hexdigest() == SRC_PROMPT_SHA, "v13_rst_prompt.md moved"
    assert hashlib.sha256(open(SRC_SCHEMA, "rb").read()).hexdigest() == SRC_SCHEMA_SHA, "the v13-rst schema moved"
    prompt = derive_prompt(open(SRC_PROMPT).read())
    text = json.dumps(derive_schema(json.load(open(SRC_SCHEMA))), indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        ok = open(OUT_PROMPT).read() == prompt and open(OUT_SCHEMA).read() == text
        print("on-disk v13-harbor2 matches this derivation" if ok else "MISMATCH")
        return 0 if ok else 1
    open(OUT_PROMPT, "w").write(prompt); open(OUT_SCHEMA, "w").write(text)
    for p in (OUT_PROMPT, OUT_SCHEMA):
        print(f"  {os.path.basename(p)}  sha256 {hashlib.sha256(open(p,'rb').read()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
