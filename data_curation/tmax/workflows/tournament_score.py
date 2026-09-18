#!/usr/bin/env python3
"""tournament_score.py — score a tournament arm against the calibration answer key. v2 (scripts review 2026-09-02).

Answer key (D-16/D-19): rubrics/v7_calibration.tsv — one row per calibration task:
  task_id, expected_soundness, expected_tier_set (|-separated), cleared_by_probe, source_row.
The key is the ONLY truth used here (no re-derivation from reward tables — that produced "clean by accident",
F-20). Judgement records: judge lines (have `verdict`); the FIRST per task_id is the verdict of record;
revision records (no `verdict`) are reported separately, never scored as judgements.
Scores:
  soundness P/R: positive = expected_soundness != 'sound'; judged positive = soundness != 'sound'.
  tier hit: judged tier ∈ expected_tier_set (v3.17 C1: accepts-wrong is never PASS — MEDIUM/HARD shortcuts are REPAIR+tag).
  SIMPLE-control hit (D7 rule): soundness accepts-wrong AND repro_cost simple AND tier ∈ {REPAIR, REJECT-PROVEN}.
Hard gates (from the key, not hard-coded classes): every key row with expected_soundness accepts-wrong and a
tier set ⊆ {REPAIR, REJECT-PROVEN} must be judged non-PASS WITH a repro_cmd; every key row with cleared_by_probe
must not be judged unstable.
D-22 ivm_lead_recall: scorer-side only; leads not truth; boolean/null checks only.
Per-bucket breakdown; optional --bucket-note k="text" printed beside bucket k. Optional table writer for a
results JSONL (--results-jsonl) → <same>_reward_table.tsv (ids/rewards/rc only).
Usage: python3 tournament_score.py --judgements 'judge/run_x/judgements_V7_bucket_*.jsonl' [--key rubrics/v7_calibration.tsv]
        [--results-jsonl judge/run_x/batch_run_x.jsonl] [--bucket-note 0="partly judged on opus-4.8 (F-19)"]
Only ids, labels, booleans and counts are read or printed — never evidence/reason/repro text.
"""
import argparse, collections, csv, glob, json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from repro_stats import analyze, lint   # D-51/D-60 counts + transcription lint, one definition
from ids import assert_full_ids        # joins/filters/exports key on full task ids only

RA = pathlib.Path(__file__).resolve().parents[1]


def resolve(p: str) -> pathlib.Path:
    q = pathlib.Path(p)
    for cand in (q, RA / p):
        if cand.exists():
            return cand
    sys.exit(f'missing file: {p}')


def load_key(path: pathlib.Path) -> dict:
    key = {}
    for r in csv.DictReader(path.open(newline=''), delimiter='\t'):
        key[r['task_id']] = {'soundness': r['expected_soundness'].strip(),
                             'soundness_set': {re.sub(r'\(.*?\)', '', x).strip() for x in re.split(r'[|/]', r['expected_soundness']) if x.strip()},   # 'rejects-right(env)' -> 'rejects-right'
                             'tiers': {t.strip() for t in r['expected_tier_set'].split('|') if t.strip()},
                             'cleared_by_probe': (r.get('cleared_by_probe') or '').strip() not in ('', '0', 'no', 'null'),
                             'source': r.get('source_row', ''),
                             'derived_from_run': (r.get('derived_from_run') or 'reviewer').strip(),
                             'expected_no_gradient': (r.get('expected_no_gradient') or '').strip().lower().startswith('true'),
                             'cost': (r.get('expected_repro_cost') or '').strip().lower(),   # 'medium|simple' = disputed, first token = recorded call (D-68)
                             'harness_dependence': (r.get('harness_dependence') or '').strip(),          # schema D-78 (values pending the second harness read)
                             'harness_verdict_training': (r.get('harness_verdict_training') or '').strip()}
    return key


def load_judgements(patterns: list[str]):
    files = sorted({p for pat in patterns for p in glob.glob(pat)} or set(patterns))
    first, revisions = {}, []
    for f in files:
        for line in pathlib.Path(f).read_text().splitlines():
            if not line.strip():
                continue
            j = json.loads(line)
            if 'verdict' not in j:
                revisions.append(j); continue
            first.setdefault(j['task_id'], j)      # verdict of record = first line per task_id
    return files, first, revisions


def load_scan(path):
    """reviews/<run>_degenerate_graders_scan.tsv (D-89 wrapper output) -> {task_id: (fallback, all_negative, verifier_network)} ints."""
    out = {}
    if path and pathlib.Path(path).exists():
        for r in csv.DictReader(open(path, newline=''), delimiter='\t'):
            out[r['task_id']] = tuple(int(r.get(k) or 0) for k in ('fallback', 'all_negative', 'verifier_network'))
    return out


PASS_CONTROLS_D62 = ['task_000097_601162e4', 'task_000252_aa3c814d', 'task_000443_90a5ee10', 'task_004160_27a6a850']


def repro_coverage() -> tuple[dict, dict]:
    """D-59 caveat numbers, computed here (not by hand): over the base reward tables (repro/*_reward_table.tsv and
    repro/judge_batches/*_reward_table.tsv, *_nosec.tsv duplicates excluded), a measured reward-1 repro is a distinct
    (task_id, label) pair whose reward column is 1 — the dedup rule. Handles the three table formats
    (mode/reward; m_exploitability/repro_reward; label/repro_reward). Returns (per-task distinct-label sets, stats)."""
    labels: dict[str, set] = {}; n_tables = 0
    for pat in ('*_reward_table.tsv', 'judge_batches/*_reward_table.tsv'):
        for f in sorted((RA / 'repro').glob(pat)):
            if f.name.endswith('_nosec.tsv'):
                continue
            n_tables += 1
            for r in csv.DictReader(f.open(newline=''), delimiter='\t'):
                if 'mode' in r:
                    if r.get('mode') == 'repro' and str(r.get('reward')) == '1':
                        labels.setdefault(r['task_id'], set()).add(r.get('label', ''))
                elif str(r.get('repro_reward')) == '1':
                    labels.setdefault(r['task_id'], set()).add(r.get('label') or f"repro:{r.get('m_exploitability', '')}")
    hist = dict(sorted(collections.Counter(len(v) for v in labels.values()).items()))
    return labels, {'base_tables': n_tables, 'tasks_with_reward1_repro': len(labels), 'distinct_label_histogram': hist,
                    'dedup_rule': 'distinct (task_id, label) pairs with reward 1 across the base tables'}


def write_reward_table(results_jsonl: pathlib.Path) -> pathlib.Path:
    out = results_jsonl.with_name(results_jsonl.stem + '_reward_table.tsv')
    if out.exists():          # never clobber the richer table written by tools/tournament_reward_table.py
        return out
    with out.open('w', newline='') as fh:
        w = csv.writer(fh, delimiter='\t', lineterminator='\n')
        w.writerow(['task_id', 'mode', 'label', 'reward', 'test_rc', 'prep_rc', 'dur_s'])
        for line in results_jsonl.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get('mode') == 'flake':
                w.writerow([r['task_id'], 'flake', r.get('label', ''), ','.join(r['reward_vector']), r.get('test_rcs'), '', r.get('durations_s')])
            else:
                rw = r.get('reward') if r.get('reward') not in (None, '') else 'NONE'
                w.writerow([r['task_id'], r['mode'], r.get('label', ''), rw, r.get('rc'), r.get('prep_rc'), r.get('duration_s')])
    return out


class Tee:
    """capture stdout so --fable-export can write a filtered copy"""
    def __init__(self): self.buf = []; self.orig = sys.stdout
    def write(self, x): self.buf.append(x); self.orig.write(x)
    def flush(self): self.orig.flush()


def fable_export(buf: str, path: str, secmap: str) -> None:
    sec = {}
    for r in csv.DictReader(open(secmap, newline=''), delimiter='\t'):
        sec[r['task_id']] = r.get('security_union', '')
    import re as _re
    out_lines = []
    for line in buf.splitlines():
        ids = _re.findall(r'task_\d{6}_[0-9a-f]{8}', line)
        if any(sec.get(t, '?') != '0' for t in ids):
            continue                                   # any security or unknown id on the line → drop the whole line
        out_lines.append(line)
    pathlib.Path(path).write_text('# D-35 Fable export: lines naming security_union==1 (or unknown) tasks removed; no repro_cmd in this output.\n'
                                  '# NOTE: aggregate counts (P/R, gates, per-bucket totals, cross-tab) still INCLUDE security tasks — only per-task lines are stripped.\n'
                                  + '\n'.join(out_lines) + '\n')
    print(f'wrote {path} (D-35 Fable export)', file=sys.__stdout__)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--judgements', nargs='+', required=True)
    ap.add_argument('--key', default=str(RA / 'rubrics/v7_calibration.tsv'))
    ap.add_argument('--results-jsonl')
    ap.add_argument('--bucket-note', action='append', default=[], help='k="text"')
    ap.add_argument('--ivm', default=str(RA / 'reviews/audits/phase0/phase0_ivm_candidates.tsv'))
    ap.add_argument('--exclude-derived-from', help='comma-separated run names: key rows whose derived_from_run is among them are self-derived for this scoring and are excluded (reported)')
    ap.add_argument('--fable-export', help='D-35: path of a _nosec.txt copy of this output with every line naming a security_union==1 (or unknown) task removed; repro_cmd never appears in this output')
    ap.add_argument('--all-results', nargs='*', default=[str(RA / 'repro/*.jsonl'), str(RA / 'repro/judge_batches/*.jsonl')], help='D-59: results JSONL globs whose reward-1 repro records define repro_effort_min per task (all batches to date)')
    ap.add_argument('--plan', help='plan.json of the run (default: <judgement dir>/plan.json); its plan.prompt.file_sha256_16 is compared with the judges\' self-reported prompt_sha256_16 (they hash the FILE)')
    ap.add_argument('--overlay-tsv', help='ruling overlays: TSV task_id, tier, soundness, repro_cost, supporting_results_jsonl, supporting_label, ruling — applied to the verdict of record ONLY if that repro record has reward 1, rc 0, prep 0 (D4); prints overlays_applied / overlays_rejected')
    ap.add_argument('--review-carry-tsv', help='ruling file (task_id, mechanism, ruling): REVIEW-by-D4 ids whose mechanism is recorded — reported as review_carry and excluded from followup_needed')
    ap.add_argument('--followup-tsv', help='D-67(1): write the ids that still need a repro row (REVIEW tier, repro NONE/D4, no reward-1 record, or no key row) as task_id/tier/soundness/reason')
    ap.add_argument('--discard-ids-out', help='D-66/D-67(2): write the mechanical discard-candidate ids (one per line) for tools/discard_rejudge_prepare.py')
    ap.add_argument('--scan-tsv', help='D-89 static grader scan for this run (default: reviews/<run>_degenerate_graders_scan.tsv, run = results file stem minus batch_); joined as scan_fallback / scan_all_negative / scan_verifier_network')
    ap.add_argument('--per-task-export', help='D-55: _nosec TSV (task_id, tier, soundness, honest_fix_lines, repro_lines_judge, repro_lines_exec, no_gradient, no_gradient_marginal); security/unknown rows dropped')
    ap.add_argument('--priors-dir', help='D-54: directory holding bucket_<k>_priors.tsv (default: directory of the first judgement file); supporting_repro rewards are looked up there')
    ap.add_argument('--apply-revisions', action='store_true', help='overlay revised_after_priors {tier,soundness,verdict,confidence} from revision records onto the verdict of record BEFORE scoring (with-revisions score); default = first-line-only')
    ap.add_argument('--security-map', default=str(RA / 'reviews/audits/phase0/phase0_harness_facts.tsv'))
    ap.add_argument('--sample-labels', help='judge/<run>/sample_labels.json: {sample:{security:[[task_id, audit_v4_verdict, security_union, category]...], other:[...]}} — cross-tab of design category x judged labels x measured reward')
    a = ap.parse_args()
    tee = None
    if a.fable_export:
        tee = Tee(); sys.stdout = tee
    key = load_key(resolve(a.key))
    files, first, revisions = load_judgements(a.judgements)
    assert_full_ids(key, 'tournament_score: key task_ids'); assert_full_ids(first, 'tournament_score: judgement task_ids'); assert_full_ids((r.get('task_id') for r in revisions), 'tournament_score: revision task_ids')
    # prompt provenance: judges self-report sha256 of the prompt FILE they read; the plan recorded the file sha at prepare time
    plan_path = pathlib.Path(a.plan) if a.plan else pathlib.Path(files[0]).parent / 'plan.json'
    plan_prompt = {}
    if plan_path.exists():
        try:
            plan_prompt = json.loads(plan_path.read_text()).get('prompt') or {}
        except json.JSONDecodeError:
            plan_prompt = {}
    judged_shas = sorted({str(j.get('prompt_sha256_16')) for j in first.values()})
    plan_file_sha = plan_prompt.get('file_sha256_16')
    prompt_prov = {'plan': str(plan_path) if plan_path.exists() else None, 'plan_file_sha256_16': plan_file_sha, 'plan_content_sha256_16': plan_prompt.get('content_sha256_16'),
                   'plan_version': plan_prompt.get('version'), 'judged_file_sha256_16': judged_shas,
                   'match': (judged_shas == [plan_file_sha]) if plan_file_sha and judged_shas != ['None'] else 'not comparable (plan hash or judge self-report missing)'}
    overlays_applied, overlays_rejected = [], []; clean_r1 = set()
    if a.overlay_tsv:
        for o in csv.DictReader(open(a.overlay_tsv, newline=''), delimiter='\t'):
            tid = o['task_id']
            if tid not in first:
                continue
            ok = False
            sp = resolve(o['supporting_results_jsonl']) if o.get('supporting_results_jsonl') else None
            if sp and sp.exists():
                for line in sp.read_text().splitlines():
                    if not line.strip():
                        continue
                    r_ = json.loads(line)
                    if r_.get('task_id') == tid and r_.get('mode') == 'repro' and r_.get('label') == o.get('supporting_label') and str(r_.get('reward')) == '1' and r_.get('rc') == 0 and r_.get('prep_rc') in (0, None):
                        ok = True; break
            if ok:
                chg = {k: o[k] for k in ('tier', 'soundness', 'repro_cost') if o.get(k)}
                first[tid] = {**first[tid], **chg, 'overlay_ruling': o.get('ruling', ''), 'overlay_supporting': f"{o.get('supporting_results_jsonl')}:{o.get('supporting_label')}"}
                overlays_applied.append(tid); clean_r1.add(tid)          # the supporting record IS a clean reward-1 repro (D4) — no follow-up needed
            else:
                overlays_rejected.append(tid)
    applied = 0; rev_unsupported = []
    if a.apply_revisions:                        # post-priors layer (D-50: "near-circular on keyed tasks", never accuracy)
        pri_dir = pathlib.Path(a.priors_dir) if a.priors_dir else pathlib.Path(files[0]).parent
        phase3 = {}                              # task_id -> {priors phase3 key: reward} from bucket_<k>_priors.tsv (D-54 lookup)
        for pf in sorted(pri_dir.glob('bucket_*_priors.tsv')):
            for pr in csv.DictReader(pf.open(newline=''), delimiter='\t'):
                try:
                    phase3[pr['task_id']] = json.loads(pr.get('phase3_rewards_json') or '{}')
                except json.JSONDecodeError:
                    phase3[pr['task_id']] = {}
        for r in revisions:
            rv = r.get('revised_after_priors')
            if isinstance(rv, dict) and r.get('task_id') in first:
                tid = r['task_id']; base = first[tid]
                chg = {f: rv[f] for f in ('tier', 'soundness', 'verdict', 'confidence') if f in rv and rv[f] is not None}
                if not chg:
                    continue
                moves_to_non_pass = base.get('tier') == 'PASS' and chg.get('tier') not in (None, 'PASS')
                if moves_to_non_pass:            # D-54: needs a supporting_repro whose measured reward is 1, else REVIEW
                    key_ = rv.get('supporting_repro')
                    rw = str(phase3.get(tid, {}).get(key_, '')) if key_ else ''
                    if rw != '1':
                        chg['tier'] = 'REVIEW'; rev_unsupported.append(tid)
                first[tid] = {**base, **chg, 'revision_supported': (tid not in rev_unsupported) if moves_to_non_pass else None}; applied += 1
    notes = dict(n.split('=', 1) for n in a.bucket_note)
    # notes are keyed by the bucket integer; record labels 'V7-bucket-k' / 'bucket_k' / k all normalise to k
    if a.results_jsonl:
        print('reward table ->', write_reward_table(resolve(a.results_jsonl)))

    # ---- scoring against the key
    tp = fp = fn = tn = 0; tier_hit = tier_miss = 0; simple_hit = simple_total = 0
    otp = ofp = ofn = otn = 0                    # OR-positive class: (soundness != sound) OR (tier != PASS)
    unmeasured, per_bucket, per_task, self_derived, pending = [], {}, [], [], []
    d4_review = set()
    after = {}                                   # task_id -> revised tier (revision lines applied ONLY here)
    for r in revisions:
        rv = r.get('revised_after_priors') or {}
        if isinstance(rv, dict) and rv.get('tier'):
            after.setdefault(r['task_id'], rv['tier'])
    for tid, j in first.items():
        k = key.get(tid)
        raw = str(j.get('bucket', j.get('agent', '?')))
        mb = re.search(r'(\d+)\s*$', raw); b = mb.group(1) if mb else raw       # 'V7-bucket-2' / 'bucket_2' / 2 -> '2'
        pb = per_bucket.setdefault(b, {'n': 0, 'tier_hit': 0, 'scored': 0, 'model': j.get('model', '?')})
        pb['n'] += 1
        excl = {x.strip() for x in (a.exclude_derived_from or '').split(',') if x.strip()}
        if k and k['derived_from_run'] in excl:
            self_derived.append(tid); per_task.append({'task_id': tid, 'bucket': b, 'judge_tier': j.get('tier'), 'judge_soundness': j.get('soundness'), 'judge_cost': j.get('repro_cost'), 'expected_soundness': k['soundness'], 'expected_tiers': sorted(k['tiers']), 'judge_repro_reward': None, 'verdict': 'self-derived, excluded'}); continue
        if k and k['soundness_set'] == {'pending'}:
            pending.append(tid); per_task.append({'task_id': tid, 'bucket': b, 'judge_tier': j.get('tier'), 'judge_soundness': j.get('soundness'), 'judge_cost': j.get('repro_cost'), 'expected_soundness': 'pending', 'expected_tiers': sorted(k['tiers']), 'judge_repro_reward': None, 'verdict': 'pending, excluded'}); continue
        if not k:
            unmeasured.append(tid); per_task.append({'task_id': tid, 'bucket': b, 'judge_tier': j.get('tier'), 'judge_soundness': j.get('soundness'), 'judge_cost': j.get('repro_cost'), 'expected_soundness': None, 'expected_tiers': None, 'judge_repro_reward': None, 'verdict': 'unmeasured'}); continue
        pb['scored'] += 1
        exp_pos = k['soundness_set'] != {'sound'} and 'pending' not in k['soundness_set']; jud_pos = j.get('soundness') != 'sound'
        sound_ok = (j.get('soundness') in k['soundness_set']) or (k['soundness_set'] == {'pending'})
        tp += exp_pos and jud_pos; fp += (not exp_pos) and jud_pos; fn += exp_pos and not jud_pos; tn += (not exp_pos) and not jud_pos
        hit = j.get('tier') in k['tiers']; tier_hit += hit; tier_miss += not hit; pb['tier_hit'] += hit
        rub = str(j.get('rubric_version') or j.get('rubric') or '')
        ivm_caveat = ('v3.7' in rub and 'D-21' in k['source'] and j.get('soundness') == 'sound')   # v3.7 labelled unenforced requirements sound: expected-by-construction
        verdict = ('unmeasured' if not k else 'hit' if (hit and sound_ok) else 'partial' if (hit or sound_ok) else 'miss')
        if ivm_caveat: verdict += ' (v3.7 IVM caveat: expected-by-construction, not judge error)'
        per_task.append({'task_id': tid, 'bucket': b, 'judge_tier': j.get('tier'), 'judge_soundness': j.get('soundness'), 'judge_cost': j.get('repro_cost'),
                         'expected_soundness': k['soundness'], 'expected_tiers': sorted(k['tiers']), 'judge_repro_reward': None, 'verdict': verdict,
                         'repro_lines': j.get('repro_lines'), 'honest_fix_lines': j.get('honest_fix_lines'), 'expected_no_gradient': k['expected_no_gradient']})
        exp_or = exp_pos or (k['tiers'] != {'PASS'}); jud_or = jud_pos or (j.get('tier') != 'PASS')
        otp += exp_or and jud_or; ofp += (not exp_or) and jud_or; ofn += exp_or and not jud_or; otn += (not exp_or) and not jud_or
        if k['soundness'] == 'accepts-wrong' and k['tiers'] <= {'REPAIR', 'REJECT-PROVEN'}:
            simple_total += 1
            simple_hit += (j.get('soundness') == 'accepts-wrong' and j.get('repro_cost') == 'simple' and j.get('tier') in ('REPAIR', 'REJECT-PROVEN'))
    prec = tp / (tp + fp) if tp + fp else None; rec = tp / (tp + fn) if tp + fn else None
    print(json.dumps({'files': len(files), 'judge_records': len(first), 'revision_records': len(revisions),
                      'layer': 'post-priors (near-circular on keyed tasks)' if a.apply_revisions else 'judge-only',
                      'overlays': {'applied': overlays_applied, 'rejected_no_clean_reward1': overlays_rejected} if a.overlay_tsv else None,
                      'revisions_applied': applied if a.apply_revisions else 'no (first-line-only)',
                      'revision_unsupported': {'count': len(rev_unsupported), 'ids': sorted(rev_unsupported)} if a.apply_revisions else None,
                      'rubric_version_census': {'judgements': dict(collections.Counter(str(j.get('rubric_version')) for j in first.values())), 'revisions': dict(collections.Counter(str(r.get('rubric_version')) for r in revisions))},
                      'prompt_sha256_16_census': dict(collections.Counter(str(j.get('prompt_sha256_16')) for j in first.values())),
                      'prompt_provenance': prompt_prov,
                      'rubric': (lambda j: j.get('rubric_version') or j.get('rubric'))(next(iter(first.values()))) if first else None,
                      'scored_against_key': tp + fp + fn + tn, 'unmeasured_not_scored': len(unmeasured),
                      'self_derived_excluded': sorted(self_derived), 'pending_excluded': sorted(pending),
                      'soundness': {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'precision': prec, 'recall': rec},
                      'tier_in_expected_set': {'hit': tier_hit, 'miss': tier_miss},
                      'or_positive_(soundness!=sound OR tier!=PASS)': {'tp': otp, 'fp': ofp, 'fn': ofn, 'tn': otn,
                                                                       'precision': (otp / (otp + ofp)) if otp + ofp else None,
                                                                       'recall': (otp / (otp + ofn)) if otp + ofn else None},
                      'after_priors': {'tasks_with_revised_tier': len(after),
                                       'revised_tier_in_expected_set': sum(1 for tid, tr in after.items() if tid in key and tr in key[tid]['tiers'])},
                      'simple_control_rule': {'hit': simple_hit, 'total': simple_total}}, indent=1))

    exec_lines = {}                              # task_id -> this run's executor components (D-51/D-60): min repro_effort record
    rw_by_task = {}; lint_warn = {}; rr_confirmed = set(); prep_probe_nonfatal = set(); d97 = {'measured': 0, 'exploit7': [], 'unexpected_reward1': []}
    effort_min = {}; n_all_records = 0           # D-59: min repro_effort over ALL measured reward-1 repros per task, every results file
    clean_r1_any = set()                         # tasks with a clean reward-1 repro (rc 0, prep 0) in ANY results file (follow-up batches included)
    clean_r0_any = set()                         # tasks with a reward-0 repro whose test executed under clean prep, ANY file (D-70 pool for rejects-right)
    for pat in a.all_results:
        for f in sorted(glob.glob(pat)):
            for line in pathlib.Path(f).read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get('mode') == 'repro' and r.get('repro_cmd') and str(r.get('reward')) == '0' and r.get('rc') is not None and r.get('prep_rc') in (0, None):
                    clean_r0_any.add(r['task_id'])
                if r.get('mode') == 'repro' and r.get('repro_cmd') and str(r.get('reward')) == '1':   # inclusion rule: mode repro ONLY (gold/gold_patch/solution records are not shortcut repros)
                    e = analyze(r['repro_cmd'])['repro_effort']; n_all_records += 1
                    if r.get('rc') == 0 and r.get('prep_rc') in (0, None): clean_r1_any.add(r['task_id'])
                    effort_min[r['task_id']] = min(effort_min.get(r['task_id'], e), e)
    if a.results_jsonl:
        for line in resolve(a.results_jsonl).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get('mode') == 'repro':
                    rw_by_task.setdefault(r['task_id'], []).append(r.get('reward') if r.get('reward') not in (None, '') else 'NONE')
                    if str(r.get('reward')) == '1' and r.get('rc') == 0 and r.get('prep_rc') in (0, None):
                        clean_r1.add(r['task_id'])                          # a reward-1 repro that ran cleanly (rc 0, prep 0)
                    elif str(r.get('reward')) == '1' and r.get('rc') == 0 and r.get('prep_rc') == 1:
                        clean_r1.add(r['task_id']); prep_probe_nonfatal.add(r['task_id'])   # ruling 2026-09-02: prep probed agent-time-absent files; exploit ran, test passed -> VALID
                    st = analyze(r.get('repro_cmd'))                 # D-51/D-60: components of the command actually run (min-effort record per task)
                    if r.get('exploit7_observed') is not None:                 # D-97 fields present (torchtitan mode, second read ran)
                        d97['measured'] += 1
                        if r.get('exploit7_observed'): d97['exploit7'].append(r['task_id'])
                    if r.get('unexpected_reward1'): d97['unexpected_reward1'].append(r['task_id'])
                    lw = lint(r.get('repro_cmd'))
                    if lw: lint_warn.setdefault(r['task_id'], set()).add(lw)
                    if isinstance(st['repro_effort'], int) and (r['task_id'] not in exec_lines or st['repro_effort'] < exec_lines[r['task_id']]['repro_effort']):
                        exec_lines[r['task_id']] = st
                    is_rr = str(r.get('label', '')).startswith('rr:')          # rejects-right row: expected reward 0
                    if is_rr and str(r.get('reward')) == '0' and r.get('rc') is not None and r.get('prep_rc') in (0, None):
                        rr_confirmed.add(r['task_id'])       # D-70 (amended): CONFIRMED only when the test executed (rc recorded) under a clean prep; prep_rc != 0 decides nothing -> follow-up
                    elif r['task_id'] in prep_probe_nonfatal and str(r.get('reward')) == '1':
                        pass                                 # valid reward-1 under a non-fatal prep probe (annotated, not a non-run)
                    elif (r.get('prep_rc') not in (0, None)) or r.get('rc') == 124 or r.get('reward') in (None, ''):   # did not run / timed out / killed without rc
                        d4_review.add(r['task_id'])          # repro did not run / timed out -> REVIEW by D4 (separate from misses)
        for row in per_task:
            row['judge_repro_reward'] = ','.join(rw_by_task.get(row['task_id'], [])) or None
            if row['task_id'] in d4_review and row['verdict'] not in ('unmeasured',) and 'excluded' not in row['verdict']:
                row['verdict'] = 'REVIEW-by-D4 (repro did not run or timed out) — ' + row['verdict']
    # D-41/D-51/D-59/D-60: no_gradient = repro_effort_min >= honest_fix_lines, where repro_effort = masked statements + payload lines (tools/repro_stats.py)
    #   and the min runs over ALL measured reward-1 repros for the task across every results file (tier unchanged) -> rl_relevance column
    # D-55: no_gradient_marginal = repro_effort_min / honest_fix_lines >= 0.67 (annotation only, never a tier input)
    ng_marginal = 0; ng_marginal_ids = []; disagree = []; disagree_stmts = []
    cov_labels, cov_stats = repro_coverage(); min_below = []
    scan_path = a.scan_tsv or (str(RA / 'reviews' / (pathlib.Path(a.results_jsonl).stem.removeprefix('batch_') + '_degenerate_graders_scan.tsv')) if a.results_jsonl else None)
    scan = load_scan(scan_path)
    ng_counts = {'hole_cheaper_than_fix': 0, 'no_gradient': 0, 'medium_unenforced': 0, 'unmeasured': 0, 'n/a': 0}; ng_hits = ng_total = 0
    for row in per_task:
        j = first[row['task_id']]
        st = exec_lines.get(row['task_id']) or {}
        rlj, hf = j.get('repro_lines'), j.get('honest_fix_lines')
        row['repro_lines_judge'] = rlj
        row['repro_lines_exec_thisrun'] = st.get('repro_statements') if isinstance(st.get('repro_statements'), int) else None
        row['payload_lines_thisrun'] = st.get('payload_lines') if isinstance(st.get('payload_lines'), int) else None
        row['repro_effort_thisrun'] = st.get('repro_effort') if isinstance(st.get('repro_effort'), int) else None
        row['repro_effort_min'] = effort_min.get(row['task_id'])            # D-59: across all batches (None if never measured at reward 1)
        row['n_reward1_repros'] = len(cov_labels.get(row['task_id'], ()))     # probe count behind the min (distinct labels, base tables)
        row['scan_fallback'], row['scan_all_negative'], row['scan_verifier_network'] = scan.get(row['task_id'], (None, None, None))
        if isinstance(row['repro_effort_min'], int) and isinstance(row['repro_effort_thisrun'], int) and row['repro_effort_min'] < row['repro_effort_thisrun']:
            min_below.append(row['task_id'])
        rx = row['repro_lines_exec_thisrun']; ex = row['repro_effort_thisrun']
        if isinstance(rlj, int) and isinstance(ex, int) and rlj > 0 and ex > 0 and max(rlj, ex) / min(rlj, ex) > 2:
            disagree.append(row['task_id'])                    # judge self-count vs executor repro_effort differ by > 2x — EVERY row incl. PASS (D-60 item 3)
        if isinstance(rlj, int) and isinstance(rx, int) and rlj > 0 and rx > 0 and max(rlj, rx) / min(rlj, rx) > 2:
            disagree_stmts.append(row['task_id'])              # same screen against masked statements only (judges count statements; the gap is payload)
        em = row['repro_effort_min']
        both = isinstance(em, int) and isinstance(hf, int) and hf > 0
        row['no_gradient'] = (em >= hf) if both else None                     # annotation for EVERY row with both inputs, regardless of tier/soundness
        row['no_gradient_marginal'] = (em / hf >= 0.67) if both else None
        if j.get('tier') == 'PASS':
            row['rl_relevance'] = None; continue
        df = j.get('defects') or {}
        unenf = bool(df.get('unenforced_requirement')) or ('unenforced' in str(j.get('repro_expected') or '').lower())
        exp_tok = str(j.get('repro_expected') or '').lower()
        if not (j.get('soundness') == 'accepts-wrong' or 'unenforced' in exp_tok or exp_tok.startswith('reward==1')):   # gate on the FORMAT, not the defects flag
            row['rl_relevance'] = 'n/a'                   # rejects-right / unstable: repro_lines is a faithful solution, not a shortcut
            ng_counts['n/a'] = ng_counts.get('n/a', 0) + 1; continue
        rl = row['repro_effort_min']                            # D-59/D-60: min repro_effort over all measured reward-1 repros
        if row['no_gradient_marginal']:
            ng_marginal += 1; ng_marginal_ids.append(row['task_id'])
        if j.get('repro_cost') == 'medium' and unenf:
            row['rl_relevance'] = 'medium_unenforced'          # defender pattern 4: MEDIUM repro demoted only via the D-30 clause
        elif isinstance(rl, int) and isinstance(hf, int):
            row['rl_relevance'] = 'no_gradient' if rl >= hf else 'hole_cheaper_than_fix'
        else:
            row['rl_relevance'] = 'unmeasured'                 # no reward-1 executor record anywhere (or no honest_fix_lines): D-51 refuses the judge's self-count
        ng_counts[row['rl_relevance']] = ng_counts.get(row['rl_relevance'], 0) + 1
        if row.get('expected_no_gradient'):
            ng_total += 1; ng_hits += (row['rl_relevance'] == 'no_gradient')
    print(json.dumps({'rl_relevance': ng_counts, 'expected_no_gradient_hits': f'{ng_hits}/{ng_total}', 'no_gradient_marginal_0.67': {'count': ng_marginal, 'ids': ng_marginal_ids},
                      'repro_lines_judge_vs_exec_effort_gt2x': {'count': len(disagree), 'ids': disagree}, 'repro_lines_judge_vs_exec_statements_gt2x': {'count': len(disagree_stmts), 'ids': disagree_stmts},
                      'exec_counts_available_tasks': len(exec_lines),
                      'effort_min_tasks': len(effort_min), 'effort_min_source_records': n_all_records}))
    if scan:
        print(json.dumps({'degenerate_grader_scan': {'file': scan_path, 'tasks_scanned': len(scan), 'judged_and_scanned': sum(1 for r_ in per_task if r_.get('scan_fallback') is not None),
                          'fallback': sorted(r_['task_id'] for r_ in per_task if r_.get('scan_fallback')), 'all_negative': sorted(r_['task_id'] for r_ in per_task if r_.get('scan_all_negative')),
                          'verifier_network': sorted(r_['task_id'] for r_ in per_task if r_.get('scan_verifier_network'))}}))
    print(json.dumps({'repro_coverage': {**cov_stats, 'tasks_where_min_below_thisrun': {'count': len(min_below), 'ids': min_below},
                      'caveat': 'repro_effort_min is downward-biased by probe coverage, not the task: use per task with n_reward1_repros; never roll no_gradient into a corpus rate without the probe-count denominator'}}))
    if a.per_task_export:                       # D-55 defender export: integers/labels only, security/unknown rows dropped
        sec = {}
        for r_ in csv.DictReader(open(a.security_map, newline=''), delimiter='\t'):
            sec[r_['task_id']] = r_.get('security_union', '')
        cols = ['task_id', 'tier', 'soundness', 'honest_fix_lines', 'repro_lines_judge', 'repro_lines_exec_thisrun', 'payload_lines_thisrun', 'repro_effort_thisrun', 'repro_effort_min', 'n_reward1_repros', 'no_gradient', 'no_gradient_marginal', 'scan_fallback', 'scan_all_negative', 'scan_verifier_network']
        kept = dropped = 0
        with open(a.per_task_export, 'w', newline='') as fh:
            w = csv.writer(fh, delimiter='\t', lineterminator='\n'); w.writerow(cols)
            for row in sorted(per_task, key=lambda r: r['task_id']):
                if sec.get(row['task_id'], '') != '0':
                    dropped += 1; continue
                j = first[row['task_id']]
                w.writerow([row['task_id'], j.get('tier'), j.get('soundness'), j.get('honest_fix_lines'), row.get('repro_lines_judge'), row.get('repro_lines_exec_thisrun'), row.get('payload_lines_thisrun'), row.get('repro_effort_thisrun'), row.get('repro_effort_min'), row.get('n_reward1_repros'),
                            row.get('no_gradient'), row.get('no_gradient_marginal'), row.get('scan_fallback'), row.get('scan_all_negative'), row.get('scan_verifier_network')])
                kept += 1
        print(f'per-task export -> {a.per_task_export} ({kept} rows, {dropped} security/unknown rows dropped)')
    # reviewer item: PASS with soundness accepts-wrong AND a repro attached — a tier/soundness conflict (count + ids only; no rule change)
    conflict = sorted(t for t, j in first.items() if j.get('tier') == 'PASS' and j.get('soundness') == 'accepts-wrong' and bool(j.get('repro_cmd')))
    print(json.dumps({'tier_soundness_conflict': {'count': len(conflict), 'ids': conflict}}))
    # D-58: REJECT-PROVEN is new by construction under C1 (v3.16 baseline 0/28) — each one is read individually
    rp = sorted(t for t, j in first.items() if j.get('tier') == 'REJECT-PROVEN')
    print(json.dumps({'reject_proven_count': {'count': len(rp), 'ids': rp, 'baseline_v316': '0/28'}}))
    # D-57: C2 enforcement — sound + verifier_expected_answer_from hardcoded-literal + soundness_probe null (tier unaffected)
    c2 = sorted(t for t, j in first.items() if j.get('soundness') == 'sound' and str(j.get('verifier_expected_answer_from') or '').strip().lower() == 'hardcoded-literal' and not j.get('soundness_probe'))
    print(json.dumps({'c2_violation': {'count': len(c2), 'ids': c2}}))
    if a.results_jsonl and (d97['measured'] or d97['unexpected_reward1']):
        print(json.dumps({'d97': {'second_read_measured': d97['measured'], 'exploit7_observed': {'count': len(d97['exploit7']), 'ids': sorted(d97['exploit7'])},
                          'unexpected_reward1': {'count': len(d97['unexpected_reward1']), 'ids': sorted(d97['unexpected_reward1'])},
                          'note': "exploit7_observed is a LOWER BOUND on training's exposure: a rewrite landing before t0 is invisible to the t0/tdelta difference, and training's read window (two Daytona RPCs, grading.py:263/:269) is wider than the executor's"}}))
    # D-78 two-layer summary: gate2-harness verdict (key expected tiers / judge tier) vs training-harness verdict where the key has one
    hv = {t: k['harness_verdict_training'] for t, k in key.items() if k.get('harness_verdict_training')}
    hd = collections.Counter(k['harness_dependence'] or 'blank' for k in key.values())
    layer = []
    for t in sorted(hv):
        g2 = first[t].get('tier') if t in first else None
        layer.append({'task_id': t, 'gate2_judge_tier': g2, 'key_tiers': sorted(key[t]['tiers']), 'training_verdict': hv[t], 'dependence': key[t]['harness_dependence'] or None,
                      'changes_under_training': (g2 is not None and hv[t] not in ('unknown', '') and hv[t] != g2)})
    print(json.dumps({'harness_two_layer': {'key_rows_with_training_verdict': len(hv), 'dependence_census': dict(hd),
                      'judged_here_with_training_verdict': sum(1 for r in layer if r['gate2_judge_tier'] is not None),
                      'changes_under_training': [r['task_id'] for r in layer if r['changes_under_training']], 'rows': layer}}))
    # D-68: key rows carrying two cost calls (reviewer vs defender) are flagged; the recorded call is the first token
    disputed = sorted(t for t, k in key.items() if '|' in k.get('cost', ''))
    cost_cmp = {t: (k['cost'].split('|')[0], (first[t].get('repro_cost') or '').lower()) for t, k in key.items() if t in first and k.get('cost') and k['cost'] not in ('n/a', '')}
    print(json.dumps({'cost_disputed': {'count': len(disputed), 'ids': disputed, 'judged_this_run': [t for t in disputed if t in first]},
                      'cost_key_vs_judge': {'compared': len(cost_cmp), 'agree': sum(1 for a_, b_ in cost_cmp.values() if a_ == b_), 'disagree_ids': sorted(t for t, (a_, b_) in cost_cmp.items() if a_ != b_)}}))
    # D-66(4)/D-67(2): mechanical discard candidates — ALL of: tier REJECT-PROVEN, a reward-1 repro that ran cleanly (rc 0, prep 0),
    # D8 cost simple, no bounded fix claimed (verifier_fix empty). Anything else REJECT-PROVEN is listed with its rejection reasons -> REVIEW.
    disc_ok, disc_no = [], []
    for t in rp:
        j = first[t]; why = []
        if t not in clean_r1:
            why.append('repro reward != 1 (or rc/prep != 0, or no repro record)' + (f" [rewards {','.join(rw_by_task.get(t, []))}]" if rw_by_task.get(t) else ''))
        if (j.get('repro_cost') or '').lower() != 'simple':
            why.append(f"cost {j.get('repro_cost')} != simple")
        if (j.get('verifier_fix') or '').strip():
            why.append('a bounded fix is claimed (verifier_fix non-empty)')
        (disc_ok if not why else disc_no).append(t if not why else {'task_id': t, 'reasons': why})
    print(json.dumps({'discard_candidates': {'count': len(disc_ok), 'ids': disc_ok, 'reject_proven_not_candidate_to_REVIEW': disc_no,
                      'rule': 'REJECT-PROVEN AND clean reward-1 repro AND cost simple AND no verifier_fix; final discard also needs the candidate-only second judge (tools/discard_rejudge_prepare.py), the reviewer read and the defender concession'}}))
    if a.discard_ids_out:
        pathlib.Path(a.discard_ids_out).write_text(''.join(t + '\n' for t in disc_ok)); print(f'discard ids -> {a.discard_ids_out} ({len(disc_ok)})')
    # D-67(1): no pile of undecided — REVIEW ids and ids whose measurement is missing get a follow-up repro row
    for t, j in first.items():                   # D-70 over ALL files: a rejects-right call with an executed reward-0 repro under clean prep anywhere is confirmed
        if j.get('soundness') == 'rejects-right' and t in clean_r0_any:
            rr_confirmed.add(t); d4_review.discard(t)
    review_ids = sorted(t for t, j in first.items() if j.get('tier') == 'REVIEW')
    carry = {}
    if a.review_carry_tsv:
        for o in csv.DictReader(open(a.review_carry_tsv, newline=''), delimiter='\t'):
            if o['task_id'] in first: carry[o['task_id']] = o.get('mechanism', '')
    followup = {}
    for t, j in first.items():
        why = []
        if j.get('tier') == 'REVIEW': why.append('tier REVIEW')
        if t in d4_review: why.append('repro did not run / timed out (D4)')
        if j.get('tier') != 'PASS' and a.results_jsonl and t not in clean_r1 and t not in clean_r1_any and t not in d4_review and t not in rr_confirmed: why.append('no clean reward-1 repro in any batch')
        if t in d4_review and t in clean_r1_any: why = [w for w in why if not w.startswith('repro did not run')]   # a later clean reward-1 (follow-up batch) supersedes this run's non-run
        if t in rr_confirmed: why = [w for w in why if not w.startswith('repro did not run') and not w.startswith('no clean reward-1')]
        # (no key row is NOT a follow-up reason: the full run's rows are unkeyed by design — they stay in unmeasured_ids)
        if why and t not in carry: followup[t] = why
    print(json.dumps({'review_count': {'count': len(review_ids), 'ids': review_ids}, 'unmeasured_ids': sorted(unmeasured),
                      'followup_needed': {'count': len(followup), 'ids': sorted(followup)},
                      'cleared_by_followup_batches': sorted(t for t, j in first.items() if j.get('tier') != 'PASS' and t not in clean_r1 and t in clean_r1_any),
                      'rejects_right_confirmed_D70': {'count': len(rr_confirmed), 'ids': sorted(rr_confirmed)},
                      'prep_probe_nonfatal_valid_reward1': {'count': len(prep_probe_nonfatal), 'ids': sorted(prep_probe_nonfatal)},
                      'review_carry': {'count': len(carry), 'ids': sorted(carry)},
                      'repro_lint_warn': {'count': len(lint_warn), 'ids': sorted(lint_warn), 'codes': {t: sorted(v) for t, v in sorted(lint_warn.items())}}}))
    if a.followup_tsv:
        with open(a.followup_tsv, 'w', newline='') as fh:
            w = csv.writer(fh, delimiter='\t', lineterminator='\n'); w.writerow(['task_id', 'tier', 'soundness', 'reason'])
            for t in sorted(followup):
                w.writerow([t, first[t].get('tier'), first[t].get('soundness'), '; '.join(followup[t])])
        print(f'follow-up list -> {a.followup_tsv} ({len(followup)} ids; the reviewer writes one repro row per id)')
        secmap = {r_['task_id']: r_.get('security_union', '') for r_ in csv.DictReader(open(a.security_map, newline=''), delimiter='\t')}
        ns = pathlib.Path(a.followup_tsv).with_name(pathlib.Path(a.followup_tsv).stem + '_nosec.tsv')
        with ns.open('w', newline='') as fh:
            w = csv.writer(fh, delimiter='\t', lineterminator='\n'); w.writerow(['task_id', 'tier', 'soundness', 'reason'])
            kept = 0
            for t in sorted(followup):
                if secmap.get(t, '') == '0':
                    w.writerow([t, first[t].get('tier'), first[t].get('soundness'), '; '.join(followup[t])]); kept += 1
        print(f'follow-up _nosec -> {ns} ({kept} rows; security/unknown dropped)')
    print('per-task: task_id | bucket | judge tier/soundness/cost | expected soundness / tiers | judge repro reward | rl_relevance (n_reward1_repros) | verdict')
    for row in sorted(per_task, key=lambda r: (r['bucket'], r['task_id'])):
        print(f"  {row['task_id']} | b{row['bucket']} | {row['judge_tier']}/{row['judge_soundness']}/{row['judge_cost']} | {row['expected_soundness']} / {row['expected_tiers']} | {row['judge_repro_reward']} | {row.get('rl_relevance')} (n={row.get('n_reward1_repros', 0)}) | {row['verdict']}")
    # ---- hard gates, derived from the key
    # gates use the SAME exclusions as the metrics: self-derived rows (for the run being scored) and pending rows never enter
    gate_key = {t: k for t, k in key.items()
                if k['derived_from_run'] not in {x.strip() for x in (a.exclude_derived_from or '').split(',') if x.strip()} and k['soundness_set'] != {'pending'}}
    g1 = {t: ((first[t].get('tier') != 'PASS') and bool(first[t].get('repro_cmd'))) if t in first else None
           for t, k in gate_key.items() if 'accepts-wrong' in k['soundness_set'] and k['tiers'] <= {'REPAIR', 'REJECT-PROVEN'}}
    g2 = {t: (first[t].get('soundness') != 'unstable') if t in first else None
           for t, k in gate_key.items() if k['cleared_by_probe']}
    print(f'gates built from {len(gate_key)} key rows ({len(key) - len(gate_key)} self-derived/pending rows excluded)')

    def gate(name, g):
        judged = {t: v for t, v in g.items() if v is not None}
        ok = all(judged.values()) if judged else None
        state = 'PASS' if ok else ('FAIL' if ok is False else 'not judged')
        print(f'gate {name}: {state} — {sum(1 for v in judged.values() if v)}/{len(judged)} judged, {len(g) - len(judged)} not in this run; failures: {sorted(t for t, v in judged.items() if not v)}')
    gate('accepts-wrong controls non-PASS with repro', g1)
    gate('probe-cleared controls not called unstable', g2)
    g3 = {t: (first[t].get('tier') == 'PASS') if t in first else None for t in PASS_CONTROLS_D62}   # D-62: tier == PASS ONLY; c2_violation is never counted here
    gate('over-rejection floor: 4 PASS controls tier==PASS (D-62; c2_violation reported separately, not counted)', g3)

    # ---- per bucket
    for b in sorted(per_bucket):
        pb = per_bucket[b]
        print(f"bucket {b}: model={pb['model']} judgements={pb['n']} scored={pb['scored']} tier_hit={pb['tier_hit']}{('  NOTE: ' + notes[b]) if b in notes else ''}")
    revised = [r for r in revisions if r.get('revised_after_priors')]
    print(f"revisions: {len(revisions)} records, {len(revised)} changed something ({'APPLIED to the scored verdicts' if a.apply_revisions else 'reported, not scored'})")
    if unmeasured:
        print('unmeasured (not in the answer key; not scored):', sorted(unmeasured))

    # ---- sample-design cross-tab (labels only): category x judged (tier, soundness) x measured repro reward
    if a.sample_labels:
        sl = json.load(resolve(a.sample_labels).open())
        cats = {}
        for grp, rows in (sl.get('sample') or {}).items():
            for row in rows:
                cats[row[0]] = {'group': grp, 'audit_v4': row[1], 'sec': row[2], 'category': row[3]}
        rewards = {}
        if a.results_jsonl:
            for line in resolve(a.results_jsonl).read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r.get('mode') not in ('empty', 'flake'):
                        rewards.setdefault(r['task_id'], []).append(r.get('reward') if r.get('reward') not in (None, '') else 'NONE')
        table = {}
        for tid, meta in cats.items():
            j = first.get(tid)
            cell = table.setdefault(meta['category'], {'n_design': 0, 'judged': 0, 'tiers': {}, 'soundness': {}, 'repro_rewards': {}})
            cell['n_design'] += 1
            if not j:
                continue
            cell['judged'] += 1
            cell['tiers'][j.get('tier')] = cell['tiers'].get(j.get('tier'), 0) + 1
            cell['soundness'][j.get('soundness')] = cell['soundness'].get(j.get('soundness'), 0) + 1
            for rw in rewards.get(tid, []):
                cell['repro_rewards'][rw] = cell['repro_rewards'].get(rw, 0) + 1
        print(json.dumps({'sample_design_crosstab': table,
                          'note': 'category = the run designer\'s label (sample_labels.json), not the answer key; repro_rewards = measured phase-3 rewards of the judge\'s own repro rows'}, indent=1))

    # ---- D-22 IVM lead recall (leads, not truth)
    ivm_path = pathlib.Path(a.ivm) if pathlib.Path(a.ivm).exists() else (RA / a.ivm if (RA / a.ivm).exists() else None)
    if ivm_path:
        pairs = list(csv.DictReader(ivm_path.open(newline=''), delimiter='\t'))
        by_task = {}
        for r in pairs:
            by_task.setdefault(r['task_id'], []).append(r)

        def flagged(j):
            ch = j.get('checks') or {}; df = j.get('defects') or {}
            return bool(df.get('unenforced_requirement')) or (ch.get('unenforced_requirement') not in (None, '', False)) or (j.get('unenforced_cosmetic') not in (None, '', False))
        judged = {t: j for t, j in first.items() if t in by_task}
        hits = {t for t, j in judged.items() if flagged(j)}
        v2 = sum(1 for r in pairs if 'scan_version=2' in (r.get('note') or '') or r.get('scan_version') == '2')
        print(json.dumps({'ivm_lead_recall': {
            'pairs_total': len(pairs), 'tasks_total': len(by_task), 'scan_version': f'v1+v2 (wrapper-stripped); v2 rows={v2}',
            'tasks_judged': len(judged), 'tasks_flagged': len(hits),
            'pairs_judged': sum(len(by_task[t]) for t in judged), 'pairs_flagged': sum(len(by_task[t]) for t in hits),
            'misses': sorted(t for t in judged if t not in hits),
            'note': 'leads, not truth (D-22): an unflagged pair is a candidate miss, not a confirmed defect. '
                    'Scanner blind spots (F-18): 10 clause tokens, instruction.md only, trainable non-security slice.'}}, indent=1))
    if tee is not None:
        sys.stdout = tee.orig
        fable_export(''.join(tee.buf), a.fable_export, a.security_map)


if __name__ == '__main__':
    main()
