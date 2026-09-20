#!/usr/bin/env python3
"""Runner-side candidate inventory for the V12 judge (paths and line numbers only; no task prose is emitted).

For each task directory:
  programs  -- absolute paths the three files mention that look like files (not directories, not toolchain):
              instruction.md mentions, setup.sh build/copy/chmod targets and heredoc-created files, tests/test.sh
              reads/executes. Each entry: {path, seen_in:[file:line,...]}.
  failing_statements -- tests/test.sh lines that can fail the run: def test_, assert, pytest.fail, sys.exit(1),
              exit 1, raise. Each entry: {line, kind}.
  imports   -- third-party Python modules tests/test.sh imports (not stdlib, not pytest) and whether setup.sh has an
              install line naming them: {module, line, installed_by_setup: bool}.
The judge must dispose of every `programs` entry (excluded_material / a Step-3 field / benign) and cover every
`failing_statements` line in `assertions`; it may add items the extractor missed. This is a superset of leads, not a
semantic certification. Usage: candidates_extract.py <tasks_root> <out_dir> [task_id ...]
"""
import json
import os
import re
import sys

PATH_RE = re.compile(r"(?<![\w./-])(/(?:app|home|opt|tmp|workspace|srv|var|root|usr/local|data|mnt)(?:/[A-Za-z0-9_.+-]+)+)")
TOOLCHAIN_BASENAMES = {"python", "python3", "bash", "sh", "gcc", "g++", "cc", "make", "cargo", "rustc", "go", "node", "npm",
                       "pip", "pip3", "java", "javac", "perl", "ruby", "sqlite3", "ffmpeg", "curl", "wget", "git", "tar", "gzip"}
DIR_HINTS = ("/app", "/home/user", "/workspace", "/tmp", "/opt", "/root", "/var/log", "/srv", "/data")
FAIL_RE = [("def test_", re.compile(r"^\s*def test_")), ("assert", re.compile(r"^\s*assert\b")),
           ("pytest.fail", re.compile(r"pytest\.fail")), ("sys.exit(1)", re.compile(r"sys\.exit\(\s*1\s*\)")),
           ("exit 1", re.compile(r"\bexit\s+1\b")), ("raise", re.compile(r"^\s*raise\b"))]
IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w]*)|import\s+([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*))")
try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:
    STDLIB = set()
STDLIB |= {"pytest", "typing_extensions"}


def read(p):
    return open(p, errors="replace").read() if os.path.exists(p) else ""


def looks_like_file(path):
    base = path.rstrip("/").split("/")[-1]
    if not base or base in TOOLCHAIN_BASENAMES:
        return False
    if path.rstrip("/") in DIR_HINTS:
        return False
    # a directory-looking path: no extension and ends a known dir hint; keep paths with an extension or a hyphen/underscore name
    return True


def extract(task_dir):
    files = {"instruction.md": read(os.path.join(task_dir, "instruction.md")),
             "setup.sh": read(os.path.join(task_dir, "setup.sh")),
             "tests/test.sh": read(os.path.join(task_dir, "tests", "test.sh"))}
    programs = {}
    for name, text in files.items():
        for i, line in enumerate(text.splitlines(), 1):
            for m in PATH_RE.finditer(line):
                if line[m.end():m.end() + 1] in ("{", "$", "*", "%"):
                    continue  # a format-string or glob prefix (f"/x/app_{d}.log", "/x/frame_%d"), not a path
                p = m.group(1).rstrip(".,;:)\"'")
                if looks_like_file(p):
                    programs.setdefault(p, []).append(f"{name}:{i}")
    # drop pure directories: a path that is a strict prefix of another candidate and has no extension
    keys = sorted(programs)
    drop = {p for p in keys if "." not in p.split("/")[-1] and any(q != p and q.startswith(p + "/") for q in keys)}
    programs = {p: v for p, v in programs.items() if p not in drop}
    test = files["tests/test.sh"].splitlines()
    fails = []
    for i, line in enumerate(test, 1):
        for kind, rx in FAIL_RE:
            if rx.search(line):
                fails.append({"line": i, "kind": kind})
                break
    setup = files["setup.sh"]
    imports = []
    seen = set()
    for i, line in enumerate(test, 1):
        m = IMPORT_RE.match(line)
        if not m:
            continue
        mods = [m.group(1)] if m.group(1) else [x.strip() for x in m.group(2).split(",")]
        for mod in mods:
            root = mod.split(".")[0]
            if not root or root in STDLIB or root in seen:
                continue
            seen.add(root)
            installed = bool(re.search(rf"pip3?\s+install[^\n]*\b{re.escape(root)}\b|apt-get install[^\n]*python3-{re.escape(root)}\b", setup))
            imports.append({"module": root, "line": i, "installed_by_setup": installed})
    return {"programs": [{"path": p, "seen_in": v} for p, v in sorted(programs.items())],
            "failing_statements": fails, "test_sh_lines": len(test), "imports": imports}


def main():
    root, out = sys.argv[1], sys.argv[2]
    ids = sys.argv[3:] or sorted(d for d in os.listdir(root) if d.startswith("task_"))
    os.makedirs(out, exist_ok=True)
    stats = []
    for tid in ids:
        d = os.path.join(root, tid)
        if not os.path.isdir(d):
            continue
        c = extract(d)
        c["task_id"] = tid
        json.dump(c, open(os.path.join(out, f"{tid}.json"), "w"), indent=1)
        stats.append((tid, len(c["programs"]), len(c["failing_statements"]), len(c["imports"])))
    n = len(stats)
    print(f"tasks {n} | programs/task median {sorted(s[1] for s in stats)[n // 2]} max {max(s[1] for s in stats)} | failing statements/task median {sorted(s[2] for s in stats)[n // 2]} | tasks with 3rd-party imports {sum(1 for s in stats if s[3])}")


if __name__ == "__main__":
    main()
