import json, pathlib, shutil, collections
out=pathlib.Path('data/tmax-audit-v4/rounds'); out.mkdir(parents=True, exist_ok=True)

SRC=[  # (来源, 归档名, 轮次标签, 说明)
 ('/tmp/tmax_gate_all.json',            'gate1_empty_submission.json', 'gate1',
  'Empty-submission gate over all 14,601: grader on an untouched container must return 0'),
 ('/tmp/tmax_review_opus.json',         'v1_round1_641.json',          'v1r1',
  'v1 rubric audit of the first 641-task build (Opus review + Fable rejudge)'),
 ('/tmp/tmax_r2_all_verdicts.jsonl',    'v1_round2_opus_1409.jsonl',   'v1r2_opus',
  'v1 rubric audit, round 2 primary judge (Opus 5) over 1,422 candidates'),
 ('/tmp/tmax_r2_salvage_fable.jsonl',   'v1_round2_fable_547.jsonl',   'v1r2_fable',
  'v1 rubric audit, round 2 blind re-judge (Fable 5) over 547'),
 ('/tmp/tmax_r2_salvage.jsonl',         'v1_round2_salvage_1422.jsonl','v1r2_salvage',
  'per-task salvage from round 2 — fuller than the merged file (1,422 vs 1,409)'),
 ('/tmp/v2_salvage.jsonl',              'v2_legacy_324.jsonl',         'v2',
  'v2 audit (provenance check added) over 324 legacy-pool tasks'),
 ('/tmp/v2_recheck.jsonl',              'v3_recheck_90.jsonl',         'v3',
  'v3 re-judge (checks bind the verdict) of the 90 CLEANs from v2'),
 ('/tmp/v4_sample.jsonl',               'v4_sample_100.jsonl',         'v4',
  'v4 audit of a random 100 of the 398 published tasks'),
]
manifest=[]
for src,dst,tag,desc in SRC:
    p=pathlib.Path(src)
    if not p.exists() or not p.stat().st_size:
        manifest.append({"round":tag,"file":dst,"status":"missing/empty at archive time","desc":desc}); continue
    shutil.copy(p, out/dst)
    n=sum(1 for l in p.read_text().splitlines() if l.strip()) if dst.endswith('jsonl') else None
    manifest.append({"round":tag,"file":dst,"source":src,"lines":n,"bytes":p.stat().st_size,"desc":desc})
    print(f"  {tag:<12} {dst:<32} {n if n else '(json)'}")

# 行为门也一起归档
BEH=[('logs/tmax_g2_r2.jsonl','gate2_solve_1710.jsonl','gate2','Solve gate: 570 tasks x 3 luna attempts'),
     ('logs/tmax_regate_gold.jsonl','regate_gold_351.jsonl','regate','Independent re-gate of 351 gold artifacts'),
     ('logs/tmax_mutate_gold.jsonl','mutation_gold_350.jsonl','mutation','Mutation test: corrupt the gold, re-grade'),
     ('logs/tmax_prov400.jsonl','shortcut_train_400.jsonl','shortcut1','Shortcut test over the shipped set'),
     ('logs/tmax_prov_legacy.jsonl','shortcut_legacy_297.jsonl','shortcut2','Shortcut test over legacy candidates'),
     ('logs/tmax_prov2_legacy.jsonl','shortcut_v2_legacy.jsonl','shortcut3','Shortcut gate v2 (setup-materialised references)')]
for src,dst,tag,desc in BEH:
    p=pathlib.Path(src)
    if not p.exists() or not p.stat().st_size: continue
    shutil.copy(p, out/dst)
    n=sum(1 for l in p.read_text().splitlines() if l.strip())
    manifest.append({"round":tag,"file":dst,"source":src,"lines":n,"desc":desc,"kind":"behavioural"})
    print(f"  {tag:<12} {dst:<32} {n}")
(out/'MANIFEST.json').write_text(json.dumps(manifest,indent=1,ensure_ascii=False))
print(f"\n归档 {len([m for m in manifest if 'lines' in m])} 个文件 -> {out}")
