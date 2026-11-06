"""Item 8/2 by hint level. args: grades_glob out_suffix. Level of a version = max over easier rewrites on its path."""
import json, os, glob, sys
import pandas as pd
ORDER = ['0', '1', '2', '3', '3a', '3b']
gr = {}
for f in sorted(glob.glob(sys.argv[1])):
    for l in open(f):
        g = json.loads(l); gr[g['id']] = str(g['level'])
suf = sys.argv[2]
meta = '1030c/meta'; path = {}
for task in os.listdir(meta):
    p = f'{meta}/{task}/lineage.jsonl'
    if not os.path.exists(p): continue
    steps = {0: []}
    for l in open(p):
        d = json.loads(l)
        if d.get('event') == 'fold':
            name = os.path.basename(d['rewrite'])
            lvl = gr.get(f'{task}@{name}') if name.endswith('easier') else None
            steps[d['to_rev']] = steps.get(d['from_rev'], []) + [lvl]
    for r, v in steps.items(): path[(task, r)] = v
g = pd.read_csv('analysis/groups_1030c.csv').dropna(subset=['train_step', 'solved', 'rev'])
g = g[(g.solved > 0) & (g.solved < g.total)].copy()
g['path'] = [path.get((a, int(b))) for a, b in zip(g.task, g.rev)]
g = g[g.path.notna()]
def lab(p):
    ls = [x for x in p if x is not None]
    if not p: return 'original'
    if not ls: return 'harder-only'
    return 'L' + max(ls, key=ORDER.index)
g['lvl'] = g.path.apply(lab)
g['bin'] = (g.train_step // 50) * 50
t = pd.crosstab(g.bin, g.lvl, normalize='index').round(3)
t = t[[c for c in ['original', 'harder-only'] + ['L' + o for o in ORDER] if c in t.columns]]
t['n'] = g.groupby('bin').size()
print(t.to_string()); t.to_csv(f'figs/item8_by_hint_level_{suf}.csv')
r = pd.read_csv('figs/item2_rows.csv'); r = r[(r.job == 'easier') & (r.arm == 'accepted')]
ids = {}
for f in glob.glob(f'{meta}/*/rewrites/*easier/rewrite.json'):
    d = json.load(open(f))
    if d.get('status') == 'accepted' and d.get('signal'):
        ids[(d['task'], int(d['signal'].rsplit('--g', 1)[1]))] = f"{d['task']}@{os.path.basename(os.path.dirname(f))}"
r['lvl'] = [gr.get(ids.get((a, int(b)))) for a, b in zip(r.task, r.signal_group)]
print(r.groupby('lvl')[['m1', 'p1', 'p2', 'p3']].agg(['mean', 'count']).round(2).to_string())
print('accepted easier by level:', pd.Series(gr).value_counts().sort_index().to_dict())
