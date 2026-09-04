#!/bin/bash
# The mix rows for a few task ids, as one jsonl, ready to ship to the eval
# host for difficulty_probe.sh. Rows are self-contained (the build context is
# inline), so the file is all the probe needs.
#
#   usage: probe_rows.sh <mix.jsonl> <out.jsonl> <task_id> [task_id ...]
set -euo pipefail
MIX=${1:?mix.jsonl}
OUT=${2:?out.jsonl}
shift 2
[ $# -gt 0 ] || { echo "no task ids"; exit 1; }
python3 - "$MIX" "$OUT" "$@" <<'PY'
import json, sys
mix, out, *want = sys.argv[1:]
rows = {}
with open(mix) as f:
    for ln in f:
        if ln.strip():
            r = json.loads(ln)
            if r["label"] in want:
                rows[r["label"]] = ln.rstrip("\n")
missing = [t for t in want if t not in rows]
if missing:
    sys.exit(f"not in {mix}: {' '.join(missing)}")
with open(out, "w") as f:
    for t in want:
        f.write(rows[t] + "\n")
print(f"{len(want)} row(s) -> {out}")
PY
