#!/usr/bin/env python
"""Score how hard each pooled task's verifier is to satisfy without solving it.

A task whose tests only check that a file exists, is non-empty, or contains a
substring hands out reward for free. Picking such a task as a near-neighbour of
a TB2.1 task is worse than picking nothing: it teaches the policy to produce
something superficially shaped right. So every pooled task carries a verifier
grade, and the reranking agents see it.

Read from the ORIGINAL sources, never from work/cache/*.jsonl. The cached
composed text concatenates a task's precondition tests with its reward tests and
truncates the result at a character cap, which grades honest verifiers as free
rewards. That mistake is the reason this script exists in this shape.

Three mechanisms, graded on their own terms:

  repo-tests        runs the upstream project's own suite at named pytest node
                    ids (SWE-Smith, SWE-Rebench). Not substring-gameable by
                    construction; graded `repo-tests`, not scored.
  generated-pytest  an authored test file (TMax test_final_state, TerminalWorld
                    test_state.py, Terminal-Lego and CalibForge
                    tests/test_outputs.py). Scored per assertion, below.
  absent            no verifier ships with the copy of the corpus we pooled
                    (Recursive-Task-Synthesis). Graded `absent`, not scored --
                    absence is reported, never treated as good.

For a generated-pytest task, per test function:
  substantive  pins a concrete expected value (== literal, assertEqual, approx)
  cheap        everything it asserts is under the solver's own control
               (exists / non-empty / substring in content / exit code 0)

  strength = substantive / total        in [0, 1]
  grade    = strong      (>=0.50 and >=3 substantive) or >=8 substantive
             ok          >=0.25 or >=4 substantive
             weak        >0
             behavioral  no pinned value, but at least one test runs the
                         artifact and asserts on its exit status
             FREE        nothing but file-exists / non-empty / substring
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "work" / "verifier_strength.json"

# Enumerate what is VACUOUS, not what is substantive. Listing the substantive
# forms is endless -- earlier versions of this file missed `== f"..."`,
# `mse <= threshold`, `jaccard >= 1.0` and `a.lower() == b.lower()`, and so
# graded careful verifiers as free rewards. The vacuous forms are a short,
# closed list, so everything outside it counts as real verification.
#
# An assertion is vacuous when the solver can satisfy it by writing a file with
# the right name and the right words in it, without the task being done:
VACUOUS_ASSERT = re.compile(r"""(?x)
      ^assert\s+(not\s+)?os\.path\.(exists|isfile|isdir)\(
    | ^assert\s+(not\s+)?os\.access\(
    | ^assert\s+(not\s+)?\w[\w.]*\.(exists|is_file|is_dir)\(\s*\)
    | ^assert\s+len\([^)]*\)\s*(>|>=|!=)\s*[01]\s*(,|$)
    | ^assert\s+os\.path\.getsize\([^)]*\)\s*>\s*0
    | ^assert\s+(not\s+)?["'][^"']*["']\s+(not\s+)?in\s+\w+\s*(,|$)
    | ^assert\s+\w+\.(startswith|endswith)\(["']
    | ^assert\s+\w*(result|proc|r|out|p)\w*\.returncode\s*==\s*0\s*(,|$)
    | ^assert\s+isinstance\(
    | ^assert\s+\w+\s*(,|$)
    | ^assert\s+os\.path\.\w+\([^)]*\)\s*(,|$)
""")


def _asserts(fn: str) -> list[str]:
    out = []
    for line in fn.splitlines():
        t = line.strip()
        if t.startswith("assert"):
            out.append(re.sub(r"\s+", " ", t))
    return out


# `assert ok` reads as vacuous but is substantive when `ok` was computed by a
# comparison earlier in the function (`ok = got == want`). Look at the body.
COMPARES = re.compile("(==|!=|<=|>=|isclose|allclose|approx|re[.](search|match|fullmatch)[(])")


# `assert "login:" in combined` is vacuous when `combined` is a file the solver
# wrote, and substantive when it is what the solver's program actually emitted.
# The haystack's provenance is the whole distinction, so look at how it was bound.
LIVE_SOURCE = re.compile(
    r"(subprocess|check_output|communicate|\.stdout|\.stderr|Popen|os\.popen"
    r"|socket|\.recv\(|\.readline\(\)|pexpect|spawn\(|sendline|telnet"
    r"|requests\.|urlopen|urllib|\.get\(|\.post\(|docker|run\()")


def _haystack_is_live(fn: str, var: str) -> bool:
    """True if `var` was bound from something the solution had to run."""
    for line in fn.splitlines():
        t = line.strip()
        if re.match(rf"{re.escape(var)}\s*(\+)?=", t) and LIVE_SOURCE.search(t):
            return True
    return False


def _is_vacuous(fn: str) -> bool:
    """True only if every assertion in the function is a known-vacuous form."""
    a = _asserts(fn)
    if not a:
        # no assert at all: a bare pytest.fail guard still exercises something
        return "pytest.fail" not in fn and "raise" not in fn
    for x in a:
        if not VACUOUS_ASSERT.search(x):
            return False
        # substring check against live program output is real verification
        m = re.match(r"^assert\s+(?:not\s+)?[\"'][^\"']*[\"']\s+(?:not\s+)?in\s+(\w+)", x)
        if m and _haystack_is_live(fn, m.group(1)):
            return False
        # a bare `assert name` backed by a comparison in the body is real
        if re.match(r"^assert\s+\w+\s*(,|$)", x):
            body = "\n".join(l for l in fn.splitlines() if not l.strip().startswith("assert"))
            if COMPARES.search(body):
                return False
    return True


TREES = {
    "TerminalWorld-Seeds-Clean": (
        Path("/data/yichuan_wang/tmax-9b-centralia/data/sources/tw-extract/tasks"),
        ["tests/test_state.py", "tests/test_outputs.py"],
    ),
    "Terminal-Lego-15k": (
        Path("/data/yichuan_wang/terminal-lego-15k"),
        ["tests/test_outputs.py"],
    ),
}
REPO_TESTS = {
    "SWE-Smith-Seeds-Clean":
        Path("/data/yichuan_wang/tmax-9b-centralia-mix-v2/data/sources/swesmith-58af1819/tasks"),
    "SWE-Rebench-Tasks-Clean":
        Path("/data/yichuan_wang/tmax-9b-centralia-rebench/data/sources/swe-rebench-tasks-clean/tasks"),
}
CALIBFORGE = Path("/data/yichuan_wang/calibforge")
TMAX_PARQUET = Path("/data/yichuan_wang/tb21-tfidf-overlap/hf/tmax15k-train.parquet")
RTS_PARQUET = Path("/data/yichuan_wang/tb21-tfidf-overlap/hf/rts-tasks.parquet")


# Executing the artifact and asserting on its exit status is a behavioural
# check the solver cannot satisfy by pasting a string. It is weaker than
# pinning a value, so it gets its own grade rather than being lumped in with
# the vacuous suites that only stat a file.
RUNS_ARTIFACT = re.compile(
    r"subprocess\.(run|check_output|check_call|Popen)|os\.system|runpy|importlib")
EXIT_CHECK = re.compile(r"returncode\s*(==|!=)|check=True|check_call")


def score_pytest(src: str) -> dict:
    fns = [f for f in re.split(r"\n(?=\s*def test_)", src) if re.search(r"def test_", f)]
    if not fns:
        return {"grade": "absent", "mechanism": "generated-pytest",
                "strength": None, "n_tests": 0, "n_substantive": 0}
    n_sub = sum(1 for f in fns if not _is_vacuous(f))
    n_beh = sum(1 for f in fns if _is_vacuous(f)
                and RUNS_ARTIFACT.search(f) and EXIT_CHECK.search(f))
    s = n_sub / len(fns)
    # Ratio alone punishes fine-grained suites: Terminal-Lego routinely runs
    # 20-60 small assertions, so a task with 19 substantive ones scored `weak`
    # while a 3-assertion task with 2 scored `strong`. Absolute count rescues it.
    grade = (("behavioral" if n_beh else "FREE") if n_sub == 0 else
             "strong" if (s >= 0.50 and n_sub >= 3) or n_sub >= 8 else
             "ok" if s >= 0.25 or n_sub >= 4 else "weak")
    kinds = Counter()
    for f in fns:
        if _is_vacuous(f):
            for x in _asserts(f):
                kinds["exists" if "os.path" in x or "os.access" in x
                      else "substring" if " in " in x
                      else "non-empty" if "len(" in x or "getsize" in x
                      else "other"] += 1
    return {"grade": grade, "mechanism": "generated-pytest", "strength": round(s, 3),
            "n_tests": len(fns), "n_substantive": n_sub, "n_behavioral": n_beh,
            "cheap_kinds": dict(kinds)}


def read(p: Path, cap: int = 200_000) -> str:
    try:
        return p.read_text(errors="ignore")[:cap]
    except Exception:
        return ""


def collect() -> dict[str, dict]:
    out: dict[str, dict] = {}

    # TMax: score test_final_state only. test_initial_state checks the task's
    # preconditions -- it is *meant* to only assert that files exist, and
    # counting it drags honest tasks down to FREE.
    if TMAX_PARQUET.exists():
        import pyarrow.parquet as pq
        t = pq.read_table(TMAX_PARQUET, columns=["task_id", "test_final_state"]).to_pylist()
        for r in t:
            out[r["task_id"]] = score_pytest(r["test_final_state"] or "")
        print(f"[vs] TMax-15K: {len(t)} from parquet column test_final_state")

    if RTS_PARQUET.exists():
        import pyarrow.parquet as pq
        ids = pq.read_table(RTS_PARQUET, columns=["task_id"]).column(0).to_pylist()
        for tid in ids:
            out[tid] = {"grade": "absent", "mechanism": "none-shipped",
                        "strength": None, "n_tests": 0, "n_substantive": 0}
        print(f"[vs] Recursive-Task-Synthesis: {len(ids)} marked absent "
              f"(the parquet ships no verifier at all)")

    for name, root in REPO_TESTS.items():
        if not root.exists():
            continue
        n = 0
        for d in root.iterdir():
            if d.is_dir():
                out[d.name] = {"grade": "repo-tests", "mechanism": "repo-tests",
                               "strength": None, "n_tests": None, "n_substantive": None}
                n += 1
        print(f"[vs] {name}: {n} marked repo-tests (upstream suite at named node ids)")

    for name, (root, rels) in TREES.items():
        if not root.exists():
            continue
        n = 0
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            src = ""
            for rel in rels:
                if (d / rel).exists():
                    src = read(d / rel)
                    break
            if not src and not (d / "tests").exists():
                continue
            out[d.name] = score_pytest(src)
            n += 1
        print(f"[vs] {name}: {n} scored from {rels[0]}")

    if CALIBFORGE.exists():
        n = 0
        for sub in ("contrastive_solver", "multi_solver"):
            base = CALIBFORGE / sub
            if not base.exists():
                continue
            for d in sorted(base.iterdir()):
                if d.is_dir() and (d / "tests").exists():
                    out[d.name] = score_pytest(read(d / "tests" / "test_outputs.py"))
                    n += 1
        print(f"[vs] CalibForge: {n} scored from tests/test_outputs.py")

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    scores = collect()

    by = {}
    for tid, s in scores.items():
        by.setdefault(s["mechanism"], Counter())[s["grade"]] += 1
    G = ("strong", "ok", "weak", "behavioral", "FREE", "repo-tests", "absent")
    print(f"\n{'mechanism':20} " + "  ".join(f"{g:>10}" for g in G))
    print("-" * 100)
    for mech, c in by.items():
        print(f"{mech:20} " + "  ".join(f"{c[g]:10}" for g in G))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scores))
    print(f"\nwrote {len(scores)} scores -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
