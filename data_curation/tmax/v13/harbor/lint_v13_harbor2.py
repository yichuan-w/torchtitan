#!/usr/bin/env python3
"""Row lint for rubric v13-harbor2 (prompt v13_harbor211_prompt.md): native harbor files, no sidecar, no separator.

lint_v13_rst.py cannot run on harbor rows: it needs the RST stager's sidecar and splits tests/test.sh at the staged
separator. This lint reads what the judge read -- harbor_files.read_list -- and the v21 candidate list, and carries
the v13 row lints over to the harbor file contract. Same numbers as ../validation/lint_v13.py where the check is the
same, prefixed H; helpers are imported from it, not copied.

  H0   the row fails output_schema_v13_harbor21.json
  H1   a path a blocking field, A4 or an exclusion names occurs in none of the task's files (toolchain and
       grade_time_generated exempt, as in L1)
  H2   last_assert_line is not a line of tests/test_state.py at or after the last failing statement, or no
       `assertions` entry covers it
  H2b  a failing statement of the inventory (tests/test_state.py) is covered by no `assertions` entry
  H3   an excluded path is also named by A1/A2/A3, or a `toolchain` entry names a program the Dockerfile builds or
       instruction.md names
  H5   a backslash-n inside a quoted python -c in repro_cmd
  H7   evidence_line is not a line of evidence_file; or a blocking rule fired and the evidence is a verifier line with
       no assertion token; or the evidence is a bootstrap line although the row is not the B1 case of an instruction
       that stops the bootstrap (overspecific_check anchored in tests/test.sh, no leak field)
  H9   the trace: outcome suffixes, repro_expected against the first fired rule, and a SEED pass_probe anchored at a
       failing tests/test_state.py entry
  H10  a program the Dockerfile builds or marks executable, named by instruction.md, is recorded nowhere
  H11  an anchor is out of its file's range, or names a file the task does not have; buggy_premise cites the verifier
  H12  an `assertions` entry numbers a line past the end of tests/test_state.py
  H13  a `programs` candidate is disposed of nowhere
  H15  a derivation_is_core_step basis does not cite the core step
  H16  protected_paths guards (as L16)
  HB   bootstrap_fetch: null although the bootstrap fetches; its span is not a range of tests/test.sh covering every
       fetch line; it omits a --with / -w package, the `-p` interpreter, or the uv installer's version; or it does not
       write each of apt-get, curl and uvx that the bootstrap's fetch lines run in the command part (before ` -> `) of an
       entry, arguments allowed
  HL   a leak field (A1-A3), unstable_reward or uncertain_fact is anchored in tests/test.sh: the bootstrap is harness
       code, its fetches go to Step 4b only, and tests/test.sh is cited only by Step 4b, the B1 bootstrap-stop case and
       an exclusion's basis (REVIEW_v13_harbor21_prompt.md M1; v1's most common defect)

lint(task_dir, row, candidates) -> sorted ids. CLI, ids only, nothing from the task files is printed:
    lint_v13_harbor2.py --tasks <tasks_dir> --candidates <candidates_dir> <row.jsonl>...
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "validation"))
sys.path.insert(1, os.path.join(HERE, "..", "tools"))   # the same lint_v13.py in the PR layout, data_curation/tmax/v13/tools
sys.path.insert(0, HERE)
import lint_v13 as base  # noqa: E402
import harbor_files as hf  # noqa: E402
import candidates_harbor as ch  # noqa: E402

SCHEMA = os.path.join(HERE, "output_schema_v13_harbor21.json")
ANCHOR_RE = re.compile(r"^(instruction\.md|environment/Dockerfile|tests/test\.sh|tests/test_state\.py|tests/test\.patch|"
                       r"environment/bug\.patch):(\d+)(?:-(\d+))?\b")
FETCH_RE = re.compile(r"\bapt-get\s+(?:update|install)\b|astral\.sh|\buvx?\b|\bpip3?\s+install\b|\bgit\s+clone\b|\bwget\b")
UV_VERSION_RE = re.compile(r"astral\.sh/uv/([0-9][0-9.]*)/install")
PIN_RE = re.compile(r"(?:^|\s)(?:-p|--python)[ =]+([0-9][0-9.]*)")


def anchor_of(text):
    m = ANCHOR_RE.match(str(text or ""))
    return (m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None) if m else None


def bootstrap_facts(text):
    """Fetch lines, --with packages, the -p pin and the uv installer version of one tests/test.sh."""
    lines = text.splitlines()
    fetch = [i for i, l in enumerate(lines, 1) if not l.lstrip().startswith("#") and FETCH_RE.search(l)]
    # a uvx call continued over several lines fetches through its last line
    for i in list(fetch):
        j = i
        while j <= len(lines) and lines[j - 1].rstrip().endswith("\\"):
            j += 1
        fetch += range(i + 1, j + 1)
    joined = re.sub(r"\\\n", " ", text)
    uv = UV_VERSION_RE.search(text)
    pin = PIN_RE.search(" ".join(l for l in joined.splitlines() if re.search(r"\buvx?\b", l)))
    commands = [c for c in ("apt-get", "curl", "uvx")
                if any(re.search(rf"(?<![\w-]){re.escape(c)}\b", lines[i - 1]) for i in fetch)]
    return {"fetch_lines": sorted(set(fetch)), "with": ch.with_packages(text), "commands": commands,
            "pin": pin.group(1) if pin else None, "uv_version": uv.group(1) if uv else None}


def schema_errors(row):
    import jsonschema
    V = getattr(jsonschema, "Draft202012Validator", None) or jsonschema.Draft7Validator  # same verdicts on this schema
    v = V(json.load(open(SCHEMA)))
    return sorted(v.iter_errors(row), key=lambda e: list(e.path))


def lint(task_dir, r, candidates=None):
    v = set()
    texts = hf.load_texts(task_dir)
    alltext = "\n".join(texts.values())
    ver = texts[hf.VERIFIER].splitlines()
    lens = {k: len(x.splitlines()) for k, x in texts.items()}
    cand = candidates if candidates is not None else ch.extract(task_dir)
    built = base.built_paths(texts[hf.DOCKERFILE])
    tfiles = {"instruction.md": texts[hf.INSTRUCTION]}   # the shape base.mislabelled_toolchain reads

    if schema_errors(r):
        v.add("H0")

    def in_files(p):
        if p.startswith("instruction.md:") or p.startswith("http"):
            return True
        b = p.split("*")[0].rstrip("/")
        bn = os.path.basename(b)
        return bool(b) and (b in alltext or (len(bn) > 3 and bn in alltext))

    # H1
    for k in ("oracle_reachable", "expectation_revealed", "expectation_movable"):
        if r.get(k):
            ps = [p for p in base.paths_in(r.get(k)) if p.startswith("/")]
            if not ps or any(not in_files(p) for p in ps):
                v.add("H1")
    if r.get("value_derivable"):
        vd = base.paths_in(r.get("value_derivable"))
        if not vd or not any(in_files(p) for p in vd):
            v.add("H1")
    for e in r.get("excluded_material") or []:
        exn, raw = str(e.get("exclusion")), str(e.get("path", ""))
        if exn == "grade_time_generated" or (exn == "toolchain" and not base.mislabelled_toolchain(raw, tfiles, built)):
            continue
        if not any(in_files(p) for p in (base.paths_in(raw) or [raw])):
            v.add("H1")

    # H2 / H2b / H12 -- the census, on tests/test_state.py
    inv = [int(f["line"]) for f in cand.get("failing_statements") or []]
    lal = r.get("last_assert_line")
    spans = base.spans_of(r.get("assertions"))
    if inv and (not isinstance(lal, int) or lal < max(inv) or lal > len(ver)):
        v.add("H2")
    if isinstance(lal, int) and not base.covered(lal, spans):
        v.add("H2")
    if any(not base.covered(x, spans) for x in inv):
        v.add("H2b")
    if any(b > len(ver) for _a, b in spans):
        v.add("H12")

    # H3
    ex = set()
    for e in r.get("excluded_material") or []:
        ex.update(base.paths_in(str(e.get("path", ""))) or [str(e.get("path", ""))])
        if str(e.get("exclusion")) == "toolchain" and base.mislabelled_toolchain(str(e.get("path", "")), tfiles, built):
            v.add("H3")
    for k in base.LEAK:
        if any(p in ex for p in base.paths_in(r.get(k)) if p.startswith("/")):
            v.add("H3")

    # H5
    if re.search(r'python3? -c "[^"]*\\n', r.get("repro_cmd") or ""):
        v.add("H5")

    # H7
    ef, el = r.get("evidence_file"), r.get("evidence_line")
    src = texts.get(ef, "").splitlines()
    b1_bootstrap = bool(r.get("overspecific_check")) and not any(r.get(k) for k in base.LEAK) \
        and (anchor_of(r.get("overspecific_check")) or ("",))[0] == hf.BOOTSTRAP
    if not isinstance(el, int) or el < 1 or el > len(src):
        v.add("H7")
    else:
        if any(r.get(k) for k in base.BLOCKING) and ef == hf.VERIFIER \
                and not any(t in src[el - 1].lower() for t in base.ASSERT_TOKENS):
            v.add("H7")
        if ef == hf.BOOTSTRAP and not b1_bootstrap:   # on any tier: only that B1 case has its evidence in the bootstrap
            v.add("H7")

    # HL -- the bootstrap is harness code: no leak, no B3 and no uncertain_fact is anchored in it (Step 4b (iii),
    # B3's scope sentence, the glossary's permitted citations of tests/test.sh)
    for k in tuple(base.LEAK) + ("unstable_reward", "uncertain_fact"):
        a = anchor_of(r.get(k))
        if a and a[0] == hf.BOOTSTRAP:
            v.add("HL")

    # H9
    outcomes = []
    for a in r.get("assertions") or []:
        m = base.OUTCOME_RE.search(str(a))
        outcomes.append(m.group(1) if m else None)
    exp = r.get("repro_expected")
    if r.get("tier") == "UNSURE":
        if any(o is not None for o in outcomes):
            v.add("H9")
    elif any(o is None for o in outcomes):
        v.add("H9")
    if r.get("repro_cmd"):
        if (exp == "reward==1" and "fail" in outcomes) or (exp == "reward==0" and "fail" not in outcomes):
            v.add("H9")
    if r.get("tier") == "SEED" and r.get("pass_probe"):
        pa = anchor_of(r.get("pass_probe"))
        fail_spans = [s for s, o in zip(spans, outcomes) if o == "fail"]
        if "fail" not in outcomes or not pa or pa[0] != hf.VERIFIER or not base.covered(pa[1], fail_spans):
            v.add("H9")
    elif r.get("tier") == "SEED" and "fail" in outcomes:
        v.add("H9")
    fired = [k for k in base.BLOCKING if r.get(k)]
    if fired and r.get("repro_cmd"):
        want = {"overspecific_check": {"reward==0"},
                "unstable_reward": {"reward-varies", "external-fetch"}}.get(fired[0], {"reward==1"})
        if exp not in want:
            v.add("H9")

    # H10
    recorded = set(ex) | {p for k in base.RULE_FIELDS for p in base.paths_in(r.get(k))}
    for p in built:
        if p in texts[hf.INSTRUCTION] and p not in recorded and not any(
                p == q or p.startswith(q.rstrip("/") + "/") for q in recorded):
            v.add("H10")

    # H11
    def in_range(a):
        f, s, e = a
        return f in lens and 1 <= s <= lens[f] and (e is None or s <= e <= lens[f])
    for k in base.ANCHORED + ("pass_probe",):
        if r.get(k):
            a = anchor_of(r.get(k))
            if not a or not in_range(a):
                v.add("H11")
    for e in r.get("excluded_material") or []:
        a = anchor_of(e.get("basis"))
        if not a or not in_range(a):
            v.add("H11")
        elif e.get("exclusion") == "buggy_premise" and a[0] != hf.VERIFIER:
            v.add("H11")

    # H13
    disposed = set(ex) | {p for k in base.RULE_FIELDS for p in base.paths_in(r.get(k))}
    for c in cand.get("programs") or []:
        p = str(c.get("path", ""))
        if p and p not in disposed and not any(
                p == q or p.startswith(q.rstrip("/") + "/") or q.startswith(p.rstrip("/") + "/") for q in disposed):
            v.add("H13")

    # H15
    core_words = base.words(r.get("core_step"))
    for e in r.get("excluded_material") or []:
        if str(e.get("exclusion")) == "derivation_is_core_step":
            m = re.search(r"core_step:\s*(.+)$", str(e.get("basis", "")), re.S)
            if not m or len(base.words(m.group(1)) & core_words) < 2:
                v.add("H15")

    # H16
    pins = [str(p).rstrip("/") for p in (r.get("protected_paths") or [])]
    a4 = str(r.get("expectation_movable") or "")
    writes = {str(e.get("path", "")).rstrip("/") for e in r.get("excluded_material") or []
              if str(e.get("exclusion")) in base.AGENT_WRITES}
    for p in pins:
        if p not in a4 or not in_files(p) or p in writes or any(q != p and q.startswith(p + "/") for q in pins) \
                or (p + "/") in alltext:
            v.add("H16")

    # HB -- the bootstrap record against the bootstrap itself
    facts = bootstrap_facts(texts[hf.BOOTSTRAP])
    bf = r.get("bootstrap_fetch")
    if facts["fetch_lines"]:
        a = anchor_of(bf)
        if not bf or not a or a[0] != hf.BOOTSTRAP or not in_range(a):
            v.add("HB")
        else:
            lo, hi = a[1], a[2] or a[1]
            low = str(bf).lower()
            if lo > min(facts["fetch_lines"]) or hi < max(facts["fetch_lines"]):
                v.add("HB")
            if any(w.lower() not in low for w in facts["with"]):
                v.add("HB")
            if facts["pin"] and f"python {facts['pin']}" not in low:
                v.add("HB")
            if facts["uv_version"] and facts["uv_version"] not in low:
                v.add("HB")
            # the record's form, Step 4b (ii): each fetching command appears in the command part (before " -> ") of
            # some "; "-separated entry. The command may carry its arguments ("uvx -p 3.13 -> pypi.org: ...").
            heads = [seg.split(" -> ", 1)[0] for seg in low.split("; ") if " -> " in seg]
            if any(not any(re.search(rf"(?<![\w-]){re.escape(c)}\b", h) for h in heads) for c in facts["commands"]):
                v.add("HB")
    return sorted(v)


def main(argv):
    if len(argv) < 5 or argv[0] != "--tasks" or argv[2] != "--candidates":
        print(__doc__); return 2
    tasks, cdir, files = argv[1], argv[3], argv[4:]
    bad = 0
    for f in files:
        row = json.loads(open(f).readline())
        tid = row.get("task_id") or os.path.basename(f).split(".")[0]
        cp = os.path.join(cdir, f"{tid}.json")
        hits = lint(os.path.join(tasks, tid), row, json.load(open(cp)) if os.path.exists(cp) else None)
        bad += bool(hits)
        print(f"{tid}: {' '.join(hits) if hits else 'ok'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
