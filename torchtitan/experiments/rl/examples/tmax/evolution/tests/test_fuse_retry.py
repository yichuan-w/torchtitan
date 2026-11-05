"""fuse_retry: an EPERM under the root is retried; a retry whose first attempt
landed reports success; anything else, or a path outside the root, is untouched."""

import errno
import os

import pytest

import fuse_retry


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(fuse_retry, "DELAYS_SEC", (0.0, 0.0))


def _eperm():
    return PermissionError(errno.EPERM, "Operation not permitted")


def _flaky(result=None, fail=1, then=None):
    calls = []

    def fn(*args, **kwargs):
        calls.append(args)
        if len(calls) <= fail:
            raise _eperm()
        if then is not None:
            raise then
        return result

    return fn, calls


def test_eperm_under_the_root_is_retried(tmp_path):
    fn, calls = _flaky(result="ok")
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),))
    assert wrapped(str(tmp_path / "x")) == "ok"
    assert len(calls) == 2


def test_path_outside_the_root_is_not_retried(tmp_path):
    fn, calls = _flaky(result="ok")
    wrapped = fuse_retry._retrying(fn, (str(tmp_path / "root"),))
    with pytest.raises(PermissionError):
        wrapped("/elsewhere/x")
    assert len(calls) == 1


def test_lasting_eperm_is_raised(tmp_path):
    fn, calls = _flaky(fail=99)
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),))
    with pytest.raises(PermissionError):
        wrapped(str(tmp_path / "x"))
    assert len(calls) == 3


def test_other_errors_pass_through(tmp_path):
    fn, calls = _flaky(fail=0, then=FileNotFoundError(errno.ENOENT, "missing"))
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),))
    with pytest.raises(FileNotFoundError):
        wrapped(str(tmp_path / "x"))
    assert len(calls) == 1


def test_mkdir_that_landed_before_the_eperm_counts_as_done(tmp_path):
    fn, _ = _flaky(then=FileExistsError(errno.EEXIST, "exists"))
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),), landed=fuse_retry._exists_now)
    assert wrapped(str(tmp_path / "d")) is None


def test_mkdir_of_an_existing_dir_without_eperm_still_raises(tmp_path):
    fn, _ = _flaky(fail=0, then=FileExistsError(errno.EEXIST, "exists"))
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),), landed=fuse_retry._exists_now)
    with pytest.raises(FileExistsError):
        wrapped(str(tmp_path / "d"))


def test_rename_that_landed_before_the_eperm_counts_as_done(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    dst.write_text("moved")

    fn, _ = _flaky(then=FileNotFoundError(errno.ENOENT, "src gone"))
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),), landed=fuse_retry._moved)
    assert wrapped(str(src), str(dst)) is None


def test_rename_with_neither_side_present_raises(tmp_path):
    fn, _ = _flaky(then=FileNotFoundError(errno.ENOENT, "src gone"))
    wrapped = fuse_retry._retrying(fn, (str(tmp_path),), landed=fuse_retry._moved)
    with pytest.raises(FileNotFoundError):
        wrapped(str(tmp_path / "a"), str(tmp_path / "b"))


def test_real_calls_still_work_through_the_wrapper(tmp_path):
    stat = fuse_retry._retrying(os.stat, (str(tmp_path),))
    (tmp_path / "f").write_text("x")
    assert stat(tmp_path / "f").st_size == 1
