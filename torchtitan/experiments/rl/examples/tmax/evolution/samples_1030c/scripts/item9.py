"""Item 9: per TB-2.1 category, accepted rewrites of the same category in training, and pass@5 at the first and last eval."""
import glob, json, re, sys
from collections import Counter, defaultdict
import pandas as pd

meta, vdir, out = sys.argv[1], sys.argv[2], sys.argv[3]
CATS = {
 "software-engineering": "build-pmars build-pov-ray cancel-async-tasks circuit-fibsqrt cobol-modernization code-from-image fix-git fix-ocaml-gc git-leak-recovery gpt2-codegolf headless-terminal kv-store-grpc make-doom-for-mips make-mips-interpreter path-tracing path-tracing-reverse polyglot-c-py polyglot-rust-c prove-plus-comm pypi-server regex-chess schemelike-metacircular-eval torch-pipeline-parallelism torch-tensor-parallelism winning-avg-corewars write-compressor",
 "system-administration": "compile-compcert configure-git-webserver git-multibranch install-windows-3.11 mailman nginx-request-logging qemu-alpine-ssh qemu-startup sqlite-with-gcov",
 "scientific-computing": "adaptive-rejection-sampler bn-fit-modify dna-assembly dna-insert modernize-scientific-stack protein-assembly raman-fitting tune-mjcf",
 "security": "break-filter-js-from-html crack-7z-hash filter-js-from-html fix-code-vulnerability openssl-selfsigned-cert password-recovery sanitize-git-repo vulnerable-secret",
 "data-science": "hf-model-inference mcmc-sampling-stan mteb-leaderboard mteb-retrieve query-optimize reshard-c4-data rstan-to-pystan sam-cell-seg",
 "debugging": "build-cython-ext custom-memory-heap-crash merge-diff-arc-agi-task overfull-hbox sqlite-db-truncate",
 "file-operations": "db-wal-recovery extract-elf extract-moves-from-video gcode-to-text large-scale-text-editing",
 "model-training": "count-dataset-tokens pytorch-model-cli pytorch-model-recovery train-fasttext",
 "mathematics": "feal-differential-cryptanalysis feal-linear-cryptanalysis largest-eigenval model-extraction-relu-logits",
 "data-processing": "financial-document-processor log-summary-date-ranges multi-source-data-merger regex-log",
 "machine-learning": "caffe-cifar-10 distribution-search llm-inference-batching-scheduler",
 "games": "chess-best-move", "personal-assistant": "constraints-scheduling", "optimization": "portfolio-optimization",
 "data-querying": "sparql-university", "video-processing": "video-processing"}
cat_of = {t: c for c, ts in CATS.items() for t in ts.split()}
assert len(cat_of) == 89, len(cat_of)


def train_cat(task):
    m = re.match(r"([a-z-]+)_2026\d{4}_", task)
    return m.group(1) if m else None


acc = Counter(); tasks = defaultdict(set); untyped = 0
for f in glob.glob(f"{meta}/*/rewrites/*/rewrite.json"):
    d = json.load(open(f))
    if d.get("status") != "accepted":
        continue
    c = train_cat(d["task"])
    if c is None:
        untyped += 1
        continue
    acc[(c, d["job"])] += 1; tasks[c].add(d["task"])


def pass5(step):
    idx = json.load(open(f"{vdir}/step-{step}/index.json"))
    per = defaultdict(list)
    for r in idx:
        per[r.get("task") or r.get("instance_id") or r.get("task_id")].append((r.get("reward") or 0) >= 1)
    return per


first, last = pass5(12), pass5(300)
missing = set(cat_of) - set(first)
rows = []
for c in sorted(CATS, key=lambda c: -len(CATS[c].split())):
    ts = CATS[c].split()
    p0 = sum(any(first.get(t, [])) for t in ts); p1 = sum(any(last.get(t, [])) for t in ts)
    a0 = sum(sum(first.get(t, [])) for t in ts) / (5 * len(ts)); a1 = sum(sum(last.get(t, [])) for t in ts) / (5 * len(ts))
    rows.append({"category": c, "eval tasks": len(ts), "train tasks rewritten": len(tasks[c]),
                 "accepted easier": acc[(c, "easier")], "accepted harder": acc[(c, "harder")],
                 "pass@5 pv12": f"{p0}/{len(ts)}", "pass@5 pv300": f"{p1}/{len(ts)}",
                 "avg@5 pv12": round(a0, 3), "avg@5 pv300": round(a1, 3)})
df = pd.DataFrame(rows)
print(df.to_markdown(index=False))
print("accepted rewrites on tasks without a category prefix (agentpick task_/tw_/rts_/swe):", untyped, "; eval ids missing from index:", missing)
df.to_csv(f"{out}/item9_category_table.csv", index=False)
