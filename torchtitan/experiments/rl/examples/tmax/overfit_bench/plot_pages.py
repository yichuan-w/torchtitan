# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""HTML pages (inline SVG, Chinese) for the overfit bench, the two views agreed in #321.

    plot_curve.py --timeline <job dir> [<job dir> ...] --out timeline.html
    plot_curve.py --heatmap <jobs root> --results tb21_results.md --out heatmap.html

Timeline, one job per figure: the original on its own lane and one lane per version; a readout row on top. A
group that had both passes and failures (it produced a gradient) is a filled dot with its count; groups with no
gradient at the same step fold into one hollow marker per kind, a circle for all-fail and a square for all-pass,
written as "0/12×N" / "12/12×N". Readouts are diamonds with their exact count from the validation traces. A new
version is a thick dashed line from its parent's lane at the step it entered the pool.

Heatmap, one row per task: the original's passes over attempts (readouts and training groups pooled) per 10-step
cell, the count written in the cell. Marks tell the two bars apart: ◆ two-stage solved (13/64 or more on the
64-attempt confirm), ★ lenient bar reached through training, ☆ lenient bar already met at step 0.
"""

from __future__ import annotations

import html
import json
import re
import sys
from bisect import bisect_right
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

layout = bench.layout

STYLE = """<style>
:root{--bg:#fcfcfb;--fg:#0b0b0b;--muted:#52514e;--line:#e4e3df;--orig:#eb6834;--ver:#2a78d6;--ro:#1baf7a;
  --born:#6250d6;--h0:#eef4fc;--h1:#9cc3ef;--h2:#2a78d6;--h3:#0d3b75}
@media (prefers-color-scheme: dark){:root{--bg:#1a1a19;--fg:#f0efec;--muted:#c3c2b7;--line:#33322f;--orig:#d95926;
  --ver:#3987e5;--ro:#199e70;--born:#9085e9;--h0:#20262e;--h1:#2a4a72;--h2:#3987e5;--h3:#a9cdf5}}
body{background:var(--bg);color:var(--fg);font-family:"Noto Sans SC","Noto Sans CJK SC",system-ui,sans-serif;
  margin:0;padding:20px 16px 48px}
.wrap{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:28px}
h1{font-size:24px;margin:0} h2{font-size:18px;margin:0} p{margin:0;line-height:1.7}
figure{margin:0;display:flex;flex-direction:column;gap:6px} .figscroll{overflow-x:auto}
figure svg{display:block;width:100%;min-width:680px;height:auto}
figcaption{font-size:13.5px;color:var(--muted);line-height:1.6}
svg text{fill:var(--fg);font-size:12px} svg .small{font-size:11px;fill:var(--muted)}
svg .lane{fill:var(--muted)} svg .lane.orig{fill:var(--orig);font-weight:600}
svg .grid{stroke:var(--line)} svg .lanebg{stroke:var(--line);stroke-width:1.2}
svg .mix{fill:var(--ver);stroke:var(--bg)} svg .mix.orig{fill:var(--orig)}
svg .flat{fill:none;stroke:var(--ver);stroke-width:1.3} svg .flat.orig{stroke:var(--orig)}
svg .cnt{font-size:10.5px}
svg .ro{fill:none;stroke:var(--ro);stroke-width:1.6} svg .ro.hit{fill:var(--ro)}
svg .born{fill:none;stroke:var(--born);stroke-width:2.6;stroke-dasharray:6 4}
svg .cell text{font-size:9.5px} svg .cell.dark text{fill:#fcfcfb}
svg .mark{font-size:13px}
</style>"""


def _page(title: str, intro: str, body: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title></head><body>{STYLE}<div class=\"wrap\">"
        f"<h1>{html.escape(title)}</h1><p>{intro}</p>{body}</div></body></html>"
    )


def _events(job: Path) -> tuple[list[tuple[int, int, int, int]], list[tuple[str, int]]]:
    """[(step, rev, passed, scored)] per finished group and [(stamp, step)] per step, from the scheduler logs."""
    groups, steps = [], []
    for run in layout.Root(job).run_dirs():
        path = run.scheduler / "events.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"group_done"' not in line and '"step_done"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e["event"] == "group_done" and e.get("scored"):
                groups.append((int(e.get("policy_version") or 0), int(e["rev"]), e["passed"], e["scored"]))
            elif e["event"] == "step_done":
                steps.append((e["stamp"], int(e["policy_version"])))
    return sorted(groups), sorted(steps)


def _versions(job: Path, steps: list[tuple[str, int]]) -> list[dict]:
    """Each version with the step it entered the pool (the last step finished before it was added) and its parent."""
    path = layout.Root(job).mix.pool_state
    try:
        pool = json.loads(path.read_text())["versions"]
    except (OSError, ValueError, KeyError):
        return []
    stamps = [s for s, _ in steps]
    rev_of = {v.get("id"): v.get("rev") for v in pool}
    out = []
    for v in pool:
        if not v.get("rev"):
            continue
        i = bisect_right(stamps, v.get("added_at", ""))
        parent = v.get("parent")
        parent = rev_of.get(parent, parent) if parent is not None else 0
        out.append({"rev": v["rev"], "parent": parent if isinstance(parent, int) else 0,
                    "step": steps[i - 1][1] if i else 0})
    return out


def _flat_label(kind: str, cell: list[tuple[int, int]]) -> str:
    """"0/12×N" or "12/12×N", with the commonest group size; the hover title lists every size."""
    sizes = [n for _, n in cell]
    n = max(set(sizes), key=sizes.count)
    return f"{0 if kind == 'fail' else n}/{n}×{len(cell)}"


def timeline_svg(job: Path) -> tuple[str, str]:
    task = json.loads((job / bench.JOB_JSON).read_text())["task"]
    groups, steps = _events(job)
    readouts = sorted(bench.validations(job).items())
    versions = _versions(job, steps)
    lanes = [0] + sorted({v["rev"] for v in versions} | {g[1] for g in groups} - {0})
    cells: dict[tuple[int, int], dict[str, list]] = {}
    for st, rev, p, n in groups:
        kind = "fail" if p == 0 else "pass" if p == n else "mix"
        cells.setdefault((st, rev), {"mix": [], "fail": [], "pass": []})[kind].append((p, n))
    height = {k: len(c["mix"]) + bool(c["fail"]) + bool(c["pass"]) for k, c in cells.items()}
    xmax = max([g[0] for g in groups] + [s for s, _ in readouts] + [v["step"] for v in versions] + [5])
    left, right, top = 92, 70, 56
    pps = 56.0  # room for a "0/12×3" label before the next step; long jobs scroll sideways
    width = int(xmax * pps) + left + right
    lane_h = {r: max(30, 12 * max([c for (s, rr), c in height.items() if rr == r] + [1]) + 12) for r in lanes}
    y_of, y = {}, top
    for r in lanes:
        y_of[r] = y + lane_h[r] / 2
        y += lane_h[r]
    total_h = y + 40

    def sx(s: float) -> float:
        return left + (width - left - right) * s / xmax

    o = [f'<svg viewBox="0 0 {width} {total_h}" style="width:{width}px;min-width:{width}px" role="img" '
         f'aria-label="{html.escape(task)} 的训练时间线">']
    tick = 5 if xmax <= 60 else 10 if xmax <= 150 else 20
    for s in range(0, xmax + 1, tick):
        o.append(f'<line class="grid" x1="{sx(s):.1f}" x2="{sx(s):.1f}" y1="{top - 30}" y2="{total_h - 34}"/>'
                 f'<text class="small" x="{sx(s):.1f}" y="{total_h - 16}" text-anchor="middle">{s}</text>')
    o.append(f'<text class="small" x="{width - right}" y="{total_h - 2}" text-anchor="end">训练步</text>')
    yr = top - 22
    o.append(f'<text class="lane" x="{left - 10}" y="{yr + 4}" text-anchor="end">readout</text>')
    for s, (n, c) in readouts:
        x = sx(s)
        o.append(f'<path class="ro{" hit" if c * 16 >= 3 * n else ""}" d="M{x:.1f},{yr - 7} l7,7 l-7,7 l-7,-7z">'
                 f'<title>step {s}: readout {c}/{n}</title></path>'
                 f'<text class="cnt" x="{x + 9:.1f}" y="{yr + 4}">{c}/{n}</text>')
    for r in lanes:
        o.append(f'<line class="lanebg" x1="{left}" x2="{width - right}" y1="{y_of[r]}" y2="{y_of[r]}"/>'
                 f'<text class="lane{" orig" if r == 0 else ""}" x="{left - 10}" y="{y_of[r] + 4}" '
                 f'text-anchor="end">{"原题" if r == 0 else f"r{r}"}</text>')
    for v in versions:
        if v["rev"] not in y_of:
            continue
        x, y0, y1 = sx(v["step"]), y_of.get(v["parent"], y_of[0]), y_of[v["rev"]]
        parent = "原题" if v["parent"] == 0 else f"r{v['parent']}"
        o.append(f'<path class="born" d="M{x:.1f},{y0:.1f} L{x:.1f},{y1:.1f} L{x + 10:.1f},{y1:.1f}">'
                 f'<title>r{v["rev"]} 在 step {v["step"]} 进池（父版本 {parent}）</title></path>')
    for (st, r), cell in sorted(cells.items()):
        if r not in y_of:
            continue
        who = "原题" if r == 0 else f"r{r}"
        orig = " orig" if r == 0 else ""
        items = [("mix", [g]) for g in cell["mix"]] + [(k, cell[k]) for k in ("fail", "pass") if cell[k]]
        for k, (kind, gs) in enumerate(items):
            x, y = sx(st), y_of[r] + (k - (len(items) - 1) / 2) * 12
            if kind == "mix":
                p, n = gs[0]
                o.append(f'<circle class="mix{orig}" cx="{x:.1f}" cy="{y:.1f}" r="4.2">'
                         f'<title>step {st} · {who} · {p}/{n}</title></circle>')
                label = f"{p}/{n}" if r == 0 or 2 * p >= n else ""
            else:
                text = _flat_label(kind, gs)
                sizes = "、".join(f"{p}/{n}" for p, n in sorted(set(gs)))
                shape = (f'<circle class="flat{orig}" cx="{x:.1f}" cy="{y:.1f}" r="3.6">' if kind == "fail" else
                         f'<rect class="flat{orig}" x="{x - 3.4:.1f}" y="{y - 3.4:.1f}" width="6.8" height="6.8">')
                o.append(f'{shape}<title>step {st} · {who} · {len(gs)} 组没有梯度（{sizes}）</title></{"circle" if kind == "fail" else "rect"}>')
                label = text
            if label:
                o.append(f'<text class="cnt" x="{x + 6:.1f}" y="{y + 3.5:.1f}">{label}</text>')
    o.append("</svg>")
    best = max(readouts, key=lambda t: t[1][1] / t[1][0], default=(0, (16, 0)))[1]
    orig = [(p, n) for _, r, p, n in groups if r == 0]
    best_g = max(orig, key=lambda t: t[0] / t[1], default=(0, 12))
    caption = (f"{task}：最好的一次 readout {best[1]}/{best[0]}，原题训练组最好 {best_g[0]}/{best_g[1]}。"
               "原题横道上实心点多起来、顶上的菱形填满（≥3/16）是学会原题的样子；"
               "版本横道上实心点很多而原题横道全是空心，是版本上学到的没有迁移到原题。")
    return task, (f'<figure><h2>{html.escape(task)}</h2><div class="figscroll">{"".join(o)}</div>'
                  f'<figcaption>{html.escape(caption)}</figcaption></figure>')


def timeline_page(jobs: list[Path]) -> str:
    intro = ("每个版本一条横道，原题在最上面单独一条。实心点 = 这一组有对有错、产生了梯度，旁边写通过数/组大小；"
             "同一步没有梯度的组合成一个空心标记，圆圈是全错、方块是全对，旁边写 0/12×N 或 12/12×N。"
             "顶上的菱形是 readout，写确切的通过数/尝试数，≥3/16 时填满。紫色粗虚线 = teacher 在这一步写出新版本，"
             "从父版本的横道连过来。版本横道上有梯度的组只标过半的。图比屏幕宽时可以左右滑动。")
    return _page("Overfit bench：训练时间线", intro, "".join(timeline_svg(j)[1] for j in jobs))


_TRACK = re.compile(r"^- \[([ xX])\] ([\w.-]+) \((?:base )?(\d+)/(\d+)\) - (.*)")
_CONFIRM = re.compile(r"step (\d+): \**(\d+)/64")


def parse_results(path: Path) -> dict[str, dict]:
    """Per task from tb21_results.md: ticked on the lenient bar and at which step, untrained (at the bar at step 0),
    base rate, and the step of a two-stage solve (a 64-attempt confirm of 13/64 or more)."""
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TRACK.match(line)
        if m:
            note = m.group(5)
            step = re.search(r"step ~?(\d+)", note)
            rows[m.group(2)] = {"lenient": m.group(1) != " ", "base": (int(m.group(3)), int(m.group(4))),
                                "step": int(step.group(1)) if step and m.group(1) != " " else None,
                                "untrained": "untrained" in note, "two_stage": None}
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = [c.strip() for c in line.split("|")]
        if len(cols) > 5 and cols[1].split(" ")[0] in rows:
            for s, k in _CONFIRM.findall(cols[4]):
                if int(k) >= 13:
                    rows[cols[1].split(" ")[0]]["two_stage"] = int(s)
    return rows


def _latest_job(jobs_root: Path, task: str) -> Path | None:
    """The task's training job directory with the most recent run (the run names carry their start time);
    64-attempt confirm jobs (``--confirm-*``) only evaluate, so they are skipped."""
    best, best_run = None, ""
    for d in jobs_root.glob(f"{task}--*"):
        if "--confirm" in d.name or not (d / bench.JOB_JSON).is_file():
            continue
        names = [r.path.name.split("--attempt")[0] for r in layout.Root(d).run_dirs()]
        if names and max(names) > best_run:
            best, best_run = d, max(names)
    return best


def heatmap_page(jobs_root: Path, results: Path, width: int = 10, cells: int = 20) -> str:
    rows = parse_results(results)
    order = sorted(rows, key=lambda t: (
        0 if rows[t]["two_stage"] is not None else 1 if rows[t]["lenient"] and not rows[t]["untrained"]
        else 2 if rows[t]["lenient"] else 3, rows[t]["step"] or 0, t))
    data = {}
    for t in order:
        job = _latest_job(jobs_root, t)
        acc: dict[int, list[int]] = {}
        if job is not None:
            for s, (n, c) in bench.validations(job).items():
                a = acc.setdefault(s // width, [0, 0])
                a[0], a[1] = a[0] + c, a[1] + n
            for s, r, p, n in _events(job)[0]:
                if r == 0:
                    a = acc.setdefault(s // width, [0, 0])
                    a[0], a[1] = a[0] + p, a[1] + n
        data[t] = (job, acc)
    left, top, cw, rh = 250, 50, 50, 24
    w, h = left + cw * cells + 20, top + rh * len(order) + 40
    o = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="每道题原题通过率随训练的变化">']
    for b in range(cells):
        o.append(f'<text class="small" x="{left + cw * b + cw / 2}" y="{top - 8}" text-anchor="middle">{b * width}</text>')
    o.append(f'<text class="small" x="{left + cw * cells}" y="{h - 8}" text-anchor="end">训练步（每 {width} 步一格）</text>')
    for i, t in enumerate(order):
        y, row = top + rh * i, rows[t]
        mark = "◆" if row["two_stage"] is not None else ("☆" if row["untrained"] else "★") if row["lenient"] else ""
        o.append(f'<text x="{left - 8}" y="{y + rh / 2 + 4}" text-anchor="end">{html.escape(t)} '
                 f'<tspan class="small">{row["base"][0]}/{row["base"][1]}</tspan> <tspan class="mark">{mark}</tspan></text>')
        for b, (c, n) in sorted(data[t][1].items()):
            if b >= cells or not n:
                continue
            rate = c / n
            level = 0 if rate == 0 else 1 if rate < 0.15 else 2 if rate < 0.35 else 3
            o.append(f'<g class="cell{" dark" if level >= 2 else ""}"><rect x="{left + cw * b + 1}" y="{y + 1}" '
                     f'width="{cw - 2}" height="{rh - 2}" rx="2" fill="var(--h{level})"><title>{html.escape(t)} · '
                     f'step {b * width}–{b * width + width - 1}: {c}/{n}</title></rect>'
                     f'<text x="{left + cw * b + cw / 2}" y="{y + rh / 2 + 3.5}" text-anchor="middle">{c}/{n}</text></g>')
        for kind, step in (("◆", row["two_stage"]), ("☆" if row["untrained"] else "★", row["step"])):
            if step is not None and step // width < cells and (kind == "◆" or row["two_stage"] is None):
                o.append(f'<text class="mark" x="{left + cw * (step // width) + cw - 9}" y="{y + 11}">{kind}</text>')
    o.append("</svg>")
    lenient = sum(r["lenient"] for r in rows.values())
    two = sum(r["two_stage"] is not None for r in rows.values())
    untrained = sum(r["lenient"] and r["untrained"] for r in rows.values())
    title = f"Overfit bench：宽松线 {lenient}/{len(rows)}，两段式 {two}/{len(rows)}"
    intro = (f"每行一道题，题名后是 base（起始模型在早期 step-0 尝试里的通过数）。每格是这 {width} 步里原题的通过数/尝试数，"
             "readout 和原题训练组合在一起算，颜色按通过率分四档，空白是没跑到。"
             f"◆ = 两段式解出（64 次 confirm ≥ 13/64），标在 confirm 的那一步；★ = 训练后过了宽松线（readout ≥ 3/16 或原题训练组过半）；"
             f"☆ = 第 0 步就已经在宽松线上（{untrained} 道），不算训练的功劳。每行用这道题最近一次开的 job。")
    caption = "从浅到深的行是学会了的题；整行接近白色、尝试数很大的，是版本练了很久但原题一直不过的题。"
    return _page(title, intro, f'<figure><div class="figscroll">{"".join(o)}</div>'
                               f'<figcaption>{caption}</figcaption></figure>')
