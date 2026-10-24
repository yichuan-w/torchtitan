#!/usr/bin/env python3
"""Prepare and validate an evolution root on the shared MAST mount."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from torchtitan.experiments.rl.examples.tmax import (
    layout,
    new_root,
    rollout_record,
)

DEFAULT_RUN_TAG = "tmax-longlongcheck431-evolve-codex-s150-v2"
_OPERATOR = os.environ.get("TMAX_OPERATOR") or getpass.getuser()
_LOCAL_ROOT = Path(os.environ.get("TMAX_LOCAL_ROOT") or Path.home() / "rl")
DEFAULT_LOCAL_MOUNT = _LOCAL_ROOT / ".torchtrain_datasets"
DEFAULT_MAST_MOUNT = Path("/mnt/torchtrain_datasets")
DEFAULT_STORAGE_PREFIX = Path("tree") / _OPERATOR / "tmax_evolution"
DEFAULT_BIN_DIR = _LOCAL_ROOT / ".cache/tmax-codex/0.155.1"
_SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class SeedProfile:
    name: str
    description: str
    default_release: Path
    release_repo: str | None
    release_revision: str | None
    release_sha256: str
    corpus_counts: tuple[tuple[str, int], ...]
    solution_formats: tuple[tuple[str, str], ...]
    row_filter: str
    default_purpose: str

    def counts(self) -> dict[str, int]:
        return dict(self.corpus_counts)

    def formats(self) -> dict[str, str]:
        return dict(self.solution_formats)


SEED_PROFILES = {
    "tmax-longlongcheck431": SeedProfile(
        name="tmax-longlongcheck431",
        description="431-row repaired TMax longlongcheck release",
        default_release=REPO_ROOT / "mast_rl/evolution_data/release",
        release_repo="andylizf/TerminalWorld-Seeds-Clean",
        release_revision="ef0a100cd93cd2ecd316adb30eb3819ec3f37201",
        release_sha256=(
            "3d0f1eedc6e68c15e67b67cd8a59c2d763de0c8efa19d4f002083ff7cc17c328"
        ),
        corpus_counts=(("tmax", 431),),
        solution_formats=(("tmax", "solution/gpt6_actions.json"),),
        row_filter="metadata.corpus == tmax",
        default_purpose="TMax-only online hardening with Qwen3.5-9B RL",
    ),
    "agentpick-cleancf743": SeedProfile(
        name="agentpick-cleancf743",
        description=(
            "743-row agentpick mix: 341 clean and 402 calibforge_clean at 071b1706"
        ),
        default_release=Path(
            os.environ.get(
                "TMAX_AGENTPICK_RELEASE",
                "/data/users/yichuan/scratch/agentpick/prepared/releases/"
                "58d825a308ec28753f1f76df6b771ff1725a6995db475b3f9b8b4ff1e3f9b703",
            )
        ),
        release_repo="Fzz1/tb-training-mix-agentpick",
        release_revision="071b170639c63cd9d5bdad5837fd57fa2e0454c6",
        release_sha256=(
            "58d825a308ec28753f1f76df6b771ff1725a6995db475b3f9b8b4ff1e3f9b703"
        ),
        corpus_counts=(
            ("agentpick-clean", 341),
            ("agentpick-calibforge-clean", 402),
        ),
        # agentpick-clean mixes TMax-style recorded actions and solve.sh tasks.
        solution_formats=(
            (
                "agentpick-clean",
                "solution/gpt6_actions.json|solution/solve.sh",
            ),
            ("agentpick-calibforge-clean", "solution/gpt6_actions.json"),
        ),
        row_filter="metadata.corpus in {agentpick-clean, agentpick-calibforge-clean}",
        default_purpose="agentpick clean+calibforge online hardening with Qwen3.5-9B RL",
    ),
    "agentpick-fast-repair1132": SeedProfile(
        name="agentpick-fast-repair1132",
        description=(
            "1,132-row agentpick mix at 7dee55fe: 340 clean_fast, 171 repair, "
            "397 calibforge_clean_fast, 224 calibforge_repair (6 binary-fixture "
            "packages skipped)"
        ),
        default_release=Path(
            os.environ.get(
                "TMAX_AGENTPICK_FAST_RELEASE",
                "/data/users/yichuan/scratch/agentpick_fast/prepared/releases/"
                "4f41f4f5c10b011e41cb1922bffef0af5c26a14b5a6912d50e5ff41acedd1518",
            )
        ),
        release_repo="Fzz1/tb-training-mix-agentpick",
        release_revision="7dee55fe37cf5d2119539a7996d04c9fd558ca09",
        release_sha256=(
            "4f41f4f5c10b011e41cb1922bffef0af5c26a14b5a6912d50e5ff41acedd1518"
        ),
        corpus_counts=(
            ("agentpick-clean-fast", 340),
            ("agentpick-repair", 171),
            ("agentpick-calibforge-clean-fast", 397),
            ("agentpick-calibforge-repair", 224),
        ),
        # Repair rows may ship no reference solution yet ("unsolved"); the
        # loop's first easier rewrite writes one (DATA_RELEASES.md).
        solution_formats=(
            ("agentpick-clean-fast", "solution/gpt6_actions.json|solution/solve.sh"),
            ("agentpick-repair", "solution/gpt6_actions.json|solution/solve.sh|unsolved"),
            ("agentpick-calibforge-clean-fast", "solution/gpt6_actions.json"),
            ("agentpick-calibforge-repair", "solution/gpt6_actions.json|solution/solve.sh|unsolved"),
        ),
        row_filter=(
            "metadata.corpus in {agentpick-clean-fast, agentpick-repair, "
            "agentpick-calibforge-clean-fast, agentpick-calibforge-repair}"
        ),
        default_purpose="agentpick fast+repair online hardening with Qwen3.5-9B RL",
    ),
    "rebench-tmax-reaudit452": SeedProfile(
        name="rebench-tmax-reaudit452",
        description=("1,769-row static-baseline mix: 1,317 SWE-ReBench and 452 TMax"),
        default_release=REPO_ROOT / "mast_rl/evolution_data/release_reaudit_452",
        release_repo=None,
        release_revision=None,
        release_sha256=(
            "14fd9af954deeee889f2be1d94d897ba10bc2e64abab008ba2052711a0393c67"
        ),
        corpus_counts=(("swe-rebench", 1317), ("tmax", 452)),
        solution_formats=(
            ("swe-rebench", "solution/solve.sh"),
            ("tmax", "solution/solve.sh"),
        ),
        row_filter="metadata.corpus in {swe-rebench, tmax}",
        default_purpose=("SWE-ReBench and TMax online hardening with Qwen3.5-9B RL"),
    ),
    "tmaxclean-rebench1748": SeedProfile(
        name="tmaxclean-rebench1748",
        description=(
            "1,748-row mix: 1,317 SWE-ReBench (Fzz1/SWE-Rebench-Tasks-Clean@3eef12fd) "
            "and 431 TMax longlongcheck (Fzz1/Tmax-Tasks-Clean@bb6cf8fd)"
        ),
        default_release=Path(
            os.environ.get(
                "TMAX_TMAXCLEAN_REBENCH_RELEASE",
                "/home/yichuan/data_audit/build_out/releases/"
                "0e4f2cb8987b361c32ff250c7b9742d6a723d4614e95de67ac3813c21c929734",
            )
        ),
        release_repo=None,
        release_revision=None,
        release_sha256=(
            "0e4f2cb8987b361c32ff250c7b9742d6a723d4614e95de67ac3813c21c929734"
        ),
        corpus_counts=(("swe-rebench", 1317), ("tmax", 431)),
        solution_formats=(
            ("swe-rebench", "solution/solve.sh"),
            ("tmax", "solution/gpt6_actions.json"),
        ),
        row_filter="metadata.corpus in {swe-rebench, tmax}",
        default_purpose=(
            "SWE-ReBench and clean TMax online hardening and simplification "
            "with Qwen3.5-9B RL"
        ),
    ),
}
DEFAULT_SEED_PROFILE = os.environ.get(
    "TMAX_EVOLVE_SEED_PROFILE", "tmax-longlongcheck431"
)


def _format_ok(actual: str | None, expected: str) -> bool:
    """A profile format may list alternatives separated by ``|``."""
    options = expected.split("|")
    return actual in options or (actual is None and "unsolved" in options)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".incoming")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _validate_relative_path(path: Path, name: str) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be a relative path without '..': {path}")


def _profile(name: str) -> SeedProfile:
    try:
        return SEED_PROFILES[name]
    except KeyError as error:
        choices = ", ".join(sorted(SEED_PROFILES))
        raise ValueError(
            f"unknown seed profile {name!r}; choose one of: {choices}"
        ) from error


def _release_manifest(release: Path, profile: SeedProfile) -> dict:
    manifest_path = release / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(
            f"release is missing {manifest_path}; fetch the pinned release first"
        )
    manifest = json.loads(manifest_path.read_text())
    release_id = manifest.get("release_sha256")
    unsigned = {
        key: value for key, value in manifest.items() if key != "release_sha256"
    }
    actual_id = hashlib.sha256(
        (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    if release_id != profile.release_sha256 or actual_id != profile.release_sha256:
        raise ValueError(
            "release identity mismatch for "
            f"{profile.name}: expected {profile.release_sha256}, got {release_id}"
        )
    expected_files = set(manifest["files"]) | {"manifest.json"}
    actual_files = {
        str(path.relative_to(release)) for path in release.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("release file membership differs from its manifest")
    for relative, expected in manifest["files"].items():
        if _digest(release / relative) != expected:
            raise ValueError(f"release file digest mismatch: {relative}")
    return manifest


def _source_paths(release: Path, profile: SeedProfile) -> dict[str, Path]:
    sources = {corpus: release / "sources" / corpus for corpus in profile.counts()}
    missing = [str(path) for path in sources.values() if not path.is_dir()]
    if missing:
        raise ValueError(f"release is missing source directories: {missing}")
    return sources


def _seed_rows(release: Path, profile: SeedProfile) -> list[dict]:
    import evolve

    rows = [
        json.loads(line)
        for line in (release / "mix.jsonl").read_text().splitlines()
        if line.strip()
    ]
    expected_counts = profile.counts()
    result = [
        row for row in rows if row.get("metadata", {}).get("corpus") in expected_counts
    ]
    actual_counts = Counter(row.get("metadata", {}).get("corpus") for row in result)
    if actual_counts != Counter(expected_counts):
        raise ValueError(
            f"seed corpus counts differ for {profile.name}: "
            f"expected {expected_counts}, got {dict(actual_counts)}"
        )
    ids = [row.get("metadata", {}).get("instance_id") for row in result]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("seed instance IDs are empty or duplicated")

    sources = _source_paths(release, profile)
    expected_formats = profile.formats()
    invalid_packages = []
    for corpus, expected_count in expected_counts.items():
        corpus_rows = [
            row for row in result if row.get("metadata", {}).get("corpus") == corpus
        ]
        corpus_ids = {row.get("metadata", {}).get("instance_id") for row in corpus_rows}
        source_ids = {
            path.name for path in (sources[corpus] / "tasks").iterdir() if path.is_dir()
        }
        if len(corpus_ids) != expected_count or corpus_ids != source_ids:
            raise ValueError(
                f"{corpus} mix rows and source packages have different membership"
            )
        for task_id in sorted(corpus_ids):
            package = sources[corpus] / "tasks" / task_id
            try:
                task = evolve.load(package)
                actual_format = task.get("_solution_rel")
                if not _format_ok(actual_format, expected_formats[corpus]):
                    raise ValueError(
                        f"expected {expected_formats[corpus]}, got {actual_format}"
                    )
            except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
                invalid_packages.append(f"{corpus}/{task_id}: {error}")
    if invalid_packages:
        sample = "; ".join(invalid_packages[:3])
        raise ValueError(
            f"{len(invalid_packages)} seed packages are invalid (for example: {sample})"
        )
    return result


def _create_root(
    base: Path,
    *,
    mix: Path,
    sources: dict[str, Path],
    name: str,
    purpose: str,
    profile: SeedProfile,
    release_manifest: dict,
    bin_dir: Path,
) -> None:
    root = new_root.create(
        base,
        mix=mix,
        sources=[sources[corpus] for corpus in profile.counts()],
        bin_dir=bin_dir,
        name=name,
        purpose=purpose,
        profile=os.environ.get("TRL_PROFILE") or f"{_OPERATOR}-mast",
        fork_from=None,
    )
    experiment = json.loads(root.experiment_json.read_text())
    experiment["seed_mix"]["path"] = os.path.relpath(mix, root.path)
    experiment["data_release"] = {
        "profile": profile.name,
        "description": profile.description,
        "repo": profile.release_repo,
        "revision": profile.release_revision,
        "release_sha256": release_manifest["release_sha256"],
        "filter": profile.row_filter,
        "rows": sum(profile.counts().values()),
        "corpus_counts": profile.counts(),
        "source_config": release_manifest.get("config", {}).get("sources", []),
    }
    experiment["seed_solution_formats"] = profile.formats()
    unique_formats = set(profile.formats().values())
    if len(unique_formats) == 1:
        experiment["seed_solution_format"] = unique_formats.pop()
    experiment["source_visibility"] = (
        "The source symlinks are local to the devvm evolution sidecar. "
        "The MAST trainer reads only data/mix/live.jsonl."
    )
    layout.write_json_atomic(root.experiment_json, experiment)


def _create_canary_signals(root_path: Path, rows: list[dict]) -> dict[str, str]:
    import evolve

    root = layout.Root(root_path)
    candidates: dict[str, list[tuple[int, str, dict]]] = {}
    for row in rows:
        corpus = row["metadata"]["corpus"]
        task_id = row["metadata"]["instance_id"]
        package = root.data / "sources" / corpus / "tasks" / task_id
        evolve.load(package)
        size = sum(path.stat().st_size for path in package.rglob("*") if path.is_file())
        candidates.setdefault(corpus, []).append((size, task_id, row))
    if not candidates:
        raise ValueError("no seed can be loaded by the evolution pipeline")
    run_name = "prelaunch-canary"
    run = root.run(run_name)
    signals = {}
    for group, corpus in enumerate(sorted(candidates)):
        _size, _task_id, row = min(candidates[corpus])
        task = row["metadata"]["instance_id"]
        attempts: list[str] = []
        for index in range(2):
            path = run.rollout_record(task, group, index)
            rollout_record.write_record(
                path,
                {
                    "task": task,
                    "rev": 0,
                    "run": run_name,
                    "group": group,
                    "rollout": index,
                    "reward": 1.0,
                    "turns": 1,
                    "canary": True,
                    "corpus": corpus,
                },
                [
                    {
                        "turn": 1,
                        "keystrokes": ["ls -la\n"],
                        "output": "Synthetic prelaunch trace; no training data was used.",
                    }
                ],
            )
            attempts.append(str(path.relative_to(run.path)))
        layout.write_json_atomic(
            run.signal(task, group),
            {
                "task": task,
                "rev": 0,
                "run": run_name,
                "group": group,
                "direction": "harder",
                "solved": 2,
                "total": 2,
                "created": layout.stamp(),
                "attempts": attempts,
                "corpus": corpus,
            },
        )
        signals[corpus] = layout.signal_id(run_name, task, group)
    return signals


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    relative = args.storage_prefix / args.run_tag
    return {
        "experiment": args.local_mount / relative,
        "root": args.local_mount / relative / "root",
        "canary_root": args.local_mount / relative / "canary-root",
        "mast_root": args.mast_mount / relative / "root",
    }


def _check_root(
    root_path: Path,
    rows: list[dict],
    expected_sources: dict[str, Path],
    expected_bin: Path,
    profile: SeedProfile,
) -> dict:
    import evolve

    root = layout.Root(root_path)
    live = root.mix.live_version()
    if live is None:
        raise ValueError(f"{root_path} has no valid live mix version")
    live_rows = [
        json.loads(line)
        for line in root.mix.live.read_text().splitlines()
        if line.strip()
    ]
    if len(live_rows) != len(rows):
        raise ValueError(f"{root_path} has {len(live_rows)} rows, expected {len(rows)}")
    actual_counts = Counter(row.get("metadata", {}).get("corpus") for row in live_rows)
    if actual_counts != Counter(profile.counts()):
        raise ValueError(
            f"{root_path} corpus counts differ: expected {profile.counts()}, "
            f"got {dict(actual_counts)}"
        )
    source_links = {}
    for corpus, expected_source in expected_sources.items():
        source = root.data / "sources" / corpus
        if not source.is_symlink() or not source.resolve().is_dir():
            raise ValueError(f"{source} is not a usable source link on this devvm")
        if source.resolve() != expected_source.resolve():
            raise ValueError(
                f"{source} resolves to {source.resolve()}, "
                f"expected {expected_source.resolve()}"
            )
        source_links[corpus] = str(source.readlink())
    if not root.bin.is_symlink() or root.bin.resolve() != expected_bin.resolve():
        raise ValueError(
            f"{root.bin} does not resolve to the expected tool directory {expected_bin}"
        )
    for tool in ("codex", "jq"):
        path = root.bin / tool
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(
                f"required evolution tool is missing or not executable: {path}"
            )
    seed_ids = {row["metadata"]["instance_id"] for row in rows}
    live_ids = {row["metadata"]["instance_id"] for row in live_rows}
    if live_ids != seed_ids:
        raise ValueError(f"{root_path} task membership differs from the seed release")
    expected_formats = profile.formats()
    # The links were verified above; load through their local targets, since a
    # lookup through a root on FUSE storage costs a round trip per file.
    resolved = {corpus: source.resolve() for corpus, source in expected_sources.items()}
    for row in live_rows:
        metadata = row["metadata"]
        corpus = metadata["corpus"]
        task_id = metadata["instance_id"]
        task = evolve.load(resolved[corpus] / "tasks" / task_id)
        if not _format_ok(task.get("_solution_rel"), expected_formats[corpus]):
            raise ValueError(
                f"unexpected solution format for {corpus}/{task_id}: "
                f"{task.get('_solution_rel')}"
            )
    result = {
        "path": str(root_path),
        "mix_version": live[0],
        "mix_sha256": _digest(root.mix.live),
        "rows": len(live_rows),
        "corpus_counts": dict(actual_counts),
        "source_links": source_links,
    }
    if len(source_links) == 1:
        result["source_link"] = next(iter(source_links.values()))
    return result


def prepare(args: argparse.Namespace) -> dict:
    if not args.local_mount.is_mount():
        raise ValueError(f"shared storage is not mounted at {args.local_mount}")
    profile = _profile(args.seed_profile)
    release = args.release or profile.default_release
    manifest = _release_manifest(release, profile)
    rows = _seed_rows(release, profile)
    sources = _source_paths(release, profile)
    for tool in ("codex", "jq"):
        path = args.bin_dir / tool
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(
                f"required evolution tool is missing or not executable: {path}"
            )
    paths = _paths(args)
    destination = paths["experiment"]
    if destination.exists():
        raise ValueError(f"experiment path already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(
        f".{destination.name}.incoming-{uuid.uuid4().hex[:8]}"
    )
    try:
        seed = staging / "seed"
        mix = seed / "mix.jsonl"
        mix.parent.mkdir(parents=True, exist_ok=True)
        mix.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
        _write_json(
            seed / "mix.manifest.json",
            {
                "profile": profile.name,
                "description": profile.description,
                "repo": profile.release_repo,
                "revision": profile.release_revision,
                "release_sha256": manifest["release_sha256"],
                "filter": profile.row_filter,
                "rows": len(rows),
                "corpus_counts": profile.counts(),
                "sha256": _digest(mix),
                "sources": {
                    corpus: {
                        "path": str(source),
                        "solution_format": profile.formats()[corpus],
                        "transformation": "none",
                    }
                    for corpus, source in sources.items()
                },
            },
        )
        _create_root(
            staging / "root",
            mix=mix,
            sources=sources,
            name=args.run_tag,
            purpose=args.purpose or profile.default_purpose,
            profile=profile,
            release_manifest=manifest,
            bin_dir=args.bin_dir,
        )
        _create_root(
            staging / "canary-root",
            mix=mix,
            sources=sources,
            name=f"{args.run_tag}-canary",
            purpose=f"Dry prelaunch canary for {args.run_tag}",
            profile=profile,
            release_manifest=manifest,
            bin_dir=args.bin_dir,
        )
        canary_signals = _create_canary_signals(staging / "canary-root", rows)
        canary_corpus = "tmax" if "tmax" in canary_signals else min(canary_signals)
        canary_signal = canary_signals[canary_corpus]
        _write_json(
            staging / "launch_paths.json",
            {
                "run_tag": args.run_tag,
                "seed_profile": profile.name,
                "seed_mix": "seed/mix.jsonl",
                "local_root": str(paths["root"]),
                "mast_root": str(paths["mast_root"]),
                "canary_root": str(paths["canary_root"]),
                "canary_signal": canary_signal,
                "canary_corpus": canary_corpus,
                "canary_signals": canary_signals,
            },
        )
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    result = _check_root(paths["root"], rows, sources, args.bin_dir, profile)
    result["canary"] = _check_root(
        paths["canary_root"], rows, sources, args.bin_dir, profile
    )
    result["seed_profile"] = profile.name
    result["mast_root"] = str(paths["mast_root"])
    result["canary_signal"] = canary_signal
    result["canary_corpus"] = canary_corpus
    result["canary_signals"] = canary_signals
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("plan", "prepare", "check"), nargs="?", default="plan"
    )
    parser.add_argument("--run-tag", default=DEFAULT_RUN_TAG)
    parser.add_argument(
        "--seed-profile",
        choices=sorted(SEED_PROFILES),
        default=DEFAULT_SEED_PROFILE,
    )
    parser.add_argument("--release", type=Path)
    parser.add_argument("--local-mount", type=Path, default=DEFAULT_LOCAL_MOUNT)
    parser.add_argument("--mast-mount", type=Path, default=DEFAULT_MAST_MOUNT)
    parser.add_argument("--storage-prefix", type=Path, default=DEFAULT_STORAGE_PREFIX)
    parser.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    parser.add_argument(
        "--purpose",
    )
    args = parser.parse_args()
    if not _SAFE_TAG.fullmatch(args.run_tag):
        parser.error("--run-tag must be one safe path component")
    _validate_relative_path(args.storage_prefix, "--storage-prefix")
    profile = _profile(args.seed_profile)
    release = args.release or profile.default_release
    paths = _paths(args)
    if args.action == "prepare":
        result = prepare(args)
    else:
        manifest = _release_manifest(release, profile)
        rows = _seed_rows(release, profile)
        sources = _source_paths(release, profile)
        if args.action == "check":
            result = _check_root(paths["root"], rows, sources, args.bin_dir, profile)
            result["canary"] = _check_root(
                paths["canary_root"], rows, sources, args.bin_dir, profile
            )
            result["seed_profile"] = profile.name
            result["mast_root"] = str(paths["mast_root"])
        else:
            result = {
                "action": "plan",
                "run_tag": args.run_tag,
                "seed_profile": profile.name,
                "seed_description": profile.description,
                "release_repo": profile.release_repo,
                "release_revision": profile.release_revision,
                "release_sha256": manifest["release_sha256"],
                "release": str(release),
                "rows": len(rows),
                "corpus_counts": profile.counts(),
                "local_root": str(paths["root"]),
                "mast_root": str(paths["mast_root"]),
                "canary_root": str(paths["canary_root"]),
                "sources": {corpus: str(path) for corpus, path in sources.items()},
                "sources_exist": all(path.exists() for path in sources.values()),
                "bin_dir": str(args.bin_dir),
                "bin_ready": all(
                    (args.bin_dir / tool).is_file()
                    and os.access(args.bin_dir / tool, os.X_OK)
                    for tool in ("codex", "jq")
                ),
                "destination_exists": paths["experiment"].exists(),
            }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
