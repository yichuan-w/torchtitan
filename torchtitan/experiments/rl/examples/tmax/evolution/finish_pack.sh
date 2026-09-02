#!/usr/bin/env bash
# After the hackable rerun drains: assemble the full training pack.
# Four layers fold by instance_id (pack_to_dataset adds unseen ids, replaces
# seen ones); the audit verdicts partition the seed ids, so layers are disjoint
# and order is cosmetic.
set -euo pipefail
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

# The tmux server that hosted the repair session keeps the launch command in
# its own argv forever, so a bare pgrep -f matches it and never falls through.
while pgrep -af "python3 repair_hackable" | grep -v tmux | grep -q .; do sleep 120; done

python3 - <<'PY'
import json, pathlib, shutil, tarfile
import docker_validate as dv
ids = []
for l in open("results/seed_solvability_v2.jsonl"):
    if not l.strip():
        continue
    r = json.loads(l)
    if r.get("verdict") == "solvable":
        ids.append(r["task_id"])
root = pathlib.Path("data/seed-solvable")
if root.exists():
    shutil.rmtree(root)
root.mkdir(parents=True)
with tarfile.open("chunk000.tar") as tf:
    for tid in ids:
        dv.extract(tf, tid, root / tid)
print(f"extracted {len(ids)} solvable seeds")
PY

python3 pack_to_dataset.py --evolved data/seed-solvable --out data/.pack_a.jsonl
python3 pack_to_dataset.py --evolved data/seed-specfixed --base data/.pack_a.jsonl --out data/.pack_b.jsonl
python3 pack_to_dataset.py --evolved data/seed-hardened --base data/.pack_b.jsonl --out data/.pack_c.jsonl
python3 pack_to_dataset.py --evolved data/feedback-r1b --ids results/feedback_r1b_clean_ids.txt \
    --base data/.pack_c.jsonl --out data/train_v2.jsonl
wc -l data/train_v2.jsonl
echo "train_v2 pack done at $(date -u +%FT%TZ)"
