import pandas as pd, pathlib, re, json
tr=pd.read_parquet('data/tmax-tasks-clean/splits/train.parquet')
W=pathlib.Path('/tmp/tmax_gate_work')
ORACLE=re.compile(r'(oracle|reference)[\w./-]*', re.I)
GUARD=re.compile(r'islink|realpath|st_ino|os\.stat|samefile|readlink|is_symlink')
rows=[]
for t in tr.task_id:
    ts=W/t/'tests/test.sh'; su=W/t/'setup.sh'
    if not ts.exists(): continue
    T=ts.read_text(errors='replace'); S=su.read_text(errors='replace') if su.exists() else ''
    # verifier invokes an oracle/reference executable that lives in the image
    paths=set(re.findall(r'[\'"](/(?:app|opt|oracle|usr/local)[\w./-]*(?:oracle|reference)[\w./-]*)[\'"]', T, re.I))
    paths|=set(re.findall(r'(/(?:app|opt|oracle)/[\w.-]*(?:oracle|reference)[\w.-]*)', T, re.I))
    if not paths: continue
    guarded=bool(GUARD.search(T))
    # is the oracle a plaintext script planted by setup.sh?
    plain=bool(re.search(r"cat\s*<<\s*'?\w+'?\s*>\s*/(?:app|opt|oracle)[\w./-]*(?:oracle|reference)", S, re.I))
    src_left = bool(re.search(r'/(?:opt|app)/[\w./-]*(?:src|main)\.(?:go|c|py|rs)', S)) and 'rm ' not in S
    rows.append(dict(task=t, dom=tr[tr.task_id==t].tb_domain.iloc[0],
                     paths=sorted(paths)[:2], guarded=guarded, plaintext_oracle=plain))
print(f"shipped tasks whose verifier references an in-image oracle/reference: {len(rows)}")
print(f"  of those, WITHOUT any provenance guard (islink/realpath/st_ino/samefile): "
      f"{sum(1 for r in rows if not r['guarded'])}")
print(f"  with a plaintext oracle planted by setup.sh heredoc: "
      f"{sum(1 for r in rows if r['plaintext_oracle'])}")
import collections
print("  by domain:", dict(collections.Counter(r['dom'] for r in rows).most_common()))
pathlib.Path('/tmp/tmax_oracle_family.ids').write_text('\n'.join(sorted(r['task'] for r in rows))+'\n')
print("\nplaintext-oracle tasks (answer source readable):")
for r in rows:
    if r['plaintext_oracle']: print("   ",r['task'], r['dom'], r['paths'])
