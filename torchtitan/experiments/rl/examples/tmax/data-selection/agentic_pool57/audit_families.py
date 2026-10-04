"""Print sampled near-duplicate families so the merge threshold can be judged by eye."""
import random
from common import ROOT, read_jsonl

work = ROOT / "work"
fams = [d for d in read_jsonl(work / "reps.jsonl") if len(d["family"]) > 1]
sizes = sorted((len(d["family"]) for d in fams), reverse=True)
print("families>1:", len(fams), "largest:", sizes[:10])
random.seed(0)
pick = random.sample(fams, 6) + sorted(fams, key=lambda d: -len(d["family"]))[:2]
need = {m for d in pick for m in d["family"][:3]}
texts = {d["id"]: d for d in read_jsonl(work / "docs.jsonl") if d["id"] in need}
for d in pick:
    print("=" * 100, "\nfamily", d["id"], "size", len(d["family"]), "sources", sorted({o[0] for o in d["occ"]}))
    for m in d["family"][:3]:
        t = texts[m]["text"]
        print(f"--- {m} [{texts[m]['occ'][0][0]}] len={len(t)}\n{' '.join(t.split())[:300]}")
        print("   ...", " ".join(t.split())[-200:])
