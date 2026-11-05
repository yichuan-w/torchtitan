"""Dump hardening.md plus keystroke sequences of the first successful old-rev and new-rev traces for top-N item 5 candidates."""
import json, os, sys
import pandas as pd

root, base, out, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
cand = pd.read_csv(f"{base}/../figs/item5_candidates.csv").head(n)
hdr = {}
for line in open(f"{base}/rollout_headers.jsonl"):
    h = json.loads(line)
    hdr.setdefault(h["group"], []).append(h)


def first_success(group):
    latest = {}
    for h in hdr.get(group, []):
        if h["rollout"] not in latest or h["run"] > latest[h["rollout"]]["run"]:
            latest[h["rollout"]] = h
    hs = sorted((h for h in latest.values() if (h["reward"] or 0) >= 1.0), key=lambda h: h["rollout"])
    return hs[0] if hs else None


def keys(path):
    lines = open(os.path.join(root, path)).read().splitlines()[1:]
    seq = []
    for l in lines:
        d = json.loads(l)
        k = d.get("keystrokes")
        if k:
            seq.append(f"[t{d.get('turn')}] " + (k if isinstance(k, str) else json.dumps(k, ensure_ascii=False)).strip())
    return seq


for _, c in cand.iterrows():
    o, nw = first_success(int(c.signal_group)), first_success(int(c.first_solved_group_after))
    with open(f"{out}/{c.task}.md", "w") as w:
        w.write(f"# {c.task}\nsignal g{c.signal_group} ({c.signal_solved}/12), new rev r{c.new_rev} seq {c.new_seq}\n\n## hardening.md\n")
        w.write(open(f"{c.dir}/notes/hardening.md").read() if os.path.exists(f"{c.dir}/notes/hardening.md") else "(missing)\n")
        for name, h in (("OLD rev success", o), ("NEW rev success", nw)):
            w.write(f"\n## {name}: {h['_file'] if h else None}\n")
            if h:
                w.write("\n".join(keys(h["_file"])) + "\n")
    print(c.task, "old", bool(o), "new", bool(nw))
