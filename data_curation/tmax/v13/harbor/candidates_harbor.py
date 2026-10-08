#!/usr/bin/env python3
"""Candidate inventory ({CANDIDATES_PATH}) for a v13-harbor2 judge: paths and line numbers only, no task prose.

The v12 extractor (../../v12/candidates_extract.py) reads instruction.md, setup.sh and tests/test.sh -- the RST staged
shape, where tests/test.sh held the verifier. On a native harbor package it therefore never looked at the verifier or
the image: for the 99 TerminalWorld packages of the v13-harbor v1 run it found failing statements in 4 (every one a
bootstrap `exit 1` guard) and program paths only in instruction.md and the bootstrap. This extractor keeps the v12
regexes and reads what the v2.1 judge reads (harbor_files.read_list):

  programs            -- absolute paths mentioned in instruction.md, environment/Dockerfile, each text
                         build-context file, tests/test.sh and tests/test_state.py: {path, seen_in: [file:line, ...]}
  failing_statements  -- tests/test_state.py lines that can fail the run (def test_, assert, pytest.fail,
                         sys.exit(1), exit 1, raise), numbered in that file: {line, kind}
  verifier_lines      -- the line count of tests/test_state.py
  copied_files        -- the build-context files, as harbor_files.copied_files gives them (rel, copy_line, text, bytes)
  imports             -- third-party modules tests/test_state.py imports, and whether the bootstrap's --with list
                         names them: {module, line, in_bootstrap_with}. The judge is told not to use it.

    python3 candidates_harbor.py <tasks_dir> <out_dir> [task_id ...]
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "v12"))
sys.path.insert(1, os.path.join(HERE, "..", "tools"))   # the same candidates_extract.py in the PR layout
import candidates_extract as ce  # noqa: E402
import harbor_files as hf  # noqa: E402

WITH_RE = re.compile(r"(?:--with|-w)[ =]+('[^']*'|\"[^\"]*\"|[^\s\\]+)")


def with_packages(bootstrap_text):
    """The package names of the bootstrap's --with / -w specs, quoted or not (`name==1.2`, `'name @ file:///x'`)."""
    joined = re.sub(r"\\\n", " ", bootstrap_text)
    return [re.split(r"[=<>!~@ \[]", s.strip("'\""), 1)[0] for s in WITH_RE.findall(joined)]


def extract(task_dir):
    texts = hf.load_texts(task_dir)
    programs = {}
    for name, text in texts.items():
        for i, line in enumerate(text.splitlines(), 1):
            for m in ce.PATH_RE.finditer(line):
                if line[m.end():m.end() + 1] in ("{", "$", "*", "%"):
                    continue  # a format-string or glob prefix, not a path
                p = m.group(1).rstrip(".,;:)\"'")
                if ce.looks_like_file(p):
                    programs.setdefault(p, []).append(f"{name}:{i}")
    keys = sorted(programs)
    drop = {p for p in keys if "." not in p.split("/")[-1] and any(q != p and q.startswith(p + "/") for q in keys)}
    programs = {p: v for p, v in programs.items() if p not in drop}

    verifier = texts[hf.VERIFIER].splitlines()
    fails = []
    for i, line in enumerate(verifier, 1):
        for kind, rx in ce.FAIL_RE:
            if rx.search(line):
                fails.append({"line": i, "kind": kind})
                break
    withs = {w.lower().replace("_", "-") for w in with_packages(texts[hf.BOOTSTRAP])}
    imports, seen = [], set()
    for i, line in enumerate(verifier, 1):
        m = ce.IMPORT_RE.match(line)
        if not m:
            continue
        for mod in ([m.group(1)] if m.group(1) else [x.strip() for x in m.group(2).split(",")]):
            root = mod.split(".")[0]
            if not root or root in ce.STDLIB or root in seen:
                continue
            seen.add(root)
            imports.append({"module": root, "line": i, "in_bootstrap_with": root.lower().replace("_", "-") in withs})
    return {"programs": [{"path": p, "seen_in": v} for p, v in sorted(programs.items())],
            "failing_statements": fails, "verifier_lines": len(verifier),
            "copied_files": hf.copied_files(task_dir), "imports": imports}


def main():
    root, out = sys.argv[1], sys.argv[2]
    ids = sys.argv[3:] or sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    os.makedirs(out, exist_ok=True)
    for tid in ids:
        c = extract(os.path.join(root, tid)); c["task_id"] = tid
        dst = os.path.join(out, f"{tid}.json")
        assert not os.path.exists(dst), f"{dst} exists: candidates go to a fresh directory"
        json.dump(c, open(dst, "w"), indent=1)
    print(f"{len(ids)} candidate lists written to {out}")


if __name__ == "__main__":
    main()
