#!/usr/bin/env python3
"""tournament_reward_table.py — turn a repro_exec results JSONL into the reward table the scorer/CHANGELOG cite.
Columns: task_id, mode, label, expected (from the batch TSV if given), reward, test_rc, prep_rc, dur_s, repro_lines (masked statements), payload_lines, repro_effort (D-60).
Ids, labels, rewards and statement counts only — no tails are read or written.
Usage: python3 tournament_reward_table.py --results repro/judge_batches/batch_x.jsonl [--tsv repro/judge_batches/batch_x.tsv] [--out <path>]
"""
import sys, argparse, csv, json, pathlib, re

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from repro_stats import analyze, lint   # D-51/D-60 counts + transcription lint: single definitions shared with tournament_score.py
from ids import assert_full_ids
EXPLOIT7_NOTE = "lower bound on training's exposure: a rewrite landing before t0 is invisible to the t0/tdelta difference, and training's read window (two Daytona RPCs) is wider than the executor's"


PIN_SKIP_MARK = 'no active entries'                                    # pin_block: "reference pin manifest has no active entries; integrity check skipped"


def pin_manifest_empty(rec: dict) -> str:
    """'1' when THIS run's verifier logged that the pin manifest had no active entries.

    Read from the run's own output rather than from today's scratch tree, because the tree is mutable: a later
    --refresh-pins re-apply would rewrite the manifest and silently make a past measurement look pinned. A control or
    discriminating row measured with an empty manifest tested a verifier with no pin in it and is not evidence about
    the pin (wave-3: 33 specs, reviews/spec_waves/wave3_empty_pin_manifest_scan_20260903.tsv).
    """
    blob = (rec.get('stdout_tail') or '') + (rec.get('prep_tail') or '')
    return '1' if PIN_SKIP_MARK in blob else ''

ap = argparse.ArgumentParser(); ap.add_argument('--results', required=True); ap.add_argument('--tsv'); ap.add_argument('--out')
ap.add_argument('--fable-export', action='store_true', help='D-35: also write <out>_nosec.tsv with security_union==1 rows dropped (no repro_cmd column exists in this table anyway)')
ap.add_argument('--security-map', default=str(pathlib.Path(__file__).resolve().parents[1] / 'reviews/audits/phase0/phase0_harness_facts.tsv'))
a = ap.parse_args()
res = pathlib.Path(a.results); out = pathlib.Path(a.out) if a.out else res.with_name(res.stem + '_reward_table.tsv')
exp = {}
if a.tsv:
    with open(a.tsv, newline='') as _fh:                      # '#' header lines are caveats/notes, not rows (same reader rule as repro_exec)
        for r in csv.DictReader((ln for ln in _fh if not ln.startswith('#')), delimiter='\t'):
            exp[(r['task_id'], r['mode'], r.get('label', ''))] = r.get('expected_reward', '')
assert_full_ids((json.loads(l)['task_id'] for l in res.read_text().splitlines() if l.strip()), 'tournament_reward_table: results task_ids')
with out.open('w', newline='') as fh:
    w = csv.writer(fh, delimiter='\t', lineterminator='\n')
    w.writerow(['task_id', 'mode', 'label', 'expected', 'reward', 'test_rc', 'prep_rc', 'dur_s', 'repro_lines', 'payload_lines', 'repro_effort', 'repro_lint_warn', 'exploit7_observed', 'unexpected_reward1', 'exploit7_note', 'tests_source', 'pin_manifest_empty'])   # pin_manifest_empty: pin_block logged 'no active entries' in THIS run — the row measured a verifier with no pin in it   # tests_source: repair_tally needs it to tell UNDER-TIGHT from a void row
    _records = [json.loads(l) for l in res.read_text().splitlines() if l.strip()]
    _chosen = {}; _order = []                                          # attempt-awareness: one row per (task_id, mode, label) = the LAST non-platform-error record (a create_failed void attempt beside its successful retry must not block the verdict, e.g. 000122)
    for r in _records:
        key = (r['task_id'], r.get('mode'), r.get('label', ''))
        if key not in _chosen:
            _order.append(key)
        if key not in _chosen or not r.get('platform_error') or _chosen[key].get('platform_error'):
            _chosen[key] = r                                          # keep the newest non-platform-error; only fall back to a platform-error row if every attempt errored
    for key in _order:
        r = _chosen[key]; lab = r.get('label', '')
        if r.get('mode') == 'flake':
            w.writerow([r['task_id'], 'flake', lab, exp.get((r['task_id'], 'flake', lab), ''), ','.join(r['reward_vector']), r.get('test_rcs'), '', r.get('durations_s'), '', '', '', '', '', '', '', '', '']); continue
        rw = r.get('reward') if r.get('reward') not in (None, '') else 'NONE'
        st = analyze(r.get('repro_cmd'))
        w.writerow([r['task_id'], r['mode'], lab, exp.get((r['task_id'], r['mode'], lab), ''), rw, r.get('rc'), r.get('prep_rc'), r.get('duration_s'), st['repro_statements'], st['payload_lines'], st['repro_effort'], lint(r.get('repro_cmd')) if r.get('mode') == 'repro' else '',
                    '' if r.get('exploit7_observed') is None else str(r.get('exploit7_observed')), '' if r.get('unexpected_reward1') is None else str(r.get('unexpected_reward1')),
                    EXPLOIT7_NOTE if r.get('exploit7_observed') is not None else '', r.get('tests_source') or '', pin_manifest_empty(r)])   # D-97 carry-forward: the label travels with the figure
print(f'wrote {out}')
if a.fable_export:
    sec = {}
    for r in csv.DictReader(open(a.security_map, newline=''), delimiter='\t'):
        sec[r['task_id']] = r.get('security_union', '')
    nosec = out.with_name(out.stem + '_nosec.tsv')
    rows = list(csv.reader(out.open(newline=''), delimiter='\t'))
    kept = [rows[0]] + [r for r in rows[1:] if sec.get(r[0], '?') == '0']     # unknown security status is dropped too (fail-safe)
    with nosec.open('w', newline='') as fh:
        csv.writer(fh, delimiter='\t', lineterminator='\n').writerows(kept)
    print(f'wrote {nosec} (D-35 Fable export: {len(rows)-1} -> {len(kept)-1} rows, security_union==1 or unknown dropped)')
