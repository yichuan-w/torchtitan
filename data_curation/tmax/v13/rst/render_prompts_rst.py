#!/usr/bin/env python3
"""Render one v13-rst judge prompt per staged RST task.
Usage: render_prompts_rst.py <run_dir> [--only ids.txt] [--out prompts_dir] [--rows-dir rows_dir]

The prompt and schema digests are computed here, at render time, and written to render_manifest.json: a hardcoded
digest went stale once (the schema changed after the renderer was written), and the judge refuses to work on a
mismatch. The judge is told where the separator line is (a fact of the staged file) and nothing else about the task:
no gate outcome, no static flag, no category.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT, SCHEMA = os.path.join(HERE, "v13_rst_prompt.md"), os.path.join(HERE, "output_schema_v13_rst.json")
PY = os.environ.get("JUDGE_PYTHON", sys.executable)      # an interpreter that has jsonschema; the judge validates with it


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def prompt_for(run, tid, k, sep, total, psha, ssha, rows):
    tasks, cand, batches = (os.path.join(run, d) for d in ("tasks", "candidates", "batches"))
    return f'''You are a seed-audit judge applying the v13-rst rubric to one task of the RST corpus. Judge strictly and honestly. You are BLIND to any prior verdict on this task and you do not look for one: do not read any other judge's row, do not open any directory named results*, sidecar or validation*, and do not search the filesystem for other judgments. The only file you write is your own row. Your agent label is "judge:rst:b{k:04d}" (write it in the row's "agent" field exactly).

SETUP FIRST:
1. Read the v13-rst judge prompt IN FULL: {PROMPT}. Compute its sha256 (sha256sum); confirm it equals {psha}. On mismatch, STOP and reply {{"results":[]}}. Use that prompt EXACTLY as your rubric. Wherever it says {{TASK_ROOT}}, the directory is {tasks}; wherever it says {{OUTPUT_PATH}}, the file is {rows}/{tid}.jsonl; wherever it says {{CANDIDATES_PATH}}, the file is {cand}/{tid}.json (the runner's candidate list: programs to dispose of, failing statements to cover).
2. Read the output schema: {SCHEMA} (sha256 {ssha}). Your row must validate against it (rubric_version "v13-rst"; the allOf entries are hard constraints). Validate with: {PY} -c 'import json,jsonschema,sys; s=json.load(open("{SCHEMA}")); r=json.loads(open(sys.argv[1]).readline()); jsonschema.Draft202012Validator(s).validate(r); print("valid")' {rows}/{tid}.jsonl  -- before you consider the task finished; fix the row if it fails.
3. Read your batch id list: {batches}/batch_{k:04d}.json (a JSON array holding this one task id).

FOR THE task_id IN YOUR BATCH:
- Read {tasks}/{tid}/instruction.md, setup.sh, and tests/test.sh IN FULL, and the candidate list {cand}/{tid}.json; nothing else about the task. In this task the separator is line {sep} of tests/test.sh: lines 1-{sep - 1} are the harness bootstrap, lines {sep + 1}-{total} are the verifier (test_state.py). The graded agent NEVER sees tests/test.sh; judge what the reward SIGNAL does.
- Apply the v13-rst procedure and produce the exact output JSON object (every key, including bootstrap_fetch, which Step 4b fills on every task). Every entry of the candidate list's programs gets exactly one disposition and every failing_statements line falls inside an assertions entry, as the rubric requires. You have an interpreter for your own arithmetic: {PY} in a scratch file in your current working directory; NEVER build the Dockerfile, NEVER execute the bootstrap, the verifier or any task program, no docker, no network.
- Write the object as ONE JSON line to {rows}/{tid}.jsonl (the file must not already exist), then validate it as in step 2.

Then reply with labels ONLY (no task text, evidence, reasons or commands): {{"results": [{{"task_id": "{tid}", "tier": ..., "verdict": ..., "fired_fields": [names of the non-null blocking and note fields], "row_written": true}}]}}.'''


def main():
    run = os.path.abspath(sys.argv[1])
    args = sys.argv[2:]
    only = None
    out = os.path.join(run, "prompts")
    rows = os.path.join(run, "rows")
    while args:
        if args[0] == "--only":
            only = [l.strip() for l in open(args[1]) if l.strip()]
        elif args[0] == "--out":
            out = os.path.abspath(args[1])
        elif args[0] == "--rows-dir":
            rows = os.path.abspath(args[1])
        args = args[2:]
    for d in (out, os.path.join(run, "batches"), rows):
        os.makedirs(d, exist_ok=True)
    psha, ssha = sha(PROMPT), sha(SCHEMA)
    staged = sorted(d for d in os.listdir(os.path.join(run, "tasks")) if d.startswith("rts_task_"))
    order = {t: k for k, t in enumerate(staged, 1)}               # batch numbers are stable across --only renders
    n = 0
    for tid in (only or staged):
        if tid not in order:
            print("not staged, skipped:", tid)
            continue
        side = json.load(open(os.path.join(run, "sidecar", tid + ".json")))
        sep = side["bootstrap"]["separator_line"]
        total = sep + side["test_state_lines"]
        k = order[tid]
        json.dump([tid], open(os.path.join(run, "batches", f"batch_{k:04d}.json"), "w"))
        open(os.path.join(out, f"rst__{tid}.txt"), "w").write(prompt_for(run, tid, k, sep, total, psha, ssha, rows))
        n += 1
    json.dump({"prompt": PROMPT, "prompt_sha256": psha, "schema": SCHEMA, "schema_sha256": ssha, "rendered": n,
               "prompt_dir": out, "rows_dir": rows}, open(os.path.join(os.path.dirname(out), "render_manifest.json"), "w"), indent=1)
    print(f"{n} prompts rendered into {out} | prompt {psha[:12]} | schema {ssha[:12]}")


if __name__ == "__main__":
    main()
