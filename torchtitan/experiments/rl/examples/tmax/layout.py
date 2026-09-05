"""Where everything under one experiment root lives. LAYOUT.md is the contract;
this module is the only place a path is spelled.

A process writes only under the directory it owns: the trainer under its run
(``Run``), the evolve loop under ``Evolution`` and ``MixDir``. Names carry
identity: ``<what>--<UTC stamp>``, task ids first. ``stamp()`` is the one
clock format, ``YYYYMMDD-HHMMSSZ``, so names sort by time.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

STAMP_FMT = "%Y%m%d-%H%M%SZ"
RUN_PREFIX = "tmax-9b"
_SAFE = re.compile(r"[^A-Za-z0-9._+=@-]")
_VERSION = re.compile(r"^v(\d+)--(\d{8}-\d{6}Z)\.jsonl$")


def stamp(t: float | None = None) -> str:
    """UTC, second resolution, sortable as a string."""
    return time.strftime(STAMP_FMT, time.gmtime(time.time() if t is None else t))


def parse_stamp(s: str) -> float:
    """The unix time a ``stamp()`` string names."""
    return calendar.timegm(time.strptime(s, STAMP_FMT))


def safe(name: str) -> str:
    """A task id or run name as one path segment."""
    return _SAFE.sub("_", str(name))


def write_json_atomic(path: Path, value: dict) -> None:
    """Write beside, then rename in: a reader never sees a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".incoming")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, value: dict) -> None:
    """One line, one write, flushed: the append-only files are built from these."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink (same inode, no second copy); copy only across filesystems."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def signal_id(run: str, task: str, group: int) -> str:
    """How the ledger names a signal: ``<run>/<task>--g<group>``."""
    return f"{run}/{safe(task)}--g{group}"


@dataclass(frozen=True)
class Root:
    """``$TRL_BASE``: one experiment."""

    path: Path

    @classmethod
    def from_env(cls) -> "Root":
        base = os.environ.get("TRL_BASE")
        if not base:
            raise RuntimeError("TRL_BASE is not set; it names the experiment root")
        return cls(Path(base))

    @property
    def experiment_json(self) -> Path:
        return self.path / "experiment.json"

    @property
    def bin(self) -> Path:
        return self.path / "bin"

    @property
    def data(self) -> Path:
        return self.path / "data"

    @property
    def mix(self) -> "MixDir":
        return MixDir(self.path / "data" / "mix")

    @property
    def runs(self) -> Path:
        return self.path / "runs"

    @property
    def latest(self) -> Path:
        return self.runs / "latest"

    @property
    def evals(self) -> Path:
        return self.path / "evals"

    @property
    def evolution(self) -> "Evolution":
        return Evolution(self.path / "evolution")

    @property
    def logs(self) -> Path:
        return self.path / "logs"

    def run(self, name: str) -> "Run":
        return Run(self.runs / name)

    def new_run_name(self, prefix: str = RUN_PREFIX, t: float | None = None) -> str:
        return f"{prefix}--{stamp(t)}"

    def run_dirs(self) -> list["Run"]:
        """Every run, oldest first; the ``latest`` link is not a run."""
        if not self.runs.exists():
            return []
        return [Run(p) for p in sorted(self.runs.iterdir())
                if p.is_dir() and not p.is_symlink() and p.name != "latest"]


@dataclass(frozen=True)
class MixDir:
    """``data/mix``: every version served, and a hardlink named ``live.jsonl``."""

    path: Path

    @property
    def history(self) -> Path:
        return self.path / "history"

    @property
    def live(self) -> Path:
        return self.path / "live.jsonl"

    def version_path(self, version: int, stamp_: str | None = None) -> Path:
        return self.history / f"v{version:04d}--{stamp_ or stamp()}.jsonl"

    @staticmethod
    def manifest_of(version_path: Path) -> Path:
        return version_path.with_name(version_path.name[: -len(".jsonl")] + ".manifest.json")

    @staticmethod
    def inputs_of(version_path: Path) -> Path:
        """The build manifest of a version that came from outside (v1, the
        seed): what the seed's builder pinned by sha256, copied in."""
        return version_path.with_name(version_path.name[: -len(".jsonl")] + ".inputs.json")

    def versions(self) -> list[tuple[int, Path]]:
        """(version, file), ascending, from the history directory's names."""
        if not self.history.exists():
            return []
        found = []
        for p in self.history.iterdir():
            m = _VERSION.match(p.name)
            if m:
                found.append((int(m.group(1)), p))
        return sorted(found)

    def live_version(self) -> tuple[int, Path] | None:
        """Which history file ``live.jsonl`` is: matched by inode, then by content."""
        if not self.live.exists():
            return None
        try:
            ino = self.live.stat().st_ino
            for version, p in self.versions():
                if p.stat().st_ino == ino:
                    return version, p
        except OSError:
            pass
        digest = sha256_file(self.live)
        for version, p in self.versions():
            manifest = self.manifest_of(p)
            try:
                if json.loads(manifest.read_text()).get("sha256") == digest:
                    return version, p
            except (OSError, ValueError):
                continue
        return None

    def publish(self, rows: list[str], *, parent_version: int | None = None,
                t: float | None = None) -> tuple[int, Path]:
        """Write the next version and its manifest, then point ``live.jsonl`` at it.

        ``rows`` are complete JSON lines without the trailing newline. The
        version file is written whole beside the history and renamed in; the
        live name is replaced by a new hardlink in one ``os.replace``, so a
        reader holds either the old inode or the new one, never a torn file.
        """
        existing = self.versions()
        version = (existing[-1][0] + 1) if existing else 1
        if parent_version is None and existing:
            parent_version = existing[-1][0]
        st = stamp(t)
        target = self.version_path(version, st)
        self.history.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".incoming")
        tmp.write_text("".join(r + "\n" for r in rows))
        os.replace(tmp, target)
        write_json_atomic(self.manifest_of(target), {
            "version": version, "parent_version": parent_version, "stamp": st,
            "sha256": sha256_file(target), "rows": len(rows),
        })
        link_tmp = self.live.with_name("live.jsonl.incoming")
        if link_tmp.exists():
            link_tmp.unlink()
        try:
            os.link(target, link_tmp)
        except OSError:
            shutil.copy2(target, link_tmp)
        os.replace(link_tmp, self.live)
        return version, target


def mix_dir_of(path: Path) -> "MixDir | None":
    """The MixDir a file belongs to when it is a root's ``live.jsonl``, else None."""
    path = Path(path)
    if path.name == "live.jsonl" and (path.parent / "history").is_dir():
        return MixDir(path.parent)
    return None


def write_mix(path: Path, rows: list[str]) -> tuple[int, Path] | None:
    """Write a mix file the way its location requires.

    A root's ``live.jsonl`` shares its inode with a history version, so it is
    never written in place: the rows become the next published version and
    the link moves. Any other path (a seed file being prepared, a copy under
    a scratch directory) is replaced atomically. Returns the published
    (version, path), or None when the file was not a root's live mix.
    """
    mix = mix_dir_of(path)
    if mix is not None:
        return mix.publish(rows)
    path = Path(path)
    tmp = path.with_name(path.name + ".incoming")
    tmp.write_text("".join(r + "\n" for r in rows))
    os.replace(tmp, path)
    return None


@dataclass(frozen=True)
class Run:
    """``runs/<name>``: one trainer process lifetime."""

    path: Path

    @classmethod
    def from_env(cls) -> "Run | None":
        d = os.environ.get("TRL_RUN_DIR")
        return cls(Path(d)) if d else None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def launch_json(self) -> Path:
        return self.path / "launch.json"

    @property
    def launch_diff(self) -> Path:
        """The checkout's uncommitted changes, present only when tt_commit is -dirty."""
        return self.path / "launch.diff"

    @property
    def stdout_log(self) -> Path:
        return self.path / "stdout.log"

    @property
    def inputs(self) -> Path:
        return self.path / "inputs"

    @property
    def inputs_mix(self) -> Path:
        return self.inputs / "mix.jsonl"

    @property
    def trainer(self) -> Path:
        return self.path / "trainer"

    @property
    def mix_versions(self) -> Path:
        return self.trainer / "mix_versions.jsonl"

    @property
    def checkpoints(self) -> Path:
        return self.path / "checkpoints"

    @property
    def rollouts(self) -> Path:
        return self.path / "rollouts"

    @property
    def signals(self) -> Path:
        return self.path / "signals"

    @property
    def advisories(self) -> Path:
        return self.path / "advisories"

    def rollout_record(self, task: str, group: int, idx: int) -> Path:
        return self.rollouts / safe(task) / f"g{group}-r{idx}.jsonl"

    def pane(self, task: str, group: int, idx: int) -> Path:
        """The terminal transcript beside the record, when TMAX_PANE_DUMP=1."""
        return self.rollouts / safe(task) / f"g{group}-r{idx}.pane"

    def signal(self, task: str, group: int) -> Path:
        return self.signals / f"{safe(task)}--g{group}.json"

    def advisory(self, name: str) -> Path:
        return self.advisories / f"{name}.jsonl"

    def signal_files(self) -> list[Path]:
        return sorted(self.signals.glob("*.json")) if self.signals.exists() else []


@dataclass(frozen=True)
class Evolution:
    """``evolution/``: the loop's state. Only the loop writes here."""

    path: Path

    @property
    def loop_log(self) -> Path:
        return self.path / "loop.log"

    @property
    def loop_lock(self) -> Path:
        return self.path / "loop.lock"

    @property
    def loop_env(self) -> Path:
        return self.path / "loop.env"

    @property
    def ledger(self) -> Path:
        return self.path / "ledger.jsonl"

    @property
    def status(self) -> Path:
        return self.path / "status.json"

    @property
    def tasks(self) -> Path:
        return self.path / "tasks"

    def task(self, task_id: str) -> "TaskDir":
        return TaskDir(self.tasks / safe(task_id))

    def task_dirs(self) -> list["TaskDir"]:
        if not self.tasks.exists():
            return []
        return [TaskDir(p) for p in sorted(self.tasks.iterdir()) if p.is_dir()]


@dataclass(frozen=True)
class TaskDir:
    """``evolution/tasks/<task>``: one task's revisions and rewrites."""

    path: Path

    @property
    def task_id(self) -> str:
        return self.path.name

    @property
    def lineage(self) -> Path:
        return self.path / "lineage.jsonl"

    def rev(self, n: int) -> Path:
        return self.path / f"r{n}"

    def revs(self) -> list[int]:
        if not self.path.exists():
            return []
        out = []
        for p in self.path.iterdir():
            if p.is_dir() and re.fullmatch(r"r\d+", p.name):
                out.append(int(p.name[1:]))
        return sorted(out)

    def latest_rev(self) -> int | None:
        revs = self.revs()
        return revs[-1] if revs else None

    @property
    def rewrites(self) -> Path:
        return self.path / "rewrites"

    def rewrite(self, job: str, stamp_: str | None = None) -> "RewriteDir":
        return RewriteDir(self.rewrites / f"{stamp_ or stamp()}--{job}")

    def rewrite_dirs(self) -> list["RewriteDir"]:
        if not self.rewrites.exists():
            return []
        return [RewriteDir(p) for p in sorted(self.rewrites.iterdir()) if p.is_dir()]


@dataclass(frozen=True)
class RewriteDir:
    """``…/rewrites/<stamp>--<job>``: one handled signal."""

    path: Path

    @property
    def meta(self) -> Path:
        return self.path / "rewrite.json"

    @property
    def package(self) -> Path:
        return self.path / "package"

    @property
    def traces(self) -> Path:
        return self.package / "traces"

    @property
    def sessions(self) -> Path:
        return self.path / "sessions"

    def session(self, kind: str, stamp_: str | None = None) -> "SessionDir":
        return SessionDir(self.sessions / f"{stamp_ or stamp()}--{kind}")

    def session_dirs(self) -> list["SessionDir"]:
        if not self.sessions.exists():
            return []
        return [SessionDir(p) for p in sorted(self.sessions.iterdir()) if p.is_dir()]


@dataclass(frozen=True)
class SessionDir:
    """``…/sessions/<stamp>--<kind>``: one codex invocation."""

    path: Path

    @property
    def meta(self) -> Path:
        return self.path / "session.json"

    @property
    def prompt(self) -> Path:
        return self.path / "prompt.md"

    @property
    def stdout(self) -> Path:
        return self.path / "stdout.txt"

    @property
    def stderr(self) -> Path:
        return self.path / "stderr.txt"

    @property
    def codex_home(self) -> Path:
        return self.path / "codex"

    @property
    def package(self) -> Path:
        """A session that writes a package of its own (the blind verifier
        author works without seeing the solution) keeps it here."""
        return self.path / "package"
