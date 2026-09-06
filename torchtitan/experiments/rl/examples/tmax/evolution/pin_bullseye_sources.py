#!/usr/bin/env python3
"""Pin the apt sources of every bullseye-based task environment to a Debian snapshot.

Debian 11 (bullseye) left LTS on 2026-08-31. Since then deb.debian.org's
bullseye-security index names .deb files its pool no longer serves (404), so a
cold `apt-get install` fails on whichever task happens to need one of them, and
that index's Release file expires on 2026-09-07, after which `apt-get update`
itself exits 100 on every bullseye image. Both go away by pointing every suite
at snapshot.debian.org -- the same snapshot the official debian:bullseye image
carries commented out in its own sources.list -- and telling apt to accept that
snapshot's aged Release file.

Two places carry a copy of each Dockerfile and BOTH get the pin: the source
package (`<tasks-dir>/<id>/environment/Dockerfile`, what future folds and the
published tar are built from) and the live mix row (`metadata.dockerfile`,
what training builds right now). The pin is one RUN block inserted after the
first FROM, before anything runs apt; it is idempotent, so a Dockerfile that
already names snapshot.debian.org is left alone.

Selection: a Dockerfile whose FROM tag says bullseye (debian:bullseye[-slim],
node:18-bullseye, golang:1.21-bullseye, ...) or debian:11, and that runs
apt-get or apt at build time. --ids narrows or overrides that.

Dry run by default. --apply backs every package Dockerfile up under
--backup-dir first (default: <tasks-dir>/../../archive/task-backups-bullseye-<stamp>,
so a tasks dir at <root>/data/tw-extract/tasks backs up under <root>/archive/),
then writes; the mix goes through layout.write_mix, which on a root's live mix
publishes the next version, so the history is that backup.

  pin_bullseye_sources.py --tasks-dir $TRL_BASE/data/tw-extract/tasks \
      [--mix $ROOT/data/mix/live.jsonl] [--ids tw_538641 ...] [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
# layout is imported where the mix is written: importing the package pulls in
# torch, and pinning packages alone (a dry run on a laptop) needs neither.

SNAPSHOT_DEFAULT = "20260824T000000Z"
MARKER = "snapshot.debian.org"

_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.M)
_APT = re.compile(r"\bapt(?:-get)?\s+(?:update|install)\b")


def pin_block(snapshot: str) -> str:
    deb = f"http://snapshot.debian.org/archive/debian/{snapshot}"
    sec = f"http://snapshot.debian.org/archive/debian-security/{snapshot}"
    return "\n".join([
        "# Debian 11 left LTS on 2026-08-31: deb.debian.org's bullseye-security index names",
        "# .debs its pool no longer serves, and its Release file expires 2026-09-07. Every",
        "# suite is pinned to the snapshot the official image was built from, and apt is",
        "# told to accept that snapshot's aged Release file.",
        "RUN printf '%s\\n' \\",
        f"      'deb {deb} bullseye main' \\",
        f"      'deb {sec} bullseye-security main' \\",
        f"      'deb {deb} bullseye-updates main' \\",
        "      > /etc/apt/sources.list \\",
        " && rm -f /etc/apt/sources.list.d/debian.sources \\",
        " && printf '%s\\n' 'Acquire::Check-Valid-Until \"false\";' 'Acquire::Retries \"3\";' \\",
        "      > /etc/apt/apt.conf.d/99snapshot",
        "",
    ])


def is_bullseye(text: str) -> bool:
    m = _FROM.search(text)
    if not m:
        return False
    image = m.group(1).lower()
    return "bullseye" in image or image.startswith("debian:11")


def needs_pin(text: str) -> bool:
    return is_bullseye(text) and bool(_APT.search(text)) and MARKER not in text


def pin(text: str, snapshot: str) -> str:
    """Insert the pin block right after the first FROM line. Idempotent."""
    if MARKER in text:
        return text
    m = _FROM.search(text)
    if not m:
        raise ValueError("no FROM line")
    end = text.index("\n", m.end()) + 1 if "\n" in text[m.end():] else len(text)
    return text[:end] + "\n" + pin_block(snapshot) + text[end:]


def dockerfile_of(task_dir: Path) -> Path | None:
    for cand in (task_dir / "environment" / "Dockerfile", task_dir / "Dockerfile"):
        if cand.exists():
            return cand
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tasks-dir", required=True, type=Path,
                    help="directory holding <task_id>/ packages")
    ap.add_argument("--mix", type=Path, default=None,
                    help="a mix whose matching rows get the same pin (a root's live.jsonl publishes)")
    ap.add_argument("--ids", nargs="*", default=None,
                    help="only these tasks (default: every bullseye+apt Dockerfile in --tasks-dir)")
    ap.add_argument("--snapshot", default=SNAPSHOT_DEFAULT,
                    help=f"snapshot.debian.org timestamp (default {SNAPSHOT_DEFAULT})")
    ap.add_argument("--backup-dir", type=Path, default=None)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    tasks_dir = a.tasks_dir.resolve()
    backup_dir = a.backup_dir or (tasks_dir.parent.parent / "archive" / f"task-backups-bullseye-{stamp}")
    print(f"tasks_dir={tasks_dir} mix={a.mix} snapshot={a.snapshot} apply={a.apply} backup_dir={backup_dir}")

    if a.ids:
        task_dirs = [tasks_dir / t for t in a.ids]
        missing = [str(p) for p in task_dirs if not p.is_dir()]
        if missing:
            raise SystemExit(f"no package directory for: {missing}")
    else:
        task_dirs = sorted(p for p in tasks_dir.iterdir() if p.is_dir())

    pinned: list[str] = []
    skipped: list[tuple[str, str]] = []
    for td in task_dirs:
        df = dockerfile_of(td)
        if df is None:
            skipped.append((td.name, "no Dockerfile"))
            continue
        text = df.read_text(errors="replace")
        if not is_bullseye(text):
            if a.ids:
                skipped.append((td.name, "not a bullseye image"))
            continue
        if MARKER in text:
            skipped.append((td.name, "already pinned"))
            continue
        if not _APT.search(text):
            skipped.append((td.name, "bullseye but never runs apt"))
            continue
        pinned.append(td.name)
        if a.apply:
            dst = backup_dir / td.name / "Dockerfile"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(df, dst)
            df.write_text(pin(text, a.snapshot))
        print(f"  package {td.name:10s} {'pinned' if a.apply else 'would pin'}  ({df})")
    for name, why in skipped:
        print(f"  package {name:10s} skipped: {why}")
    print(f"packages {'pinned' if a.apply else 'to pin'}: {len(pinned)}")

    if a.mix:
        from torchtitan.experiments.rl.examples.tmax import layout
        rows = [json.loads(l) for l in a.mix.read_text().splitlines() if l.strip()]
        want = set(pinned) | set(a.ids or [])
        changed: list[str] = []
        for r in rows:
            md = r.get("metadata") or {}
            label = r.get("label") or md.get("instance_id")
            df = md.get("dockerfile")
            if label not in want or not isinstance(df, str):
                continue
            if not needs_pin(df):
                print(f"  row     {label:10s} skipped: {'already pinned' if MARKER in df else 'not bullseye+apt'}")
                continue
            changed.append(label)
            if a.apply:
                md["dockerfile"] = pin(df, a.snapshot)
            print(f"  row     {label:10s} {'pinned' if a.apply else 'would pin'}")
        absent = sorted(want - {(r.get("label") or (r.get("metadata") or {}).get("instance_id")) for r in rows})
        if absent:
            print(f"  rows not in mix: {' '.join(absent)}")
        print(f"mix rows {'pinned' if a.apply else 'to pin'}: {len(changed)} of {len(rows)}")
        if a.apply and changed:
            published = layout.write_mix(a.mix, [json.dumps(r, ensure_ascii=False) for r in rows])
            print(f"mix rewritten ({len(rows)} rows preserved)"
                  + (f"; published mix v{published[0]:04d} -> {published[1]}" if published else ""))

    if not a.apply:
        print("dry run -- pass --apply to write")
    else:
        print(f"backups: {backup_dir}")


if __name__ == "__main__":
    main()
