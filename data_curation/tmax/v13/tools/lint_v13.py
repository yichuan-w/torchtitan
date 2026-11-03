#!/usr/bin/env python3
"""Post-hoc row lints for rubric v13. Same ids as v12 where the check survives; retired with their fields: L4 (secondary
clauses), L6 (A5), L8 and L12 (dependency hold). New: L16, the guards on protected_paths.

lint(task_files, row, candidates=None) -> sorted list of lint ids that fired. task_files is {"instruction.md": str,
"setup.sh": str, "tests/test.sh": str}; candidates is the parsed {CANDIDATES_PATH} inventory for the task (L13, L14
are skipped when it is None). Only token / line-number checks are performed on the task files; nothing is printed
from them.

CLI: lint_v13.py --tasks <tasks_dir> [--candidates <candidates_dir>] <row.jsonl>...
     (prints "<task_id>: <lints or ok>" per row, ids only)
"""
import json
import os
import re
import sys

ANCHOR_RE = re.compile(r"^(instruction\.md|setup\.sh|tests/test\.sh):(\d+)(?:-(\d+))?\b")
PATH_RE = re.compile(r"(?<![\w.])(/[A-Za-z0-9_./*+-]+|instruction\.md:\d+|https?://[^\s'\")]+)")
ENTRY_RE = re.compile(r"^L(\d+)(?:-(\d+))?: (.*)$", re.S)
OUTCOME_RE = re.compile(r" \| (pass|fail|skip)\s*$")
FAIL_STMT_RE = re.compile(r"^\s*assert\b|\bexit 1\b|pytest\.fail|sys\.exit\(1\)|^\s*raise\b")
ASSERT_TOKENS = ("assert", "exit 1", "fail", "raise", "==", "!=", "<", ">", " in ")

LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
BLOCKING = LEAK + ("overspecific_check", "unstable_reward")
NOTES = ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")
RULE_FIELDS = BLOCKING + NOTES + ("ambiguity",)
ANCHORED = RULE_FIELDS + ("uncertain_fact",)
AGENT_WRITES = ("expected_edit", "benign")


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

    # L1 -- paths named by the blocking fields, A4 and the exclusions occur in the three files; the toolchain exemption does
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

    # L5 -- no backslash-n inside a quoted python -c
    rc = r.get("repro_cmd") or ""
    if re.search(r'python3? -c "[^"]*\\n', rc):
        v.add("L5")

    # L7 -- evidence_line in range; an assertion line when a blocking rule fired
    ef, el = r.get("evidence_file"), r.get("evidence_line")
    src_lines = t.get(ef, "").splitlines()
    if not isinstance(el, int) or el < 1 or el > max(len(src_lines), 1):
        v.add("L7")
    else:
        strong = any(r.get(k) for k in BLOCKING)
        if strong and ef == "tests/test.sh":
            line = src_lines[el - 1].lower()
            if not any(tok in line for tok in ASSERT_TOKENS):
                v.add("L7")

    # L9 -- the trace: outcomes under the row's probe, and the binding of repro_expected to the rule reproduced
    outcomes = []
    for a in r.get("assertions") or []:
        m = OUTCOME_RE.search(str(a))
        outcomes.append(m.group(1) if m else None)
    exp = r.get("repro_expected")
    if r.get("tier") == "UNSURE":
        if any(o is not None for o in outcomes):  # no probe on an UNSURE row: entries carry no suffix
            v.add("L9")
    elif any(o is None for o in outcomes):
        v.add("L9")
    if r.get("repro_cmd"):
        if exp == "reward==1":
            if "fail" in outcomes:
                v.add("L9")
        elif exp == "reward==0":
            if "fail" not in outcomes:
                v.add("L9")
    if r.get("tier") == "SEED":
        if any(o is None for o in outcomes):
            v.add("L9")
        elif r.get("pass_probe"):
            pa = anchor_of(r.get("pass_probe"))
            fail_spans = [s_ for s_, o in zip(spans_of(r.get("assertions")), outcomes) if o == "fail"]
            if "fail" not in outcomes or not pa or pa[0] != "tests/test.sh" or not covered(pa[1], fail_spans):
                v.add("L9")
        elif "fail" in outcomes:  # a note stands in for the probe: the submission it describes fails nothing
            v.add("L9")
    fired = [k for k in BLOCKING if r.get(k)]  # BLOCKING is in rank order: the reproduction belongs to the first one fired
    if fired and r.get("repro_cmd"):
        want = {"overspecific_check": {"reward==0"}, "unstable_reward": {"reward-varies", "external-fetch"}}.get(fired[0], {"reward==1"})
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
    # L16 -- protected_paths: each entry is named by the A4 note, occurs in the three files, is not a path the agent is
    # asked to write, and is not a directory above another entry (globs and working roots are refused by the schema)
    pins = [str(p).rstrip("/") for p in (r.get("protected_paths") or [])]
    a4_text = str(r.get("expectation_movable") or "")
    writes = {str(e.get("path", "")).rstrip("/") for e in r.get("excluded_material") or [] if str(e.get("exclusion")) in AGENT_WRITES}
    for p in pins:
        if p not in a4_text or not in_files(p) or p in writes or any(q != p and q.startswith(p + "/") for q in pins) \
                or (p + "/") in alltext:  # the task text itself uses p as a directory
            v.add("L16")
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
