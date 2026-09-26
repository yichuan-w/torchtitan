#!/usr/bin/env python3
"""Row lints for the v13-rst variant: every v13 lint (L1-L16), plus five that only make sense for this corpus.

  LR1  bootstrap_fetch disagrees with the bootstrap itself: the stager found a grade-time fetch and the field is null,
       or the stager found none and the field is set
  LR2  bootstrap_fetch is anchored outside the bootstrap, or its span misses the lines where the fetches are
  LR3  a blocking field other than B1 is anchored inside the bootstrap (a bootstrap fetch reported as B3, or a leak
       "found" in harness code); B1 may anchor there: a state the instruction asks for that stops the bootstrap
  LR4  bootstrap_fetch leaves out a kind of fetch, or a --with package, that the bootstrap contains
  LR5  no assertions entry lies in the verifier (below the separator): the wrong half of the file was mapped
L7 (v13: the evidence line must look like an assertion) is suppressed for a B1 whose evidence is a bootstrap line: the
one B1 shape that anchors there points at a harness command, not at an assert. It is the counterpart of LR3.

The sidecar is what stage_rst.py extracted mechanically from the same staged file, so LR1/LR2/LR4 are a consistency
check of the judge against code, not a second opinion.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in ("validation", "tools"):                       # where lint_v13.py lives depends on the checkout's layout
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), _d))
import lint_v13  # noqa: E402

KIND_WORDS = {"apt": ("apt",), "astral.sh": ("astral",), "pypi:uvx": ("pypi", "uvx"), "pypi:pip": ("pypi", "pip"),
              "git": ("git",), "other": ("curl", "wget", "http")}
BLOCKING_NOT_B1 = ("oracle_reachable", "expectation_revealed", "value_derivable", "unstable_reward")


def anchor(text):
    m = re.match(r"^(instruction\.md|setup\.sh|tests/test\.sh):([0-9]+)(?:-([0-9]+))?", str(text or ""))
    return (m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))) if m else None


def lint(task_files, row, candidates, sidecar):
    out = set(lint_v13.lint(task_files, row, candidates))
    boot = (sidecar or {}).get("bootstrap") or {}
    sep = int(boot.get("separator_line") or 0)
    kinds = boot.get("fetches") or []
    bf = row.get("bootstrap_fetch")
    if bool(kinds) != bool(bf):
        out.add("LR1")
    if bf:
        a = anchor(bf)
        span = boot.get("fetch_span")
        if not a or a[0] != "tests/test.sh" or (sep and a[2] >= sep):
            out.add("LR2")
        elif span and (a[2] < span[0] or a[1] > span[1]):
            out.add("LR2")
        low = bf.lower()
        if any(not any(w in low for w in KIND_WORDS.get(k, (k,))) for k in kinds):
            out.add("LR4")
        if any(p.lower() not in low for p in boot.get("with_packages") or []):
            out.add("LR4")
        if boot.get("python_pin") and boot["python_pin"] not in low:
            out.add("LR4")
    for k in BLOCKING_NOT_B1:
        a = anchor(row.get(k))
        if a and a[0] == "tests/test.sh" and sep and a[2] < sep:
            out.add("LR3")
    ev, ef = row.get("evidence_line"), row.get("evidence_file")
    if ("L7" in out and ef == "tests/test.sh" and isinstance(ev, int) and sep and ev < sep
            and row.get("overspecific_check") and not any(row.get(k) for k in BLOCKING_NOT_B1)
            and 1 <= ev <= len((task_files.get("tests/test.sh") or "").splitlines())):
        out.discard("L7")
    # L15 (v13) wants the literal token `core_step:` in a derivation_is_core_step basis. Judges often write "core step:".
    # Accept either spelling when the cited words still overlap the row's core_step by two or more.
    if "L15" in out:
        core = lint_v13.words(row.get("core_step"))
        bases = [str(e.get("basis", "")) for e in row.get("excluded_material") or []
                 if str(e.get("exclusion")) == "derivation_is_core_step"]
        cites = [re.search(r"core[ _]step\W+(.+)$", b, re.S | re.I) for b in bases]
        if bases and all(c and len(lint_v13.words(c.group(1)) & core) >= 2 for c in cites):
            out.discard("L15")
    lines = [int(m.group(1)) for e in row.get("assertions") or [] for m in [re.match(r"^L([0-9]+)", str(e))] if m]
    if sep and lines and max(lines) < sep:
        out.add("LR5")
    return sorted(out)


if __name__ == "__main__":
    import json
    run = os.path.abspath(sys.argv[1])
    bad = 0
    for f in sys.argv[2:]:
        row = json.loads(open(f).readline())
        tid = row["task_id"]
        side = json.load(open(os.path.join(run, "sidecar", tid + ".json")))
        hits = lint(lint_v13.load_task(os.path.join(run, "tasks"), tid), row,
                    lint_v13.load_candidates(os.path.join(run, "candidates"), tid), side)
        bad += bool(hits)
        print(f"{tid}: {' '.join(hits) if hits else 'ok'}")
    sys.exit(1 if bad else 0)
