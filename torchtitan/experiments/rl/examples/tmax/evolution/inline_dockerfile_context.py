#!/usr/bin/env python3
"""Rewrite task Dockerfiles to need no build context ("菜谱自带食材").

Daytona's server-side build takes a bare Dockerfile; COPY/ADD from build
context requires an upload permission our tier (and Yichuan's) lacks. But the
referenced files ship inside each task package, so we inline them:

  - text file   -> COPY <<'EOF_...' /dest  ... EOF_...  (heredoc)
  - binary file -> RUN mkdir -p $(dirname /dest) && echo '<base64>' | base64 -d > /dest
  - directory   -> recurse into files (same rules)
  - --chmod/--chown flags -> emitted as a RUN chmod/chown after the write

Tasks it cannot fully rewrite (source genuinely absent, or inlined payload
over the size cap) are recorded and left unchanged.

Usage:
  uv run python scripts/inline_dockerfile_context.py \
      --tar data/seed-dataset/data/tasks-00000.tar --out data/seed-dataset-ctxfree
Writes: <out>/dockerfiles/<task_id>.Dockerfile for rewritten tasks,
        <out>/report.jsonl (one row per task: rewritten|unchanged|failed).
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import tarfile
from pathlib import Path

MAX_INLINE = 512 * 1024  # per-task cap on inlined bytes; beyond this, skip

COPY_RE = re.compile(r"^\s*(COPY|ADD)\s+(.*)$", re.I)


def parse_flags(args: list[str]) -> tuple[dict, list[str]]:
    flags, rest = {}, []
    for a in args:
        if a.startswith("--"):
            k, _, v = a.partition("=")
            flags[k] = v
        else:
            rest.append(a)
    return flags, rest


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def heredoc(data: bytes, dest: str, n: int) -> str:
    tag = f"EOF_INLINE_{n}"
    body = data.decode("utf-8")
    if not body.endswith("\n"):
        body += "\n"
    return f"COPY <<'{tag}' {dest}\n{body}{tag}"


def b64_run(data: bytes, dest: str) -> str:
    payload = base64.b64encode(data).decode()
    return (f"RUN mkdir -p \"$(dirname '{dest}')\" && "
            f"echo '{payload}' | base64 -d > '{dest}'")


def _join_continuations(dockerfile: str) -> list[str]:
    """Yield logical Dockerfile lines: backslash continuations joined with a
    space, heredoc bodies kept verbatim so their content is never reinterpreted."""
    out: list[str] = []
    buf: list[str] = []
    tag = None
    for raw in dockerfile.splitlines():
        if tag is not None:
            buf.append(raw)
            if raw.strip() == tag:
                out.append("\n".join(buf)); buf, tag = [], None
            continue
        m = re.search(r"<<-?['\"]?(\w+)['\"]?", raw)
        if m and (buf or re.match(r"\s*(COPY|RUN|ADD)\b", raw, re.I)):
            tag = m.group(1); buf.append(raw); continue
        buf.append(raw)
        if not raw.rstrip().endswith("\\"):
            out.append(" ".join(x.strip().rstrip("\\").strip() for x in buf)
                       if len(buf) > 1 else buf[0])
            buf = []
    if buf:
        out.append(" ".join(x.strip().rstrip("\\").strip() for x in buf))
    return out


def rewrite(dockerfile: str, env: dict[str, bytes], task_id: str) -> tuple[str | None, str]:
    """Return (new_dockerfile, status). status: rewritten|unchanged|failed:<why>."""
    out, changed, inlined, n = [], False, 0, 0
    # Join backslash continuations first. Scanning raw lines makes
    # `COPY a.yml \` parse with dest="\", and the emitted heredoc then reads
    # `COPY <<'TAG' \` — a Dockerfile that builds a shell script with an
    # unterminated destination, which fails much later and looks unrelated.
    for raw in _join_continuations(dockerfile):
        m = COPY_RE.match(raw)
        if not m:
            out.append(raw)
            continue
        flags, parts = parse_flags(m.group(2).split())
        if "--from" in flags or (parts and parts[0].startswith("<<")):
            out.append(raw)  # multi-stage copy / heredoc already: fine as-is
            continue
        if len(parts) < 2:
            out.append(raw)
            continue
        *srcs, dest = parts
        emitted = []
        for src in srcs:
            base = src.lstrip("./").rstrip("/")
            hits = {p: d for p, d in env.items()
                    if p == base or p.startswith(base + "/")}
            if not hits and "*" in base:
                pat = re.escape(base).replace(r"\*", "[^/]*") + "$"
                hits = {p: d for p, d in env.items() if re.match(pat, p)}
            if not hits:
                return None, f"failed:source '{src}' not in package"
            for rel, data in sorted(hits.items()):
                # destination: dir-style if multiple files or trailing slash
                if len(hits) > 1 or dest.endswith("/") or rel != base:
                    sub = rel[len(base):].lstrip("/") if rel != base else Path(rel).name
                    d = dest.rstrip("/") + "/" + (sub or Path(rel).name)
                else:
                    d = dest
                inlined += len(data)
                if inlined > MAX_INLINE:
                    return None, "failed:inline payload over cap"
                n += 1
                emitted.append(heredoc(data, d, n) if is_text(data) else b64_run(data, d))
                if "--chmod" in flags:
                    emitted.append(f"RUN chmod {flags['--chmod']} '{d}'")
                if "--chown" in flags:
                    emitted.append(f"RUN chown {flags['--chown']} '{d}'")
        out.append("\n".join(emitted))
        changed = True
    return ("\n".join(out) + "\n", "rewritten") if changed else (None, "unchanged")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    outdir = Path(args.out)
    (outdir / "dockerfiles").mkdir(parents=True, exist_ok=True)

    # gather per-task environment files + dockerfile
    tasks: dict[str, dict] = {}
    with tarfile.open(args.tar) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            tid, rel = parts[1], "/".join(parts[2:])
            t = tasks.setdefault(tid, {"env": {}, "dockerfile": None})
            if rel == "environment/Dockerfile":
                t["dockerfile"] = tf.extractfile(m).read().decode("utf-8", "replace")
            elif rel.startswith("environment/"):
                t["env"][rel.removeprefix("environment/")] = tf.extractfile(m).read()

    counts = {"rewritten": 0, "unchanged": 0, "failed": 0}
    with open(outdir / "report.jsonl", "w") as rep:
        for tid, t in sorted(tasks.items()):
            new, status = rewrite(t["dockerfile"] or "", t["env"], tid)
            if status == "rewritten":
                (outdir / "dockerfiles" / f"{tid}.Dockerfile").write_text(new)
            counts["failed" if status.startswith("failed") else status] += 1
            rep.write(json.dumps({"task_id": tid, "status": status}) + "\n")
    print(counts)


if __name__ == "__main__":
    main()
