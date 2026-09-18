#!/usr/bin/env python3
"""Post-hoc row lints L1-L15 of DESIGN.md section h (rubric v12), implemented exactly as stated there.

lint(task_files, row, candidates=None) -> sorted list of lint ids that fired. task_files is {"instruction.md": str,
"setup.sh": str, "tests/test.sh": str}; candidates is the parsed {CANDIDATES_PATH} inventory for the task (L13, L14
are skipped when it is None). Only token / line-number checks are performed on the task files; nothing is printed
from them.

CLI: lint_v12.py --tasks <tasks_dir> [--candidates <candidates_dir>] <row.jsonl>...
     (prints "<task_id>: <lints or ok>" per row, ids only)
"""
import json
import os
import re
import sys

ANCHOR_RE = re.compile(r"^(instruction\.md|setup\.sh|tests/test\.sh):(\d+)(?:-(\d+))?\b")
UIP_RE = re.compile(r"^(yes|no|unknown): (instruction\.md|setup\.sh|tests/test\.sh):(\d+)(?:-(\d+))?")
PATH_RE = re.compile(r"(?<![\w.])(/[A-Za-z0-9_./*+-]+|instruction\.md:\d+|https?://[^\s'\")]+)")
ENTRY_RE = re.compile(r"^L(\d+)(?:-(\d+))?: (.*)$", re.S)
OUTCOME_RE = re.compile(r" \| (pass|fail|skip)\s*$")
FAIL_STMT_RE = re.compile(r"^\s*assert\b|\bexit 1\b|pytest\.fail|sys\.exit\(1\)|^\s*raise\b")
TIMING_TOKENS = ("time.time", "perf_counter", "monotonic", "elapsed", "execution_time", "duration", "wall_clock", "wallclock", " seconds", "threshold")
ASSERT_TOKENS = ("assert", "exit 1", "fail", "raise", "==", "!=", "<", ">", " in ")

# The lint's half of the one-line switch of rule B2 / prompt section 3 fact 8: modules the base image provides beyond the
# Python standard library and pytest. Empty until the base-image probe answers; adding a name here removes it from
# the dependency inventory D, so a row that omits it is clean and a row that still holds it fires L8.
BASE_IMAGE_MODULES: set[str] = set()

LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
A_FIELDS = LEAK + ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")
B_FIELDS = ("overspecific_check", "env_mismatch", "unstable_reward")
RULE_FIELDS = A_FIELDS + B_FIELDS + ("ambiguity", "trivial")
ANCHORED = RULE_FIELDS + ("unconfirmed_dependency", "uncertain_fact")

# L8 alias table: import name -> tokens that count as an install line naming it (prefix match on tokens)
ALIASES = {"cv2": ["opencv"], "sklearn": ["scikit-learn", "scikit_learn"], "yaml": ["pyyaml"], "PIL": ["pillow"],
           "bs4": ["beautifulsoup4", "bs4"], "dateutil": ["python-dateutil"], "redis": ["redis"], "flask": ["flask"],
           "requests": ["requests"], "numpy": ["numpy"], "scipy": ["scipy"], "pandas": ["pandas"]}
try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:  # older interpreters
    STDLIB = set()


def paths_in(value):
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for v in value:
            out += paths_in(v.get("path") if isinstance(v, dict) else v)
        return out
    return [p.rstrip(".,;:)'\"") for p in PATH_RE.findall(str(value))]


def anchor_of(text):
    m = ANCHOR_RE.match(str(text or ""))
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None


def spans_of(assertions):
    spans = []
    for a in assertions or []:
        m = ENTRY_RE.match(str(a))
        if m:
            spans.append((int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1))))
    return spans


def covered(line, spans):
    return any(a <= line <= b for a, b in spans)


def inventory(test_lines):
    """Failing statements (1-based lines) from the first `def test_` on (whole file when there is none)."""
    first_test = next((i + 1 for i, l in enumerate(test_lines) if re.match(r"^\s*def test_", l)), 1)
    return [i + 1 for i, l in enumerate(test_lines) if i + 1 >= first_test and FAIL_STMT_RE.search(l)]


def imported_modules(test_text):
    mods = set()
    for m in re.finditer(r"^\s*import\s+([A-Za-z_][\w\.]*)(?:\s*,\s*([A-Za-z_][\w\.]*))*", test_text, re.M):
        for g in m.groups():
            if g:
                mods.add(g.split(".")[0])
    for m in re.finditer(r"^\s*from\s+([A-Za-z_][\w\.]*)\s+import", test_text, re.M):
        mods.add(m.group(1).split(".")[0])
    return {m for m in mods if m not in STDLIB and m != "pytest" and m not in BASE_IMAGE_MODULES}


def install_tokens(setup_text):
    joined = re.sub(r"\\\n", " ", setup_text)
    toks = set()
    for line in joined.splitlines():
        if re.search(r"\bpip3?\s+install\b|\bapt-get\s+install\b|\bapt\s+install\b|\bconda\s+install\b", line):
            for t in re.split(r"\s+", line.strip()):
                t = t.strip("\"'").lower()
                if t:
                    toks.add(t)
                    if t.startswith("python3-"):
                        toks.add(t[len("python3-"):])
    return toks


def installed(module, toks):
    names = [module.lower()] + [a.lower() for a in ALIASES.get(module, [])]
    for t in toks:
        base = re.split(r"[=<>\[]", t)[0]
        if any(base == n or base.startswith(n + "-") or base.startswith(n + "_") for n in names):
            return True
    return False


def task_module(module, task_files):
    """A module that is part of the task itself (setup.sh or instruction.md names <module>.py or a <module>/ package)."""
    text = task_files["setup.sh"] + "\n" + task_files["instruction.md"]
    return re.search(r"\b" + re.escape(module) + r"\.py\b|/" + re.escape(module) + r"/", text) is not None


def dependency_set(task_files):
    toks = install_tokens(task_files["setup.sh"])
    return {m for m in imported_modules(task_files["tests/test.sh"]) if not installed(m, toks) and not task_module(m, task_files)}


def built_paths(setup_text):
    """Absolute paths setup.sh compiles (-o /path) or marks executable (chmod +x /path)."""
    joined = re.sub(r"\\\n", " ", setup_text)
    built = set()
    for line in joined.splitlines():
        if re.search(r"\b(gcc|cc|g\+\+|clang|rustc|go build)\b", line):
            for m in re.finditer(r"-o\s+(/\S+)", line):
                built.add(m.group(1))
        m = re.search(r"\bchmod\s+\+x\s+(.+)$", line)
        if m:
            for p in m.group(1).split():
                if p.startswith("/"):
                    built.add(p)
    return built


def mislabelled_toolchain(path_text, task_files, built):
    """A `toolchain` entry may not name a program setup.sh builds/chmod +x'es or instruction.md names by path."""
    for p in paths_in(path_text) or [str(path_text)]:
        if not p.startswith("/"):
            continue
        if p in built or any(p == q or p.startswith(q.rstrip("/") + "/") for q in built):
            return True
        if p in task_files["instruction.md"]:
            return True
    return False


def words(text):
    return {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(text or ""))}


def lint(task_files, r, candidates=None):
    v = set()
    t = task_files
    alltext = "\n".join(t.values())
    test = t["tests/test.sh"]
    test_lines = test.splitlines()
    lens = {k: len(x.splitlines()) for k, x in t.items()}

    def in_files(p):
        if p.startswith("instruction.md:") or p.startswith("http"):
            return p.split(":")[0] in alltext or p in alltext or p.startswith("instruction.md:")
        base = p.split("*")[0].rstrip("/")
        if not base:
            return False
        if base in alltext:
            return True
        bn = os.path.basename(base)
        return bool(bn) and len(bn) > 3 and bn in alltext

    built = built_paths(t["setup.sh"])

    # L1 -- paths named by the floor fields and the exclusions occur in the three files; the toolchain exemption does
    # not cover a path setup.sh builds or instruction.md names (L3 rejects that entry)
    for k in ("oracle_reachable", "expectation_revealed", "expectation_movable"):
        if r.get(k):
            ps = [p for p in paths_in(r.get(k)) if p.startswith("/")]
            if not ps or any(not in_files(p) for p in ps):
                v.add("L1")
    if r.get("value_derivable"):
        vd = paths_in(r.get("value_derivable"))
        if not vd or not any(in_files(p) for p in vd):
            v.add("L1")
    for e in r.get("excluded_material") or []:
        exn = str(e.get("exclusion"))
        raw = str(e.get("path", ""))
        if exn == "grade_time_generated" or (exn == "toolchain" and not mislabelled_toolchain(raw, t, built)):
            continue
        toks = paths_in(raw) or [raw]
        if not any(in_files(p) for p in toks):
            v.add("L1")

    # L2 / L2b -- the assertion census
    inv = inventory(test_lines)
    lal = r.get("last_assert_line")
    spans = spans_of(r.get("assertions"))
    if inv and (not isinstance(lal, int) or lal < max(inv) or lal > len(test_lines)):
        v.add("L2")
    if isinstance(lal, int) and not covered(lal, spans):
        v.add("L2")
    if any(not covered(x, spans) for x in inv):
        v.add("L2b")

    # L3 -- excluded paths disjoint from A1/A2/A3 paths; no mislabelled `toolchain` entry
    ex = set()
    for e in r.get("excluded_material") or []:
        ex.update(paths_in(str(e.get("path", ""))) or [str(e.get("path", ""))])
        if str(e.get("exclusion")) == "toolchain" and mislabelled_toolchain(str(e.get("path", "")), t, built):
            v.add("L3")
    for k in LEAK:
        if any(p in ex for p in paths_in(r.get(k)) if p.startswith("/")):
            v.add("L3")

    # L4 -- no secondary clause names an A-field path
    floor_paths = [p for k in A_FIELDS for p in paths_in(r.get(k)) if p.startswith("/")]
    for s in r.get("secondary_clauses_unenforced") or []:
        if any(p in str(s) for p in floor_paths):
            v.add("L4")

    # L5 -- no backslash-n inside a quoted python -c
    rc = r.get("repro_cmd") or ""
    if re.search(r'python3? -c "[^"]*\\n', rc):
        v.add("L5")

    # L6 -- an A5 'no:' cites a non-timing tests/test.sh line
    uip = str(r.get("untouched_image_passes") or "")
    if uip.startswith("no:"):
        m = UIP_RE.match(uip)
        if m and m.group(2) == "tests/test.sh":
            ln = int(m.group(3))
            if 1 <= ln <= len(test_lines) and any(tok in test_lines[ln - 1].lower() for tok in TIMING_TOKENS):
                v.add("L6")

    # L7 -- evidence_line in range; an assertion line when an A rule (not A7-only), B1 or B3 fired
    ef, el = r.get("evidence_file"), r.get("evidence_line")
    src_lines = t.get(ef, "").splitlines()
    if not isinstance(el, int) or el < 1 or el > max(len(src_lines), 1):
        v.add("L7")
    else:
        strong = any(r.get(k) for k in LEAK + ("expectation_movable", "weak_verifier_exploit", "overspecific_check", "unstable_reward")) or uip.startswith("yes:")
        if strong and ef == "tests/test.sh":
            line = src_lines[el - 1].lower()
            if not any(tok in line for tok in ASSERT_TOKENS):
                v.add("L7")

    # L8 -- the dependency inventory
    D = dependency_set(t)
    imported = imported_modules(test)
    held_text = " ".join(str(r.get(k) or "") for k in ("unconfirmed_dependency", "env_mismatch"))
    for m in D:
        if not re.search(r"\b" + re.escape(m) + r"\b", held_text):
            v.add("L8")
    if r.get("unconfirmed_dependency"):
        named = {m for m in imported if re.search(r"\b" + re.escape(m) + r"\b", str(r["unconfirmed_dependency"]))}
        if not named or any(m not in D for m in named):
            v.add("L8")

    # L9 -- the trace and the single-rule binding of repro_expected
    outcomes = []
    for a in r.get("assertions") or []:
        m = OUTCOME_RE.search(str(a))
        outcomes.append(m.group(1) if m else None)
    exp = r.get("repro_expected")
    if r.get("repro_cmd"):
        if exp in ("reward==1", "reward==1-unenforced"):
            if any(o is None for o in outcomes) or "fail" in outcomes:
                v.add("L9")
        elif exp == "reward==0":
            if "fail" not in outcomes:
                v.add("L9")
    if r.get("tier") == "PASS":
        if "fail" not in outcomes or any(o is None for o in outcomes):
            v.add("L9")
        else:
            pa = anchor_of(r.get("pass_probe"))
            fail_spans = [s for s, o in zip(spans_of(r.get("assertions")), outcomes) if o == "fail"]
            if not pa or pa[0] != "tests/test.sh" or not covered(pa[1], fail_spans):
                v.add("L9")
    fired = [k for k in A_FIELDS + B_FIELDS if r.get(k)] + (["A5"] if uip.startswith("yes:") else [])
    if len(fired) == 1 and r.get("repro_cmd") and not str(exp or "").startswith("none:"):
        k = fired[0]
        want = {"core_clause_unenforced": {"reward==1-unenforced"}, "overspecific_check": {"reward==0"}, "env_mismatch": {"reward==0"},
                "unstable_reward": {"reward-varies", "external-fetch"}}.get(k, {"reward==1"})
        if exp not in want:
            v.add("L9")

    # L10 -- programs setup.sh builds or marks executable that instruction.md names are inventoried
    recorded = set(ex) | {p for k in RULE_FIELDS for p in paths_in(r.get(k))}
    for p in built:
        if p in t["instruction.md"] and p not in recorded and not any(p == q or p.startswith(q.rstrip("/") + "/") for q in recorded):
            v.add("L10")

    # L11 -- anchors in range; buggy_premise cites tests/test.sh
    def in_range(a):
        f, s, e = a
        return 1 <= s <= lens.get(f, 0) and (e is None or s <= e <= lens.get(f, 0))
    for k in ANCHORED:
        if r.get(k):
            a = anchor_of(r.get(k))
            if not a or not in_range(a):
                v.add("L11")
    if uip:
        m = UIP_RE.match(uip)
        if not m or not in_range((m.group(2), int(m.group(3)), int(m.group(4)) if m.group(4) else None)):
            v.add("L11")
    if r.get("pass_probe"):
        a = anchor_of(r.get("pass_probe"))
        if not a or not in_range(a):
            v.add("L11")
    for e in r.get("excluded_material") or []:
        a = anchor_of(e.get("basis"))
        if not a or not in_range(a):
            v.add("L11")
        elif e.get("exclusion") == "buggy_premise" and a[0] != "tests/test.sh":
            v.add("L11")

    # L12 -- the hold is the only channel: a held module may not reappear in uncertain_fact or in an A5 `unknown:`
    reroute = str(r.get("uncertain_fact") or "")
    if uip.startswith("unknown:"):
        reroute += " " + uip
    for m in D:
        if re.search(r"\b" + re.escape(m) + r"\b", reroute):
            v.add("L12")

    # L13 / L14 -- the runner's candidate inventory is disposed of and its failing lines are covered
    if candidates:
        disposed = set(ex) | {p for k in RULE_FIELDS for p in paths_in(r.get(k))}
        for c in candidates.get("programs") or []:
            p = str(c.get("path", ""))
            if not p:
                continue
            if p in disposed:
                continue
            if any(p == q or p.startswith(q.rstrip("/") + "/") or q.startswith(p.rstrip("/") + "/") for q in disposed):
                continue
            v.add("L13")
        for f in candidates.get("failing_statements") or []:
            if not covered(int(f.get("line", 0)), spans):
                v.add("L14")

    # L15 -- a `derivation_is_core_step` exclusion cites the core step it performs
    core_words = words(r.get("core_step"))
    for e in r.get("excluded_material") or []:
        if str(e.get("exclusion")) != "derivation_is_core_step":
            continue
        basis = str(e.get("basis", ""))
        m = re.search(r"core_step:\s*(.+)$", basis, re.S)
        if not m:
            v.add("L15")
            continue
        cited = words(m.group(1))
        if len(cited & core_words) < 2:
            v.add("L15")
    return sorted(v)


def load_task(tasks_dir, tid):
    d = os.path.join(tasks_dir, tid)
    out = {}
    for name in ("instruction.md", "setup.sh", "tests/test.sh"):
        p = os.path.join(d, name)
        out[name] = open(p, errors="replace").read() if os.path.exists(p) else ""
    return out


def load_candidates(cand_dir, tid):
    if not cand_dir:
        return None
    p = os.path.join(cand_dir, tid + ".json")
    return json.load(open(p)) if os.path.exists(p) else None


def main(argv):
    if len(argv) < 3 or argv[0] != "--tasks":
        print(__doc__)
        return 2
    tasks_dir = argv[1]
    argv = argv[2:]
    cand_dir = None
    if argv and argv[0] == "--candidates":
        cand_dir, argv = argv[1], argv[2:]
    bad = 0
    for f in argv:
        line = open(f).readline()
        row = json.loads(line)
        tid = row.get("task_id") or os.path.basename(f).split(".")[0]
        hits = lint(load_task(tasks_dir, tid), row, load_candidates(cand_dir, tid))
        bad += bool(hits)
        print(f"{tid}: {' '.join(hits) if hits else 'ok'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
