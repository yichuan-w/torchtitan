#!/usr/bin/env python3
"""Contract-validity heuristic v1 for TerminalWorld seeds.

Yichuan's question: do TW verifiers test things the task never told the agent
about? (RST calls the property "contract validity": every verifier check must
be stated in the instruction or discoverable in the workspace.)

Heuristic: extract absolute paths asserted in tests/ (test_state.py + test.sh),
then check whether each path is mentioned in instruction.md, or discoverable in
the environment (a file shipped in the package / mentioned in Dockerfile or
entrypoint). A path asserted by the verifier but absent everywhere the agent
can look is a "dark check" — the agent could only satisfy it by luck.

This is a lexical v1: paths constructed dynamically or hinted in prose escape
it both ways. Output: results/contract_validity_v1.md + per-task jsonl.
"""
from __future__ import annotations

import json
import re
import tarfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAR = ROOT / "data" / "seed-dataset" / "data" / "tasks-00000.tar"
OUT_MD = ROOT / "results" / "contract_validity_v1.md"
OUT_JL = ROOT / "results" / "contract_validity_v1.jsonl"

PATH_RE = re.compile(r'["\'](/(?:app|home|tmp|etc|var|opt|usr|data|srv|root|workspace)[^"\'\s]*)["\']')
NOISE = ("/logs/verifier", "/tests/", "/oracle/")


def main() -> None:
    tasks: dict[str, dict] = {}
    with tarfile.open(TAR) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            tid, rel = parts[1], "/".join(parts[2:])
            tasks.setdefault(tid, {})[rel] = tf.extractfile(m).read().decode("utf-8", "replace")

    dark_counts = []
    with open(OUT_JL, "w") as jl:
        for tid, files in sorted(tasks.items()):
            tests = "\n".join(v for k, v in files.items() if k.startswith("tests/"))
            asserted = {p.rstrip("/") for p in PATH_RE.findall(tests)
                        if not any(n in p for n in NOISE)}
            if not asserted:
                jl.write(json.dumps({"task_id": tid, "asserted": 0, "dark": []}) + "\n")
                dark_counts.append((tid, 0, 0))
                continue
            instruction = files.get("instruction.md", "")
            env_blob = "\n".join(v for k, v in files.items()
                                 if k.startswith("environment/") or k == "task.toml")
            env_files = {"/" + k.removeprefix("environment/") for k in files
                         if k.startswith("environment/")}
            dark = []
            for p in asserted:
                visible = (p in instruction or p in env_blob
                           or any(p.endswith(Path(e).name) for e in env_files))
                # parent-dir mention also counts as discoverable
                if not visible:
                    parent = str(Path(p).parent)
                    visible = parent != "/" and (parent in instruction or parent in env_blob)
                if not visible:
                    dark.append(p)
            jl.write(json.dumps({"task_id": tid, "asserted": len(asserted),
                                 "dark": sorted(dark)}) + "\n")
            dark_counts.append((tid, len(asserted), len(dark)))

    with_asserts = [x for x in dark_counts if x[1] > 0]
    with_dark = [x for x in with_asserts if x[2] > 0]
    fully_dark = [x for x in with_asserts if x[2] == x[1]]
    ratio = Counter()
    for _, a, d in with_asserts:
        ratio[round(d / a, 1)] += 1

    lines = [
        "# TerminalWorld seeds: contract-validity heuristic v1",
        "",
        "Dark check = a filesystem path asserted by the verifier that appears nowhere",
        "the agent can see (instruction, environment files, Dockerfile/entrypoint,",
        "task.toml; parent-directory mentions count as discoverable).",
        "",
        f"- tasks with path-asserting verifiers: {len(with_asserts)}/{len(dark_counts)}",
        f"- tasks with >=1 dark check: **{len(with_dark)}** "
        f"({100*len(with_dark)/max(1,len(with_asserts)):.1f}% of path-asserting tasks)",
        f"- tasks where ALL asserted paths are dark: **{len(fully_dark)}**",
        "",
        "Worst offenders (most dark paths):",
    ]
    for tid, a, d in sorted(with_asserts, key=lambda x: -x[2])[:10]:
        lines.append(f"- {tid}: {d}/{a} asserted paths dark")
    lines += ["", "Caveat: lexical heuristic. Prose hints ('save the report next to the",
              "input') and dynamically built paths evade it in both directions; treat",
              "flagged tasks as candidates for manual/LLM review, not verdicts."]
    OUT_MD.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
