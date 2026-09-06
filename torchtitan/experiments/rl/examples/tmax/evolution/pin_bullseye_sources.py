#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

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

Selection: a Dockerfile whose base image is an out-of-support Debian release
(bullseye, buster, stretch, jessie), read from the tag when it names one and
from --resolved (resolve_base_images.py) when it does not: python:3.6-slim is
bullseye, node:14 is buster. Whether the Dockerfile itself runs apt does not
matter: the training harness appends a tmux install to every row, and a
reference solution may call apt. --ids narrows or overrides that.

Dry run by default. --apply backs every package Dockerfile up under
--backup-dir first (default: <tasks-dir>/../../archive/task-backups-bullseye-<stamp>,
so a tasks dir at <root>/data/tw-extract/tasks backs up under <root>/data/archive/),
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

MARKER = "snapshot.debian.org"

# One snapshot per release, taken just after its last archive update, and the
# suite names apt expects for it: security moved from `<codename>/updates` to
# `<codename>-security` with bullseye.
RELEASES = {
    "bullseye": ("20260824T000000Z", "bullseye-security"),
    "buster": ("20240701T000000Z", "buster/updates"),
    "stretch": ("20220701T000000Z", "stretch/updates"),
    "jessie": ("20200701T000000Z", "jessie/updates"),
}
SNAPSHOT_DEFAULT = RELEASES["bullseye"][0]

_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.M)
_APT = re.compile(r"\bapt(?:-get)?\s+(?:update|install)\b")


def pin_block(snapshot: str, codename: str = "bullseye") -> str:
    deb = f"http://snapshot.debian.org/archive/debian/{snapshot}"
    sec = f"http://snapshot.debian.org/archive/debian-security/{snapshot}"
    security_suite = RELEASES.get(codename, (None, f"{codename}-security"))[1]
    return "\n".join(
        [
            f"# Debian {codename} is out of support: the mirrors drop or stop re-signing its",
            "# suites, and apt then fails at update or at fetch. Every suite is pinned to a",
            "# snapshot.debian.org state from before that, and apt is told to accept that",
            "# snapshot's aged Release file.",
            "RUN printf '%s\\n' \\",
            f"      'deb {deb} {codename} main' \\",
            f"      'deb {sec} {security_suite} main' \\",
            f"      'deb {deb} {codename}-updates main' \\",
            "      > /etc/apt/sources.list \\",
            " && rm -f /etc/apt/sources.list.d/debian.sources \\",
            " && printf '%s\\n' 'Acquire::Check-Valid-Until \"false\";' 'Acquire::Retries \"3\";' \\",
            "      > /etc/apt/apt.conf.d/99snapshot",
            "",
        ]
    )


RESOLVED: dict[
    str, str
] = {}  # image ref -> "debian:<codename>", from resolve_base_images.py


def codename_of(text: str) -> str | None:
    """Which out-of-support Debian release this Dockerfile builds on, or None.

    The tag settles it when it names one (debian:bullseye, node:18-bullseye,
    debian:11); otherwise the registry lookup in --resolved does (python:3.6-slim
    is bullseye, node:14 is buster). A tag that names nothing and was not
    resolved is left alone and reported.
    """
    m = _FROM.search(text)
    if not m:
        return None
    image = m.group(1)
    low = image.lower()
    for cn in RELEASES:
        if cn in low:
            return cn
    if low.startswith("debian:11"):
        return "bullseye"
    if low.startswith("debian:10"):
        return "buster"
    if low.startswith("debian:9"):
        return "stretch"
    if low.startswith("debian:8"):
        return "jessie"
    os_ = RESOLVED.get(image, "")
    for cn in RELEASES:
        version = {"jessie": 8, "stretch": 9, "buster": 10, "bullseye": 11}[cn]
        if os_ == f"debian:{cn}" or os_ == f"debian:{version}":
            return cn
    return None


def needs_pin(text: str) -> bool:
    return codename_of(text) is not None and MARKER not in text


def pin(text: str, snapshot: str | None = None) -> str:
    """Insert the pin block right after the first FROM line. Idempotent.

    Applies to every image on an out-of-support release whether or not the
    Dockerfile itself runs apt: the training harness appends a tmux install to
    every row, and a reference solution may call apt too, so the sources have
    to be sound in the image regardless.
    """
    if MARKER in text:
        return text
    m = _FROM.search(text)
    if not m:
        raise ValueError("no FROM line")
    cn = codename_of(text)
    if cn is None:
        raise ValueError("not an out-of-support Debian base")
    snap = snapshot or RELEASES[cn][0]
    end = text.index("\n", m.end()) + 1 if "\n" in text[m.end() :] else len(text)
    return text[:end] + "\n" + pin_block(snap, cn) + text[end:]


def dockerfile_of(task_dir: Path) -> Path | None:
    for cand in (task_dir / "environment" / "Dockerfile", task_dir / "Dockerfile"):
        if cand.exists():
            return cand
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--tasks-dir",
        required=True,
        type=Path,
        help="directory holding <task_id>/ packages",
    )
    ap.add_argument(
        "--mix",
        type=Path,
        default=None,
        help="a mix whose matching rows get the same pin (a root's live.jsonl publishes)",
    )
    ap.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="only these tasks (default: every bullseye+apt Dockerfile in --tasks-dir)",
    )
    ap.add_argument(
        "--snapshot",
        default=None,
        help="override the snapshot.debian.org timestamp for every release "
        "(default: per release, see RELEASES)",
    )
    ap.add_argument(
        "--resolved",
        type=Path,
        default=None,
        help="resolve_base_images.py output, so tags that do not name their release still match",
    )
    ap.add_argument("--backup-dir", type=Path, default=None)
    ap.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    a = ap.parse_args()

    if a.resolved:
        for r in json.load(open(a.resolved)):
            RESOLVED[r["ref"]] = r.get("os", "?")
    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    tasks_dir = a.tasks_dir.resolve()
    backup_dir = a.backup_dir or (
        tasks_dir.parent.parent / "archive" / f"task-backups-bullseye-{stamp}"
    )
    print(
        f"tasks_dir={tasks_dir} mix={a.mix} snapshot={a.snapshot} apply={a.apply} backup_dir={backup_dir}"
    )

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
        cn = codename_of(text)
        if cn is None:
            if a.ids:
                skipped.append((td.name, "not on an out-of-support Debian release"))
            continue
        if MARKER in text:
            skipped.append((td.name, "already pinned"))
            continue
        pinned.append(td.name)
        if a.apply:
            dst = backup_dir / td.name / "Dockerfile"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(df, dst)
            df.write_text(pin(text, a.snapshot))
        print(
            f"  package {td.name:10s} {'pinned' if a.apply else 'would pin'} "
            f"[{cn}{'' if _APT.search(text) else ', no apt in Dockerfile'}]  ({df})"
        )
    for name, why in skipped:
        print(f"  package {name:10s} skipped: {why}")
    print(f"packages {'pinned' if a.apply else 'to pin'}: {len(pinned)}")

    if a.mix:
        from pack_to_dataset import _tmax_modules

        layout = _tmax_modules("layout")

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
                print(
                    f"  row     {label:10s} skipped: "
                    f"{'already pinned' if MARKER in df else 'not on an out-of-support Debian release'}"
                )
                continue
            changed.append(label)
            if a.apply:
                md["dockerfile"] = pin(df, a.snapshot)
            print(
                f"  row     {label:10s} {'pinned' if a.apply else 'would pin'} [{codename_of(df)}]"
            )
        absent = sorted(
            want
            - {
                (r.get("label") or (r.get("metadata") or {}).get("instance_id"))
                for r in rows
            }
        )
        if absent:
            print(f"  rows not in mix: {' '.join(absent)}")
        print(
            f"mix rows {'pinned' if a.apply else 'to pin'}: {len(changed)} of {len(rows)}"
        )
        if a.apply and changed:
            published = layout.write_mix(
                a.mix, [json.dumps(r, ensure_ascii=False) for r in rows]
            )
            print(
                f"mix rewritten ({len(rows)} rows preserved)"
                + (
                    f"; published mix v{published[0]:04d} -> {published[1]}"
                    if published
                    else ""
                )
            )

    if not a.apply:
        print("dry run -- pass --apply to write")
    else:
        print(f"backups: {backup_dir}")


if __name__ == "__main__":
    main()
