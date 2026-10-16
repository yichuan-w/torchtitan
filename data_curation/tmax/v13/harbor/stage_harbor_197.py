#!/usr/bin/env python3
"""Stage harbor-package top-10 tasks for judging under v13-harbor2 (prompt ../harbor/v13_harbor211_prompt.md, v2.1.1).

Population: every id of tb21_agentic_top10.source.csv whose source is SWE-Smith-Seeds-Clean (37),
SWE-Rebench-Tasks-Clean (61) or TerminalWorld-Seeds-Clean (99). v13-harbor2 describes packages whose verifier is
pytest on tests/test_state.py, started by tests/test.sh -- TerminalWorld's shape. SWE-Smith (environment/bug.patch)
and SWE-Rebench (tests/test.patch) grade differently and are refused here until a rubric describes their path; the
v13-harbor v1 staging of all three is in git at b2be2900.

Packages come from each dataset's own `data/tasks-00000.tar` on the Hub -- for TerminalWorld that is the only
archive covering all 99. Task bytes are copied, never read. `solution/` is excluded from the judging package so the
judge cannot see the answer.

Two steps, and each writes only where it says:
  --tars <dir>   extract the packages into harbor/<corpus>/tasks/ (the judged input; v1 used the same directory)
  --restage      write a fresh run directory harbor/<corpus>_v21/ -- candidates/, prompts/, batches/, rows/, scratch/ -- from
                 the packages already extracted; refuses if that directory exists, so v1's candidates, prompts and
                 rows under harbor/<corpus>/ are never touched.

The judge's read list is harbor_files.read_list: instruction.md, environment/Dockerfile, tests/test.sh,
tests/test_state.py, then every build-context file a COPY/ADD line brings in (resolved as the harness builds the image),
in the order of the prompt's §1. Candidates
come from harbor/candidates_harbor.py, which reads the same list.

    python3 stage_harbor_197.py --tars <dir>            # extract
    python3 stage_harbor_197.py --restage [--dry-run]   # stage the v21 run
"""
import argparse, csv, hashlib, json, os, sys, tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
V13 = os.path.dirname(HERE)
HARBOR = f"{V13}/harbor"
PROMPT, SCHEMA = f"{HARBOR}/v13_harbor211_prompt.md", f"{HARBOR}/output_schema_v13_harbor21.json"
PSHA = "ac3dde0e54c079bfa9ce81635cc0c45960b603f97dd6781c6180de5574db7739"
SSHA = "0d37ee14631bc41531df0af8b15e29d387a33c95e80d713a73fd5cb3d2b439ad"
RUBRIC, RUN = "v13-harbor2", "v21"
PY_ = sys.executable
# The validate command falls back to Draft7Validator where jsonschema predates 4.0 (/usr/bin/python3 here has 3.2.0,
# which has no Draft202012Validator: the v1 wrapper's command raised AttributeError as written). The schema uses only
# keywords with the same meaning in draft 7 -- measured: identical error sets under both validators on 396 rows.
sys.path.insert(0, HARBOR)
import candidates_harbor as ch  # noqa: E402
import harbor_files as hf  # noqa: E402

# source -> (short name, hub dataset, the corpus's verifier file)
CORPORA = {
    "SWE-Smith-Seeds-Clean": ("smith", "Fzz1/SWE-Smith-Seeds-Clean", "environment/bug.patch"),
    "SWE-Rebench-Tasks-Clean": ("rebench", "Fzz1/SWE-Rebench-Tasks-Clean", "tests/test.patch"),
    "TerminalWorld-Seeds-Clean": ("tw", "andylizf/TerminalWorld-Seeds-Clean", "tests/test_state.py"),
}
KIND = {"instruction": "the instruction", "image": "the image build",
        "copied": "a build-context file the Dockerfile copies into the image",
        "copied-binary": "a build-context file the Dockerfile copies into the image; not text, so do not read it",
        "bootstrap": "the harness bootstrap", "verifier": "the verifier"}


def wrapper(tid, k, corpus, tasks, cand, rows, batches, scratch):
    files = "\n".join(f"- {tasks}/{tid}/{rel} -- {KIND[kind]}" for rel, kind in hf.read_list(os.path.join(tasks, tid)))
    return f'''You are a seed-audit judge applying the {RUBRIC} rubric. Judge strictly and honestly. You are BLIND to any prior verdict on this task and you do not look for one: do not read any other judge's row, do not open any directory named results*, opus48 or validation*, and do not search the filesystem for other judgments. The only files you write are your own row and one scratch file, under {scratch}/. Your agent label is "judge:harbor:{corpus}:{RUN}-{PSHA[:8]}:b{k:04d}" (write it in the row's "agent" field exactly).

SETUP FIRST:
1. Read the {RUBRIC} judge prompt IN FULL: {PROMPT}. Compute its sha256 (sha256sum); confirm it equals {PSHA}. On mismatch, STOP and reply {{"results":[]}}. Use that prompt EXACTLY as your rubric. Wherever it says {{TASK_ROOT}}, the directory is {tasks}; wherever it says {{OUTPUT_PATH}}, the file is {rows}/{tid}.jsonl; wherever it says {{CANDIDATES_PATH}}, the file is {cand}/{tid}.json.
2. Read the output schema: {SCHEMA} (sha256 {SSHA}). Your row must validate against it (rubric_version "{RUBRIC}"; the allOf entries are hard constraints). Validate with: {PY_} -c 'import json,jsonschema,sys; s=json.load(open("{SCHEMA}")); r=json.loads(open(sys.argv[1]).readline()); V=getattr(jsonschema,"Draft202012Validator",None) or jsonschema.Draft7Validator; V(s).validate(r); print("valid")' {rows}/{tid}.jsonl  -- before you consider the task finished; fix the row if it fails.
3. Read your batch id list: {batches}/batch_{k:04d}.json (a JSON array holding this one task id).

FOR THE task_id IN YOUR BATCH, these are the task's files, in the prompt's reading order (§1):
{files}
Read every one IN FULL except a file marked not text, then the candidate list {cand}/{tid}.json; nothing else about the task. The graded agent NEVER sees the verifier; judge what the reward SIGNAL does. Anchor every rule field as §1 of the prompt says.
- Apply the {RUBRIC} procedure and produce the exact output JSON object (every key). You have an interpreter for your own arithmetic: {PY_} on a scratch file under {scratch}/; NEVER execute the task's Dockerfile build, tests/test.sh, tests/test_state.py or any task program, no docker, no network.
- Write the object as ONE JSON line to {rows}/{tid}.jsonl (the file must not already exist), then validate it as in step 2.

Then reply with labels ONLY (no task text, evidence, reasons or commands): {{"results": [{{"task_id": "{tid}", "tier": ..., "verdict": ..., "fired_fields": [names of the non-null blocking and note fields], "row_written": true}}]}}.'''


def extract(tar, ids, tasks):
    want, got = set(ids), set()
    with tarfile.open(tar) as tf:                      # ONE streaming pass, bytes copied never read
        for m in tf:
            if not m.isfile():
                continue
            p = m.name.split("/")
            tid = next((x for x in p if x in want), None)
            if not tid:
                continue
            rel = "/".join(p[p.index(tid) + 1:])
            if rel.startswith("solution/"):            # the judge must not see the answer
                continue
            dst = os.path.join(tasks, tid, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(tf.extractfile(m).read())
            got.add(tid)
    missing = sorted(want - got)
    assert not missing, f"{len(missing)} ids not in the tar, e.g. {missing[:3]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tars", help="directory holding each corpus's tasks-00000.tar: extract the packages")
    ap.add_argument("--restage", action="store_true", help=f"write the fresh run directory harbor/<corpus>_{RUN}/")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not (a.tars or a.restage):
        ap.error("give --tars, --restage, or both")
    assert hashlib.sha256(open(PROMPT, "rb").read()).hexdigest() == PSHA, "the v13-harbor2 prompt moved"
    assert hashlib.sha256(open(SCHEMA, "rb").read()).hexdigest() == SSHA, "the v13-harbor2 schema moved"
    rows = list(csv.DictReader(open(f"{HERE}/tb21_agentic_top10.source.csv")))
    total = 0
    for source, (short, ds, verifier) in CORPORA.items():
        ids = sorted({r["task_id"] for r in rows if r["source"] == source})
        if verifier != hf.VERIFIER:
            print(f"\n{source}: {len(ids)} ids REFUSED -- its verifier is {verifier}, and {RUBRIC} describes "
                  f"pytest on {hf.VERIFIER}")
            continue
        tasks = f"{HERE}/harbor/{short}/tasks"
        print(f"\n{source}: {len(ids)} ids")
        if a.tars:
            tar = next((os.path.join(a.tars, n) for n in (f"{short}_tasks-00000.tar",
                        f"{ds.split('/')[1]}_tasks-00000.tar", "tasks-00000.tar")
                        if os.path.exists(os.path.join(a.tars, n))), None)
            assert tar, f"{source}: no tasks-00000.tar in {a.tars}"
            if not a.dry_run:
                extract(tar, ids, tasks)
                print(f"   extracted {len(ids)} packages from {os.path.basename(tar)} into {tasks}")
        if not a.restage:
            continue
        for tid in ids:
            for need in (hf.INSTRUCTION, hf.DOCKERFILE, hf.BOOTSTRAP, hf.VERIFIER):
                assert os.path.exists(os.path.join(tasks, tid, need)), f"{tid}: no {need}"
            assert not os.path.exists(os.path.join(tasks, tid, "solution")), f"{tid}: solution/ leaked"
        base = f"{HERE}/harbor/{short}_{RUN}"
        assert not os.path.exists(base), f"{base} exists: a run directory is written once"
        n_copied = sum(len(hf.copied_files(os.path.join(tasks, t))) for t in ids)
        print(f"   {len(ids)} packages; {n_copied} build-context files join the read lists; run directory {base}")
        if a.dry_run:
            continue
        cand, prompts, batches, rowdir, scr = (f"{base}/candidates", f"{base}/prompts", f"{base}/batches", f"{base}/rows",
                                               f"{base}/scratch")
        for d in (cand, prompts, batches, rowdir, scr):
            os.makedirs(d)
        for k, tid in enumerate(ids, 1):
            c = ch.extract(os.path.join(tasks, tid)); c["task_id"] = tid
            json.dump(c, open(f"{cand}/{tid}.json", "x"), indent=1)
            json.dump([tid], open(f"{batches}/batch_{k:04d}.json", "x"))
            os.makedirs(f"{scr}/{tid}")
            open(f"{prompts}/{short}__{tid}.txt", "x").write(wrapper(tid, k, short, tasks, cand, rowdir, batches,
                                                                     f"{scr}/{tid}"))
        print(f"   staged {len(ids)}: candidates, prompts and batches in {base}")
        total += len(ids)
    print(f"\ntotal staged: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
