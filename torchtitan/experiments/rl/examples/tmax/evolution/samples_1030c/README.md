# 1030c evolve samples (#324)

Figures and scripts for the evolve analysis requested in issue #324. Data stays with the run; only aggregates are here.

- `scripts/headers_par.py <root> <out> rollouts|validation_rollouts <threads>`: first line of every rollout record, `exec` dropped.
- `scripts/fetch_meta.py <root> <out> <threads>`: per-task lineage, attempts, rewrite records, instructions, solve.sh, Dockerfile, tests.
- `scripts/groups.py <dir>`: one row per training group (lineage events + headers): solved, train step, dropped, rev, finish reasons.
- `scripts/item2.py`, `scripts/item6.py`: the figures in `figs/`.
