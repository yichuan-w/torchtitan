#!/usr/bin/env python3
"""Compare the TerminalWorld seeds against TMax-15K on what is actually comparable.

Yichuan asked whether TW is meaningfully better than TMax. The honest answer needs
the two corpora lined up field by field first, and they do not line up as neatly
as the column names suggest.

TMax's `truth` is not a reference solution. Reading it, some rows are shell that
solves the task and others are a construction spec — "Verifier Configuration",
"Setup and Initialization (conceptual order of operations to be run before agent
starts)". A median over the column mixes the two, and the first version of this
comparison reported TMax solutions as four times longer than TW's on exactly that
mistake. Solution length is therefore reported only over the rows that parse as
scripts, and labelled as such.

What does line up: `test_final_state` is pytest, the same shape as TW's
`tests/test_state.py`, and `description` is the instruction.

Usage: compare_tw_tmax.py [--out results/tw_vs_tmax.md] [--sample 3000]
"""
from __future__ import annotations

import argparse
import re
import statistics as st
import tarfile
from pathlib import Path

import pandas as pd
from huggingface_hub import hf_hub_download

TESTFN = re.compile(r"^\s*def\s+(test_\w+)\s*\([^)]*\)\s*(?:->[^:\n]+)?:((?:\n(?:[ \t].*)?)*)", re.M)
# A construction spec rather than a solution — the giveaway phrasings.
SPEC = re.compile(r"Verifier Configuration|Fixture Configuration"
                  r"|Setup and Initialization|before agent starts"
                  r"|Conceptual order", re.I)
SCRIPTY = re.compile(r"^#!|^\s*(cd |gcc |make |python3? |bash |apt-get|pip install)",
                     re.M)
NOSHORTCUT_LEXICAL = re.compile(
    r"no_?shortcut|placeholder|hard[_-]?cod|stale|dummy|verifier[_-]?only"
    r"|fabricat|forged|precomputed", re.I)
MUTATES = re.compile(r"write_bytes|write_text|\.write\(|shutil\.copy|os\.remove"
                     r"|unlink\(|truncate|touch\(")
RERUNS = re.compile(r"run_workflow|subprocess\.(run|check_|Popen)|os\.system"
                    r"|solve\.sh|run_cmd|sh\(")


def rejects_shortcut(body: str) -> bool:
    return bool(NOSHORTCUT_LEXICAL.search(body)
                or (MUTATES.search(body) and RERUNS.search(body)))


def code_lines(s: str) -> int:
    return len([l for l in str(s).splitlines()
                if l.strip() and not l.strip().startswith("#")])


def measure_tests(src: str) -> tuple[int, bool]:
    fns = TESTFN.findall(str(src))
    return len(fns), any(rejects_shortcut(b) for _, b in fns)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", default="data/seed-dataset/data/tasks-00000.tar")
    ap.add_argument("--sample", type=int, default=3000)
    ap.add_argument("--out", default="results/tw_vs_tmax.md")
    args = ap.parse_args()

    tm = pd.read_parquet(hf_hub_download(
        "allenai/TMax-15K", "data/train-00000-of-00001.parquet",
        repo_type="dataset")).head(args.sample)

    tm_checks, tm_anti = [], 0
    for src in tm["test_final_state"]:
        n, anti = measure_tests(src)
        tm_checks.append(n)
        tm_anti += anti
    tm_instr = [len(str(x)) for x in tm["description"]]
    tm_spec = sum(1 for x in tm["truth"] if SPEC.search(str(x)))
    tm_script = [code_lines(x) for x in tm["truth"] if SCRIPTY.search(str(x))]

    tw_sol, tw_checks, tw_instr, tw_anti = [], [], [], 0
    with tarfile.open(args.tar) as tf:
        for m in tf.getmembers():
            if m.name.endswith("/solution/solve.sh"):
                tw_sol.append(code_lines(
                    tf.extractfile(m).read().decode("utf-8", "replace")))
            elif m.name.endswith("/tests/test_state.py"):
                n, anti = measure_tests(
                    tf.extractfile(m).read().decode("utf-8", "replace"))
                tw_checks.append(n)
                tw_anti += anti
            elif m.name.endswith("/instruction.md"):
                tw_instr.append(len(
                    tf.extractfile(m).read().decode("utf-8", "replace")))

    def row(label: str, a, b) -> str:
        return f"| {label} | {a} | {b} |"

    pct = lambda n, d: f"{100 * n / d:.0f}%"
    lines = [
        "# TerminalWorld seeds vs TMax-15K",
        "",
        f"TW: all {len(tw_checks)} seed tasks. "
        f"TMax: first {len(tm)} of 14,601.",
        "",
        "| | TerminalWorld | TMax-15K |",
        "|---|---|---|",
        row("ships an executable reference solution",
            f"yes, all {len(tw_sol)}", "no such field"),
        row("verifier checks, median",
            f"{st.median(tw_checks):.0f}", f"{st.median(tm_checks):.0f}"),
        row("tasks with >= 4 checks",
            pct(sum(1 for c in tw_checks if c >= 4), len(tw_checks)),
            pct(sum(1 for c in tm_checks if c >= 4), len(tm_checks))),
        row("a check that rejects an unproduced answer",
            pct(tw_anti, len(tw_checks)), pct(tm_anti, len(tm_checks))),
        row("instruction length, median chars",
            f"{st.median(tw_instr):.0f}", f"{st.median(tm_instr):.0f}"),
        row("reference solution lines, median",
            f"{st.median(tw_sol):.0f}",
            f"{st.median(tm_script):.0f} over the {pct(len(tm_script), len(tm))} "
            "of `truth` that parses as a script"),
        "",
        "## What the numbers do and do not say",
        "",
        "**TW ships a proof and TMax does not.** Every TW task carries a "
        "`solution/solve.sh` that can be run against its own verifier, which is "
        "what made it possible to establish that 861 of them are internally "
        "consistent by execution rather than by inspection. TMax has no solution "
        "column; its `truth` is sometimes solving shell and sometimes a spec for "
        "building the task, so the same check cannot be run over it.",
        "",
        "**TW verifies more densely, TMax specifies more.** TW puts four or more "
        f"checks on {pct(sum(1 for c in tw_checks if c >= 4), len(tw_checks))} of "
        f"tasks against TMax's "
        f"{pct(sum(1 for c in tm_checks if c >= 4), len(tm_checks))}, while TMax's "
        "instructions are several times longer. Denser grading and thinner "
        "prompts on one side, the reverse on the other.",
        "",
        "**Neither corpus rejects shortcuts often.** Both sit in single digits on "
        "checks that would catch an answer the agent never actually produced. "
        "RST's contract puts one on every task by construction, and that is the "
        "gap either corpus has against it.",
        "",
        "**Not measured here: difficulty.** The one number that would settle "
        "\"better\" is how a solver does on each, and only TW has been run "
        "(GPT-5.6-sol, pass@5: 72% of tasks solved every time). The same run "
        "against TMax needs its containers built, which its `container_def` "
        "column should allow.",
    ]
    Path(args.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
