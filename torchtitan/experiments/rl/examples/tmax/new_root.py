#!/usr/bin/env python3
"""Create an experiment root in the LAYOUT.md shape from a seed mix.

    new_root.py --base /scratch/.../exp-tw-sep --mix seed.jsonl \
        --sources /scratch/.../data/swe-extract /scratch/.../data/tw-extract \
        --bin /scratch/.../terminal-rl/bin --purpose "TW-only, harder arm"

The seed mix becomes ``data/mix/history/v0001--<stamp>.jsonl`` with every row
stamped ``metadata.rev = 0``, and ``live.jsonl`` a hardlink to it; each
``--sources`` directory is symlinked under ``data/sources/<name>``; ``bin`` is
symlinked; ``experiment.json`` records the rest. A root is one experiment: one
evolving mix, one loop, a sequence of runs. ``--fork-from`` copies another
root's mix history and accepted revisions, so two experiments can diverge from
one state, and records where they diverged.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402


def _rows_with_rev(mix: Path) -> list[str]:
    rows = []
    with open(mix, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            md = row.setdefault("metadata", {})
            md.setdefault("rev", 0)
            rows.append(json.dumps(row, ensure_ascii=False))
    return rows


def create(base: Path, *, mix: Path | None, sources: list[Path], bin_dir: Path | None,
           name: str | None, purpose: str, profile: str | None,
           fork_from: Path | None) -> layout.Root:
    root = layout.Root(base)
    if root.experiment_json.exists():
        raise SystemExit(f"{base} already is an experiment root")
    for d in (root.data / "sources", root.runs, root.evals, root.evolution.tasks, root.logs):
        d.mkdir(parents=True, exist_ok=True)
    seed_version: int | None = None
    seed_mix: dict | None = None
    if fork_from is not None:
        src = layout.Root(fork_from)
        shutil.copytree(src.mix.history, root.mix.history, copy_function=os.link)
        live = src.mix.live_version()
        if live is None:
            raise SystemExit(f"{fork_from} has no live mix version to fork")
        seed_version = live[0]
        os.link(live[1], root.mix.live)
        if src.evolution.tasks.exists():
            for task in src.evolution.task_dirs():
                for rev in task.revs():
                    shutil.copytree(task.rev(rev), root.evolution.task(task.task_id).rev(rev))
        if not sources:
            sources = [p for p in (src.data / "sources").iterdir()] if (src.data / "sources").exists() else []
        bin_dir = bin_dir or (src.bin if src.bin.exists() else None)
    elif mix is not None:
        seed_version, version_path = root.mix.publish(_rows_with_rev(mix))
        manifest = mix.with_name(mix.name.removesuffix(".jsonl") + ".manifest.json")
        seed_mix = {"path": str(mix.resolve()), "sha256": layout.sha256_file(mix),
                    "inputs": None}
        if manifest.exists():
            # build_mix_v2 leaves the manifest that pins the seed's own inputs
            # by sha256 beside the seed file. The seed file lives outside the
            # root and may move; the manifest is small, so the root keeps its
            # own copy beside v1 and never has to reach outside for it.
            inputs = layout.MixDir.inputs_of(version_path)
            shutil.copy2(manifest, inputs)
            seed_mix["inputs"] = str(inputs.relative_to(root.path))
    else:
        raise SystemExit("give --mix or --fork-from")
    for s in sources:
        # The loop looks up corpus aliases (e.g. tw-extract), even when their
        # targets are versioned directories with different names.
        link = root.data / "sources" / s.name
        if not link.exists():
            link.symlink_to(s.resolve())
    if bin_dir is not None and not root.bin.exists():
        root.bin.symlink_to(Path(bin_dir).resolve())
    layout.write_json_atomic(root.experiment_json, {
        "name": name or base.name,
        "created": layout.stamp(),
        "profile": profile or os.environ.get("TRL_PROFILE"),
        "purpose": purpose,
        "seed_mix_version": seed_version,
        # Where v1 came from: the seed file and, when build_mix_v2 left one
        # beside it, the manifest that pins that file's own inputs by sha256.
        "seed_mix": seed_mix,
        "forked_from": str(fork_from) if fork_from else None,
    })
    return root


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, type=Path, help="the new root (TRL_BASE)")
    ap.add_argument("--mix", type=Path, help="seed mix jsonl; becomes v0001")
    ap.add_argument("--fork-from", type=Path, help="another root whose mix history and revisions to copy")
    ap.add_argument("--sources", nargs="*", type=Path, default=[], help="corpus directories to link under data/sources/")
    ap.add_argument("--bin", type=Path, help="directory holding codex and jq")
    ap.add_argument("--name")
    ap.add_argument("--purpose", default="")
    ap.add_argument("--profile")
    a = ap.parse_args()
    root = create(a.base, mix=a.mix, sources=a.sources, bin_dir=a.bin, name=a.name,
                  purpose=a.purpose, profile=a.profile, fork_from=a.fork_from)
    live = root.mix.live_version()
    print(f"root {root.path}: mix v{live[0] if live else '?'}, "
          f"sources {[p.name for p in (root.data / 'sources').iterdir()]}, "
          f"experiment.json written")


if __name__ == "__main__":
    main()
