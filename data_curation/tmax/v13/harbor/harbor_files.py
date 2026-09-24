#!/usr/bin/env python3
"""The files a v13-harbor2 judge reads for one harbor-format task, resolved the way the harness builds the image.

One definition, used by the stager (which lists them in the judge's wrapper), the candidate extractor (which scans
them) and the harbor row lint (which checks anchors and paths against them):

    instruction.md, environment/Dockerfile, tests/test.sh, tests/test_state.py, then every build-context file a
    COPY/ADD line brings in -- in that order, the order of the prompt's §1.

Build-context resolution mirrors the harness's own `_build_context` (external/torchtitan-cotrain/torchtitan/
experiments/rl/examples/tmax/prepare_rts_data.py:252-290, with `_LOCAL_COPY`, `_COPY_HEREDOC`, `_COPY_FLAG` at
:80-85 and `_join_continuations` at :179-186): a COPY/ADD without `--from=`, heredoc COPYs skipped, ownership flags
dropped, every source but the last word, a directory source walked whole. `--crosscheck <tasks_dir> <prepare_rts_data.py>`
runs the harness's own functions (lifted from the file by `ast`, since the module needs torch to import) over every
task and compares the file sets.

Nothing here prints task content: only paths, line numbers and sizes.
"""
import ast, os, re, shlex, sys

LOCAL_COPY = re.compile(r"^\s*(?:COPY|ADD)\s+(?!--from=)(.+?)\s*$", re.I)
COPY_HEREDOC = re.compile(r"^<<-?")
COPY_FLAG = re.compile(r"^--(chown|chmod|link)=")
BOOTSTRAP, VERIFIER, DOCKERFILE, INSTRUCTION = "tests/test.sh", "tests/test_state.py", "environment/Dockerfile", \
    "instruction.md"


def _logical_lines(text):
    """(first physical line number, joined text) per Dockerfile instruction, folding backslash continuations as the
    harness's `_join_continuations` does (`\\\\\\n\\s*` -> one space)."""
    out, buf, start = [], None, 0
    for n, line in enumerate(text.split("\n"), 1):
        if buf is None:
            buf, start = line, n
        else:
            buf += " " + line.lstrip()
        if buf.endswith("\\"):
            buf = buf[:-1]
            continue
        out.append((start, buf)); buf = None
    if buf is not None:
        out.append((start, buf))
    return out


def is_text(blob: bytes) -> bool:
    if b"\0" in blob:
        return False
    try:
        blob.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def copied_files(task_dir):
    """[{rel: 'environment/<path>', copy_line: int, text: bool, bytes: int}] in Dockerfile order, each file once."""
    env = os.path.join(task_dir, "environment")
    text = open(os.path.join(env, "Dockerfile"), errors="replace").read()
    out, seen = [], set()
    for n, line in _logical_lines(text):
        m = LOCAL_COPY.match(line)
        if not m or COPY_HEREDOC.match(m.group(1)):
            continue
        parts = [p for p in shlex.split(m.group(1)) if not COPY_FLAG.match(p)]
        for src in parts[:-1]:
            ap = os.path.normpath(os.path.join(env, src))
            files = sorted(os.path.join(dp, f) for dp, _d, fs in os.walk(ap) for f in fs) if os.path.isdir(ap) \
                else ([ap] if os.path.exists(ap) else [])
            for f in files:
                rel = "environment/" + os.path.relpath(f, env)
                if rel in seen or rel == DOCKERFILE:
                    continue
                seen.add(rel)
                blob = open(f, "rb").read()
                out.append({"rel": rel, "copy_line": n, "text": is_text(blob), "bytes": len(blob)})
    return out


def read_list(task_dir):
    """The judge's read list: [(rel, kind)] with kind in instruction/image/copied/copied-binary/bootstrap/verifier."""
    out = [(INSTRUCTION, "instruction"), (DOCKERFILE, "image"), (BOOTSTRAP, "bootstrap"), (VERIFIER, "verifier")]
    out += [(c["rel"], "copied" if c["text"] else "copied-binary") for c in copied_files(task_dir)]
    return out


def load_texts(task_dir):
    """{rel: text} for every readable text file of the read list (binary copied files are absent)."""
    return {rel: open(os.path.join(task_dir, rel), errors="replace").read()
            for rel, kind in read_list(task_dir) if kind != "copied-binary"}


def _harness_build_context(prd_path):
    """The harness's `_build_context`, executed from its own source without importing the module."""
    tree = ast.parse(open(prd_path).read())
    want_fn = {"_join_continuations", "_build_context"}
    want_var = {"_LOCAL_COPY", "_COPY_HEREDOC", "_COPY_FLAG", "_MAX_CONTEXT_BYTES"}
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in want_fn)
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in want_var for t in n.targets))]
    assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == want_fn, "harness functions moved"
    ns = {"os": os, "re": re, "shlex": shlex, "base64": __import__("base64")}
    exec(compile(ast.Module(body=body, type_ignores=[]), prd_path, "exec"), ns)
    return ns["_build_context"]


def crosscheck(tasks_dir, prd_path):
    bc = _harness_build_context(prd_path)
    ids = sorted(d for d in os.listdir(tasks_dir) if os.path.isdir(os.path.join(tasks_dir, d)))
    bad = 0
    for tid in ids:
        env = os.path.join(tasks_dir, tid, "environment")
        theirs = {"environment/" + k for k in bc(env, open(os.path.join(env, "Dockerfile")).read())}
        ours = {c["rel"] for c in copied_files(os.path.join(tasks_dir, tid))}
        if theirs != ours:
            bad += 1
            print(f"{tid}: harness {len(theirs)} files, ours {len(ours)}")
    print(f"crosscheck: {len(ids) - bad} of {len(ids)} tasks resolve the same build-context file set as the harness")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--crosscheck":
        sys.exit(crosscheck(sys.argv[2], sys.argv[3]))
    print(__doc__); sys.exit(2)
