#!/usr/bin/env python3
"""Names a verifier depends on that nothing the agent can read states.

A hardened task's verifier is written against the reference solution, so it
inherits that solution's private vocabulary: the key names of a report it
parses, the line labels it anchors a regex on, the filename it expects an
artifact under. The instruction is written last and describes those things in
prose. A policy that does all the work then writes `source_basename:` where
the verifier reads `report["source"]`, or `- Commit: <sha>` where the verifier
wants `^Commit:`, and scores zero. Reviewed on wd-20260903b (2026-09-03): of
eight hardened tasks that went 0/16, five failed on exactly this, three of
them with every other requirement met.

The dark-path audit (synth_loop.audit) catches the same defect for paths; this
is the same idea for string literals. It reads the verifier's AST and takes
the strings in *requirement positions*: dict subscripts and .get() keys,
values it compares against, runs of plain text inside the regexes it matches,
path components it opens, constants it assigns and the displays it builds
expected values from. It drops what the verifier itself plants (strings it
writes into files, mutations it applies, commands it runs, assertion
messages), keeps only key-, label- and filename-shaped literals, and then
asks whether each occurs, as a whole word, anywhere the agent can read: the
instruction, the Dockerfile, any file the image ships from the build context.
What the seed's verifier already depended on unseen is inherited, not new.

Heuristic by construction. Measured over 307 hardened packages: median 2
flagged per task, 182 with none; the five known hidden contracts came back
with 15 of their 18 names. A flagged name is a question for the author, and
the answer is either to state it where the agent reads or to make the
verifier stop depending on it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

RE_FUNCS = {"search", "match", "fullmatch", "compile", "findall", "finditer", "sub", "split"}
READ_FUNCS = {"get", "pop", "setdefault", "startswith", "endswith", "rstrip", "lstrip",
              "strip", "split", "partition", "rpartition", "find", "index", "count"}
WRITE_FUNCS = {"write_text", "write_bytes", "write", "dump", "dumps", "safe_dump",
               "dump_all", "run", "check_output", "check_call", "Popen", "call",
               "communicate", "mkdir", "makedirs"}
PLANTING_MODULES = {"subprocess", "os", "shutil"}
ALLOW = {"utf-8", "utf8", "ascii", "latin-1", "strict", "ignore", "replace", "surrogateescape",
         "__main__", "python3", "python", "bash", "stdout", "stderr", "returncode",
         "True", "False", "None"}
# identifier keys, `Label:` lines, file names, extensions, relative paths, CONSTANTS
SHAPE = re.compile(r"^(?:[a-z][a-z0-9_]{2,}|[A-Z][A-Za-z0-9_ -]*:|[\w.-]+\.[a-z0-9]{1,5}"
                   r"|\.[a-z0-9]{1,5}|[\w.-]+/[\w./-]+|[A-Z][A-Z0-9_]{2,})$")
CONTEXT_FILE_MAX = 256 * 1024


def kind_of(verifier_rel: str) -> str:
    return "python" if verifier_rel.endswith(".py") else "shell"


def _regex_runs(pattern: str) -> set[str]:
    """The plain-text runs a regex insists on: `(?mi)^Commit:\\s*` -> {"Commit:"}."""
    pattern = re.sub(r"\\[sdwSDWbBAZ]", " ", pattern)
    pattern = re.sub(r"\(\?[a-z]+\)", "", pattern)
    pattern = re.sub(r"\\(.)", r"\1", pattern)
    return {r.strip() for r in re.split(r"[\^$.|?*+()\[\]{}]", pattern) if len(r.strip()) >= 3}


def _str_consts(node: ast.AST):
    for c in ast.walk(node):
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            yield c.value


def _display_consts(node: ast.AST):
    """String constants that are elements, keys or values of a display,
    nested displays included, calls inside it excluded: `{"commit": git("rev-parse",
    "HEAD")}` yields "commit" and not "HEAD"."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node.value
    elif isinstance(node, ast.Dict):
        for k in node.keys:
            if k is not None:
                yield from _display_consts(k)
        for v in node.values:
            yield from _display_consts(v)
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for e in node.elts:
            yield from _display_consts(e)


def _joined_parts(node: ast.JoinedStr):
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            yield v.value


def _call_name(n: ast.Call) -> str:
    if isinstance(n.func, ast.Attribute):
        return n.func.attr
    if isinstance(n.func, ast.Name):
        return n.func.id
    return ""


def _plants(n: ast.Call) -> bool:
    if _call_name(n) in WRITE_FUNCS:
        return True
    f = n.func
    return (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
            and f.value.id in PLANTING_MODULES)


def extract_python(src: str) -> tuple[set[str], set[str]]:
    """(requirement literals, planted literals) of a Python verifier."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set(), set()
    assigned: dict[str, ast.AST] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            assigned[n.targets[0].id] = n.value
    # Dicts the verifier builds for itself (`expected = {"commit": sha}`) are
    # its own bookkeeping; reading them back is not a demand on the agent.
    own_dicts = {name for name, v in assigned.items()
                 if isinstance(v, ast.Dict) or (isinstance(v, ast.Call) and _call_name(v) == "dict")}
    planted: set[str] = set()
    req: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _plants(n):
            planted.update(_str_consts(n))
            for a in ast.walk(n):
                if isinstance(a, ast.Name) and a.id in assigned:
                    planted.update(_str_consts(assigned[a.id]))
        elif isinstance(n, ast.Assert) and n.msg is not None:
            planted.update(_str_consts(n.msg))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            name = _call_name(n)
            if name in RE_FUNCS and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    req.update(_regex_runs(a.value))
                elif isinstance(a, ast.JoinedStr):
                    for part in _joined_parts(a):
                        req.update(_regex_runs(part))
            if name in READ_FUNCS and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                req.add(n.args[0].value)
            if name in ("Path", "join"):
                for a in n.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        req.add(a.value)
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, str):
            if not (isinstance(n.value, ast.Name) and n.value.id in own_dicts):
                req.add(n.slice.value)
        elif isinstance(n, ast.For) and isinstance(n.iter, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            req.update(_display_consts(n.iter))
        elif isinstance(n, ast.Compare):
            for s in [n.left, *n.comparators]:
                if isinstance(s, ast.Constant) and isinstance(s.value, str):
                    req.add(s.value)
                elif isinstance(s, ast.JoinedStr):
                    req.update(_joined_parts(s))
                elif isinstance(s, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                    req.update(_display_consts(s))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) \
                and isinstance(n.right, ast.Constant) and isinstance(n.right.value, str):
            req.add(n.right.value)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                req.add(n.value.value)
            elif isinstance(n.value, (ast.List, ast.Tuple, ast.Set)) or (
                    isinstance(n.value, ast.Dict) and _spec_dict(n.value)):
                req.update(_display_consts(n.value))
    return req, planted


def _spec_dict(node: ast.Dict) -> bool:
    """A dict display that spells out expected content (some value is a
    literal or a display), as opposed to one the verifier fills from what it
    computes (`{"commit": git(...)}`), whose keys bind nobody."""
    return any(isinstance(v, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict))
               for v in node.values)


def extract_shell(src: str) -> tuple[set[str], set[str]]:
    """A bash verifier: the quoted strings its greps and tests look for."""
    req = set()
    for m in re.finditer(r"""(?:grep|test|\[\[?|==|-e|-f|-d|cat)\s+[^\n]*?["']([^"'\n]{3,80})["']""", src):
        req.add(m.group(1))
    return req, set()


def extract(src: str, kind: str = "python") -> set[str]:
    """The literals a verifier depends on, planted ones and noise removed."""
    req, planted = extract_python(src) if kind == "python" else extract_shell(src)
    planted_words = {w for p in planted for w in re.findall(r"[A-Za-z]{4,}", p)}

    def keep(v: str) -> bool:
        v = v.strip()
        if not (3 <= len(v) <= 80) or v in ALLOW or "\n" in v:
            return False
        if v.startswith("-") or v.startswith("/") or not re.search(r"[A-Za-z]{3}", v):
            return False        # flags; absolute paths are the dark-path audit's
        if any(v in p for p in planted if p != v):
            return False        # the verifier wrote it into the task itself
        if " " in v and any(w in planted_words for w in re.findall(r"[A-Za-z]{4,}", v)):
            return False        # a phrase built from what the verifier planted
        return True

    return {v.strip() for v in req if keep(v)}


def visible_text(pkg: Path, instruction: str | None = None, dockerfile: str | None = None) -> str:
    """Everything the agent can read: the instruction, the Dockerfile, and
    every file the build context ships into the image."""
    parts = [instruction if instruction is not None else _read(pkg / "instruction.md"),
             dockerfile if dockerfile is not None else _read(pkg / "environment" / "Dockerfile")]
    env = pkg / "environment"
    if env.is_dir():
        for f in sorted(env.rglob("*")):
            if f.is_file() and f.name != "Dockerfile" and f.stat().st_size <= CONTEXT_FILE_MAX:
                parts.append(f.read_text(errors="replace"))
    return "\n".join(parts)


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _visible(literal: str, text: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(literal) + r"(?![\w])", text) is not None


def unseen(src: str, kind: str, visible: str, baseline=()) -> list[str]:
    """Key-, label- and filename-shaped literals the verifier depends on that
    the visible text never states, minus `baseline` (what the seed's verifier
    already depended on unseen)."""
    base = set(baseline)
    return sorted(v for v in extract(src, kind)
                  if SHAPE.match(v) and v not in base and not _visible(v, visible))


def audit_package(pkg: Path, verifier_rel: str, baseline=()) -> list[str]:
    """`unseen` over a package directory laid out on disk."""
    return unseen(_read(pkg / verifier_rel), kind_of(verifier_rel), visible_text(pkg), baseline)


def why(literals: list[str]) -> str:
    return ("The verifier depends on names that nothing an agent can read states: "
            + ", ".join(repr(x) for x in literals)
            + ". They are not in the instruction, the Dockerfile or any file the "
              "image ships; the reference solution knows them, an agent that "
              "reads the task does not. For each one, either state it where the "
              "agent will read it (the instruction, or a file in the image the "
              "instruction points at), or make the verifier stop depending on it; "
              "a value the verifier only plants as test data can also move into a "
              "fixture the image ships.")
