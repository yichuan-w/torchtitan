"""Unit tests for the /opt/verifier snapshot/restore shell logic.

The two scripts take VDIR/SDIR/MFILE (and SNAP_MODE) from the environment, so these tests run
them on a temp tree with no sandbox and no torchtitan import: the module is loaded straight from
its file. Smart mode keeps pure benign additions and restores only on destructive or
grader-affecting changes; strict mode always restores to the snapshot.
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


def _run(script: str, root: Path, env_extra: dict | None = None) -> dict:
    env = {
        **os.environ,
        "VDIR": str(root / "verifier"),
        "SDIR": str(root / ".snap"),
        "MFILE": str(root / ".snap.manifest"),
    }
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return vs._parse(p.stdout)


def _sig(v: Path) -> dict:
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
    (v / "lib" / "python3.13" / "site-packages" / "pkgA").mkdir(parents=True)
    (v / "bin" / "python").write_text("#real-python\n")
    (v / "bin" / "python3").symlink_to("python")
    (v / "lib" / "python3.13" / "site-packages" / "pkgA" / "__init__.py").write_text("x = 1\n")
    (v / "pyvenv.cfg").write_text("home = /opt\n")
    return v


def _sp(v: Path) -> Path:
    return v / "lib" / "python3.13" / "site-packages"


def test_absent_is_noop(tmp_path):
    snap = _run(vs.SNAPSHOT_SH, tmp_path)
    assert snap["existed"] is False and "secs" in snap
    rest = _run(vs.RESTORE_SH, tmp_path)
    assert rest["snapshot"] is False and "secs" in rest


def test_snapshot_reports_size(tmp_path):
    _mkverifier(tmp_path)
    snap = _run(vs.SNAPSHOT_SH, tmp_path)
    assert snap["existed"] is True and snap["entries"] > 0 and snap["bytes"] > 0


def test_no_change_branch_none(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "none" and r["restored"] is True
    assert _sig(v) == before
    # the snapshot tree + manifest are cleaned up after the decision
    assert not (tmp_path / ".snap").exists()
    assert not (tmp_path / ".snap.manifest").exists()


def test_pure_addition_is_kept(tmp_path):
    """Smart mode: a new package the model installs is KEPT (no restore)."""
    v = _mkverifier(tmp_path)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (_sp(v) / "torch").mkdir()
    (_sp(v) / "torch" / "__init__.py").write_text("# added by the model\n")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "additions_kept" and r["added"] >= 1
    assert (_sp(v) / "torch" / "__init__.py").exists()  # kept
    assert not (tmp_path / ".snap").exists()  # snapshot cleaned even when additions are kept


def test_strict_mode_restores_additions(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (_sp(v) / "torch").mkdir()
    (_sp(v) / "torch" / "__init__.py").write_text("# added\n")
    r = _run(vs.RESTORE_SH, tmp_path, {"SNAP_MODE": "strict"})
    assert r["branch"] == "restored" and r["reason"] == "strict"
    assert _sig(v) == before  # addition removed


def test_dangerous_addition_pth_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (_sp(v) / "inject.pth").write_text("import os\n")  # a .pth runs at interpreter start
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and r["reason"] == "pth_added"
    assert _sig(v) == before


_SETUPTOOLS_PTH = (
    "import os; var = 'SETUPTOOLS_USE_DISTUTILS'; enabled = os.environ.get(var, 'local') "
    "== 'local'; enabled and __import__('_distutils_hack').add_shim();\n"
)


def test_setuptools_distutils_precedence_pth_is_kept(tmp_path):
    """setuptools' own .pth is benign: any install pulling in setuptools drops it."""
    v = _mkverifier(tmp_path)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (_sp(v) / "distutils-precedence.pth").write_text(_SETUPTOOLS_PTH)
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "additions_kept"
    assert (_sp(v) / "distutils-precedence.pth").exists()  # kept


def test_same_named_pth_other_content_restores(tmp_path):
    """A file named distutils-precedence.pth but with other content is still dangerous."""
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (_sp(v) / "distutils-precedence.pth").write_text("import evil; evil.run()\n")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and r["reason"] == "pth_added"
    assert _sig(v) == before


def test_dangerous_addition_sitecustomize_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (_sp(v) / "sitecustomize.py").write_text("print('x')\n")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and r["reason"] == "customize_added"
    assert _sig(v) == before


def test_dangerous_addition_pytest11_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    di = _sp(v) / "fakeplugin-1.0.dist-info"
    di.mkdir()
    (di / "entry_points.txt").write_text("[pytest11]\nfakeplugin = fakeplugin.plugin\n")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and r["reason"] == "pytest11_added"
    assert _sig(v) == before


def test_non_pytest_entrypoint_addition_is_kept(tmp_path):
    """A new dist with a non-pytest entry point is a benign addition, kept."""
    v = _mkverifier(tmp_path)
    _run(vs.SNAPSHOT_SH, tmp_path)
    di = _sp(v) / "somepkg-1.0.dist-info"
    di.mkdir()
    (di / "entry_points.txt").write_text("[console_scripts]\nsomepkg = somepkg.cli:main\n")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "additions_kept"
    assert di.exists()


def test_replace_file_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    tgt = _sp(v) / "pkgA" / "__init__.py"
    tmp = _sp(v) / "pkgA" / ".tmp"
    tmp.write_text("x = 999  # upgraded\n")
    os.replace(tmp, tgt)  # atomic replace (new inode), as pip/uv write
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and r["reason"] == "destructive"
    assert _sig(v) == before


def test_remove_file_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    (v / "pyvenv.cfg").unlink()
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and _sig(v) == before


def test_python3_replaced_by_symlink_restores(tmp_path):
    """The g677/r0 case: /opt/verifier/bin/python3 swapped for a different symlink."""
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    p3 = v / "bin" / "python3"
    p3.unlink()
    p3.symlink_to("/usr/bin/python3")
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and _sig(v) == before


def test_rm_site_packages_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    import shutil

    shutil.rmtree(_sp(v))
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and _sig(v) == before


def test_whole_dir_removed_restores(tmp_path):
    v = _mkverifier(tmp_path)
    before = _sig(v)
    _run(vs.SNAPSHOT_SH, tmp_path)
    import shutil

    shutil.rmtree(v)
    r = _run(vs.RESTORE_SH, tmp_path)
    assert r["branch"] == "restored" and _sig(v) == before


def test_disk_overhead_is_hardlinked(tmp_path):
    v = _mkverifier(tmp_path)
    _run(vs.SNAPSHOT_SH, tmp_path)
    orig = v / "bin" / "python"
    snap = tmp_path / ".snap" / "bin" / "python"
    assert snap.exists()
    assert orig.stat().st_ino == snap.stat().st_ino  # hardlink, zero extra data


def test_parse_and_enabled(monkeypatch):
    assert vs._parse('x\n{"branch": "none"}\n')["branch"] == "none"
    assert vs._parse("garbage").get("ok") is False
    for off in ("", "0", "false", "off"):
        monkeypatch.setenv(vs.SWITCH_ENV, off)
        assert vs.enabled() is False and vs.strict_mode() is False
    monkeypatch.delenv(vs.SWITCH_ENV, raising=False)
    assert vs.enabled() is False
    monkeypatch.setenv(vs.SWITCH_ENV, "1")
    assert vs.enabled() is True and vs.strict_mode() is False
    monkeypatch.setenv(vs.SWITCH_ENV, "strict")
    assert vs.enabled() is True and vs.strict_mode() is True


def test_exempt(monkeypatch, tmp_path):
    monkeypatch.delenv(vs.EXEMPT_ENV, raising=False)
    # seed is empty by default; a seeded id (if any) is exempt, others are not
    assert vs.exempt("some_other_task_123") == ("some_other_task_123" in vs._EXEMPT_SEED)
    assert vs.exempt(None) is False
    monkeypatch.setenv(vs.EXEMPT_ENV, "task_a, task_b task_c")
    assert vs.exempt("task_a") and vs.exempt("task_b") and vs.exempt("task_c")
    assert vs.exempt("task_z") is False
    f = tmp_path / "exempt.txt"
    f.write_text("# comment\ntask_x\n\ntask_y\n")
    monkeypatch.setenv(vs.EXEMPT_ENV, f"@{f}")
    assert vs.exempt("task_x") and vs.exempt("task_y")
    assert vs.exempt("task_a") is False
    monkeypatch.setenv(vs.EXEMPT_ENV, "NONE")  # drops seed + env
    assert vs.exempt("task_a") is False
    for s in vs._EXEMPT_SEED:
        assert vs.exempt(s) is False
