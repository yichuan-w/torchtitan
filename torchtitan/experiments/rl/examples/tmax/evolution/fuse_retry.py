# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Repeat file-system calls the FUSE root refuses with a transient EPERM.

Runs 1029-1030 evolved on an OilFS mount that now and then answers a stat,
create, rename or listing under the root with EPERM; the same call a second
later succeeds (or reports ENOENT for a missing path). Every such error ended an
evolve round or killed its fold thread. ``install`` wraps the os/io calls the
loop uses so that, for paths under the root only, an EPERM is retried, with
each retry kept idempotent: a create, link or rename whose first attempt landed
before the error reports success instead of FileExistsError/FileNotFoundError.
"""

from __future__ import annotations

import builtins
import errno
import functools
import io
import logging
import os
import time

log = logging.getLogger(__name__)

DELAYS_SEC = (0.5, 1.0, 2.0, 4.0)
_installed: list[str] = []


def _under(path, prefix: tuple[str, ...]) -> bool:
    if isinstance(path, int):
        return False
    try:
        p = os.fspath(path)
    except TypeError:
        return False
    return isinstance(p, str) and p.startswith(prefix)


def _eperm(e: OSError) -> bool:
    return isinstance(e, PermissionError) and e.errno == errno.EPERM


def _retrying(fn, prefix: tuple[str, ...], *, landed=None):
    """``landed(exc, *args)`` decides whether an error on a retry means the
    refused first attempt took effect after all."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not args or not any(_under(a, prefix) for a in args[:2]):
            return fn(*args, **kwargs)
        for attempt, delay in enumerate((*DELAYS_SEC, None)):
            try:
                return fn(*args, **kwargs)
            except OSError as e:
                if attempt and landed is not None and landed(e, *args):
                    return None
                if not _eperm(e) or delay is None:
                    raise
                log.warning("EPERM from %s(%s), retrying in %.1fs", fn.__name__, args[0], delay)
                time.sleep(delay)

    wrapper.__fuse_retry__ = True
    return wrapper


def _exists_now(e, *args):
    return isinstance(e, FileExistsError)


def _moved(e, src, dst, *rest):
    return isinstance(e, FileNotFoundError) and os.path.lexists(dst)


def _gone(e, *args):
    return isinstance(e, FileNotFoundError)


def install(prefix: str) -> None:
    """Wrap the calls once per process; ``prefix`` is the FUSE root's path."""
    if prefix in _installed:
        return
    _installed.append(prefix)
    raw = prefix
    prefix = tuple({raw, os.path.realpath(raw)})
    for name in ("stat", "lstat", "listdir", "scandir", "rmdir"):
        setattr(os, name, _retrying(getattr(os, name), prefix))
    os.mkdir = _retrying(os.mkdir, prefix, landed=_exists_now)
    os.link = _retrying(os.link, prefix, landed=_exists_now)
    os.unlink = _retrying(os.unlink, prefix, landed=_gone)
    os.remove = os.unlink
    os.replace = _retrying(os.replace, prefix, landed=_moved)
    os.rename = _retrying(os.rename, prefix, landed=_moved)
    io.open = builtins.open = _retrying(io.open, prefix)
    log.info("fuse_retry installed for %s", raw)
