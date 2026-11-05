"""Parallel copy of small per-task evolve metadata (lineage, attempts, rewrite/simplify/hardening, instructions)."""
import glob, os, shutil, sys
from concurrent.futures import ThreadPoolExecutor

root, out, nthreads = sys.argv[1], sys.argv[2], int(sys.argv[3])
tasks_root = os.path.join(root, "evolution", "tasks")
PATTERNS = ["lineage.jsonl", "attempts.jsonl", "r*/instruction.md", "r*/solution/solve.sh", "r*/environment/Dockerfile",
            "r*/tests/*.py", "r*/tests/test.sh", "r*/task.toml", "r*/seed.json",
            "rewrites/*/rewrite.json", "rewrites/*/notes/*"]


def task_files(t):
    fs = []
    for p in PATTERNS:
        fs += glob.glob(os.path.join(tasks_root, t, p))
    return fs


def copy(f):
    dst = os.path.join(out, os.path.relpath(f, tasks_root))
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(f):
        return 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if os.path.getsize(f) > 2_000_000:
            return 0
        shutil.copyfile(f, dst)
        return 1
    except Exception:
        return 0


tasks = sorted(os.listdir(tasks_root))
with ThreadPoolExecutor(nthreads) as ex:
    files = [f for fs in ex.map(task_files, tasks) for f in fs]
print("tasks", len(tasks), "files", len(files), flush=True)
with ThreadPoolExecutor(nthreads) as ex:
    n = sum(ex.map(copy, files))
print("copied", n, flush=True)
