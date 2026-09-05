# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""tmax terminal-agent dataset for the coding-agent RL example.

Reads a JSONL produced by ``prepare_tmax_data.py`` (R2E-compatible schema with a
``tmax`` metadata blob instead of ``r2e``). Each row::

    {
      "prompt": <instruction.md>,
      "label": <task_id>,
      "metadata": {
        "instance_id", "image" (docker.io/...), "workdir",
        "problem_statement": <instruction.md>,
        "tmax": {"test_sh", "fixtures": {relpath: content}, "reward_path"}
      }
    }

The dataset is an endless, seeded stream of frozen ``TMaxSample``s, mirroring
``SWER2EDataset`` (same Configurable interface: ``data_path`` / ``seed`` /
``shuffle`` config, ``__iter__`` / ``__next__``, ``state_dict`` /
``load_state_dict``).
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from torchtitan.config import Configurable
from torchtitan.experiments.rl.examples.tmax import layout
from torchtitan.experiments.rl.training_lineage import canonical_json, content_revision
from torchtitan.experiments.rl.types import SampleLineage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class TMaxSample:
    """One tmax terminal-agent task: a containerized env, an instruction, and a
    verifier script that writes a 0/1 reward inside the container."""

    instance_id: str
    """Stable task id (e.g. ``task_000000_c19dda5b``)."""

    image: str
    """Public docker image the task runs in (e.g. ``docker.io/hamishi740/...``).
    Empty when the task ships a ``dockerfile`` for the backend to build instead."""

    dockerfile: str | None = None
    """Dockerfile text, for corpora that publish no image (e.g. RTS). The sandbox
    backend builds it and caches the result; see DaytonaSandbox._declarative_image."""

    build_context: dict[str, str] | None = None
    """That Dockerfile's COPY sources as {relpath: base64}, materialized next to
    the Dockerfile at build time. None when the Dockerfile needs no context."""

    entrypoint: str | None = None
    """The image's ENTRYPOINT with CMD as its arguments, as one shell command.

    Sandbox backends exec commands directly and never run PID 1, so a task whose
    environment is set up by its ENTRYPOINT (a localhost server standing in for a
    hardcoded URL, an /etc/hosts entry, a daemon the instruction assumes) needs it
    started explicitly before the agent. None when the Dockerfile declares none."""

    agent_timeout_sec: float | None = None
    """The task's own wall-clock budget for the agent (Harbor ``[agent].timeout_sec``).

    Harbor states this per task, not per benchmark. None for corpora that do not
    declare one, in which case the rollouter falls back to its configured default."""

    verifier_timeout_sec: float | None = None
    """The task's own wall-clock budget for the GRADER (Harbor ``[verifier].timeout_sec``).

    Stated per task like ``agent_timeout_sec``, and for the same reason: a task whose
    test suite compiles a renderer needs a different budget than one that greps a log.
    TB-2.1 declares 360s to 12000s across its 89 tasks. None for corpora that declare
    nothing, in which case the rollouter uses its configured default."""

    daytona_cpu: int | None = None
    """Optional per-task Daytona vCPU allocation. None = TT_DAYTONA_CPU default."""

    daytona_mem_gb: int | None = None
    """Optional per-task Daytona memory allocation in GiB. None = TT_DAYTONA_MEM_GB default."""

    daytona_disk_gb: int | None = None
    """Optional per-task Daytona root-disk allocation in GiB."""

    workdir: str
    """Working directory inside the sandbox (best-guess; default ``/workspace``)."""

    problem_statement: str
    """The instruction the agent must satisfy (instruction.md)."""

    tmax: dict = field(default_factory=dict)
    """Grading payload: ``test_sh``, ``fixtures`` ({relpath: content}), ``reward_path``."""

    rev: int = 0
    """Which revision of the task this row is (``metadata.rev``): 0 is the seed,
    and each accepted rewrite folded into the mix raises it. Travels into every
    rollout record and signal, so a verdict names the revision it was reached on."""

    lineage: SampleLineage | None = field(default=None, compare=False)
    """Per-yield identity; excluded from equality because it is not task content."""


def _parse_sample_row(row: dict) -> TMaxSample:
    """One jsonl row -> TMaxSample; shared by __init__ and hot reload so a
    reloaded row passes exactly the checks a boot-time row does."""
    md = row.get("metadata") or {}
    instance_id = (
        md.get("instance_id")
        or (row.get("label") if isinstance(row.get("label"), str) else None)
        or "unknown"
    )
    image = md.get("image")
    dockerfile = md.get("dockerfile")
    build_context = md.get("build_context")
    tmax = md.get("tmax") or {}
    if not (image or dockerfile) or not tmax:
        raise ValueError(
            f"row {instance_id!r} missing image/dockerfile/tmax in metadata"
        )
    # Per-task Daytona resource overrides (cpu / mem GiB / disk GiB); each is
    # optional and a missing/None field falls back to the TT_DAYTONA_* env default.
    daytona_resources: dict[str, int | None] = {}
    for md_key in ("daytona_cpu", "daytona_mem_gb", "daytona_disk_gb"):
        val = md.get(md_key)
        if val is not None and (
            isinstance(val, bool) or not isinstance(val, int) or val <= 0
        ):
            raise ValueError(
                f"row {instance_id!r} has invalid {md_key} "
                f"{val!r}; expected a positive integer"
            )
        daytona_resources[md_key] = val
    return TMaxSample(
        instance_id=instance_id,
        image=image or "",
        dockerfile=dockerfile,
        build_context=build_context,
        entrypoint=md.get("entrypoint"),
        agent_timeout_sec=md.get("agent_timeout_sec"),
        verifier_timeout_sec=md.get("verifier_timeout_sec"),
        daytona_cpu=daytona_resources["daytona_cpu"],
        daytona_mem_gb=daytona_resources["daytona_mem_gb"],
        daytona_disk_gb=daytona_resources["daytona_disk_gb"],
        workdir=md.get("workdir") or "/workspace",
        problem_statement=md.get("problem_statement")
        or _coerce_prompt(row.get("prompt")),
        tmax=tmax,
        rev=int(md.get("rev") or 0),
    )


def _load_samples(path: str) -> tuple[list[TMaxSample], list[str], str]:
    """Load and hash every complete JSONL row in source order."""
    samples: list[TMaxSample] = []
    revisions: list[str] = []
    canonical_rows: list[str] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            canonical_row = canonical_json(row)
            samples.append(_parse_sample_row(row))
            revisions.append(content_revision(row))
            canonical_rows.append(canonical_row)
    mix_revision = content_revision(canonical_rows)
    return samples, revisions, mix_revision


class TMaxDataset(Configurable):
    """Endless, seeded stream of tmax terminal-agent samples loaded from a JSONL."""

    @dataclass(kw_only=True, slots=True)
    class Config(Configurable.Config):
        data_path: str = ""
        """Path to the tmax JSONL file (required)."""

        seed: int = 42
        """Seed for the row-order shuffle."""

        shuffle: bool = True
        """Shuffle row order (reshuffling on each wrap). Set False for validation."""

        holdout_n: int = 0
        """Reserve the LAST ``holdout_n`` rows (file order) as a held-out validation slice,
        disjoint from training. 0 = no split (whole file). Both the train and validation
        instances must pass the same ``holdout_n`` so the split matches."""

        split: str = "train"
        """Which slice this instance serves: ``train`` (rows[:-holdout_n]) or ``validation``
        (rows[-holdout_n:]). Ignored when ``holdout_n == 0``."""

        include_ids_path: str = ""
        """Optional instance-ID whitelist. The file accepts JSONL rows containing
        ``instance_id`` or one bare ID per line. Filtering preserves the canonical
        seeded order within the selected split. Empty = keep all rows."""

        skip_ids_path: str = ""
        """Optional skip source: a run's ``signals/`` directory (one
        ``<task>--g<group>.json`` per training group with zero reward variance, see
        LAYOUT.md), or a single JSONL/bare-id file. Every task id in it is dropped at
        load, so prompts that gave no learning signal (all-pass or all-fail groups)
        are not sampled again. Empty = keep all rows."""

        initial_skip_samples: int = 0
        """Consume this many samples before the first sample is returned.

        This supports an explicit data-stream offset when resuming a run whose controller
        dataset state was not checkpointed. The skipped samples advance shuffle state in
        exactly the same way as normal iteration, including across dataset wraps.
        """

    def __init__(self, config: Config) -> None:
        if not config.data_path:
            raise ValueError("TMaxDataset.Config.data_path is required")
        if config.split not in ("train", "validation"):
            raise ValueError(
                f"TMaxDataset.Config.split must be 'train' or 'validation', got {config.split!r}"
            )
        samples, sample_revisions, mix_revision = _load_samples(config.data_path)
        if not samples:
            raise ValueError(f"no rows found in {config.data_path}")

        # Held-out split: the last holdout_n rows (in file order) form the validation slice,
        # disjoint from the training slice, so periodic validation measures generalization
        # rather than training-set recall. Deterministic (file order), no separate file.
        # Done BEFORE the ID filters so the train/val boundary is stable regardless of
        # which IDs are selected or skipped.
        if config.holdout_n > 0:
            if config.holdout_n >= len(samples):
                raise ValueError(
                    f"holdout_n={config.holdout_n} >= dataset size {len(samples)}"
                )
            samples = (
                samples[-config.holdout_n :]
                if config.split == "validation"
                else samples[: -config.holdout_n]
            )
            sample_revisions = (
                sample_revisions[-config.holdout_n :]
                if config.split == "validation"
                else sample_revisions[: -config.holdout_n]
            )
        self._samples = samples
        self._sample_revisions = sample_revisions
        self._mix_revision = mix_revision

        self._rng = random.Random(config.seed)
        self._shuffle = config.shuffle
        self._order = list(range(len(self._samples)))
        if self._shuffle:
            self._rng.shuffle(self._order)

        # Apply an explicit curriculum whitelist AFTER the canonical shuffle. This
        # retains the original seed-relative order instead of independently shuffling
        # a shortened dataset. Unlike the optional zero-std skip source below, a bad
        # include path is fatal: silently falling back to the full corpus would launch
        # a materially different training run.
        if config.include_ids_path:
            include_ids = _load_instance_ids(config.include_ids_path, missing_ok=False)
            if not include_ids:
                raise ValueError(
                    f"include_ids_path={config.include_ids_path} contains no instance IDs"
                )
            available_ids = {self._samples[i].instance_id for i in self._order}
            unknown_ids = include_ids - available_ids
            if unknown_ids:
                example = sorted(unknown_ids)[0]
                raise ValueError(
                    f"include_ids_path={config.include_ids_path} contains "
                    f"{len(unknown_ids)} ID(s) outside the {config.split} split; "
                    f"example: {example}"
                )
            before = len(self._order)
            self._order = [
                i for i in self._order if self._samples[i].instance_id in include_ids
            ]
            logger.info(
                "TMaxDataset: included %d/%d prompt(s) from %s",
                len(self._order),
                before,
                config.include_ids_path,
            )

        # Skip prompts annotated zero-std by a prior run (no learning signal). Applied
        # AFTER the shuffle as a lazy filter over the canonical (seed-fixed) order: a skip
        # run then walks the SAME prompt sequence as the wash that produced the
        # annotations, just with the dead prompts removed in place -- it inherits the
        # wash's ordering instead of getting an independent shuffle of a shorter list.
        if config.skip_ids_path:
            skip_ids = _load_instance_ids(config.skip_ids_path, missing_ok=True)
            if skip_ids:
                before = len(self._order)
                self._order = [
                    i
                    for i in self._order
                    if self._samples[i].instance_id not in skip_ids
                ]
                logger.info(
                    f"TMaxDataset: skipped {before - len(self._order)} zero-std prompt(s) "
                    f"from {config.skip_ids_path} ({len(self._order)}/{before} remain)"
                )
                if not self._order:
                    raise ValueError(
                        f"all rows filtered out by skip_ids_path={config.skip_ids_path}"
                    )
        self._pos = 0
        self._epoch = 0
        self._stream_position = 0
        self._stream_id = uuid.uuid4().hex
        self._pending_lineage_events: list[dict] = []
        # True online task evolution: when SWE_DATA_HOT_RELOAD=1 and this is the
        # train split, a republished data_path (a new hardlink or file renamed
        # over the name) is picked up mid-run - same-id rows are swapped in place
        # (indices, shuffle order and checkpoint state all stay valid), new ids
        # are appended to the tail of the current epoch. Rows are parsed by the
        # same _parse_sample_row as boot; a malformed reload file is logged and
        # IGNORED, never fatal. Validation stays pinned to the boot-time file
        # (holdout stability).
        self._hot_reload = (
            os.environ.get("SWE_DATA_HOT_RELOAD", "0") == "1"
            and config.split == "train"
        )
        self._data_path = config.data_path
        self._holdout_n = config.holdout_n
        self._split = config.split
        self._data_ino, self._data_mtime = self._source_id()
        # The mix directory this file is served from, when it is the live link of
        # one (LAYOUT.md: data/mix/live.jsonl is a hardlink to the current history
        # version). That is the only place a version number comes from; a file
        # anywhere else has none.
        live = Path(config.data_path)
        mix = layout.MixDir(live.parent)
        self._mix = mix if live.name == mix.live.name else None
        self._mix_version = self._resolve_version()
        # The boot line of trainer/mix_versions.jsonl is written on the first
        # draw, not here: every rollout worker builds this dataset too and never
        # draws from it, and the controller's is the one whose mix the run trains on.
        self._boot_recorded = False
        self._reload_lock = threading.Lock()
        self._last_reload_check = time.monotonic()
        if config.initial_skip_samples < 0:
            raise ValueError(
                "TMaxDataset.Config.initial_skip_samples must be non-negative, "
                f"got {config.initial_skip_samples}"
            )
        for _ in range(config.initial_skip_samples):
            next(self)
        if config.initial_skip_samples:
            logger.info(
                "TMaxDataset: skipped %d initial sample(s)",
                config.initial_skip_samples,
            )

    def __iter__(self) -> Iterator[TMaxSample]:
        return self

    def __next__(self) -> TMaxSample:
        if not self._boot_recorded:
            self._boot_recorded = True
            self._record_mix_version("boot")
        if self._hot_reload:
            self._maybe_reload()
        if self._pos >= len(self._order):
            if self._shuffle:
                self._rng.shuffle(self._order)
            self._pos = 0
            self._epoch += 1
        dataset_position = self._pos
        idx = self._order[self._pos]
        self._pos += 1
        lineage = SampleLineage(
            occurrence_id=f"{self._stream_id}:{self._stream_position}",
            task_id=self._samples[idx].instance_id,
            sample_revision=self._sample_revisions[idx],
            mix_revision=self._mix_revision,
            dataset_epoch=self._epoch,
            dataset_position=dataset_position,
            stream_position=self._stream_position,
            stream_id=self._stream_id,
        )
        self._stream_position += 1
        return replace(self._samples[idx], lineage=lineage)

    def drain_lineage_events(self) -> list[dict]:
        """Return hot-reload events not yet collected by the controller."""
        events = self._pending_lineage_events
        self._pending_lineage_events = []
        return events

    def _source_id(self) -> tuple[int, int]:
        """(inode, mtime_ns) of data_path, or (0, 0) when it cannot be read.

        The mix directory publishes a version by renaming a fresh hardlink over
        ``live.jsonl`` (``layout.MixDir.publish``), so the inode moves on every
        version while the name stays; the mtime covers a file rewritten in place.
        Either changing means new content."""
        try:
            st = os.stat(self._data_path)
        except OSError:
            return 0, 0
        return st.st_ino, st.st_mtime_ns

    def _resolve_version(self) -> int | None:
        """The mix version data_path currently is, by inode against the history
        directory (``MixDir.live_version``); None for a file outside a mix dir."""
        if self._mix is None:
            return None
        found = self._mix.live_version()
        return found[0] if found else None

    def _record_mix_version(
        self, event: str, *, replaced: int = 0, appended: int = 0, retired: int = 0
    ) -> None:
        """One line in the run's ``trainer/mix_versions.jsonl`` per boot and per
        hot reload: which mix version this dataset serves, by version number and
        by the file's sha256, so a step can be tied to the rows behind it. Only
        the train split of a file served from a mix directory, and only under a
        run directory: the holdout validation slice reads the same file and a
        benchmark or smoke-test file has no version to record."""
        run = layout.Run.from_env()
        if run is None or self._mix is None or self._split != "train":
            return
        try:
            layout.append_jsonl(
                run.mix_versions,
                {
                    "stamp": layout.stamp(),
                    "event": event,
                    "version": self._mix_version,
                    "sha256": layout.sha256_file(Path(self._data_path)),
                    "replaced": replaced,
                    "appended": appended,
                    "retired": retired,
                },
            )
        except OSError as e:
            logger.warning("TMaxDataset: mix_versions line not written (%s)", e)

    def _maybe_reload(self, min_interval_sec: float = 20.0) -> None:
        """Swap in a republished data file without disturbing sampler state.

        Rate-limited; the whole reload is best-effort. Same-id rows replace their
        TMaxSample in place so every index in _order (and in a restored checkpoint
        order) still points at the same task, now re-tuned. New ids append to _samples
        and join the tail of the current epoch; the next epoch shuffle mixes them in
        fully. Ids absent from the new file are dropped from _order (their sample
        stays, unreferenced, so indices never shift).

        A change is a new inode or mtime on data_path itself (see _source_id). A
        ``<stem>.v<N>.jsonl`` scan used to sit here for a remote FUSE client that
        cached same-name mtimes across hosts; the trainer and the loop now share
        one host and one filesystem, and the version is read off the mix
        directory instead of a filename."""
        now = time.monotonic()
        if now - self._last_reload_check < min_interval_sec:
            return
        self._last_reload_check = now
        ino, mtime = self._source_id()
        if (ino, mtime) == (self._data_ino, self._data_mtime):
            return
        with self._reload_lock:
            # Re-check under the lock: another thread may already have this file.
            if (ino, mtime) == (self._data_ino, self._data_mtime):
                return
            src = self._data_path
            try:
                fresh, fresh_revisions, fresh_mix_revision = _load_samples(src)
                if self._holdout_n > 0:
                    if self._holdout_n >= len(fresh):
                        raise ValueError(
                            f"holdout_n={self._holdout_n} >= reloaded size {len(fresh)}"
                        )
                    fresh = fresh[: -self._holdout_n]
                    fresh_revisions = fresh_revisions[: -self._holdout_n]
                if not fresh:
                    raise ValueError(f"reloaded mix {src} has no train rows")
            except (OSError, ValueError, json.JSONDecodeError) as e:
                logger.warning("TMaxDataset: hot reload skipped (%s)", e)
                # Mark this file seen so a broken one is not retried every interval.
                self._data_ino, self._data_mtime = ino, mtime
                return
            previous_mix_revision = self._mix_revision
            previous_live_indices = set(self._order)
            by_id = {
                sample.instance_id: (sample, revision)
                for sample, revision in zip(fresh, fresh_revisions, strict=True)
            }
            changes: list[dict[str, str | None]] = []
            replaced = 0
            for i, old in enumerate(self._samples):
                new_pair = by_id.pop(old.instance_id, None)
                if new_pair is not None:
                    new, new_revision = new_pair
                    old_revision = self._sample_revisions[i]
                    if new_revision != old_revision:
                        replaced += 1
                        changes.append(
                            {
                                "task_id": old.instance_id,
                                "change": "replaced",
                                "previous_sample_revision": old_revision,
                                "sample_revision": new_revision,
                            }
                        )
                    self._samples[i] = new
                    self._sample_revisions[i] = new_revision
            appended = 0
            for new, new_revision in by_id.values():
                self._samples.append(new)
                self._sample_revisions.append(new_revision)
                self._order.append(len(self._samples) - 1)
                appended += 1
                changes.append(
                    {
                        "task_id": new.instance_id,
                        "change": "appended",
                        "previous_sample_revision": None,
                        "sample_revision": new_revision,
                    }
                )
            live_ids = {s.instance_id for s in fresh}
            for i in previous_live_indices:
                old = self._samples[i]
                if old.instance_id not in live_ids:
                    changes.append(
                        {
                            "task_id": old.instance_id,
                            "change": "retired",
                            "previous_sample_revision": self._sample_revisions[i],
                            "sample_revision": None,
                        }
                    )
            before = len(self._order)
            self._order = [
                i for i in self._order if self._samples[i].instance_id in live_ids
            ]
            self._pos = min(self._pos, len(self._order))
            self._mix_revision = fresh_mix_revision
            self._data_ino, self._data_mtime = ino, mtime
            version = self._resolve_version()
            self._mix_version = version
            logger.info(
                "TMaxDataset: hot reload - %d replaced, %d appended, %d retired "
                "(%d in rotation) from %s version %s",
                replaced,
                appended,
                before - len(self._order),
                len(self._order),
                os.path.basename(src),
                version,
            )
            self._pending_lineage_events.append(
                {
                    "event": "hot_reload",
                    "observed_time_unix_ns": time.time_ns(),
                    "source": os.path.basename(src),
                    "source_version": version,
                    "previous_mix_revision": previous_mix_revision,
                    "mix_revision": fresh_mix_revision,
                    "dataset_epoch": self._epoch,
                    "dataset_position": self._pos,
                    "replaced": replaced,
                    "appended": appended,
                    "retired": before - len(self._order),
                    "changes": changes,
                }
            )
            self._record_mix_version(
                "hot_reload",
                replaced=replaced,
                appended=appended,
                retired=before - len(self._order),
            )

    def state_dict(self) -> dict:
        return {
            "rng_state": self._rng.getstate(),
            "order": list(self._order),
            "pos": self._pos,
            "epoch": self._epoch,
            "stream_position": self._stream_position,
            "stream_id": self._stream_id,
        }

    def load_state_dict(self, state_dict: dict) -> None:
        self._rng.setstate(state_dict["rng_state"])
        self._order = list(state_dict["order"])
        self._pos = state_dict["pos"]
        self._epoch = state_dict.get("epoch", 0)
        self._stream_position = int(state_dict.get("stream_position", self._pos))
        self._stream_id = state_dict.get("stream_id", self._stream_id)


def _load_instance_ids(path: str, *, missing_ok: bool) -> set[str]:
    """Read task ids from a run's ``signals/`` directory, a JSONL file, or a
    bare-id file.

    A directory is a run's ``signals/`` (LAYOUT.md): one ``<task>--g<group>.json``
    per training group with zero reward variance, so the task id is the name up
    to the last ``--g`` and a listing is enough, no file is opened. Ids read back
    as ``layout.safe`` wrote them, which is the id itself for every corpus in use
    (``tw_*``, ``task_*``). A FILE holds JSONL rows ``{"instance_id": ...}`` or a
    bare id per line. Optional skip sources may be missing on a first run;
    explicit include sources fail closed.
    """
    ids: set[str] = set()
    if os.path.isdir(path):
        for name in os.listdir(path):
            if not name.endswith(".json"):
                continue
            task, sep, _group = name[: -len(".json")].rpartition("--g")
            if sep and task:
                ids.add(task)
        return ids
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{"):
                    iid = (json.loads(line) or {}).get("instance_id")
                    if iid:
                        ids.add(iid)
                else:
                    ids.add(line)
    except FileNotFoundError:
        if not missing_ok:
            raise ValueError(f"instance ID source {path} not found") from None
        logger.warning(f"TMaxDataset: ID source {path} not found; filtering nothing")
    return ids


def _coerce_prompt(prompt) -> str:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        for m in prompt:
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    return content
    return ""
