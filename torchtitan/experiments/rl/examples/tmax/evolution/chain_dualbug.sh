#!/usr/bin/env bash
# After the underspec pass: seeds it rejected as "reads as hackable" carry both
# defects, and sit in neither repair queue — the original hackable set was
# frozen before these were re-read. Route them through the hackable chain:
# fresh audit (for the shortcut claim) -> execute the claim -> harden confirmed.
# Results go to repair_dualbug.jsonl, NOT repair_seeds_all.jsonl: the main
# hackable run may still be appending there, and two writers interleave on NFS.
set -euo pipefail
cd /work/tianxia/tw-recover
set -a; . ./.synth_env; set +a

while pgrep -f "python3 repair_underspec" >/dev/null 2>&1; do sleep 60; done

python3 - <<'PY'
import json
ids = []
for l in open("results/repair_underspec_all.jsonl"):
    if not l.strip():
        continue
    r = json.loads(l)
    if r["status"] == "not_underspecified" and r.get("why") == "reads as hackable":
        ids.append(r["task_id"])
open("results/dualbug.ids", "w").write("\n".join(ids) + "\n")
print(f"{len(ids)} dual-defect seeds routed to the hackable chain")
PY

python3 audit_solvability.py --ids results/dualbug.ids --tar chunk000.tar \
    --results results/dualbug_audit.jsonl --workers 4
python3 verify_shortcuts.py --audit results/dualbug_audit.jsonl --tar chunk000.tar \
    --results results/dualbug_verify.jsonl --workers 4
python3 repair_hackable.py --verify results/dualbug_verify.jsonl --tar chunk000.tar \
    --out data/seed-hardened --results results/repair_dualbug.jsonl --rounds 3 --workers 4
echo "dualbug chain done"
