"""Unit tests for the /opt/verifier snapshot/restore shell logic.

The two scripts take VDIR/SDIR/MFILE from the environment, so these tests run them on a
temp tree with no sandbox and no torchtitan import: the module is loaded straight from its
file. Each test snapshots a fake verifier tree, has "the model" mutate it, restores, and
checks the tree is back to the snapshot and the reported counts are right.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

_MOD = Path(__file__).with_name("verifier_snapshot.py")
_spec = importlib.util.spec_from_file_location("verifier_snapshot_under_test", _MOD)
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)


def _run(script: str, root: Path) -> dict:
    env = {
        **os.environ,
        "VDIR": str(root / "verifier"),
        "SDIR": str(root / ".snap"),
        "MFILE": str(root / ".snap.manifest"),
    }
    p = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return vs._parse(p.stdout)


def _sig(v: Path) -> dict:
    """relpath -> ('f', bytes) | ('l', target) | ('d', None), for the whole tree."""
    out: dict[str, tuple] = {}
    for p in sorted(v.rglob("*")):
        rel = str(p.relative_to(v))
        if p.is_symlink():
            out[rel] = ("l", os.readlink(p))
        elif p.is_dir():
            out[rel] = ("d", None)
        else:
            out[rel] = ("f", p.read_bytes())
    return out


def _mkverifier(root: Path) -> Path:
    v = root / "verifier"
    (v / "bin").mkdir(parents=True)
    (v / "lib" / "site-packages" / "pkgA").mkdir(parents=True)
    (v / "bin" / "python").write_text("#real-python\n")
    (v / "bin" / "python3").symlink_to("python")  # typical venv layout
    (v / "lib" / "site-packages" / "pkgA" / "__init__.py").write_text("x = 1\n")
    (v / "pyvenv.cfg").write_text("home = /opt\n")
    return v


def test_absent_is_noop(tmp_path):
    assert _run(vs.SNAPSHOT_SH, tmp_path) == {"existed": False}
    assert _run(vs.RESTORE_SH, tmp_path) == {"snapshot": False}


def test_no_change(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    snap = _run(vs.SNAPSHOT_SH, tmp_path)
    assert snap["existed"] is True and snap["entries"] > 0
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["snapshot"] is True and r["changed"] is False and r["restored"] is True
    assert _sig(v) == before


def test_model_adds_package(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    # the model pip-installs a new package
    (v / "lib" / "site-packages" / "torch").mkdir()
    (v / "lib" / "site-packages" / "torch" / "__init__.py").write_text("# huge\n")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["changed"] is True and r["added"] >= 1 and r["restored"] is True
    assert _sig(v) == before  # added package removed


def test_model_replaces_file_atomically(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    # atomic replace (new inode), as pip/uv write: write temp then rename over
    tgt = v / "lib" / "site-packages" / "pkgA" / "__init__.py"
    tmp = v / "lib" / "site-packages" / "pkgA" / ".tmp"
    tmp.write_text("x = 999  # model edited\n")
    os.replace(tmp, tgt)
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["changed"] is True and r["restored_entries"] >= 1 and r["restored"] is True
    assert _sig(v) == before  # original bytes back


def test_model_removes_file(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (v / "pyvenv.cfg").unlink()
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["changed"] is True and r["restored_entries"] >= 1
    assert _sig(v) == before


def test_python3_replaced_by_symlink(tmp_path):
    """The g677/r0 case: /opt/verifier/bin/python3 swapped for a different symlink."""
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    p3 = v / "bin" / "python3"
    p3.unlink()
    p3.symlink_to("/usr/bin/python3")  # model repoints it elsewhere
    assert _sig(v) != before
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["changed"] is True and r["restored"] is True
    assert _sig(v) == before  # original symlink target restored


def test_whole_dir_removed(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    import shutil

    shutil.rmtree(v)
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["changed"] is True and r["restored"] is True
    assert _sig(v) == before


def test_disk_overhead_is_hardlinked(tmp_path):
    """The snapshot must not duplicate file data: regular files share inodes."""
    v = _mkverifier(tmp_path)
    _run(vs.SNAPSHOT_SH, tmp_path)
    orig = v / "bin" / "python"
    snap = tmp_path / ".snap" / "bin" / "python"
    assert snap.exists()
    assert orig.stat().st_ino == snap.stat().st_ino  # hardlink, zero extra data


def test_exempt(monkeypatch, tmp_path):
    monkeypatch.delenv(vs.EXEMPT_ENV, raising=False)
    seeded = next(iter(vs._EXEMPT_SEED))
    assert vs.exempt(seeded) is True
    assert vs.exempt("some_other_task_123") is False
    assert vs.exempt(None) is False
    assert vs.exempt("") is False
    # extend via a comma/space list
    monkeypatch.setenv(vs.EXEMPT_ENV, "task_a, task_b task_c")
    assert vs.exempt("task_a") and vs.exempt("task_b") and vs.exempt("task_c")
    assert vs.exempt(seeded) is True  # seed still applies
    # extend via @file
    f = tmp_path / "exempt.txt"
    f.write_text("# comment\ntask_x\n\ntask_y\n")
    monkeypatch.setenv(vs.EXEMPT_ENV, f"@{f}")
    assert vs.exempt("task_x") and vs.exempt("task_y")
    assert vs.exempt("task_a") is False  # list form no longer in env


def test_parse_and_enabled(monkeypatch):
    assert vs._parse("noise\n{\"existed\": true, \"entries\": 5}\n")["entries"] == 5
    assert vs._parse("garbage").get("ok") is False
    monkeypatch.delenv(vs.SWITCH_ENV, raising=False)
    assert vs.enabled() is False
    monkeypatch.setenv(vs.SWITCH_ENV, "1")
    assert vs.enabled() is True
    monkeypatch.setenv(vs.SWITCH_ENV, "0")
    assert vs.enabled() is False
