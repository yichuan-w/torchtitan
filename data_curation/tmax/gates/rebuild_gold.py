import pandas as pd, pathlib, tarfile
sol=pathlib.Path('data/tmax-solutions')
out=pathlib.Path('/tmp/tmax_upload'); out.mkdir(exist_ok=True)
tr=pd.read_parquet('data/tmax-tasks-clean/splits/train.parquet')
ck=pd.read_parquet('data/tmax-tasks-clean/splits/checking.parquet')
held=set(ck[ck.split_role=='held_back'].task_id)
conf={l.strip() for l in open('/tmp/tmax_gold_confirmed.ids') if l.strip()}
proc={l.strip() for l in open('/tmp/tmax_procedural_gold.ids') if l.strip()}
train=set(tr.task_id)

def build(name, ids, kind):
    n=0
    with tarfile.open(out/name,'w') as tf:
        for t in sorted(ids):
            if kind=='static':
                p=sol/f'{t}.solution.tar.gz'; arc=f'gold_static/{t}.solution.tar.gz'
            else:
                p=sol/'traces'/f'{t}.rollout.jsonl.gz'; arc=f'gold_procedural/{t}.rollout.jsonl.gz'
            if p.exists(): tf.add(p, arcname=arc); n+=1
    print(f"  {name}: {n}")
    return n

print("aligned to train:")
build('gold_static.tar', conf&train, 'static')
build('gold_procedural.tar', proc&train, 'proc')
print("held-back tasks' solutions (kept as evidence, NOT for training):")
with tarfile.open(out/'gold_held_back.tar','w') as tf:
    n=0
    for t in sorted((conf|proc)&held):
        p=sol/f'{t}.solution.tar.gz'
        if p.exists(): tf.add(p, arcname=f'gold_held_back/{t}.solution.tar.gz'); n+=1
        r=sol/'traces'/f'{t}.rollout.jsonl.gz'
        if r.exists(): tf.add(r, arcname=f'gold_held_back/{t}.rollout.jsonl.gz'); n+=1
    print(f"  gold_held_back.tar: {n} files for {len((conf|proc)&held)} tasks")
