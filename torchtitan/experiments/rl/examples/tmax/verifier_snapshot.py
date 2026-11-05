"""Snapshot and restore ``/opt/verifier`` across the solving session.

In the verifier-base images, the grader's Python virtualenv at ``/opt/verifier`` is
first on the PATH that the model's shell inherits, so a ``pip install`` the model runs
while solving can land in the grader's environment instead of its own. That mutates the
environment the verifier then grades in. The harm is small in practice, but the verifier
should grade in the image's environment, not one the model changed.

This module adds a default-off switch, ``TMAX_SNAPSHOT_VERIFIER``. When it is on:

* right after setup and before the model runs, ``/opt/verifier`` is snapshotted;
* right before ``test.sh`` runs, it is restored if the model changed it.

It is a no-op when ``/opt/verifier`` is absent (the TB2.1 eval images do not have it).

Disk is the main constraint -- some rows declare a 1 GB disk, so a full copy could push a
sandbox over. The snapshot is therefore a hardlink tree (``cp -al``): it costs only new
directory entries and copied symlinks, a few MB, regardless of how large the venv is. A
manifest of each entry's ``relpath / type / inode / size / symlink-target`` drives the
restore, which:

* deletes entries the model added (present now, absent in the snapshot);
* relinks entries the model removed or replaced (a replaced regular file gets a new inode
  because ``pip``/``uv`` install atomically, so the snapshot's hardlink still points at the
  original bytes);
* fixes an entry whose type changed -- e.g. ``/opt/verifier/bin/python3`` replaced by a
  symlink -- or a symlink whose target changed.

A true in-place overwrite that keeps the same inode and size would not be detected (and the
hardlink would share the mutation, so it could not be restored anyway); ``pip``/``uv`` do not
write that way. Such an entry, if its size changes, is detected and reported as
``inplace_unrestorable`` rather than silently missed.

The logic lives in two shell scripts so the same bytes run under both the async training
sandbox (``await sb.exec``) and the sync offline sandbox (``sb.process.exec``), and so it can
be unit-tested directly on a temp directory by overriding ``VDIR`` / ``SDIR`` / ``MFILE``.
"""

from __future__ import annotations

import json
import os
import re

SWITCH_ENV = "TMAX_SNAPSHOT_VERIFIER"
EXEMPT_ENV = "TMAX_SNAPSHOT_VERIFIER_EXEMPT"
VERIFIER_DIR = "/opt/verifier"
SNAPSHOT_DIR = "/opt/.verifier_snapshot"
MANIFEST_FILE = "/opt/.verifier_snapshot.manifest"

# Tasks whose grading relies on packages the model installs into /opt/verifier while
# solving: restoring the venv before the verifier runs would remove what the test then
# imports, so the reference scores 0. These are exempt -- the task runs unchanged, as it
# does today. Seeded with the load-bearing tasks found in the first validation pilot; the
# list grows as the pilot finds more. Extend it per run through EXEMPT_ENV, a comma/space
# separated list of ids or ``@<path>`` to a file with one id per line.
_EXEMPT_SEED = frozenset(
    {
        "machine-learning_20260616_094815_002",
        "model-training_20260604_193356_003",
        "model-training_20260606_172554_002",
    }
)


def _exempt_set() -> frozenset[str]:
    ids = set(_EXEMPT_SEED)
    extra = os.environ.get(EXEMPT_ENV, "").strip()
    if extra:
        if extra.startswith("@"):
            try:
                with open(extra[1:]) as fh:
                    ids |= {
                        ln.strip()
                        for ln in fh
                        if ln.strip() and not ln.lstrip().startswith("#")
                    }
            except OSError:
                pass
        else:
            ids |= {t for t in re.split(r"[,\s]+", extra) if t}
    return frozenset(ids)


def enabled() -> bool:
    """True when the snapshot/restore switch is on (default off)."""
    return os.environ.get(SWITCH_ENV, "0") == "1"


def exempt(task_id: str | None) -> bool:
    """True for a task whose grading relies on solve-time installs (skip snapshot/restore)."""
    return bool(task_id) and task_id in _exempt_set()


# Both scripts read VDIR/SDIR/MFILE from the environment with production defaults, so a
# unit test can point them at a temp tree. Each prints exactly one JSON line last.
SNAPSHOT_SH = r"""
set -u
VDIR="${VDIR:-/opt/verifier}"
SDIR="${SDIR:-/opt/.verifier_snapshot}"
MFILE="${MFILE:-/opt/.verifier_snapshot.manifest}"
if [ ! -e "$VDIR" ]; then printf '{"existed":false}\n'; exit 0; fi
rm -rf -- "$SDIR" "$MFILE" 2>/dev/null
# Hardlink tree: near-zero extra disk (regular files share inodes with the original).
cp -al -- "$VDIR" "$SDIR"
# relpath \t type \t inode \t size \t symlink-target
( cd "$VDIR" && find . -mindepth 1 -printf '%P\t%y\t%i\t%s\t%l\n' ) | LC_ALL=C sort > "$MFILE"
nf=$(wc -l < "$MFILE" 2>/dev/null || echo 0)
printf '{"existed":true,"entries":%s}\n' "${nf:-0}"
"""

RESTORE_SH = r"""
set -u
VDIR="${VDIR:-/opt/verifier}"
SDIR="${SDIR:-/opt/.verifier_snapshot}"
MFILE="${MFILE:-/opt/.verifier_snapshot.manifest}"
if [ ! -d "$SDIR" ] || [ ! -f "$MFILE" ]; then printf '{"snapshot":false}\n'; exit 0; fi
if [ ! -e "$VDIR" ]; then
  mkdir -p -- "$(dirname -- "$VDIR")"
  cp -al -- "$SDIR" "$VDIR"
  printf '{"snapshot":true,"changed":true,"added":0,"restored_entries":0,"inplace_unrestorable":0,"restored":true}\n'
  exit 0
fi
NOW="$(mktemp)"; ACT="$(mktemp)"
( cd "$VDIR" && find . -mindepth 1 -printf '%P\t%y\t%i\t%s\t%l\n' ) | LC_ALL=C sort > "$NOW"
awk -F'\t' '
  FNR==NR { st[$1]=$2"\t"$3"\t"$4"\t"$5; next }
  { now[$1]=$2"\t"$3"\t"$4"\t"$5 }
  END {
    for (p in now) if (!(p in st)) print "DEL\t" p
    for (p in st) {
      if (!(p in now)) { print "RES\t" p; continue }
      split(st[p],a,"\t"); split(now[p],b,"\t")
      if (a[1]!=b[1]) { print "RES\t" p; continue }
      if (a[1]=="f" && a[2]!=b[2]) { print "RES\t" p; continue }
      if (a[1]=="l" && a[4]!=b[4]) { print "RES\t" p; continue }
      if (a[1]=="f" && a[2]==b[2] && a[3]!=b[3]) { print "INPLACE\t" p }
    }
  }
' "$MFILE" "$NOW" > "$ACT"
nadd=$(grep -c '^DEL' "$ACT" 2>/dev/null); nadd=${nadd:-0}
nres=$(grep -c '^RES' "$ACT" 2>/dev/null); nres=${nres:-0}
ninp=$(grep -c '^INPLACE' "$ACT" 2>/dev/null); ninp=${ninp:-0}
# Delete model-added entries deepest-first.
grep '^DEL' "$ACT" | cut -f2- | awk '{print length"\t"$0}' | LC_ALL=C sort -rn | cut -f2- | while IFS= read -r p; do
  rm -rf -- "$VDIR/$p"
done
# Restore removed/replaced/type-changed entries shallowest-first.
grep '^RES' "$ACT" | cut -f2- | awk '{print length"\t"$0}' | LC_ALL=C sort -n | cut -f2- | while IFS= read -r p; do
  mkdir -p -- "$(dirname -- "$VDIR/$p")"
  rm -rf -- "$VDIR/$p"
  cp -a --no-dereference -- "$SDIR/$p" "$VDIR/$p"
done
rm -f -- "$NOW" "$ACT"
changed=$(( nadd + nres + ninp ))
if [ "$changed" -gt 0 ]; then ch=true; else ch=false; fi
if [ "$ninp" -gt 0 ]; then rst=false; else rst=true; fi
printf '{"snapshot":true,"changed":%s,"added":%s,"restored_entries":%s,"inplace_unrestorable":%s,"restored":%s}\n' \
  "$ch" "$nadd" "$nres" "$ninp" "$rst"
"""


def _parse(stdout: str | None) -> dict:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False, "raw": (stdout or "")[-200:]}


# ---- async (training rollouter / offline revalidate; `await sb.exec`) ----
async def snapshot_verifier(sb, *, timeout: int = 300) -> dict:
    _, out, _ = await sb.exec(SNAPSHOT_SH, user="root", check=False, timeout=timeout)
    return _parse(out)


async def restore_verifier(sb, *, timeout: int = 300) -> dict:
    _, out, _ = await sb.exec(RESTORE_SH, user="root", check=False, timeout=timeout)
    return _parse(out)


# ---- sync (local_smoke / standalone daytona grading; `sb.process.exec`) ----
def _root_sh(script: str) -> str:
    # Match grading.py's _root_sh: run the body as root via bash -lc.
    import shlex

    return f"sudo -n bash -lc {shlex.quote(script)} 2>/dev/null || bash -lc {shlex.quote(script)}"


def snapshot_verifier_sync(sb, *, timeout: int = 300) -> dict:
    r = sb.process.exec(_root_sh(SNAPSHOT_SH), timeout=timeout)
    return _parse(getattr(r, "result", "") or "")


def restore_verifier_sync(sb, *, timeout: int = 300) -> dict:
    r = sb.process.exec(_root_sh(RESTORE_SH), timeout=timeout)
    return _parse(getattr(r, "result", "") or "")
