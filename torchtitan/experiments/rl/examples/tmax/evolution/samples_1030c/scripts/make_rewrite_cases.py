"""Rewrite case studies (run 1030c): solved-of-12 per trained group, revision boundaries, and what each rewrite changed."""
import os, sys, textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
groups = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data", "groups_1030c.csv"))
EASY, HARD, LINE, INK, MUTED = "#1baf7a", "#eb6834", "#2a78d6", "#0b0b0b", "#52514e"

CASES = [
    ("(a) Harder rewrite forces a new skill", "task_005436_d605aece", "nginx TLS load balancer",
     [("harder", "Certificate must pass hostname verification (pinned urllib3 2.0 drops the CN fallback); old solutions used CN-only certs + curl -k."),
      ("harder", "Also require TLS 1.3 clients (accepted after the task's last trained group).")]),
    ("(b) Easier rewrite unblocks a 0/12 task", "software-engineering_20260604_144210_017", "RLE codec repair",
     [("easier", "Fix the self-contradictory run-length comment in rle.h (every attempt built an off-by-one codec); no code or patch given."),
      ("harder", "Harder follow-up (accepted after the task's last trained group).")]),
    ("(c) Harder overshoots, easier pulls back", "software-engineering_20260416_172928_016", "C memory-pool allocator",
     [("harder", "Add C11 fundamental-alignment requirement for every returned block."),
      ("easier", "Remove the added alignment check (8/12 had fixed the original bug and failed only this).")]),
    ("(d) Failure mode: easier rewrites hand over the core work", "software-engineering_20260415_123352_017", "Python/C polyglot",
     [("easier", "Narrow N from 0-100 to 0-5."),
      ("easier", "Require only N=0; hint at C preprocessor vs Python strings."),
      ("easier", "Ship the working C implementation as a header (core work handed over)."),
      ("harder", "Harder rewrite."),
      ("easier", "Ship only a C output helper; hint the wrapper structure.")]),
]

f, axes = plt.subplots(2, 2, figsize=(13, 9.2))
for ax, (title, task, name, rws) in zip(axes.flat, CASES):
    g = groups[(groups.task == task)].dropna(subset=["solved", "rev", "train_step"]).sort_values("group")
    revs = sorted(g.rev.unique())
    for i, r in enumerate(revs):
        x = g[g.rev == r]
        if i % 2:
            lo = x.train_step.min() - 4
            hi = x.train_step.max() + 4
            ax.axvspan(lo, hi, color="#f1f0ec", zorder=0)
        ax.text(x.train_step.mean(), -1.6, f"r{int(r)}", ha="center", fontsize=8, color=MUTED)
    for r in revs:  # one segment per revision: a rewrite breaks the line
        x = g[g.rev == r]
        ax.plot(x.train_step, x.solved, color=LINE, lw=2, marker="o", ms=6, zorder=3)
    for xs, ys in zip(g.train_step, g.solved):
        ax.annotate(f"{int(ys)}", (xs, ys), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=7, color=MUTED)
    # rewrite markers between consecutive revisions; trailing rewrites (not trained) at the right edge
    xmax = g.train_step.max()
    for k, (job, _) in enumerate(rws, start=1):
        col = EASY if job == "easier" else HARD
        if k < len(revs):
            a, b = g[g.rev == revs[k - 1]].train_step.max(), g[g.rev == revs[k]].train_step.min()
            x0 = (a + b) / 2
            ls = "-"
        else:
            x0 = xmax + 10 + 6 * (k - len(revs))
            ls = ":"
        ax.axvline(x0, color=col, lw=1.6, ls=ls, zorder=2)
        ax.text(x0, 13.1, str(k), ha="center", va="center", fontsize=8, color="white",
                bbox=dict(boxstyle="circle,pad=0.25", fc=col, ec="none"))
    ax.set_ylim(-2.2, 14); ax.set_yticks([0, 3, 6, 9, 12])
    ax.set_xlim(0, max(310, xmax + 20))
    ax.set_title(f"{title}\n{name}", loc="left", fontsize=10, color=INK)
    ax.set_xlabel("Training step", fontsize=8, color=MUTED); ax.set_ylabel("Solved (of 12)", fontsize=8, color=MUTED)
    ax.grid(axis="y", color="#e6e5e1", lw=0.6)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    lines = [f"{k}. [{job}] " + "\n    ".join(textwrap.wrap(desc, 78)) for k, (job, desc) in enumerate(rws, start=1)]
    ax.text(0, -0.2, "\n".join(lines), transform=ax.transAxes, va="top", fontsize=7.2, color=INK, family="DejaVu Sans")
f.tight_layout(h_pad=5.5, w_pad=3)
for ext in ("png", "pdf"):
    f.savefig(os.path.join(HERE, f"fig_rewrite_cases.{ext}"), dpi=200, bbox_inches="tight")
print("ok")
