"""Snapshot and restore ``/opt/verifier`` across the solving session.

In the verifier-base images, the grader's Python virtualenv at ``/opt/verifier`` is
first on the PATH that the model's shell inherits, so a ``pip install`` the model runs
while solving can land in the grader's environment instead of its own. That mutates the
environment the verifier then grades in. The harm is small in practice, but the verifier
should grade in the image's environment, not one the model changed.

This module adds a default-off switch, ``TMAX_SNAPSHOT_VERIFIER``. When it is on, ``/opt/verifier``
is snapshotted right after setup and before the model runs, and examined right before ``test.sh``.

Two modes:

* ``TMAX_SNAPSHOT_VERIFIER=1`` (smart, the default-on behavior): **pure benign additions are kept**
  -- a reference that only installs new packages (the 83/89 case in the 111e data) is not disturbed,
  so the switch does not make it fail. A **full restore to the snapshot** fires only on a destructive
  change (an existing entry removed, replaced, retyped, or a symlink retargeted; an in-place write) or
  on an addition that can change how the grader runs (a new ``*.pth``, ``sitecustomize.py`` /
  ``usercustomize.py``, or a new dist declaring a ``[pytest11]`` entry point). The one new
  ``.pth`` treated as benign is setuptools' own ``distutils-precedence.pth`` (matched by name
  and content pattern), which any install that pulls in setuptools drops.
* ``TMAX_SNAPSHOT_VERIFIER=strict``: **always** restore to the snapshot on any change.

The restore is never a partial per-file restore: when it fires it rebuilds ``/opt/verifier`` to equal
the snapshot exactly (remove every added entry, relink every removed/replaced one), so a package is
never left half old and half new. Each rollout records which branch fired (none / additions_kept /
restored) and why. It is a no-op when ``/opt/verifier`` is absent (the TB2.1 eval images do not have it).

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

# Tasks exempt from snapshot/restore (the task runs unchanged). A task needs exempting only
# if the restore removes something the verifier then relies on. In smart mode that is rare:
# pure additions are kept (including setuptools' own distutils-precedence.pth), so the common
# "the model installed a package the test imports" case passes without exemption. The
# validation pilot (smart mode, exemptions off) found no task that needs it, so the seed is
# empty. Extend per run through EXEMPT_ENV (a comma/space list, or ``@<path>`` to a file of
# one id per line; the literal ``NONE`` disables the seed).
_EXEMPT_SEED: frozenset[str] = frozenset()


def _exempt_set() -> frozenset[str]:
    extra = os.environ.get(EXEMPT_ENV, "").strip()
    if extra.upper() == "NONE":  # drop the seed too (used to measure whether exemptions are still needed)
        return frozenset()
    ids = set(_EXEMPT_SEED)
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
    """True when the snapshot/restore switch is on (default off).

    ``TMAX_SNAPSHOT_VERIFIER=1`` is smart mode (keep pure benign additions, restore only on
    destructive or grader-affecting changes); ``=strict`` always restores to the snapshot.
    """
    return os.environ.get(SWITCH_ENV, "0").strip().lower() not in ("", "0", "false", "off", "no")


def strict_mode() -> bool:
    """True when the switch is ``strict`` (always restore to the snapshot)."""
    return os.environ.get(SWITCH_ENV, "0").strip().lower() == "strict"


def exempt(task_id: str | None) -> bool:
    """True for a task whose grading relies on solve-time installs (skip snapshot/restore)."""
    return bool(task_id) and task_id in _exempt_set()


# Both scripts read VDIR/SDIR/MFILE from the environment with production defaults, so a
# unit test can point them at a temp tree. Each prints exactly one JSON line last.
SNAPSHOT_SH = r"""
set -u
_t0=$(date +%s.%N)
VDIR="${VDIR:-/opt/verifier}"
SDIR="${SDIR:-/opt/.verifier_snapshot}"
MFILE="${MFILE:-/opt/.verifier_snapshot.manifest}"
_elapsed() { awk "BEGIN{printf \"%.3f\", $(date +%s.%N)-$_t0}"; }
if [ ! -e "$VDIR" ]; then printf '{"existed":false,"secs":%s}\n' "$(_elapsed)"; exit 0; fi
rm -rf -- "$SDIR" "$MFILE" 2>/dev/null
# Hardlink tree: near-zero extra disk (regular files share inodes with the original).
cp -al -- "$VDIR" "$SDIR"
# relpath \t type \t inode \t size \t symlink-target
( cd "$VDIR" && find . -mindepth 1 -printf '%P\t%y\t%i\t%s\t%l\n' ) | LC_ALL=C sort > "$MFILE"
nf=$(wc -l < "$MFILE" 2>/dev/null); nf=${nf:-0}
bytes=$(awk -F'\t' '$2=="f"{s+=$4} END{print s+0}' "$MFILE" 2>/dev/null); bytes=${bytes:-0}
printf '{"existed":true,"entries":%s,"bytes":%s,"secs":%s}\n' "$nf" "$bytes" "$(_elapsed)"
"""

RESTORE_SH = r"""
set -u
_t0=$(date +%s.%N)
VDIR="${VDIR:-/opt/verifier}"
SDIR="${SDIR:-/opt/.verifier_snapshot}"
MFILE="${MFILE:-/opt/.verifier_snapshot.manifest}"
MODE="${SNAP_MODE:-smart}"
_elapsed() { awk "BEGIN{printf \"%.3f\", $(date +%s.%N)-$_t0}"; }
# Drop the snapshot once the decision is made: it is a hardlink tree + manifest, and leaving
# it (tens of thousands of inodes for a big venv) under /opt during grading is needless
# pressure. /opt/verifier is already in its final state by here.
_cleanup() { rm -rf -- "$SDIR" "$MFILE" 2>/dev/null; }
_emit() {
  # branch reason added destructive inplace restored
  printf '{"snapshot":true,"branch":"%s","reason":"%s","added":%s,"destructive":%s,"inplace_unrestorable":%s,"restored":%s,"secs":%s}\n' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$(_elapsed)"
}
if [ ! -d "$SDIR" ] || [ ! -f "$MFILE" ]; then printf '{"snapshot":false,"secs":%s}\n' "$(_elapsed)"; exit 0; fi
if [ ! -e "$VDIR" ]; then
  mkdir -p -- "$(dirname -- "$VDIR")"; cp -al -- "$SDIR" "$VDIR"
  _cleanup; _emit restored "verifier_dir_removed" 0 1 0 true; exit 0
fi
NOW="$(mktemp)"; ACT="$(mktemp)"
( cd "$VDIR" && find . -mindepth 1 -printf '%P\t%y\t%i\t%s\t%l\n' ) | LC_ALL=C sort > "$NOW"
# ADD: entry the model added; RESTORE: existing entry removed/replaced/retyped or symlink
# retargeted (destructive); INPLACE: same inode, size changed (true in-place write).
awk -F'\t' '
  FNR==NR { st[$1]=$2"\t"$3"\t"$4"\t"$5; next }
  { now[$1]=$2"\t"$3"\t"$4"\t"$5 }
  END {
    for (p in now) if (!(p in st)) print "ADD\t" p
    for (p in st) {
      if (!(p in now)) { print "RESTORE\t" p; continue }
      split(st[p],a,"\t"); split(now[p],b,"\t")
      if (a[1]!=b[1]) { print "RESTORE\t" p; continue }
      if (a[1]=="f" && a[2]!=b[2]) { print "RESTORE\t" p; continue }
      if (a[1]=="l" && a[4]!=b[4]) { print "RESTORE\t" p; continue }
      if (a[1]=="f" && a[2]==b[2] && a[3]!=b[3]) { print "INPLACE\t" p }
    }
  }
' "$MFILE" "$NOW" > "$ACT"
nadd=$(grep -c '^ADD' "$ACT" 2>/dev/null); nadd=${nadd:-0}
ndes=$(grep -c '^RESTORE' "$ACT" 2>/dev/null); ndes=${ndes:-0}
ninp=$(grep -c '^INPLACE' "$ACT" 2>/dev/null); ninp=${ninp:-0}
if [ "$nadd" -eq 0 ] && [ "$ndes" -eq 0 ] && [ "$ninp" -eq 0 ]; then
  rm -f -- "$NOW" "$ACT"; _cleanup; _emit none unchanged 0 0 0 true; exit 0
fi
destructive=0
if [ "$ndes" -gt 0 ] || [ "$ninp" -gt 0 ]; then destructive=1; fi
# A benign-looking addition that still changes how the grader's python/pytest runs.
danger=""
awk -F'\t' '$1=="ADD"{print $2}' "$ACT" > "$ACT.add" 2>/dev/null || true
# A new .pth runs code at interpreter start, so it is dangerous -- EXCEPT setuptools'
# distutils-precedence.pth, which any install pulling in setuptools drops. Treat that one
# (matched by name + content pattern, so the setuptools version does not matter) as benign;
# every other new .pth still triggers a restore.
grep -iE '(^|/)[^/]*\.pth$' "$ACT.add" > "$ACT.pth" 2>/dev/null || true
while IFS= read -r pth; do
  [ -n "$pth" ] || continue
  if [ "${pth##*/}" = "distutils-precedence.pth" ] \
     && grep -q "SETUPTOOLS_USE_DISTUTILS" "$VDIR/$pth" 2>/dev/null \
     && grep -q "_distutils_hack" "$VDIR/$pth" 2>/dev/null; then
    continue
  fi
  danger="pth_added"; break
done < "$ACT.pth"
rm -f -- "$ACT.pth"
if [ -z "$danger" ] && grep -qE '(^|/)(site|user)customize\.py$' "$ACT.add" 2>/dev/null; then danger="customize_added"; fi
if [ -z "$danger" ]; then
  grep -iE 'entry_points\.txt$' "$ACT.add" > "$ACT.ep" 2>/dev/null || true
  while IFS= read -r ep; do
    [ -n "$ep" ] || continue
    if grep -qiE '^\[pytest11\]' "$VDIR/$ep" 2>/dev/null; then danger="pytest11_added"; break; fi
  done < "$ACT.ep"
  rm -f -- "$ACT.ep"
fi
rm -f -- "$ACT.add"
do_restore=0; reason="pure_additions"
if [ "$MODE" = "strict" ]; then do_restore=1; reason="strict"
elif [ "$destructive" -eq 1 ]; then do_restore=1; reason="destructive"
elif [ -n "$danger" ]; then do_restore=1; reason="$danger"
fi
if [ "$do_restore" -eq 1 ]; then
  # Full restore to the snapshot: remove every added entry and relink every
  # removed/replaced/retyped one. Never a partial per-file restore of a package.
  grep '^ADD' "$ACT" | cut -f2- | awk '{print length"\t"$0}' | LC_ALL=C sort -rn | cut -f2- | while IFS= read -r p; do
    rm -rf -- "$VDIR/$p"
  done
  grep '^RESTORE' "$ACT" | cut -f2- | awk '{print length"\t"$0}' | LC_ALL=C sort -n | cut -f2- | while IFS= read -r p; do
    mkdir -p -- "$(dirname -- "$VDIR/$p")"; rm -rf -- "$VDIR/$p"; cp -a --no-dereference -- "$SDIR/$p" "$VDIR/$p"
  done
  rm -f -- "$NOW" "$ACT"
  if [ "$ninp" -gt 0 ]; then rst=false; else rst=true; fi
  _cleanup; _emit restored "$reason" "$nadd" "$ndes" "$ninp" "$rst"
else
  rm -f -- "$NOW" "$ACT"
  _cleanup; _emit additions_kept "$reason:$nadd" "$nadd" 0 0 true
fi
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


def _restore_script() -> str:
    return ("SNAP_MODE=strict\n" + RESTORE_SH) if strict_mode() else RESTORE_SH


async def restore_verifier(sb, *, timeout: int = 300) -> dict:
    _, out, _ = await sb.exec(_restore_script(), user="root", check=False, timeout=timeout)
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
    r = sb.process.exec(_root_sh(_restore_script()), timeout=timeout)
    return _parse(getattr(r, "result", "") or "")
