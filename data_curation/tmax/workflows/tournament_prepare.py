#!/usr/bin/env python3
"""tournament_prepare.py — precompute everything the calibration-tournament Workflow needs.
DRAFT (2026-09-02, tmax-coder). Nothing here calls a model.

The Workflow script cannot read files, so routing (ledger D-7) and the per-bucket inputs are
prepared here and handed over as `args.plan`:
  plan = {rubric, prompt_path, tasks_dir, per_agent, other_model, prompt: {file_sha256_16, content_sha256_16, version, hashed_at: 'prepare-time'},
          buckets: [{name: 'security'|'other', index, model: 'opus'|'fable', effort: 'xhigh',
                     task_ids: [...], phase0_tsv: <path>, priors_tsv: <path>}],
          counts: {security_tasks, security_agents, other_tasks, other_agents, unknown_routed_to_security}}
Routing rule (D-7): security_union==1 → security bucket; security buckets are split EVENLY into
ceil(n/per_agent) groups (13 → 2 × 6-7), always model opus / effort xhigh; other buckets are
even-split the same way (28 → 10/9/9) with model = --other-model (--chunk-other restores plain chunks). An id whose security status is UNKNOWN is routed
to the security bucket (fail-safe: never leak to a Fable seat).
Per bucket this writes:
  <out>/bucket_<k>_phase0.tsv  — task_id + EXACTLY the 8 D2 columns (m_*, priors, HF labels withheld)
  <out>/bucket_<k>_priors.tsv  — task_id, audit_v4_verdict, v5_verdict, v6_tier, phase3_* (message 2 only)
Content quarantine: only ids, labels, column names and counts are read — never task text.

Usage:
  python3 tournament_prepare.py --rubric V7 --set calibration --out judge/tourn_v7 [--per-agent 10]
                                [--other-model opus] [--ids-file X | --ids a,b,c]
  --set calibration = 12 v6_cal ∪ 11 behavioural controls ∪ 31 manual_read_queue (D7 + reviews/audits/phase0/phase0_id_lists.json)
"""
from __future__ import annotations
import argparse, csv, json, math, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from prompt_sha import prompt_hashes   # prepare-time prompt provenance (judges re-hash at run time)
from ids import assert_full_ids        # every id list is full task ids — never 6-digit prefixes

RA = pathlib.Path(__file__).resolve().parents[1]
AUDIT = pathlib.Path('/data/users/zekaili/fangzhou/tb_check/rebench_tmax_recovery_2026-09-02/work/tmax-audit-files')
PROMPTS = {'V4': RA / 'rubrics/reference/v4_prompt.md',
           'V6': RA / 'rubrics/reference/v6_prompt.md',
           'V7': RA / 'rubrics/v7_prompt.md'}
D2_COLS = ['c1_test_runs_reference_program', 'c1_reference_paths', 'c2_reference_present_during_rollout',
           'c2_reference_agent_writable', 'c3_setup_rm', 'c4_verifier_provenance_check', 'c4_strong',
           'c5_verifier_kind']
PRIOR_COLS = ['audit_v4_verdict', 'v5_verdict', 'v6_tier']
# D7 behavioural controls (ledger-corrected set); 002119 deliberately excluded (caveat)
CONTROLS = ['task_000877_56b03bbe', 'task_003036_c9690c9a', 'task_004869_9903a46c', 'task_001078_937ebb82',
            'task_000601_fbd9ab23', 'task_003033_f6c0c4cb',                       # proven-hackable
            'task_001957_cc826ac1', 'task_002223_e0a30625',                       # guarded
            'task_000164_067c65a9', 'task_000529_44fe752d', 'task_001069_a9182649']  # no-reference
# every tracked phase-3 table feeds the priors file (message 2), incl. controls and judge batches
REWARD_TABLES = sorted(str(p.relative_to(RA / 'repro')) for pat in ('*_reward_table.tsv', 'judge_batches/*_reward_table.tsv')
                       for p in (RA / 'repro').glob(pat) if not p.name.endswith('_nosec.tsv'))


def read_tsv(path: pathlib.Path) -> list[dict]:
    with path.open(newline='') as fh:
        return list(csv.DictReader(fh, delimiter='\t'))


def calibration_set() -> list[str]:
    cal = [l.strip() for l in (AUDIT / 'v6_cal.ids').read_text().splitlines() if l.strip()]
    manual = json.loads((RA / 'reviews/audits/phase0/phase0_id_lists.json').read_text())['manual_read_queue']
    seen, out = set(), []
    for t in cal + CONTROLS + manual:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def even_split(ids: list[str], per_agent: int) -> list[list[str]]:
    if not ids:
        return []
    k = math.ceil(len(ids) / per_agent)
    base, extra = divmod(len(ids), k)
    out, i = [], 0
    for g in range(k):                      # sizes differ by at most 1 (13 → 7 + 6)
        n = base + (1 if g < extra else 0)
        out.append(ids[i:i + n]); i += n
    return out


def chunk(ids: list[str], per_agent: int) -> list[list[str]]:
    return [ids[i:i + per_agent] for i in range(0, len(ids), per_agent)]


def phase3_rewards() -> dict[str, dict]:
    """task_id -> {mode/label: reward} from every reward table (ids + reward fields only)."""
    out: dict[str, dict] = {}
    for name in REWARD_TABLES:
        p = RA / 'repro' / name
        if not p.exists():
            continue
        for r in read_tsv(p):
            tid = r['task_id']; d = out.setdefault(tid, {})
            if 'mode' in r:
                d[f"{r['mode']}:{r.get('label', '')}"] = r.get('reward', r.get('repro_reward', ''))
            else:  # batch13/batch6 tables: empty_reward / repro_reward columns
                d['empty'] = r.get('empty_reward', ''); d[f"repro:{r.get('m_exploitability', '')}"] = r.get('repro_reward', '')
    return out

def load_pinned_priors(src_dir: pathlib.Path) -> dict[str, list[str]]:
    """--priors-from: message-2 rows (task_id -> the row exactly as written) from an already prepared run's bucket_*_priors.tsv.
    Pins the priors a comparison run's judges see to what an earlier run's judges saw (regression like-for-like: the earlier run's
    own phase-3 rewards must NOT appear). Header must match the current PRIOR_COLS layout; ids are full ids."""
    hdr = ['task_id'] + PRIOR_COLS + ['phase3_rewards_json']
    out: dict[str, list[str]] = {}
    for f in sorted(src_dir.glob('bucket_*_priors.tsv')):
        with f.open(newline='') as fh:
            rows = list(csv.reader(fh, delimiter='\t'))
        assert rows and rows[0] == hdr, f'{f}: header {rows[0]} != {hdr}'
        for r in rows[1:]:
            assert r[0] not in out or out[r[0]] == r, f'{f}: conflicting pinned rows for {r[0]}'
            out[r[0]] = r
    assert out, f'no bucket_*_priors.tsv under {src_dir}'
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--rubric', choices=sorted(PROMPTS), required=True)
    ap.add_argument('--set', choices=['calibration', 'ids'], default='calibration')
    ap.add_argument('--ids'); ap.add_argument('--ids-file')
    ap.add_argument('--out', required=True)
    ap.add_argument('--per-agent', type=int, default=10)
    ap.add_argument('--other-model', choices=['opus', 'fable'], default='opus')
    ap.add_argument('--chunk-other', action='store_true', help='plain per_agent chunks for non-security groups instead of even split')
    ap.add_argument('--phase0', default=str(RA / 'reviews/audits/phase0/phase0_harness_facts.tsv'))
    ap.add_argument('--tasks-dir', default=str(RA / 'scratch/tasks'))
    ap.add_argument('--priors-from', help='pin message-2 priors rows (by task_id) to an already prepared run dir instead of recomputing them (regression like-for-like)')
    a = ap.parse_args()

    if a.set == 'ids':
        ids = [x for x in (pathlib.Path(a.ids_file).read_text().split() if a.ids_file else (a.ids or '').split(',')) if x]
    else:
        ids = calibration_set()
    assert_full_ids(ids, 'tournament_prepare: id list')
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)

    p0 = {r['task_id']: r for r in read_tsv(pathlib.Path(a.phase0))}
    missing_task_files = [t for t in ids if not (pathlib.Path(a.tasks_dir) / t / 'tests/test.sh').exists()]
    sec, other, unknown = [], [], []
    for t in ids:
        row = p0.get(t)
        flag = (row or {}).get('security_union', '')
        if flag == '1':
            sec.append(t)
        elif flag == '0':
            other.append(t)
        else:
            unknown.append(t); sec.append(t)          # fail-safe routing (D-7 pt 4 spirit)

    buckets = []
    for g in even_split(sec, a.per_agent):
        buckets.append({'name': 'security', 'model': 'opus', 'effort': 'xhigh', 'task_ids': g})
    # non-security groups are even-split too (ceil(n/per_agent) groups, sizes differ by <=1, each <= per_agent):
    # balances judge load and matches the lead's expected shapes (e.g. 28 -> 10/9/9); --chunk-other restores plain chunking
    for g in (chunk(other, a.per_agent) if a.chunk_other else even_split(other, a.per_agent)):
        buckets.append({'name': 'other', 'model': a.other_model, 'effort': 'xhigh', 'task_ids': g})

    p3 = phase3_rewards()
    pinned = load_pinned_priors(pathlib.Path(a.priors_from)) if a.priors_from else None
    if pinned is not None:
        missing = [t for t in ids if t not in pinned]
        assert not missing, f'--priors-from: {len(missing)} ids have no pinned priors row (e.g. {missing[:3]})'
    for k, b in enumerate(buckets):
        b['index'] = k
        ph = out / f'bucket_{k}_phase0.tsv'; pr = out / f'bucket_{k}_priors.tsv'
        with ph.open('w', newline='') as fh:
            w = csv.writer(fh, delimiter='\t', lineterminator='\n'); w.writerow(['task_id'] + D2_COLS)
            for t in b['task_ids']:
                w.writerow([t] + [(p0.get(t) or {}).get(c, '') for c in D2_COLS])
        with pr.open('w', newline='') as fh:
            w = csv.writer(fh, delimiter='\t', lineterminator='\n'); w.writerow(['task_id'] + PRIOR_COLS + ['phase3_rewards_json'])
            for t in b['task_ids']:
                w.writerow(pinned[t] if pinned is not None else [t] + [(p0.get(t) or {}).get(c, '') for c in PRIOR_COLS] + [json.dumps(p3.get(t, {}), sort_keys=True)])
        b['phase0_tsv'] = str(ph); b['priors_tsv'] = str(pr)

    plan = {'rubric': a.rubric, 'prompt_path': str(PROMPTS[a.rubric]), 'tasks_dir': a.tasks_dir,
            'per_agent': a.per_agent, 'other_model': a.other_model, 'buckets': buckets,
            'counts': {'tasks': len(ids), 'security_tasks': len(sec), 'security_agents': sum(1 for b in buckets if b['name'] == 'security'),
                       'other_tasks': len(other), 'other_agents': sum(1 for b in buckets if b['name'] == 'other'),
                       'unknown_routed_to_security': len(unknown), 'missing_task_files': missing_task_files},
            'output_dir': str(out),
            'priors_from': a.priors_from,   # None = priors recomputed from every reward table at prepare time
            # PREPARE-TIME provenance only: the workflow's judge re-hashes the file it actually reads (prompt_sha256_16 per record)
            'prompt': {**prompt_hashes(PROMPTS[a.rubric]), 'hashed_at': 'prepare-time'}}
    (out / 'plan.json').write_text(json.dumps(plan, indent=1))
    print(json.dumps(plan['counts'], indent=1))
    print(f"plan -> {out / 'plan.json'} ({len(buckets)} buckets); pass it as Workflow args.plan")
    if missing_task_files:
        print(f"WARNING: {len(missing_task_files)} ids have no scratch/tasks/<id>/tests/test.sh (controls outside the 398?)", file=sys.stderr)


if __name__ == '__main__':
    main()
