# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Node-local execution workspaces for task evolution.

The experiment root is durable storage. It is a good place for seeds, rewrite
records, and completed revisions, but it is a poor working directory for an
agent that creates and reads thousands of small files over several hours.
When ``EVOLVE_WORK_ROOT`` is set, this module stages the input revision and the
tool binaries below that node-local directory, keeps the whole active rewrite
there, and publishes the completed rewrite back to the experiment root.

The durable rewrite directory exists from the start, holding only a claim
``rewrite.json`` (status ``running``, the local path, the host), so the root
alone still says which rewrites are in flight and finalize_interrupted_traces
sees one a killed loop left behind. Publication copies the local entries
straight into it, many files at once, and replaces ``rewrite.json`` last,
which is the commit point. A workspace whose publication did not happen -- a
storage error, or a loop that died -- is published by ``recover`` when the
next loop starts and before every round; copying over a partial copy is safe.

With the setting unset, callers retain the original in-place behavior.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import shutil
import socket
import stat
import tarfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout

log = logging.getLogger("evolve")

WORK_ROOT_ENV = "EVOLVE_WORK_ROOT"
TOOL_BIN_ENV = "EVOLVE_TOOL_BIN"


# Each write to the durable FUSE mount is a ~0.2-1 s round trip, but calls in
# flight overlap: 16 threads write 48 files in ~8 s, against ~50 s serially.
PUBLISH_WORKERS = int(os.environ.get("EVOLVE_PUBLISH_WORKERS", "16"))
# A directory under package/run/ with more files than this is published as
# one archive: agent scratch such as a copied git tree (15k files in run 1031b)
# otherwise holds the shared publish pool for an hour on the FUSE root.
RUN_DIR_PACK_FILES = 200
# Local workspaces recover() publishes at once; each is independent.
RECOVER_WORKERS = 8
# Durable-store limits for one path component and for a whole path.
_MAX_NAME_BYTES = 255
_MAX_PATH_BYTES = 4095
# Durable roots recover() has swept for stale staging dirs in this process.
_swept_roots: set[Path] = set()


class WorkspaceTransferError(RuntimeError):
    """A rewrite could not be staged from or published to durable storage."""


class UnpublishableRewrite(WorkspaceTransferError):
    """The rewrite holds a path durable storage cannot represent.

    publish() has already committed a ``failed`` record and moved the local
    workspace aside when this is raised; handle() closes the signal on that record,
    so it happens once per rewrite.
    """


def _experiment_namespace(root: layout.Root) -> str:
    identity = str(root.path.absolute()).encode()
    digest = hashlib.sha256(identity).hexdigest()[:12]
    return f"{layout.safe(root.path.name)}-{digest}"


def configured_root(root: layout.Root) -> Path | None:
    """The node-local namespace for this experiment, or None when disabled."""
    raw = os.environ.get(WORK_ROOT_ENV, "").strip()
    if not raw:
        return None
    work_root = Path(raw).expanduser()
    if not work_root.is_absolute():
        raise ValueError(f"{WORK_ROOT_ENV} must be an absolute path: {work_root}")
    work_root.mkdir(parents=True, exist_ok=True)
    work_root = work_root.resolve()
    durable_root = root.path.resolve()
    try:
        work_root.relative_to(durable_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            f"{WORK_ROOT_ENV} must be outside the durable experiment root: "
            f"{work_root} is under {durable_root}"
        )
    return work_root / _experiment_namespace(root)


def _copytree(src: Path, dst: Path, *, ignore=None, symlinks: bool = False) -> None:
    try:
        shutil.copytree(src, dst, ignore=ignore, symlinks=symlinks)
    except (OSError, shutil.Error) as exc:
        raise WorkspaceTransferError(
            f"could not copy {src} to {dst}; partial data was left in place: {exc}"
        ) from exc


@dataclass(frozen=True)
class RewriteWorkspace:
    """The active and durable locations for one rewrite."""

    durable: layout.RewriteDir
    active: layout.RewriteDir
    seed_dir: Path
    local_seed_dir: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.active.path != self.durable.path

    def stage_source(self, source: Path, *, ignore=None) -> None:
        """Materialize the immutable input and the agent's working package."""
        if not self.is_local:
            # Preserve the legacy path's failure behavior: handle() records a
            # setup failure in the already-created durable rewrite.
            shutil.copytree(source, self.active.package, ignore=ignore)
            return
        assert self.local_seed_dir is not None
        _copytree(source, self.local_seed_dir, ignore=ignore)
        _copytree(self.local_seed_dir, self.active.package)

    def copy_input(self, src: Path, dst: Path) -> None:
        """Copy an input into an active rewrite without retaining a remote inode."""
        if not self.is_local:
            layout.link_or_copy(src, dst)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise WorkspaceTransferError(
                f"could not stage {src} in {dst}; local rewrite left at "
                f"{self.active.path}: {exc}"
            ) from exc

    def copy_input_tree(self, src: Path, dst: Path) -> None:
        """Copy an input directory without retaining remote links."""
        if not self.is_local:
            shutil.copytree(src, dst)
            return
        _copytree(src, dst)

    def claim(self, meta: dict) -> None:
        """Write the durable record of an active local rewrite."""
        if not self.is_local:
            return
        layout.write_json_atomic(
            self.durable.meta,
            {
                **meta,
                "active_path": str(self.active.path),
                "host": socket.gethostname(),
            },
        )

    def publish(self) -> layout.RewriteDir:
        """Copy the local record into its durable directory, rewrite.json last."""
        if not self.is_local:
            return self.durable
        target = self.durable.path
        if target.exists() and self.durable.meta.exists():
            try:
                claimed = json.loads(self.durable.meta.read_text()).get("active_path")
            except (OSError, ValueError) as exc:
                raise WorkspaceTransferError(
                    f"could not read the claim at {self.durable.meta}: {exc}"
                ) from exc
            if claimed != str(self.active.path):
                raise RuntimeError(f"refusing to overwrite rewrite record {target}")
        bad: list[str] = []
        try:
            _pack_session_payloads(self.active.path)
            _pack_bulky_run_dirs(self.active.path)
            # A complete record always has readable metadata. Validate it
            # before anything replaces the claim.
            metadata = json.loads(self.active.meta.read_text())
            if not isinstance(metadata, dict):
                raise ValueError("rewrite.json is not an object")
            skip = {Path("rewrite.json")}
            bad = _unpublishable_paths(self.active.path, target)
            if bad:
                skip |= self._mark_unpublishable(metadata, bad)
            target.mkdir(parents=True, exist_ok=True)
            # Straight into the target, no staging directory: a directory
            # rename there is cheap, but a crashed publication left the
            # staging copy behind. A partial copy here is still covered by the
            # running claim, and recover() copies over it. Links stay links,
            # so a CLI credential link is not followed into the record.
            _copy_into(self.active.path, target, skip=skip)
            # The commit point: until this rename the durable record still
            # reads as the running claim.
            _copy_file_atomic(self.active.meta, self.durable.meta)
        except (OSError, shutil.Error, ValueError) as exc:
            raise WorkspaceTransferError(
                f"could not publish {self.active.path} to {target}; the local "
                f"rewrite was preserved and a partial copy may remain: {exc}"
            ) from exc
        _remove_stale_incoming(
            target.parent, f".{glob.escape(target.name)}.incoming-*"
        )
        if bad:
            quarantine = self._quarantine()
            raise UnpublishableRewrite(
                f"{target} published as failed: {metadata['reason']}; local "
                f"workspace moved to {quarantine}"
            )
        return self.durable

    def _mark_unpublishable(self, metadata: dict, bad: list[str]) -> set[Path]:
        """Turn the local record into a failure and return the paths to omit.

        A junk name in ``package/`` (say, a file named after a shell snippet)
        is not renamed or dropped to make the package fit: that would publish
        a package other than the one validated. The rewrite fails once
        instead, the package stays out of the durable record, and the rest
        (traces, sessions) is published for diagnosis.
        """
        omit = {Path(p) for p in bad}
        if any(Path(p).parts[0] == "package" for p in bad):
            omit.add(Path("package"))
        shown = ", ".join(repr(p) for p in bad[:3])
        more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
        metadata.update(
            status="failed",
            stage="publish",
            reason=f"unpublishable file names: {shown}{more}"[:300],
            unpublishable=[repr(p) for p in bad[:20]],
            finished=layout.stamp(),
        )
        layout.write_json_atomic(self.active.meta, metadata)
        return omit

    def _quarantine(self) -> Path:
        """Move a failed local rewrite out of recover()'s reach, for inspection."""
        # active.path is <work_root>/active/tasks/<task>/rewrites/<name>.
        active_root = self.active.path.parents[3]
        assert active_root.name == "active", self.active.path
        quarantine = (
            active_root.parent
            / "quarantine"
            / self.active.path.relative_to(active_root)
        )
        try:
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.active.path, quarantine)
        except OSError as exc:
            log.warning("could not quarantine %s, removing it: %s", self.active.path, exc)
            shutil.rmtree(self.active.path, ignore_errors=True)
        if self.local_seed_dir is not None:
            shutil.rmtree(self.local_seed_dir, ignore_errors=True)
        return quarantine

    def cleanup(self) -> None:
        """Remove local staging only after publication completed."""
        if not self.is_local:
            return
        for path in (self.active.path, self.local_seed_dir):
            if path is None:
                continue
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("could not remove local rewrite workspace %s: %s", path, exc)


def prepare_rewrite(
    root: layout.Root,
    durable: layout.RewriteDir,
    source: Path,
) -> RewriteWorkspace:
    """Stage an input revision and return the location used by the agent."""
    work_root = configured_root(root)
    if work_root is None:
        durable.path.mkdir(parents=True)
        return RewriteWorkspace(durable, durable, source)

    workspace = _workspace(root, work_root, durable)
    assert workspace.local_seed_dir is not None
    if workspace.active.path.exists() or workspace.local_seed_dir.exists():
        raise WorkspaceTransferError(
            f"local rewrite workspace already exists: {workspace.active.path} "
            f"or {workspace.local_seed_dir}"
        )
    durable.path.mkdir(parents=True)
    workspace.active.path.parent.mkdir(parents=True, exist_ok=True)
    workspace.local_seed_dir.parent.mkdir(parents=True, exist_ok=True)
    workspace.active.path.mkdir()
    return workspace


def _workspace(
    root: layout.Root, work_root: Path, durable: layout.RewriteDir
) -> RewriteWorkspace:
    relative = durable.path.relative_to(root.evolution.path)
    local_seed = work_root / "inputs" / relative
    return RewriteWorkspace(
        durable,
        layout.RewriteDir(work_root / "active" / relative),
        local_seed,
        local_seed,
    )


def _pack_session_payloads(active: Path) -> None:
    """Archive each session's subdirectories, ``codex/`` aside, into one file.

    A verifier or probe session keeps its own package and replay copies, a few
    hundred small files that nothing reads once the rewrite is finished. On
    durable storage every file costs a round trip, so each such directory is
    published as ``<name>.tar.gz``. The flat files (``session.json``,
    prompt, stdout/stderr) and the codex transcripts stay readable in place.
    Links are archived as links.
    """
    for session in active.glob("sessions/*"):
        if not session.is_dir() or session.is_symlink():
            continue
        for child in sorted(session.iterdir()):
            if child.name.startswith(".codex-cache-") and not child.is_symlink():
                # A pruned home's caches whose background deletion never ran.
                shutil.rmtree(child, ignore_errors=True)
                continue
            if child.name == "codex" or child.is_symlink() or not child.is_dir():
                continue
            archive = session / f"{child.name}.tar.gz"
            partial = archive.with_name(f".{archive.name}.partial")
            with tarfile.open(partial, "w:gz") as tar:
                tar.add(child, arcname=child.name)
            os.replace(partial, archive)
            shutil.rmtree(child)


def _pack_bulky_run_dirs(active: Path) -> None:
    """Archive each ``package/run/<dir>`` holding more than RUN_DIR_PACK_FILES
    files into ``<dir>.tar.gz``. ``run/`` is harness scratch, stripped from the
    package at fold, so only its diagnostic copy changes shape."""
    run = active / "package" / "run"
    if not run.is_dir() or run.is_symlink():
        return
    for child in sorted(run.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        count = 0
        for _dirpath, _dirnames, filenames in os.walk(child):
            count += len(filenames)
            if count > RUN_DIR_PACK_FILES:
                break
        if count <= RUN_DIR_PACK_FILES:
            continue
        archive = run / f"{child.name}.tar.gz"
        partial = archive.with_name(f".{archive.name}.partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(child, arcname=child.name)
        os.replace(partial, archive)
        shutil.rmtree(child)


def _unpublishable_name(name: str) -> bool:
    # The durable FUSE mount fails a name with a newline as ENOENT. Tab and the other
    # control characters pass today, but no file a tool means to create
    # carries one, so they are refused alike.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return True
    try:
        return len(name.encode("utf-8")) > _MAX_NAME_BYTES
    except UnicodeEncodeError:
        return True


def _unpublishable_paths(active: Path, target: Path) -> list[str]:
    """Paths under ``active`` the durable store cannot hold, relative, sorted."""
    bad = []
    base = len(os.fsencode(target)) + 1
    for dirpath, dirnames, filenames in os.walk(active):
        rel = Path(dirpath).relative_to(active)
        for name in dirnames + filenames:
            path = rel / name
            if (
                _unpublishable_name(name)
                or base + len(os.fsencode(path)) > _MAX_PATH_BYTES
            ):
                bad.append(str(path))
        # Whatever is below a bad directory is left out with it.
        dirnames[:] = [d for d in dirnames if str(rel / d) not in bad]
    return sorted(bad)


# One pool for the whole process: concurrent publishes share PUBLISH_WORKERS
# storage round trips instead of each opening its own.
_PUBLISH_POOL: ThreadPoolExecutor | None = None
_PUBLISH_POOL_LOCK = threading.Lock()


def _pool_map(fn, items: list) -> None:
    global _PUBLISH_POOL
    if not items:
        return
    with _PUBLISH_POOL_LOCK:
        if _PUBLISH_POOL is None:
            _PUBLISH_POOL = ThreadPoolExecutor(
                max_workers=PUBLISH_WORKERS, thread_name_prefix="publish"
            )
        pool = _PUBLISH_POOL
    # list() re-raises the first failure.
    list(pool.map(fn, items))


def _copy_file_atomic(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.incoming")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _copy_into(src: Path, dst: Path, *, skip: set[Path]) -> None:
    """Copy the tree at ``src`` over ``dst`` in parallel, links as links.

    Directories are made level by level, then all files and links at once.
    JSON records go through a rename, so a crash never leaves one torn for
    finalize_interrupted_traces to fail on; other files are written in place.
    """
    levels: dict[int, list[Path]] = {}
    files: list[Path] = []
    links: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        descend = []
        for name in dirnames:
            path = rel / name
            if path in skip:
                continue
            if (src / path).is_symlink():
                links.append(path)
            else:
                descend.append(name)
                levels.setdefault(len(path.parts), []).append(path)
        dirnames[:] = descend
        for name in filenames:
            path = rel / name
            if path not in skip:
                (links if (src / path).is_symlink() else files).append(path)

    for depth in sorted(levels):
        _pool_map(lambda p: (dst / p).mkdir(exist_ok=True), levels[depth])

    def copy_file(path: Path) -> None:
        if path.suffix == ".json":
            _copy_file_atomic(src / path, dst / path)
        else:
            shutil.copy2(src / path, dst / path)

    def copy_link(path: Path) -> None:
        target = os.readlink(src / path)
        try:
            os.symlink(target, dst / path)
        except FileExistsError:
            (dst / path).unlink()
            os.symlink(target, dst / path)

    jobs = [(copy_file, p) for p in files] + [(copy_link, p) for p in links]
    _pool_map(lambda job: job[0](job[1]), jobs)


def _remove_tree(path: Path) -> None:
    """rmtree with the unlinks in parallel; each one is a durable round trip."""
    entries: list[Path] = []
    dirs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        dirs.append(here)
        entries.extend(here / name for name in filenames)
        # os.walk lists a link to a directory as a directory but never enters it.
        entries.extend(here / name for name in dirnames if (here / name).is_symlink())
    _pool_map(os.unlink, entries)
    for directory in reversed(dirs):
        directory.rmdir()


def _remove_stale_incoming(parent: Path, pattern: str) -> None:
    """Drop the ``.<name>.incoming-*`` dirs older publications left behind.

    Earlier versions staged a full copy there and a crash stranded it; it also
    reads as one more rewrite to anything listing ``rewrites/``.
    """
    try:
        stale = sorted(parent.glob(pattern))
    except OSError as exc:
        log.warning("could not list stale publications under %s: %s", parent, exc)
        return
    for path in stale:
        try:
            _remove_tree(path)
        except OSError as exc:
            log.warning("could not remove stale publication %s: %s", path, exc)
        else:
            log.warning("removed stale publication %s", path)


def _prune_interrupted_codex_homes(active: Path) -> None:
    """Drop the client caches a cut-short Codex session left in its home.

    A session that ends normally keeps only ``sessions/`` (evolve_codex
    ``_prune_private_home``); one stopped mid-run still holds the CLI's plugin
    and skill caches, ~4k small files per session, which publication would
    copy one by one into the durable root. Claude Code keeps its transcript
    elsewhere in its home, so its homes are left whole.
    """
    agent = os.environ.get("EVOLVE_AGENT") or (
        "claude" if os.environ.get("SWE_RETUNE_AGENT") == "claude" else "codex"
    )
    if agent == "claude":
        return
    for home in active.glob("sessions/*/codex"):
        if not home.is_dir() or home.is_symlink():
            continue
        for child in home.iterdir():
            if child.name == "sessions":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def recover(root: layout.Root) -> list[layout.RewriteDir]:
    """Publish every local rewrite a stopped loop or a failed publication left.

    Only safe while no rewrite of this root is running: at loop start under
    the singleton lock, and between rounds. Returns the durable records
    published; a record still ``running`` afterwards was cut short, and
    finalize_interrupted_traces marks it. A publication that fails again is
    logged and left for the next call; one with unpublishable names is
    published as failed and not tried again.
    """
    work_root = configured_root(root)
    if work_root is None:
        return []
    if root.path not in _swept_roots:
        # Once per process: staging copies that crashed publications by older
        # versions left, including those of rewrites no longer local.
        _swept_roots.add(root.path)
        _remove_stale_incoming(root.evolution.path, "tasks/*/rewrites/.*.incoming-*")
    active_root = work_root / "active"
    actives = [p for p in sorted(active_root.glob("tasks/*/rewrites/*")) if p.is_dir()]

    def one(active: Path) -> layout.RewriteDir | None:
        durable = layout.RewriteDir(
            root.evolution.path / active.relative_to(active_root)
        )
        workspace = _workspace(root, work_root, durable)
        if not workspace.active.meta.exists():
            # Stopped before its first record: nothing ran, nothing to keep.
            log.warning("removing local rewrite with no record: %s", active)
            workspace.cleanup()
            try:
                durable.path.rmdir()
            except OSError:
                pass
            return None
        t0 = time.monotonic()
        _prune_interrupted_codex_homes(active)
        try:
            workspace.publish()
        except UnpublishableRewrite as exc:
            log.warning("could not recover local rewrite %s: %s", active, exc)
            return durable
        except (WorkspaceTransferError, RuntimeError) as exc:
            log.warning("could not recover local rewrite %s: %s", active, exc)
            return None
        workspace.cleanup()
        log.warning(
            "recovered local rewrite %s into %s in %.1fs",
            active,
            durable.path,
            time.monotonic() - t0,
        )
        return durable

    if not actives:
        return []
    # Workspaces are independent; each keeps prune -> publish -> cleanup in
    # order. Their file copies share the process-wide publish pool.
    with ThreadPoolExecutor(
        min(RECOVER_WORKERS, len(actives)), thread_name_prefix="recover"
    ) as pool:
        results = list(pool.map(one, actives))
    return [d for d in results if d is not None]


def _tool_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        digest.update(str(entry.relative_to(path)).encode())
        if entry.is_symlink():
            digest.update(os.readlink(entry).encode())
            continue
        if not entry.is_file():
            continue
        digest.update(stat.S_IMODE(entry.stat().st_mode).to_bytes(4, "big"))
        with entry.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()[:16]


def prepare_tool_bin(root: layout.Root) -> Path:
    """Copy the root's pinned command-line tools to node-local storage."""
    if override := os.environ.get(TOOL_BIN_ENV, "").strip():
        return Path(override)
    work_root = configured_root(root)
    if work_root is None:
        return root.bin
    try:
        source = root.bin.resolve(strict=True)
        digest = _tool_digest(source)
    except OSError as exc:
        raise WorkspaceTransferError(
            f"could not read tool directory {root.bin}: {exc}"
        ) from exc
    target = work_root / "tools" / digest
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    incoming = target.with_name(
        f".{target.name}.incoming-{os.getpid()}-{uuid.uuid4().hex}"
    )
    _copytree(source, incoming)
    try:
        os.rename(incoming, target)
    except FileExistsError:
        shutil.rmtree(incoming)
    except OSError as exc:
        try:
            shutil.rmtree(incoming)
        except OSError:
            pass
        raise WorkspaceTransferError(
            f"could not install node-local tools at {target}: {exc}"
        ) from exc
    return target
