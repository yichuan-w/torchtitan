#!/usr/bin/env python3
"""Judge a synthesis run on what it produced, not on whether it finished.

A run that clears its gates can still be worthless: the gates ask whether a task
is self-consistent, and none of them asks whether it is harder than the seed,
whether the verifier checks anything real, or whether the difficulty landed
somewhere a policy can learn from. Those are the questions here, each as a number
with a target next to it, so "the rewrite got better" is a claim that can be
checked rather than felt.

Targets, and where they come from:

  yield             — no published figure; RST reports $0.05 an accepted task
  pass@k in (0,1)   — the only band with a gradient. 1.0 teaches nothing and
                      0.0 teaches nothing, and the seed corpus is 72% at 1.0,
                      which is the problem the rewrite exists to fix
  solve.sh lines    — seeds have a median of 10 against TB2's 61; RST's own
                      round-1 rewrites land at 67, so that is the mark
  >=4 checks        — RST's contract requires four, and its STEP 2 names their
                      roles; the seeds manage a median of 3 and 46% at four
  no-shortcut check — 100% by construction in RST, 24% in the seeds
  dark paths/leaks  — the audit's own flags, which should stay near zero

Usage:
  review_synth.py --results 'results/synth_run1_s*.jsonl' --tasks data/synth-run1
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import statistics as st
from pathlib import Path

TESTFN = re.compile(r"^\s*def\s+(test_\w+)", re.M)
TESTBODY = re.compile(r"^\s*def\s+(test_\w+)\s*\([^)]*\)\s*(?:->[^:\n]+)?:((?:\n(?:[ \t].*)?)*)", re.M)
# Naming a shortcut is the weak form of rejecting one. The strong form is
# behavioural and looks nothing like the words: mutate an input, re-run the
# workflow, assert the output followed. A first pass at this scored the run 0%
# on anti-shortcut checks while the model was in fact writing exactly that,
# which would have sent a working prompt back for repair.
NOSHORTCUT_LEXICAL = re.compile(
    r"no_?shortcut|placeholder|hard[_-]?cod|stale|dummy|verifier[_-]?only"
    r"|fabricat|forged|precomputed", re.I)
MUTATES = re.compile(r"write_bytes|write_text|\.write\(|shutil\.copy|os\.remove"
                     r"|unlink\(|truncate|touch\(")
RERUNS = re.compile(r"run_workflow|subprocess\.(run|check_|Popen)|os\.system"
                    r"|solve\.sh|run_cmd|sh\(")


def rejects_shortcut(body: str, whole: str = "") -> bool:
    """Whether a check would catch an answer the agent never actually produced.

    The mutation has to be in this check's own body — that is what makes it this
    check rather than a neighbour. The re-run does not: verifiers routinely put
    it in a module-level helper and call `_build(tag)` or `_run_workflow()` from
    the test, and looking for a subprocess call inside the function body misses
    every one of them. Fourth detector in this file to have been narrower than
    what the model writes, so the search widens to the file for that half.
    """
    whole = whole or body
    if NOSHORTCUT_LEXICAL.search(body):
        return True
    return bool(MUTATES.search(body) and RERUNS.search(whole))

SEED_MEDIAN_LINES = 10      # measured over the seed corpus
RST_ROUND1_LINES = 67       # the paper's own round-1 median, and the mark here


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True,
                    help="glob over the run's jsonl files")
    ap.add_argument("--tasks", required=True,
                    help="directory holding the generated task packages")
    ap.add_argument("--json", help="write the summary here as well")
    args = ap.parse_args()

    rows = []
    for path in sorted(glob.glob(args.results)):
        rows += [json.loads(l) for l in Path(path).read_text().splitlines()
                 if l.strip()]
    if not rows:
        raise SystemExit("no records matched")

    status = collections.Counter(r["status"] for r in rows)
    accepted = [r for r in rows if r["status"] in ("accepted", "usable")]
    graded = [r for r in rows if r.get("pass_at_k") is not None]

    print(f"{len(rows)} seeds attempted\n")
    print("where they ended:")
    for k, n in status.most_common():
        print(f"  {k:20s} {n:4d}  {pct(n, len(rows))}")

    # Every gate before the rollouts is a task that cost a build and taught
    # nothing, so attrition is as interesting as yield.
    reached = len(graded)
    print(f"\nreached the rollout stage: {reached}  {pct(reached, len(rows))}")
    print(f"accepted as usable:        {len(accepted)}  "
          f"{pct(len(accepted), len(rows))}")

    if graded:
        band = collections.Counter()
        for r in graded:
            p = r["pass_at_k"]
            band["1.0 — no gradient" if p == 1 else
                 "0.0 — nothing learnable" if p == 0 else
                 "(0,1) — usable"] += 1
        print("\ndifficulty the rewrite landed on:")
        for k, n in band.most_common():
            print(f"  {k:26s} {n:4d}  {pct(n, reached)}")
        usable = band["(0,1) — usable"]
        print(f"\n  seeds start at 72% pass@5 = 1.0; these land at "
              f"{pct(band['1.0 — no gradient'], reached)}")
        print(f"  gradient-carrying share: {pct(usable, reached)}")

    # The generated packages, which the records do not contain.
    tasks = sorted(Path(args.tasks).rglob("tests/test_state.py"))
    lines, checks, anti = [], [], 0
    for t in tasks:
        pkg = t.parent.parent
        solve = pkg / "solution/solve.sh"
        if solve.exists():
            body = solve.read_text(errors="replace")
            lines.append(len([l for l in body.splitlines()
                              if l.strip() and not l.strip().startswith("#")]))
        src = t.read_text(errors="replace")
        fns = TESTBODY.findall(src)
        checks.append(len(fns))
        if any(rejects_shortcut(b, src) for _, b in fns):
            anti += 1

    if tasks:
        print(f"\n{len(tasks)} generated packages on disk")
        print(f"  solve.sh lines      median {st.median(lines):.0f}   "
              f"(seeds {SEED_MEDIAN_LINES}, RST round-1 {RST_ROUND1_LINES})")
        print(f"  checks per task     median {st.median(checks):.0f}   "
              f"min {min(checks)}  max {max(checks)}")
        print(f"  >= 4 checks         {sum(1 for c in checks if c >= 4)}  "
              f"{pct(sum(1 for c in checks if c >= 4), len(checks))}"
              f"   (RST 100%, seeds 46%)")
        print(f"  no-shortcut check   {anti}  {pct(anti, len(tasks))}"
              f"   (RST 100%, seeds 24%)")

    dark = sum(1 for r in rows if (r.get("audit") or {}).get("dark_paths"))
    leak = sum(1 for r in rows if (r.get("audit") or {}).get("leaks"))
    print(f"\naudit flags: {dark} with dark paths, {leak} with leaks")

    fams = collections.Counter(r.get("family") for r in rows if r.get("family"))
    if fams:
        print("\noperator families used:")
        for k, n in fams.most_common():
            print(f"  {k:34s} {n:4d}  {pct(n, sum(fams.values()))}")

    usage = [r.get("usage") or {} for r in rows]
    tin = sum(u.get("prompt_tokens", 0) for u in usage)
    tout = sum(u.get("completion_tokens", 0) for u in usage)
    if tin:
        print(f"\ntokens: {tin:,} in, {tout:,} out over {len(rows)} seeds "
              f"({tin // len(rows):,} / {tout // len(rows):,} each)")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "seeds": len(rows), "status": dict(status),
            "reached_rollout": reached, "accepted": len(accepted),
            "median_solve_lines": st.median(lines) if lines else None,
            "median_checks": st.median(checks) if checks else None,
            "pct_four_checks": (sum(1 for c in checks if c >= 4) / len(checks)
                                if checks else None),
            "pct_no_shortcut": anti / len(tasks) if tasks else None,
        }, indent=1) + "\n")


if __name__ == "__main__":
    main()
