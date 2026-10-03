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
sees one a killed loop left behind. Publication moves the local entries in and
replaces ``rewrite.json`` last, which is the commit point. A workspace whose
publication did not happen -- a storage error, or a loop that died -- is
published by ``recover`` when the next loop starts and before every round.

With the setting unset, callers retain the original in-place behavior.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax import layout

log = logging.getLogger("evolve")

WORK_ROOT_ENV = "EVOLVE_WORK_ROOT"
TOOL_BIN_ENV = "EVOLVE_TOOL_BIN"


class WorkspaceTransferError(RuntimeError):
    """A rewrite could not be staged from or published to durable storage."""


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
        """Move the local record into its durable directory, rewrite.json last."""
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
        incoming = target.with_name(
            f".{target.name}.incoming-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            target.mkdir(parents=True, exist_ok=True)
            # Preserve links as links. Following a CLI credential link here
            # would copy private authentication into the durable record.
            shutil.copytree(self.active.path, incoming, symlinks=True)
            # A complete record always has readable metadata. Validate it
            # before anything replaces the claim.
            metadata = json.loads((incoming / "rewrite.json").read_text())
            if not isinstance(metadata, dict):
                raise ValueError("rewrite.json is not an object")
            for entry in sorted(incoming.iterdir()):
                if entry.name == "rewrite.json":
                    continue
                dst = target / entry.name
                # Left by an earlier publication that failed part way.
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(dst)
                os.replace(entry, dst)
            # The commit point: until this rename the durable record still
            # reads as the running claim.
            os.replace(incoming / "rewrite.json", self.durable.meta)
            incoming.rmdir()
        except (OSError, shutil.Error, ValueError) as exc:
            raise WorkspaceTransferError(
                f"could not publish {self.active.path} to {target}; the local "
                f"rewrite was preserved and a partial archive may remain at "
                f"{incoming}: {exc}"
            ) from exc
        return self.durable

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


def recover(root: layout.Root) -> list[layout.RewriteDir]:
    """Publish every local rewrite a stopped loop or a failed publication left.

    Only safe while no rewrite of this root is running: at loop start under
    the singleton lock, and between rounds. Returns the durable records
    published; a record still ``running`` afterwards was cut short, and
    finalize_interrupted_traces marks it. A publication that fails again is
    logged and left for the next call.
    """
    work_root = configured_root(root)
    if work_root is None:
        return []
    published = []
    active_root = work_root / "active"
    for active in sorted(active_root.glob("tasks/*/rewrites/*")):
        if not active.is_dir():
            continue
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
            continue
        try:
            workspace.publish()
        except (WorkspaceTransferError, RuntimeError) as exc:
            log.warning("could not recover local rewrite %s: %s", active, exc)
            continue
        workspace.cleanup()
        log.warning("recovered local rewrite %s into %s", active, durable.path)
        published.append(durable)
    return published


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
