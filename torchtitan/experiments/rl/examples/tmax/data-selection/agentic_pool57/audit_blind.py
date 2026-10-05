"""Blind challenger audit: is K3's final_top10 really the top 10?

For each audited query, mixes K3's 10 picks with challengers it did not pick:
the highest dense-scored representatives in the whole pool (whatever group or
retriever), and K3's own best-scored per_source runners-up. The set is
shuffled with a fixed seed and relabelled C01..; a judge that never sees which
are K3's picks ranks its own top 10 under the same rubric. The key is written
separately and only `score` reads it.

  audit_blind.py build <qid> [...]   -> audit/<qid>.json (judge input), audit/<qid>.key.json
  audit_blind.py score <qid> [...]   reads audit/<qid>.judge.json, prints agreement
"""

from __future__ import annotations

import json
import random
import sys

import numpy as np

from common import ROOT, read_jsonl, texts_of

N_DENSE = 20
N_RUNNERS = 20
MAXCHARS = 3500


def build(qids):
    work = ROOT / "work"
    queries = {q["query_id"]: q for q in read_jsonl(work / "queries.jsonl")}
    qorder = [q for q in queries]
    ids = np.load(work / "dense_scores_ids.npy")
    S = np.load(work / "dense_scores.npy", mmap_mode="r")
    idx = json.loads((work / "rep_index.json").read_text())
    pos = {str(x): j for j, x in enumerate(ids)}
    (ROOT / "audit").mkdir(exist_ok=True)
    for qid in qids:
        r = json.loads((ROOT / "rerank" / f"{qid}.json").read_text())
        picks = [e["task_id"] for e in r["final_top10"]]
        chosen = set(picks)
        row = np.asarray(S[qorder.index(qid)], dtype=np.float32)
        dense = []
        for j in np.argsort(-row)[:200]:
            i = str(ids[j])
            if i not in chosen and idx[i]["verifier"] != "FREE":
                dense.append(i)
            if len(dense) == N_DENSE:
                break
        runners = sorted((e for es in r["per_source"].values() for e in es if e["task_id"] not in chosen),
                         key=lambda e: (-e["overall"], -e["skill"], e["rank"]))
        runner_ids = []
        for e in runners:
            if e["task_id"] not in dense and e["task_id"] not in runner_ids:
                runner_ids.append(e["task_id"])
            if len(runner_ids) == N_RUNNERS:
                break
        members = [(i, "k3_pick") for i in picks] + [(i, "dense_challenger") for i in dense] + \
                  [(i, "k3_runner_up") for i in runner_ids]
        random.Random(f"audit-{qid}").shuffle(members)
        cands, key = [], {}
        for n, (i, kind) in enumerate(members, 1):
            label = f"C{n:02d}"
            text = texts_of([i])[i]
            cands.append({"label": label, "sources": idx[i]["sources"], "verifier": idx[i]["verifier"],
                          "instruction": text[:MAXCHARS] + (" …[truncated]" if len(text) > MAXCHARS else "")})
            key[label] = {"id": i, "kind": kind,
                          "k3_rank": picks.index(i) + 1 if i in chosen else None,
                          "dense_rank": int((row > row[pos[i]]).sum()) + 1}
        (ROOT / "audit" / f"{qid}.json").write_text(json.dumps(
            {"query_id": qid, "tb21_instruction": queries[qid]["instruction"], "candidates": cands},
            ensure_ascii=False, indent=1))
        (ROOT / "audit" / f"{qid}.key.json").write_text(json.dumps(key, indent=1))
        print(qid, "candidates", len(cands), "picks", len(picks), "dense", len(dense), "runners", len(runner_ids))


def score(qids):
    idx = json.loads((ROOT / "work/rep_index.json").read_text())
    tot = {"queries": 0, "overlap": 0, "intruders_dense": 0, "intruders_runner": 0, "near_copy_in_k3_top10": 0}
    for qid in qids:
        key = json.loads((ROOT / "audit" / f"{qid}.key.json").read_text())
        judge = json.loads((ROOT / "audit" / f"{qid}.judge.json").read_text())
        top = [e["label"] for e in judge["top10"]]
        assert len(top) == 10 and all(l in key for l in top), qid
        picks = {l for l, k in key.items() if k["kind"] == "k3_pick"}
        kept = picks & set(top)
        dropped = sorted((key[l]["k3_rank"], l) for l in picks - set(top))
        intruders = [l for l in top if l not in picks]
        print(f"\n== {qid}: judge kept {len(kept)}/10 of K3's top-10")
        for l in intruders:
            k = key[l]; r = idx[k["id"]]
            print(f"   + judge rank {top.index(l) + 1}: {k['id']} ({k['kind']}, dense rank {k['dense_rank']}) "
                  f"[{','.join(r['sources'])}]")
        for rank, l in dropped:
            print(f"   - dropped K3 rank {rank}: {key[l]['id']} [{','.join(idx[key[l]['id']]['sources'])}]")
        for l in judge.get("near_copies", []):
            k = key.get(l, {})
            print(f"   ! near-copy per judge: {k.get('id')} ({k.get('kind')}, K3 rank {k.get('k3_rank')}, "
                  f"dense rank {k.get('dense_rank')})")
            tot["near_copy_in_k3_top10"] += k.get("kind") == "k3_pick"
        tot["queries"] += 1; tot["overlap"] += len(kept)
        tot["intruders_dense"] += sum(key[l]["kind"] == "dense_challenger" for l in intruders)
        tot["intruders_runner"] += sum(key[l]["kind"] == "k3_runner_up" for l in intruders)
    print("\n" + json.dumps(tot))


if __name__ == "__main__":
    {"build": build, "score": score}[sys.argv[1]](sys.argv[2:])
