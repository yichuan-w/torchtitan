import sys, tarfile, io, pathlib
sys.path.insert(0,'/homes/zhaofan/terminalworld-ladder/scripts')
sys.argv=['x','--parquet','x','--out','x']
import importlib.util
spec=importlib.util.spec_from_file_location("mg","/homes/zhaofan/terminalworld-ladder/scripts/mutate_gold.py")
mg=importlib.util.module_from_spec(spec); spec.loader.exec_module(mg)

sol=pathlib.Path('/homes/zhaofan/terminalworld-seeds/data/tmax-solutions')
for tid in ('task_000162_21261c66','task_000001_1a03ebc5','task_000753_b376cf1d'):
    p=sol/f'{tid}.solution.tar.gz'
    if not p.exists(): print(tid,"MISSING"); continue
    blob=p.read_bytes()
    mut,info=mg.build_mutant(blob)
    print(f"\n=== {tid} ===")
    print("  ",{k:v for k,v in info.items() if k!='touched'})
    print("   touched:",info['touched'][:4])
    if mut is None: print("   -> no mutable content"); continue
    a=tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz'); b=tarfile.open(fileobj=io.BytesIO(mut),mode='r:gz')
    na=[m.name for m in a.getmembers()]; nb=[m.name for m in b.getmembers()]
    print("   structure identical:", na==nb, f"({len(na)} members)")
    # show a before/after on the first mutated text file
    for m in a.getmembers():
        if m.isfile() and m.name in info['touched']:
            o=a.extractfile(m).read()[:90]; n=b.extractfile(b.getmember(m.name)).read()[:90]
            try:
                print("   before:",o.decode()[:80].replace('\n','\\n'))
                print("   after :",n.decode()[:80].replace('\n','\\n'))
            except Exception: print("   (binary)")
            break
