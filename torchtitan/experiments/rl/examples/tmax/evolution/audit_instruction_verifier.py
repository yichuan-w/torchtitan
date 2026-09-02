#!/usr/bin/env python3
"""Audit both directions of instruction<->verifier alignment.

A task can fail an agent in two opposite ways, and a dataset that claims to be
usable for RL should report both:

  * says too little ("dark check"): the verifier asserts something the agent
    cannot discover — a path that appears in no instruction, no environment
    file, no Dockerfile. The agent can only satisfy it by luck.
  * says too much ("leak"): the instruction spells out what the hidden verifier
    checks, so the agent can tick boxes instead of doing the task. Training on
    these teaches box-ticking and inflates eval scores.

Both signals here are lexical, so both are upper bounds: prose hints ("save the
report next to the input") evade the dark scan, and a task legitimately stating
its acceptance criteria looks like a leak. Flagged tasks are candidates for
review, not verdicts — the counts are reported separately for that reason.

Usage: uv run python scripts/audit_instruction_verifier.py
Writes: results/instruction_verifier_audit.jsonl + .md
"""
from __future__ import annotations

import json
import re
import tarfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAR = ROOT / "data" / "seed-dataset" / "data" / "tasks-00000.tar"
OUT_JL = ROOT / "results" / "instruction_verifier_audit.jsonl"
OUT_MD = ROOT / "results" / "instruction_verifier_audit.md"

PATH_RE = re.compile(r'["\'](/(?:app|home|tmp|etc|var|opt|usr|data|srv|root|workspace)[^"\'\s]*)["\']')
NOISE = ("/logs/verifier", "/tests/", "/oracle/")
# strings the verifier compares against: assertEqual-ish literals and numbers
LITERAL_RE = re.compile(r'==\s*["\']([^"\']{4,60})["\']')
# Harbor puts the private verifier at tests/test.sh + tests/test_*.py and mounts
# the reference solution under /oracle. Nothing an agent is legitimately asked
# to create lives at those paths.
VERIFIER_PATH_RE = re.compile(
    r"(^|[\s`'\"(/])tests/(test\.sh|test_[\w-]+\.py)|/oracle/|\btest_state\.py\b")
SIZE_RE = re.compile(r'getsize\([^)]*\)\s*[<>=]+\s*(\d{2,})')


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def ngrams(text: str, n: int = 8) -> set[str]:
    toks = norm(text).split()
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def main() -> None:
    tasks: dict[str, dict] = {}
    with tarfile.open(TAR) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            tid, rel = parts[1], "/".join(parts[2:])
            tasks.setdefault(tid, {})[rel] = tf.extractfile(m).read().decode(
                "utf-8", "replace")

    rows = []
    for tid, files in sorted(tasks.items()):
        instruction = files.get("instruction.md", "")
        tests = "\n".join(v for k, v in files.items() if k.startswith("tests/"))
        env_blob = "\n".join(v for k, v in files.items()
                             if k.startswith("environment/") or k == "task.toml")
        env_names = {"/" + k.removeprefix("environment/") for k in files
                     if k.startswith("environment/")}
        if not tests:
            continue

        # --- direction 1: dark checks (verifier asserts the invisible) ---
        asserted = {p.rstrip("/") for p in PATH_RE.findall(tests)
                    if not any(n in p for n in NOISE)}
        dark = []
        for p in asserted:
            visible = (p in instruction or p in env_blob
                       or any(p.endswith(Path(e).name) for e in env_names))
            # A path is discoverable when any ancestor directory is named and
            # the remaining components are too: "create manager1/config.json
            # under /root/.docker/machine/machines/" names no full path yet
            # leaves nothing to guess. Checking only the immediate parent
            # reports those as invisible, which is how a scan ends up claiming
            # a fifth of a well-specified corpus is unsolvable.
            if not visible:
                anc = Path(p)
                while not visible and anc.parent != anc:
                    anc = anc.parent
                    if str(anc) == "/":
                        break
                    if str(anc) in instruction or str(anc) in env_blob:
                        rest = Path(p).relative_to(anc).parts
                        visible = all(part in instruction or part in env_blob
                                      for part in rest)
            if not visible:
                dark.append(p)

        # --- direction 2: leaks (instruction reveals the hidden verifier) ---
        leak_reasons = []
        # Only the harness's own verifier counts. Matching any path containing
        # "/test/" flags tasks whose deliverable IS a test file (a Java project
        # under src/test/java, a pytest suite the agent is asked to write) —
        # naming your own deliverable is the specification, not a leak.
        if VERIFIER_PATH_RE.search(instruction):
            leak_reasons.append("names the verifier path")
        literals = [s for s in LITERAL_RE.findall(tests) if len(s) >= 6]
        quoted = [s for s in literals if s in instruction]
        if quoted:
            leak_reasons.append(f"quotes {len(quoted)} verifier literal(s)")
        sizes = [s for s in SIZE_RE.findall(tests) if s in instruction]
        if sizes:
            leak_reasons.append(f"states verifier size threshold(s): {sizes[:3]}")
        # The 8-gram overlap signal is kept for inspection but not counted:
        # instruction and verifier share the task's own nouns and filenames by
        # construction, so at a >=3 threshold it fired on 100% of the corpus,
        # which is the same as measuring nothing.
        shared = ngrams(instruction) & ngrams(tests)

        rows.append({"task_id": tid, "asserted_paths": len(asserted),
                     "dark_paths": sorted(dark), "leak_reasons": leak_reasons,
                     "shared_8grams": len(shared)})

    with open(OUT_JL, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    with_asserts = [r for r in rows if r["asserted_paths"] > 0]
    dark_any = [r for r in with_asserts if r["dark_paths"]]
    dark_all = [r for r in with_asserts
                if len(r["dark_paths"]) == r["asserted_paths"]]
    leak_any = [r for r in rows if r["leak_reasons"]]
    both = [r for r in rows if r["leak_reasons"] and r["dark_paths"]]
    reason_counts = Counter(reason.split(":")[0].split(" with")[0]
                            for r in leak_any for reason in r["leak_reasons"])

    md = [
        "# instruction <-> verifier alignment audit",
        "",
        f"Tasks with a verifier: {len(rows)}",
        "",
        "## Says too little — verifier asserts what the agent cannot discover",
        f"- tasks whose verifier asserts filesystem paths: {len(with_asserts)}",
        f"- at least one path invisible to the agent: **{len(dark_any)}** "
        f"({100*len(dark_any)/max(1,len(with_asserts)):.1f}%)",
        f"- every asserted path invisible: **{len(dark_all)}**",
        "",
        "## Says too much — instruction reveals the hidden verifier",
        f"- tasks with at least one leak signal: **{len(leak_any)}** "
        f"({100*len(leak_any)/max(1,len(rows)):.1f}%)",
        "",
        "| signal | tasks |",
        "|---|---|",
    ]
    for reason, n in reason_counts.most_common():
        md.append(f"| {reason} | {n} |")
    md += [
        "",
        f"## Both problems at once: {len(both)}",
        "",
        "Both scans are lexical and therefore upper bounds. A dark path may be "
        "inferable from convention (installing nginx implies /etc/nginx/nginx.conf); "
        "an instruction that states its acceptance criteria in prose is not "
        "necessarily leaking a hidden check. Treat rows as review candidates.",
    ]
    OUT_MD.write_text("\n".join(md) + "\n")
    print("\n".join(md[:20]))


if __name__ == "__main__":
    main()
