# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""HTML pages (inline SVG, Chinese or English) for the overfit bench, the two views agreed in #321.

    plot_curve.py --timeline <job dir> [<job dir> ...] --out timeline.html [--lang en]
    plot_curve.py --timeline <jobs root> --results tb21_results.md --out timeline.html   # every task
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
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench  # noqa: E402

layout = bench.layout

STYLE = """<style>
:root{--bg:#fcfcfb;--fg:#0b0b0b;--muted:#52514e;--line:#e4e3df;--orig:#eb6834;--ver:#2a78d6;--ro:#1baf7a;
  --born:#6250d6;--h0:#eef4fc;--h1:#9cc3ef;--h2:#2a78d6;--h3:#0d3b75;--hfg:#fcfcfb}
@media (prefers-color-scheme: dark){:root{--bg:#1a1a19;--fg:#f0efec;--muted:#c3c2b7;--line:#33322f;--orig:#d95926;
  --ver:#3987e5;--ro:#199e70;--born:#9085e9;--h0:#20262e;--h1:#2a4a72;--h2:#3987e5;--h3:#a9cdf5;--hfg:#0b0b0b}}
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
svg .cell text{font-size:9.5px} svg .cell.dark text{fill:var(--hfg)}
svg .mark{font-size:13px}
.key{font-size:12.5px;color:var(--muted)} .tag{font-size:13px;font-weight:400;color:var(--muted);margin-left:8px}
</style>"""


# Every string a page shows, per language. Counts stay "passed/attempts" in both.
TEXT = {
    "cn": {
        "orig": "原题", "step": "训练步", "aria_t": "{task} 的训练时间线",
        "born": "r{rev} 在 step {step} 进池（父版本 {parent}）", "flat": "{n} 组没有梯度（{sizes}）", "sep": "、",
        "key": "实心点 = 有对有错、产生了梯度的组；空心圆 = 全错，空心方块 = 全对（×N 是组数）；菱形 = readout，≥3/16 时填满；"
               "紫色虚线 = teacher 在这一步写出新版本。",
        "cap": "{task}：最好的一次 readout {rb}/{rn}，原题训练组最好 {gb}/{gn}。原题横道上实心点多起来、顶上的菱形填满（≥3/16）是学会原题的"
               "样子；版本横道上实心点很多而原题横道全是空心，是版本上学到的没有迁移到原题。",
        "t_title": "Overfit bench：训练时间线",
        "t_intro": "每个版本一条横道，原题在最上面单独一条。实心点 = 这一组有对有错、产生了梯度，旁边写通过数/组大小；"
                   "同一步没有梯度的组合成一个空心标记，圆圈是全错、方块是全对，旁边写 0/12×N 或 12/12×N。"
                   "顶上的菱形是 readout，写确切的通过数/尝试数，≥3/16 时填满。紫色粗虚线 = teacher 在这一步写出新版本，"
                   "从父版本的横道连过来。版本横道上有梯度的组只标过半的。图比屏幕宽时可以左右滑动。",
        "status": {"two": "两段式解出 @ step {s}", "lenient": "过宽松线 @ step {s}", "step0": "第 0 步已在宽松线上",
                   "open": "未过线"},
        "aria_h": "每道题原题通过率随训练的变化", "h_axis": "训练步（每 {w} 步一格）",
        "h_title": "Overfit bench：宽松线 {l}/{n}，两段式 {t}/{n}",
        "h_intro": "每行一道题，题名后是 base（起始模型在早期 step-0 尝试里的通过数）。每格是这 {w} 步里原题的通过数/尝试数，"
                   "readout 和原题训练组合在一起算，颜色按通过率分四档，空白是没跑到。"
                   "◆ = 两段式解出（64 次 confirm ≥ 13/64），标在 confirm 的那一步；★ = 训练后过了宽松线（readout ≥ 3/16 或原题训练组过半）；"
                   "☆ = 第 0 步就已经在宽松线上（{u} 道），不算训练的功劳。每行用这道题最近一次开的 job。",
        "h_cap": "通过率越高，格子的颜色离背景色越远；整行都是 0/n、尝试数很大的，是版本练了很久但原题一直不过的题。",
    },
    "en": {
        "orig": "original", "step": "train step", "aria_t": "training timeline of {task}",
        "born": "r{rev} entered the pool at step {step} (parent {parent})", "flat": "{n} groups without a gradient ({sizes})",
        "sep": ", ",
        "key": "Filled dot = a group with both passes and failures (it produced a gradient); hollow circle = all failed, "
               "hollow square = all passed (×N groups); diamond = readout, filled at 3/16 or more; "
               "dashed purple = the teacher wrote a new version at this step.",
        "cap": "{task}: best readout {rb}/{rn}, best training group on the original {gb}/{gn}. Learning the original looks like "
               "filled dots appearing on its lane and the diamonds filling (3/16 or more); many filled dots on version lanes "
               "with an all-hollow original lane is practice on the versions that did not carry over to the original.",
        "t_title": "Overfit bench: training timelines",
        "t_intro": "One lane per version, the original on its own lane at the top. A filled dot is a group with both passes and "
                   "failures, so it produced a gradient, with passed/group size beside it; groups without a gradient at the "
                   "same step fold into one hollow marker, a circle when all failed and a square when all passed, written "
                   "0/12×N or 12/12×N. Diamonds on top are readouts with their exact passed/attempts, filled at 3/16 or more. "
                   "A thick dashed purple line is a new version the teacher wrote at that step, drawn from its parent's lane. "
                   "On version lanes only groups at half or more carry a count. Wide figures scroll sideways.",
        "status": {"two": "two-stage solved @ step {s}", "lenient": "lenient bar @ step {s}",
                   "step0": "at the lenient bar at step 0", "open": "not at the bar"},
        "aria_h": "the original's pass rate per task over training", "h_axis": "train step ({w} steps per cell)",
        "h_title": "Overfit bench: lenient bar {l}/{n}, two-stage {t}/{n}",
        "h_intro": "One row per task; after the name is its base rate (the starting model's passes over earlier step-0 attempts). "
                   "Each cell is the original's passed/attempts over those {w} steps, readouts and training groups on the "
                   "original pooled, coloured in four bands of pass rate; blank means the run never got there. "
                   "◆ = two-stage solved (13/64 or more on the 64-attempt confirm), at the confirmed step; ★ = lenient bar "
                   "(a readout of 3/16 or more, or a training group on the original at half or more) reached through "
                   "training; ☆ = already at the lenient bar at step 0 ({u} tasks), not training's doing. "
                   "Each row uses the task's most recent job.",
        "h_cap": "The higher the pass rate, the further a cell's colour from the background; a row of 0/n with large n is a "
                 "task whose versions were practised for long while the original never passed.",
    },
}


def _page(title: str, intro: str, body: str, lang: str = "cn") -> str:
    return (
        f'<!doctype html><html lang="{"zh" if lang == "cn" else "en"}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title></head><body>{STYLE}<div class=\"wrap\">"
        f"<h1>{html.escape(title)}</h1><p>{intro}</p>{body}</div></body></html>"
    )


@lru_cache(maxsize=None)
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


@lru_cache(maxsize=None)
def _validations(job: Path) -> dict[int, tuple[int, int]]:
    return bench.validations(job)


def _flat_label(kind: str, cell: list[tuple[int, int]]) -> str:
    """"0/12×N" or "12/12×N", with the commonest group size; the hover title lists every size."""
    sizes = [n for _, n in cell]
    n = max(set(sizes), key=sizes.count)
    return f"{0 if kind == 'fail' else n}/{n}×{len(cell)}"


def timeline_svg(job: Path, lang: str = "cn", status: str = "", wrap: int = 0) -> tuple[str, str]:
    """One job's figure. ``wrap`` > 0 breaks it into rows of that many steps (for a static PNG or PDF, where a
    figure cannot scroll); 0 draws one row that scrolls sideways."""
    T = TEXT[lang]
    task = json.loads((job / bench.JOB_JSON).read_text())["task"]
    groups, steps = _events(job)
    readouts = sorted(_validations(job).items())
    versions = _versions(job, steps)
    born = {v["rev"]: v["step"] for v in versions}
    cells: dict[tuple[int, int], dict[str, list]] = {}
    for st, rev, p, n in groups:
        kind = "fail" if p == 0 else "pass" if p == n else "mix"
        cells.setdefault((st, rev), {"mix": [], "fail": [], "pass": []})[kind].append((p, n))
    xmax = max([g[0] for g in groups] + [s for s, _ in readouts] + [v["step"] for v in versions] + [5])
    span = wrap if wrap > 0 else xmax + 1
    rows = [_timeline_row(T, task, cells, readouts, versions, born, lo, min(lo + span, xmax + 1), xmax)
            for lo in range(0, xmax + 1, span)]
    best = max(readouts, key=lambda t: t[1][1] / t[1][0], default=(0, (16, 0)))[1]
    orig = [(p, n) for _, r, p, n in groups if r == 0]
    best_g = max(orig, key=lambda t: t[0] / t[1], default=(0, 12))
    caption = T["cap"].format(task=task, rb=best[1], rn=best[0], gb=best_g[0], gn=best_g[1])
    head = html.escape(task) + (f' <span class="tag">{html.escape(status)}</span>' if status else "")
    body = "".join(f'<div class="figscroll">{r}</div>' for r in rows)
    return task, (f'<figure><h2>{head}</h2><p class="key">{html.escape(T["key"])}</p>'
                  f'{body}<figcaption>{html.escape(caption)}</figcaption></figure>')


def _timeline_row(T: dict, task: str, cells: dict, readouts: list, versions: list, born: dict,
                  lo: int, hi: int, xmax: int) -> str:
    """The steps [lo, hi) of one timeline: the original's lane and every version already in the pool by hi."""
    lanes = [0] + sorted({r for (st, r) in cells if r and lo <= st < hi} |
                         {r for r, b in born.items() if b < hi and any(rr == r and st >= lo for (st, rr) in cells)} |
                         {r for r, b in born.items() if lo <= b < hi} |
                         # a version born here keeps its parent's lane, so the dashed line starts where it should
                         {v["parent"] for v in versions if lo <= v["step"] < hi and v["parent"] in born})
    height = {k: len(c["mix"]) + bool(c["fail"]) + bool(c["pass"]) for k, c in cells.items() if lo <= k[0] < hi}
    left, right, top = 92, 70, 56
    pps = 56.0  # room for a "0/12×3" label before the next step
    width = int(max(hi - lo - 1, 5) * pps) + left + right
    lane_h = {r: max(30, 12 * max([c for (s, rr), c in height.items() if rr == r] + [1]) + 12) for r in lanes}
    y_of, y = {}, top
    for r in lanes:
        y_of[r] = y + lane_h[r] / 2
        y += lane_h[r]
    total_h = y + 40

    def sx(s: float) -> float:
        return left + (s - lo) * pps

    o = [f'<svg viewBox="0 0 {width} {total_h}" style="width:{width}px;min-width:{width}px" role="img" '
         f'aria-label="{html.escape(T["aria_t"].format(task=task))}">']
    tick = 5 if xmax <= 60 else 10
    for s in range(lo + (-lo) % tick, hi, tick):
        o.append(f'<line class="grid" x1="{sx(s):.1f}" x2="{sx(s):.1f}" y1="{top - 30}" y2="{total_h - 34}"/>'
                 f'<text class="small" x="{sx(s):.1f}" y="{total_h - 16}" text-anchor="middle">{s}</text>')
    o.append(f'<text class="small" x="{width - right}" y="{total_h - 2}" text-anchor="end">{T["step"]}</text>')
    yr = top - 22
    o.append(f'<text class="lane" x="{left - 10}" y="{yr + 4}" text-anchor="end">readout</text>')
    for s, (n, c) in readouts:
        if not lo <= s < hi:
            continue
        x = sx(s)
        o.append(f'<path class="ro{" hit" if c * 16 >= 3 * n else ""}" d="M{x:.1f},{yr - 7} l7,7 l-7,7 l-7,-7z">'
                 f'<title>step {s}: readout {c}/{n}</title></path>'
                 f'<text class="cnt" x="{x + 9:.1f}" y="{yr + 4}">{c}/{n}</text>')
    for r in lanes:
        o.append(f'<line class="lanebg" x1="{left}" x2="{width - right}" y1="{y_of[r]}" y2="{y_of[r]}"/>'
                 f'<text class="lane{" orig" if r == 0 else ""}" x="{left - 10}" y="{y_of[r] + 4}" '
                 f'text-anchor="end">{T["orig"] if r == 0 else f"r{r}"}</text>')
    for v in versions:
        if v["rev"] not in y_of or not lo <= v["step"] < hi:
            continue
        x, y0, y1 = sx(v["step"]), y_of.get(v["parent"], y_of[0]), y_of[v["rev"]]
        parent = T["orig"] if v["parent"] == 0 else f"r{v['parent']}"
        note = T["born"].format(rev=v["rev"], step=v["step"], parent=parent)
        o.append(f'<path class="born" d="M{x:.1f},{y0:.1f} L{x:.1f},{y1:.1f} L{x + 10:.1f},{y1:.1f}">'
                 f'<title>{html.escape(note)}</title></path>')
    for (st, r), cell in sorted(cells.items()):
        if r not in y_of or not lo <= st < hi:
            continue
        who = T["orig"] if r == 0 else f"r{r}"
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
                sizes = T["sep"].join(f"{p}/{n}" for p, n in sorted(set(gs)))
                flat = T["flat"].format(n=len(gs), sizes=sizes)
                shape = (f'<circle class="flat{orig}" cx="{x:.1f}" cy="{y:.1f}" r="3.6">' if kind == "fail" else
                         f'<rect class="flat{orig}" x="{x - 3.4:.1f}" y="{y - 3.4:.1f}" width="6.8" height="6.8">')
                end = "circle" if kind == "fail" else "rect"
                o.append(f"{shape}<title>step {st} · {who} · {html.escape(flat)}</title></{end}>")
                label = _flat_label(kind, gs)
            if label:
                o.append(f'<text class="cnt" x="{x + 6:.1f}" y="{y + 3.5:.1f}">{label}</text>')
    o.append("</svg>")
    return "".join(o)


def _status(row: dict | None, lang: str) -> str:
    if not row:
        return ""
    S = TEXT[lang]["status"]
    if row["two_stage"] is not None:
        return S["two"].format(s=row["two_stage"])
    if row["lenient"]:
        return S["step0"] if row["untrained"] else S["lenient"].format(s=row["step"])
    return S["open"]


def timeline_page(jobs: list[Path], lang: str = "cn", results: Path | None = None, wrap: int = 0) -> str:
    """One figure per job; given a jobs root and the results file instead, one figure per task (its latest job)."""
    rows = parse_results(results) if results else {}
    if len(jobs) == 1 and not (jobs[0] / bench.JOB_JSON).is_file():
        if not rows:
            raise SystemExit("a jobs root needs --results to list the tasks")
        jobs = [j for t in sorted(rows) if (j := _latest_job(jobs[0], t)) is not None]
    figs = []
    for j in jobs:
        task = json.loads((j / bench.JOB_JSON).read_text())["task"]
        figs.append(timeline_svg(j, lang, _status(rows.get(task), lang), wrap)[1])
    T = TEXT[lang]
    return _page(T["t_title"], html.escape(T["t_intro"]), "".join(figs), lang)


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
    header: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = [c.strip() for c in line.split("|")]
        if len(cols) > 2 and cols[1] == "task":
            header = cols
            continue
        task = cols[1].split(" ")[0] if len(cols) > 2 else ""
        if not header or task not in rows or len(cols) != len(header):
            continue
        # the two-stage column when the table has one, else any confirm of 13/64 or more
        two = next((c for h, c in zip(header, cols) if h.startswith("two-stage")), None)
        if two is not None:
            m = re.search(r"step (\d+)", two)
            rows[task]["two_stage"] = int(m.group(1)) if m else None
            continue
        for h, c in zip(header, cols):
            if "confirm" in h:
                for s, k in _CONFIRM.findall(c):
                    if int(k) >= 13:
                        rows[task]["two_stage"] = int(s)
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


def heatmap_page(jobs_root: Path, results: Path, width: int = 10, cells: int = 20, lang: str = "cn") -> str:
    T = TEXT[lang]
    rows = parse_results(results)
    order = sorted(rows, key=lambda t: (
        0 if rows[t]["two_stage"] is not None else 1 if rows[t]["lenient"] and not rows[t]["untrained"]
        else 2 if rows[t]["lenient"] else 3, rows[t]["step"] or 0, t))
    data = {}
    for t in order:
        job = _latest_job(jobs_root, t)
        acc: dict[int, list[int]] = {}
        if job is not None:
            for s, (n, c) in _validations(job).items():
                a = acc.setdefault(s // width, [0, 0])
                a[0], a[1] = a[0] + c, a[1] + n
            for s, r, p, n in _events(job)[0]:
                if r == 0:
                    a = acc.setdefault(s // width, [0, 0])
                    a[0], a[1] = a[0] + p, a[1] + n
        data[t] = (job, acc)
    left, top, cw, rh = 250, 50, 50, 24
    w, h = left + cw * cells + 20, top + rh * len(order) + 40
    # drawn at its own pixel width so the cell counts stay readable on a phone; the page scrolls it sideways
    o = [f'<svg viewBox="0 0 {w} {h}" style="width:{w}px;min-width:{w}px" role="img" '
         f'aria-label="{html.escape(T["aria_h"])}">']
    for b in range(cells):
        o.append(f'<text class="small" x="{left + cw * b + cw / 2}" y="{top - 8}" text-anchor="middle">{b * width}</text>')
    o.append(f'<text class="small" x="{left + cw * cells}" y="{h - 8}" text-anchor="end">{T["h_axis"].format(w=width)}</text>')
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
    title = T["h_title"].format(l=lenient, t=two, n=len(rows))
    intro = html.escape(T["h_intro"].format(w=width, u=untrained))
    caption = html.escape(T["h_cap"])
    return _page(title, intro, f'<figure><div class="figscroll">{"".join(o)}</div>'
                               f'<figcaption>{caption}</figcaption></figure>', lang)
