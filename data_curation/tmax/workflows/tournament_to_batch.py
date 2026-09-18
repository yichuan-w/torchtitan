#!/usr/bin/env python3
"""tournament_to_batch.py — phase-3 handoff: judgements → repro_exec batch TSV. v2 (scripts review 2026-09-02).

Reads one or more judgement JSONL files (judge records as written by the judges; revision records —
those without a `verdict` — are ignored) and emits rows for tools/repro_exec.py batch:
  - every record with a non-null repro_cmd → one row, mode/label from repro_expected (tokens, D4):
        reward==0 / rejects-right           → mode repro, label rr:<rubric>:<bucket>, expected 0
        reward==1-unenforced / unenforced   → mode repro, label ur:<rubric>:<bucket>, expected 1
        no-reward-timeout | reward-varies | *timeout* | *varies* (unstable)
                                            → mode repro (label unstable-repro:…) AND mode flake (label unstable-flake:…)
        default (accepts-wrong)             → mode repro, label aw:<rubric>:<bucket>, expected 1
    PASS records with shortcut_tag also carry a repro_cmd (D3) → mode repro, label tag:<shortcut_tag>:…
  - one `empty` baseline row per distinct task_id (repro_exec --skip-if-done dedups against earlier batches)
The FIRST judge record per task_id is the verdict of record (prompt recording section); later ones are ignored.
Only ids, tiers, labels, repro_expected and the judge-written repro_cmd (a shell string) are handled — no
evidence/reason fields are read. Output default: repro/judge_batches/batch_<run>.tsv — TRACKED (ruling: judge-written repro_cmd is shell
written by the judge, not task text — same status as reviewer repros).
Usage: python3 tournament_to_batch.py --judgements 'judge/run_x/judgements_V7_bucket_*.jsonl' --run run_x
"""
import argparse, csv, glob, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from repro_stats import lint   # transcription-defect screen (flag only)
from ids import assert_full_ids

ap = argparse.ArgumentParser()
ap.add_argument('--judgements', nargs='+', required=True, help='one or more JSONL files or globs')
ap.add_argument('--out', help='default repro/judge_batches/batch_<run>.tsv (tracked: judge-written shell, not task text)')
ap.add_argument('--run', default='run', help='run name used for the default --out')
ap.add_argument('--harness', choices=['gate2', 'torchtitan'], default='gate2', help='D-78: harness column written on every row (repro_exec honours it per row; default gate2 until the lead rules)')
a = ap.parse_args()
files = sorted({p for pat in a.judgements for p in glob.glob(pat)} or set(a.judgements))
rows, seen_task, first = [], set(), {}
n_rec = n_rev = 0
for f in files:
    for line in pathlib.Path(f).read_text().splitlines():
        if not line.strip():
            continue
        j = json.loads(line)
        if 'verdict' not in j:                    # revision record: not a judgement
            n_rev += 1; continue
        tid = j['task_id']
        if tid in first:                          # verdict of record = first line per task_id
            continue
        first[tid] = j; n_rec += 1
assert_full_ids(first, 'tournament_to_batch: judgement task_ids')
for tid, j in first.items():
    rub = j.get('rubric_version') or j.get('rubric') or '?'; bk = j.get('bucket', j.get('agent', '?'))
    if tid not in seen_task:
        rows.append([tid, 'empty', '', f'empty:{rub}', '', '0']); seen_task.add(tid)
    cmd = j.get('repro_cmd')
    if not cmd:
        continue
    exp = (j.get('repro_expected') or '').lower()
    if j.get('tier') == 'PASS' and j.get('shortcut_tag'):
        rows.append([tid, 'repro', cmd, f"tag:{j['shortcut_tag']}:{rub}:{bk}", '', '1'])
    elif exp.startswith('reward==0') or 'rejects-right' in exp:
        rows.append([tid, 'repro', cmd, f'rr:{rub}:{bk}', '', '0'])
    elif 'unenforced' in exp:
        rows.append([tid, 'repro', cmd, f'ur:{rub}:{bk}', '', '1'])
    elif 'timeout' in exp or 'varies' in exp or 'reward.txt' in exp:
        rows.append([tid, 'gold', '', f'unstable-gold:{rub}:{bk}', '', '1'])
        rows.append([tid, 'flake', '', f'unstable-flake:{rub}:{bk}', '', 'vector'])
        rows.append([tid, 'repro', cmd, f'unstable-repro:{rub}:{bk}', '', 'unstable'])
    else:
        rows.append([tid, 'repro', cmd, f'aw:{rub}:{bk}', '', '1'])
out = pathlib.Path(a.out) if a.out else pathlib.Path(__file__).resolve().parents[1] / 'repro/judge_batches' / f'batch_{a.run}.tsv'
out.parent.mkdir(parents=True, exist_ok=True)
with out.open('w', newline='') as fh:
    w = csv.writer(fh, delimiter='\t', lineterminator='\n')
    w.writerow(['task_id', 'mode', 'repro_cmd', 'label', 'image', 'expected_reward', 'cmd_escaped', 'repro_lint_warn', 'harness'])
    for r in rows:
        cmd = r[2].replace('\\', '\\\\').replace('\n', '\\n') if r[2] else ''   # line-safe cells: backslash and newline escaped
        w.writerow(r[:2] + [cmd] + r[3:] + ['1' if r[2] else '', lint(r[2]) if r[1] == 'repro' else '', a.harness])
n_lint = sum(1 for r in rows if r[1] == 'repro' and lint(r[2]))
print(f'{len(files)} file(s), {n_rec} judge records (verdict of record), {n_rev} revision records ignored -> {len(rows)} rows for {len(seen_task)} tasks -> {out}; repro_lint_warn on {n_lint} repro row(s)')
print(f'run: repro_exec.py batch --tsv {out} --workers 4 --timeout 600 --skip-if-done --results {out.with_suffix(".jsonl")}')
