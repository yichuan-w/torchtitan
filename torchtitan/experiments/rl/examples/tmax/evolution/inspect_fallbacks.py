#!/usr/bin/env python3
"""Print each fallback and the assertion that consumes it, so 23 can be read.

An `except` that supplies a value is not automatically a defect. Parsing a date
two ways is fine. What is not fine is an except that supplies the value the
assertion is about to compare against, because the comparison then tests the
grader's own guess rather than the task: tw_582696 asked an NTP server for the
time, could not reach one, fell back to `datetime.now()`, and the check
collapsed into "is this within 300 seconds of now" -- which `date` satisfies.

The difference is visible in one screenful per task, so this prints it rather
than trying to judge automatically: the except body, and every assertion
mentioning a name it assigned.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout


def report(tasks: Path, task: str) -> None:
    tests = sorted((tasks / task / "tests").glob("*.py"))
    if not tests:
        return
    src = "\n".join(p.read_text(errors="replace") for p in tests)
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        print(f"== {task}: unparseable")
        return
    print(f"== {task}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        try_names = {
            t.id
            for n in ast.walk(node)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for handler in node.handlers:
            names = {
                t.id
                for n in ast.walk(handler)
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name)
            } & try_names
            if not names:
                continue
            body = "\n".join(
                "      " + lines[i - 1].strip()
                for i in range(handler.lineno, min(handler.end_lineno or 0, handler.lineno + 5) + 1)
            )
            print(f"   fallback assigns {sorted(names)}:")
            print(body)
            for a in ast.walk(tree):
                if isinstance(a, ast.Assert) and names & {
                    n.id for n in ast.walk(a.test) if isinstance(n, ast.Name)
                }:
                    print(f"      -> assert (line {a.lineno}): "
                          f"{lines[a.lineno - 1].strip()[:150]}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default="/scratch/al9080/terminal-rl/measure/degenerate_scan.jsonl")
    ap.add_argument("tasks", nargs="*")
    a = ap.parse_args()
    tasks = layout.Root.from_env().data / "sources" / "tw-extract" / "tasks"
    if a.tasks:
        todo = a.tasks
    else:
        todo = [
            json.loads(l)["task_id"]
            for l in open(a.scan)
            if l.strip() and "fallback" in json.loads(l) and json.loads(l)["in_live_mix"]
        ]
    print(f"{len(todo)} tasks\n")
    for t in todo:
        report(tasks, t)


if __name__ == "__main__":
    main()
