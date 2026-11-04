#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Plot one overfit-bench job: the training task's solve rate per step, the
original task's readout, and every accepted rewrite.

    plot_curve.py <job dir> [--out curve.png] [--smooth 5] [--since <run name>]

The two views agreed in #321 are HTML pages (plot_pages.py):

    plot_curve.py --timeline <job dir> [<job dir> ...] --out timeline.html [--lang en]
    plot_curve.py --timeline <jobs root> --results tb21_results.md --out timeline.html
    plot_curve.py --heatmap <jobs root> --results tb21_results.md --out heatmap.html

Training points come from each run's training_lineage (a group's finalized
solve count, placed at the step that trained it); the readout from the
validation traces (bench.validations); rewrites from
evolution/tasks/<task>/rewrites/*/rewrite.json, drawn at the last step trained
before the rewrite finished (it reaches training from the next draw on).
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

layout = bench.layout

TRAIN, READOUT, HARDER, EASIER = "#2a78d6", "#eb6834", "#4a3aa7", "#1baf7a"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _ts(value: str) -> float:
    if value.endswith("Z") and "-" in value[:9] and "T" not in value:
        return datetime.datetime.strptime(value, "%Y%m%d-%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc
        ).timestamp()
    return datetime.datetime.fromisoformat(value).timestamp()


def _runs(job: Path, since: str) -> list:
    return [r for r in layout.Root(job).run_dirs() if r.path.name >= since]


def readouts(job: Path, task: str, since: str) -> list[tuple[int, tuple[int, int]]]:
    """[(step, (attempts, passes))] from the selected runs' validation traces."""
    found = {}
    for run in _runs(job, since):
        for index in sorted((run.path / "trainer" / "validation_traces").glob("step-*/INDEX.md")):
            n = c = 0
            for line in index.read_text(encoding="utf-8").splitlines():
                m = bench._ROW.match(line)
                if m and m.group(1) == task:
                    n += 1
                    c += m.group(2) == "PASS"
            if n:
                found[int(index.parent.name.removeprefix("step-"))] = (n, c)
    return sorted(found.items())


def training_points(job: Path, since: str) -> tuple[list[tuple[int, int, int]], list[tuple[float, int]]]:
    """[(step, solved, scored)] per trained group, and [(time, step)] of packing."""
    points, packed_at = [], []
    for run in _runs(job, since):
        events = run.path / "trainer" / "training_lineage" / "events.jsonl"
        if not events.is_file():
            continue
        step_of, solved_of = {}, {}
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            key = (e.get("session_id"), e.get("group_id"))
            if e.get("event") == "packed":
                step_of[key] = e["reserved_train_step"]
                packed_at.append((_ts(e["timestamp"]), e["reserved_train_step"]))
            elif e.get("event") == "finalized" and e.get("group_id", -1) >= 0:
                solved_of[key] = (e["num_solved"], e["num_scored_rollouts"])
        for key, step in step_of.items():
            if key in solved_of and solved_of[key][1]:
                points.append((step, *solved_of[key]))
    return sorted(points), sorted(packed_at)


def rewrites(job: Path, task: str) -> list[dict]:
    out = []
    root = layout.Root(job).evolution.path / "tasks" / layout.safe(task) / "rewrites"
    for meta_path in sorted(root.glob("*/rewrite.json")) if root.is_dir() else []:
        meta = json.loads(meta_path.read_text())
        if meta.get("finished"):
            out.append(meta)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("job", type=Path, nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--smooth", type=int, default=5)
    ap.add_argument("--since", default="", help="first run directory name to plot")
    view = ap.add_mutually_exclusive_group()
    view.add_argument("--timeline", action="store_true", help="HTML lane timeline of each job given")
    view.add_argument("--heatmap", action="store_true", help="HTML heatmap of every task; job is the jobs root")
    ap.add_argument("--results", type=Path, help="tb21_results.md: needed by --heatmap, and by --timeline on a jobs root")
    ap.add_argument("--lang", choices=("cn", "en"), default="cn", help="language of the HTML pages")
    ap.add_argument("--wrap", type=int, default=0,
                    help="--timeline: rows of this many steps (for printing), 0 for one row that scrolls")
    a = ap.parse_args()
    if a.timeline or a.heatmap:
        import plot_pages

        if a.heatmap:
            if not a.results:
                ap.error("--heatmap needs --results")
            page = plot_pages.heatmap_page(a.job[0].resolve(), a.results, lang=a.lang)
        else:
            page = plot_pages.timeline_page([j.resolve() for j in a.job], a.lang, a.results, a.wrap)
        out = a.out or Path("heatmap.html" if a.heatmap else "timeline.html")
        out.write_text(page, encoding="utf-8")
        print(f"wrote {out}")
        return
    if len(a.job) != 1:
        ap.error("the curve takes one job directory")
    job = a.job[0].resolve()
    task = json.loads((job / bench.JOB_JSON).read_text())["task"]

    points, packed_at = training_points(job, a.since)
    readout = readouts(job, task, a.since)
    all_rewrites = rewrites(job, task)
    accepted = [r for r in all_rewrites if r.get("status") == "accepted"]

    def step_at(stamp: str) -> int:
        t = _ts(stamp)
        steps = [s for when, s in packed_at if when <= t]
        return max(steps) if steps else 0

    print(f"{task}: {len(points)} trained groups, {len(readout)} readouts, "
          f"{len(accepted)}/{len(all_rewrites)} rewrites accepted")
    for s, (n, c) in readout:
        print(f"  readout step {s}: {c}/{n}")
    for r in all_rewrites:
        print(f"  rewrite {r['job']:6s} {r['status']:9s} finished {r['finished']} "
              f"-> step {step_at(r['finished'])} rev {r.get('result_rev')}")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    if points:
        xs = [s for s, _, _ in points]
        ys = [100.0 * c / n for _, c, n in points]
        ax.scatter(xs, ys, s=10, color=MUTED, alpha=0.5, linewidths=0, zorder=2)
        k = max(1, a.smooth)
        avg = [sum(ys[max(0, i - k + 1): i + 1]) / len(ys[max(0, i - k + 1): i + 1])
               for i in range(len(ys))]
        ax.plot(xs, avg, color=TRAIN, linewidth=2, zorder=3,
                label=f"training task (current version): {k}-step mean")
    if readout:
        ax.plot([s for s, _ in readout], [100.0 * c / n for _, (n, c) in readout],
                color=READOUT, linewidth=2, marker="o", markersize=6, zorder=4,
                markeredgecolor="#fcfcfb", markeredgewidth=1.5,
                label=f"readout: original task, {readout[0][1][0]} attempts")
    for i, r in enumerate(accepted):
        harder = r["job"] == "harder"
        x = step_at(r["finished"])
        color = HARDER if harder else EASIER
        ax.axvline(x, color=color, linewidth=1, alpha=0.6, zorder=1)
        ax.annotate(f"{'▼' if harder else '▲'} r{r.get('result_rev')}", (x, 104 + 5 * (i % 3)),
                    color=INK, fontsize=8, ha="left", annotation_clip=False)
    ax.set_ylim(-3, 103)
    ax.set_xlim(left=0)
    ax.set_xlabel("train step", color=MUTED)
    ax.set_ylabel("solved (%)", color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED)
    ax.plot([], [], color=HARDER, linewidth=1, label="▼ rewrite: harder")
    ax.plot([], [], color=EASIER, linewidth=1, label="▲ rewrite: easier")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, frameon=False)
    ax.set_title(f"overfit bench: {task}", color=INK, loc="left", pad=28)
    fig.tight_layout()
    out = a.out or job / "curve.png"
    fig.savefig(out, facecolor=fig.get_facecolor())
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
