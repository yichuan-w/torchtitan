#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""EvolveBench judging agent (EVOLVE_BENCH.md, Standard, "Judging agent").

For each accepted rewrite it puts the tested student's attempts on the parent
(the signal the rewriter read) and on the rewrite (the measurement), the
parent and rewrite packages, their diffs and the rewriter's own notes into one
blinded bundle, and asks Claude Code (`claude -p`, read-only, empty home, no
settings, the same clean call as `evolve_bench.py audit-run --runner
claude-local`) the doc's questions under `agents/evolve_judge.md`. The answer
is one verdict per rewrite: worth or not, a category, the reason and verbatim
evidence quotes.

    judge   an arm root (+ the evals that measured its rewrites, or for a
            training root its own later groups) -> <out>/verdicts.jsonl
            or a calibration spec (+ --alias NAME=PATH) -> the same
    score   verdicts.jsonl + spec -> agreement with the expected verdicts,
            above all the miss rate: rewrites not worth anything judged worth

The rewrite's attempts come from `--eval` (bench: the eval dirs that scored
the accepted revisions; `--label` maps a task id to the eval's label, e.g.
`A4__{task}` for a combined evalset) or, without `--eval`, from the root's own
runs: the first `--groups` groups sampled on the result revision (a training
run's measurement of a new revision).

Every run is resumable: a bundle or verdict already written is kept.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

_EVO = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVO.parent))
sys.path.insert(0, str(_EVO))
import layout  # noqa: E402

RUBRIC = _EVO / "agents" / "evolve_judge.md"
JUDGE_TOOLS = ("Read", "Grep", "Glob")
JUDGE_TIMEOUT_SEC = 1800
JUDGE_MAX_ATTEMPTS = 3
DEFAULT_MODEL = "claude-opus-5-5"
CATEGORIES = (
    "broken_environment", "leak", "unstated_requirement", "claimed_check_missing", "bypass",
    "lengthened_work", "core_work_given", "no_new_judgement", "no_effect", "off_target", "meaningful",
)
HELP_KINDS = ("repairs_task", "supplies_information", "gives_method", "does_core_work")
PACKAGE_PARTS = ("instruction.md", "tests", "solution", "environment")
MAX_COPY_BYTES = 1_000_000
OUT_HEAD, OUT_TAIL = 700, 900
KEYS_MAX, ANALYSIS_MAX, VERIFIER_TAIL = 2000, 700, 3000


# --------------------------------------------------------------------- attempts


def _read_rollout(path: Path) -> tuple[dict, list[dict]]:
    lines = path.read_text(errors="replace").splitlines()
    header = json.loads(lines[0]) if lines else {}
    turns = []
    for line in lines[1:]:
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return header, turns


def _read_header(path: Path) -> dict:
    with open(path, errors="replace") as f:
        return json.loads(f.readline() or "{}")


def _clip(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail + 40:
        return text
    return f"{text[:head]}\n[... {len(text) - head - tail} chars cut ...]\n{text[-tail:]}"


def _keys(turn: dict) -> str:
    k = turn.get("keystrokes")
    if isinstance(k, list):
        return "".join(str(x) for x in k)
    if isinstance(k, str):
        return k
    return str(turn.get("cmd") or turn.get("raw") or "")


def _verifier_tail(header: dict) -> str:
    v = header.get("verifier") or {}
    if isinstance(v, dict) and "output_tail" not in v and isinstance(v.get("shadow"), dict):
        v = v["shadow"]
    tail = v.get("output_tail") if isinstance(v, dict) else None
    return (tail or "")[-VERIFIER_TAIL:]


def is_pass(header: dict) -> bool:
    return (header.get("reward") or 0) >= 1.0


def render_attempt(path: Path) -> str:
    """One attempt as markdown: outcome and verifier tail, then every turn's
    keystrokes, the student's analysis and a shortened terminal output."""
    header, turns = _read_rollout(path)
    fail = header.get("failure") or {}
    out = [
        f"# attempt: {'PASS' if is_pass(header) else 'FAIL'} (reward {header.get('reward')})",
        f"finish_reason: {header.get('finish_reason')}; submitted: {header.get('submitted')}; "
        f"turns: {header.get('turns', len(turns))}; secs: {header.get('secs')}; budget_sec: {header.get('budget_sec')}",
    ]
    if fail:
        out.append(f"failure: {json.dumps(fail, ensure_ascii=False)[:400]}")
    out += ["", "## verifier output (tail)", "```", _verifier_tail(header) or "(none recorded)", "```", ""]
    for i, t in enumerate(turns):
        out.append(f"## turn {t.get('turn', i + 1)}")
        analysis = " ".join(str(t.get(k) or "") for k in ("analysis", "plan")).strip()
        if analysis:
            out.append(f"analysis: {_clip(analysis, ANALYSIS_MAX, 0)}")
        keys = _keys(t)
        if keys:
            out += ["keystrokes:", "```", _clip(keys, KEYS_MAX, 0), "```"]
        output = str(t.get("output") or "")
        if output:
            out += ["output:", "```", _clip(output, OUT_HEAD, OUT_TAIL), "```"]
    return "\n".join(out) + "\n"


def _group_of(path: Path) -> int:
    m = re.search(r"g(-?\d+)-r(\d+)\.jsonl$", path.name)
    return int(m.group(1)) if m else 0


def _rollout_of(path: Path) -> int:
    m = re.search(r"-r(\d+)\.jsonl$", path.name)
    return int(m.group(1)) if m else 0


def signal_attempts(root: Path, rw: dict) -> list[Path]:
    """The attempts the rewriter read: the signal group, runs/<run>/rollouts/<task>/g<G>-r*.jsonl."""
    signal = rw.get("signal") or ""
    m = re.fullmatch(r"(.+)/(.+)--g(-?\d+)", signal)
    if not m:
        return []
    run, task, group = m.group(1), m.group(2), m.group(3)
    paths = sorted(glob.glob(glob.escape(str(root / "runs" / run / "rollouts" / task)) + f"/g{group}-r*.jsonl"),
                   key=lambda p: _rollout_of(Path(p)))
    return [Path(p) for p in paths]


def run_attempts(root: Path, task: str, rev: int, groups: int) -> list[Path]:
    """A training root's own measurement of a revision: the first ``groups``
    groups sampled on it, across the run's restart directories (a group rerun
    after a restart keeps its latest records)."""
    latest: dict[tuple[int, int], tuple[str, Path]] = {}
    for d in sorted(glob.glob(glob.escape(str(root / "runs")) + "/*/rollouts/" + glob.escape(task))):
        for p in map(Path, glob.glob(glob.escape(d) + "/g*-r*.jsonl")):
            try:
                h = _read_header(p)
            except (OSError, json.JSONDecodeError):
                continue
            if int(h.get("rev") or 0) != rev:
                continue
            key = (_group_of(p), _rollout_of(p))
            run = str(h.get("run") or d)
            if key not in latest or run >= latest[key][0]:
                latest[key] = (run, p)
    by_group: dict[int, list[Path]] = collections.defaultdict(list)
    for (g, _), (_, p) in latest.items():
        by_group[g].append(p)
    chosen = sorted(by_group)[:groups]
    return [p for g in chosen for p in sorted(by_group[g], key=_rollout_of)]


def _eval_dirs(ev: Path) -> list[Path]:
    """Where an eval's records can be: the eval dir, its controller attempt
    dirs, and the cluster output it was copied from (launch.json local_output)."""
    bases = [ev]
    launch = ev / "launch.json"
    if launch.exists():
        local = (json.loads(launch.read_text()).get("cluster") or {}).get("local_output")
        if local:
            bases.append(Path(local))
    dirs = []
    for b in bases:
        dirs.append(b)
        dirs.extend(sorted(Path(p) for p in glob.glob(glob.escape(str(b)) + "--attempt-*")))
    return dirs


def eval_attempts(evals: list[Path], label: str) -> list[Path]:
    """A label's attempts in the evals that scored it: the directory holding
    the most graded records (ties: the latest), as analyze.py reads a
    restarted eval."""
    best: tuple[int, int, list[Path]] = (-1, -1, [])
    order = 0
    for ev in evals:
        for d in _eval_dirs(ev):
            order += 1
            paths = sorted(Path(p) for p in glob.glob(glob.escape(str(d / "validation_rollouts" / label)) + "/*.jsonl"))
            if not paths:
                continue
            graded = 0
            for p in paths:
                try:
                    graded += not _read_header(p).get("infra_failed")
                except (OSError, json.JSONDecodeError):
                    pass
            if (graded, order) > best[:2]:
                best = (graded, order, paths)
    return sorted(best[2], key=lambda p: (_group_of(p), _rollout_of(p)))


def _summary(paths: list[Path]) -> dict:
    graded = [h for h in (_read_header(p) for p in paths) if not h.get("infra_failed")]
    failed = [h for h in graded if not is_pass(h)]
    return {"n": len(graded), "solved": sum(is_pass(h) for h in graded), "infra": len(paths) - len(graded),
            "finish_failed": dict(collections.Counter(h.get("finish_reason") or "?" for h in failed))}


# ----------------------------------------------------------------------- bundle


def _copy_package(src: Path, dst: Path) -> None:
    """The package minus files too large to read (a placeholder names them)."""
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_COPY_BYTES:
            target.with_name(target.name + ".omitted.txt").write_text(f"{rel}: {size} bytes, not copied\n")
        else:
            shutil.copyfile(p, target)


def _diff(cwd: Path, a: str, b: str) -> str:
    out = subprocess.run(["diff", "-ruN", a, b], capture_output=True, text=True, errors="replace", cwd=cwd).stdout
    return re.sub(r"^((?:---|\+\+\+) \S+)\t.*$", r"\1", out, flags=re.M)


def _scrub(text: str, roots: list[Path]) -> str:
    for r in roots:
        for needle in (str(r.resolve()), str(r), r.resolve().name):
            if needle and len(needle) > 3:
                text = text.replace(needle, "<root>")
    return text


def _last_agent_message(rw_dir: Path) -> str:
    """The author session's closing message (evolve_bench.py's reading)."""
    for j in sorted(rw_dir.glob("sessions/*--agent/codex/sessions/*/*/*/*.jsonl")):
        last = ""
        for line in open(j, errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "assistant":
                last = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
        if last:
            return last
    return ""


def _write_attempts(dst: Path, paths: list[Path]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    rows = ["| attempt | reward | finish_reason | turns | secs |", "|---|---|---|---|---|"]
    k = 0
    for p in paths:
        h = _read_header(p)
        if h.get("infra_failed"):
            continue
        k += 1
        name = f"a{k:02d}.md"
        (dst / name).write_text(render_attempt(p))
        rows.append(f"| {name} | {h.get('reward')} | {h.get('finish_reason')} | {h.get('turns')} | {h.get('secs')} |")
    s = _summary(paths)
    head = (f"{s['solved']} of {s['n']} graded attempts passed"
            + (f"; {s['infra']} infrastructure failures left out" if s["infra"] else "")
            + (f"; failed attempts ended: {json.dumps(s['finish_failed'])}" if s["finish_failed"] else ""))
    if not k:
        head = "No attempts were recorded on this version."
    (dst / "index.md").write_text(head + "\n\n" + "\n".join(rows) + "\n")


def write_bundle(dst: Path, item: dict) -> None:
    """One rewrite's judge bundle. Nothing in it names the arm or the root."""
    tmp = dst.with_name(dst.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    task_dir = item["root"] / "evolution" / "tasks" / item["task"]
    rw_dir = task_dir / "rewrites" / item["rewrite"]
    rw = json.loads((rw_dir / "rewrite.json").read_text())
    (tmp / "direction.txt").write_text(f"{rw['job']}\n")
    _copy_package(task_dir / f"r{rw['input_rev']}", tmp / "parent")
    _copy_package(task_dir / f"r{rw['result_rev']}", tmp / "rewrite")
    for part in PACKAGE_PARTS:
        (tmp / f"diff_{part.replace('.md', '')}.txt").write_text(_diff(tmp, f"parent/{part}", f"rewrite/{part}"))
    roots = [item["root"]]
    author = tmp / "author"
    author.mkdir()
    for p in sorted((rw_dir / "notes").glob("*")) if (rw_dir / "notes").is_dir() else []:
        if p.is_file() and p.stat().st_size <= MAX_COPY_BYTES:
            (author / p.name).write_text(_scrub(p.read_text(errors="replace"), roots))
    for key in ("simplify", "repair"):
        if rw.get(key):
            (author / f"{key}.json").write_text(_scrub(json.dumps(rw[key], indent=1, ensure_ascii=False), roots))
    closing = _last_agent_message(rw_dir)
    (author / "closing_message.md").write_text(_scrub(closing, roots) if closing else "(no closing message recorded)\n")
    _write_attempts(tmp / "attempts_parent", item["parent_attempts"])
    _write_attempts(tmp / "attempts_rewrite", item["rewrite_attempts"])
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)


# ------------------------------------------------------------------------ items


def _item_id(root: Path, task: str, rewrite: str, after: str) -> str:
    return hashlib.sha256(f"{root.resolve()}|{task}|{rewrite}|{after}".encode()).hexdigest()[:16]


def make_item(*, root: Path, task: str, rewrite: str, evals: list[Path], label: str, groups: int,
              name: str | None = None, expect: dict | None = None) -> dict:
    rw_dir = root / "evolution" / "tasks" / task / "rewrites" / rewrite
    rw = json.loads((rw_dir / "rewrite.json").read_text())
    if rw.get("status") != "accepted" or rw.get("result_rev") is None:
        raise SystemExit(f"{rw_dir}: not an accepted rewrite")
    parent = signal_attempts(root, rw)
    if evals:
        lab = label.format(task=task)
        after_src = "eval:" + ",".join(str(e.resolve()) for e in evals) + f"#{lab}"
        after = eval_attempts(evals, lab)
    else:
        after_src = f"run-groups:{groups}"
        after = run_attempts(root, task, int(rw["result_rev"]), groups)
    return {
        "id": _item_id(root, task, rewrite, after_src), "name": name, "root": root, "task": task,
        "rewrite": rewrite, "job": rw["job"], "input_rev": rw["input_rev"], "result_rev": rw["result_rev"],
        "after_source": after_src, "parent_attempts": parent, "rewrite_attempts": after, "expect": expect,
    }


def items_from_root(root: Path, *, evals: list[Path], label: str, groups: int, tasks: set[str] | None,
                    job: str | None) -> list[dict]:
    items = []
    for p in sorted((root / "evolution" / "tasks").glob("*/rewrites/*/rewrite.json")):
        rw = json.loads(p.read_text())
        if rw.get("status") != "accepted" or (job and rw.get("job") != job):
            continue
        task = p.parent.parent.parent.name
        if tasks and task not in tasks:
            continue
        items.append(make_item(root=root, task=task, rewrite=p.parent.name, evals=evals, label=label, groups=groups))
    return items


def _resolve(value: str, aliases: dict[str, Path]) -> Path:
    head, _, rest = value.partition("/")
    base = aliases.get(head)
    if base is None:
        raise SystemExit(f"{value}: no --alias {head}=<path>")
    return base / rest if rest else base


def items_from_spec(spec: dict, aliases: dict[str, Path], only: set[str] | None = None) -> list[dict]:
    items = []
    for s in spec["items"]:
        if only and s["name"] not in only and s.get("group") not in only:
            continue
        after = s.get("after") or {}
        items.append(make_item(
            root=_resolve(s["root"], aliases), task=s["task"], rewrite=s["rewrite"],
            evals=[_resolve(e, aliases) for e in after.get("evals", [])], label=after.get("label", "{task}"),
            groups=int(after.get("groups", 1)), name=s["name"],
            expect={"worth": s["expect"]["worth"], "category": s["expect"].get("category"), "group": s.get("group")},
        ))
    return items


# ------------------------------------------------------------------------ judge


def judge_argv(model: str, rubric: Path) -> list[str]:
    """The clean, read-only Claude Code call `audit-run --runner claude-local` makes."""
    return [
        "claude", "-p", "--bare", "--restricted", "--setting-sources", "", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
        "--no-session-persistence", "--system-prompt-file", str(rubric),
        "--model", model, "--tools", *JUDGE_TOOLS, "--output-format", "json",
        "--permission-prompts", "none",
    ]


def _judge_env(home: Path) -> dict[str, str]:
    """No caller environment: the CLI reaches the model through its own
    installed provider, with an empty home and no settings."""
    claude = shutil.which("claude")
    dirs = [str(Path(claude).parent)] if claude else []
    dirs.extend(("/usr/local/bin", "/usr/bin", "/bin"))
    config = home / "claude-config"
    config.mkdir(parents=True, exist_ok=True)
    return {"PATH": os.pathsep.join(dict.fromkeys(dirs)), "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config)}


def _cli_version() -> str:
    with tempfile.TemporaryDirectory(prefix="evolve-judge-version-") as raw:
        proc = subprocess.run(["claude", "--version"], env=_judge_env(Path(raw)), capture_output=True, text=True,
                              check=False)
    version = (proc.stdout or proc.stderr).strip()
    if proc.returncode or not version:
        raise SystemExit(f"claude --version failed: {version[-200:]}")
    return version.splitlines()[-1]


def parse_reply(stdout: str) -> tuple[dict, dict]:
    """Claude's JSON envelope and the verdict object inside its result."""
    lines = [ln for ln in stdout.splitlines() if ln.lstrip().startswith("{")]
    envelope = json.loads(lines[-1] if lines else stdout)
    payload = envelope.get("structured_output", envelope.get("result", envelope))
    if isinstance(payload, str):
        text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", payload)
        payload = json.loads(text[text.find("{"): text.rfind("}") + 1])
    if not isinstance(payload, dict):
        raise ValueError("judge result is not a JSON object")
    return payload, envelope


def validate(v: dict, job: str) -> list[str]:
    """What is wrong with a verdict; empty when it is usable."""
    errs = []
    if not isinstance(v.get("worth"), bool):
        errs.append("worth is not a boolean")
    if v.get("category") not in CATEGORIES:
        errs.append(f"category {v.get('category')!r} unknown")
    elif isinstance(v.get("worth"), bool) and v["worth"] != (v["category"] == "meaningful"):
        errs.append("worth must be true exactly when category is meaningful")
    if job == "easier" and v.get("help_kind") not in HELP_KINDS:
        errs.append(f"help_kind {v.get('help_kind')!r} is not one of {HELP_KINDS}")
    if not isinstance(v.get("reason"), str) or not v["reason"].strip():
        errs.append("reason missing")
    ev = v.get("evidence")
    if not isinstance(ev, list) or not ev or not all(isinstance(e, dict) and e.get("quote") for e in ev):
        errs.append("evidence must be a non-empty list of quotes")
    return errs


def _prompt(item: dict) -> str:
    return (
        f"Judge the {item['job']} rewrite in this directory under your instructions: read attempts_parent/index.md, "
        "attempts_rewrite/index.md, the diffs and author/, then the attempts. Return exactly one JSON object in "
        "the output shape of your instructions. Do not use markdown or add commentary."
    )


def judge_one(item: dict, bundle: Path, out: Path, *, argv: list[str], meta: dict, log, lock) -> bool:
    for attempt in range(1, JUDGE_MAX_ATTEMPTS + 1):
        try:
            with tempfile.TemporaryDirectory(prefix="evolve-judge-") as raw:
                proc = subprocess.run(argv, input=_prompt(item), capture_output=True, text=True, cwd=bundle,
                                      env=_judge_env(Path(raw)), timeout=JUDGE_TIMEOUT_SEC, check=False)
            if proc.returncode:
                raise RuntimeError(f"claude exited {proc.returncode}: {(proc.stdout or proc.stderr)[-300:]}")
            verdict, envelope = parse_reply(proc.stdout)
            errs = validate(verdict, item["job"])
            if errs:
                raise ValueError("; ".join(errs))
            record = {**verdict, "metadata": {
                **meta, "usage": envelope.get("usage"), "cost_usd": envelope.get("total_cost_usd"),
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}}
            layout.write_json_atomic(out, record)
            with lock:
                log(f"item={item['id']} task={item['task']} done attempt={attempt} worth={verdict['worth']} "
                    f"category={verdict['category']}")
            return True
        except Exception as exc:  # retried; the last failure is logged and the item left open
            with lock:
                log(f"item={item['id']} task={item['task']} failed attempt={attempt} {type(exc).__name__}: {exc}")
    return False


def _verdict_row(item: dict, verdict: dict | None) -> dict:
    row = {
        "id": item["id"], "name": item["name"], "root": str(item["root"].resolve()), "task": item["task"],
        "rewrite": item["rewrite"], "job": item["job"], "input_rev": item["input_rev"],
        "result_rev": item["result_rev"], "after_source": item["after_source"],
        "parent": _summary(item["parent_attempts"]), "after": _summary(item["rewrite_attempts"]),
    }
    if item.get("expect") is not None:
        row["expect"] = item["expect"]
    if verdict is None:
        row["verdict"] = None
    else:
        row["verdict"] = {k: v for k, v in verdict.items() if k != "metadata"}
        row["model"] = verdict.get("metadata", {}).get("model")
    return row


def run_judge(items: list[dict], *, out: Path, model: str, concurrency: int, dry_run: bool) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for bad in [out, *out.resolve().parents]:
        if (bad / "CLAUDE.md").is_file():
            raise SystemExit(f"refusing an output directory under {bad / 'CLAUDE.md'}: the judge would read it")
    rubric = out / "RUBRIC.md"
    shutil.copyfile(RUBRIC, rubric)
    logf = out / "judge.log"
    lock = threading.Lock()

    def log(msg: str) -> None:
        line = f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')} {msg}"
        with open(logf, "a") as f:
            f.write(line + "\n")

    ids = collections.Counter(i["id"] for i in items)
    if any(n > 1 for n in ids.values()):
        raise SystemExit(f"duplicate items: {[k for k, n in ids.items() if n > 1]}")
    for item in items:
        b = out / "bundles" / item["id"]
        if not b.exists():
            write_bundle(b, item)
            log(f"item={item['id']} task={item['task']} bundle parent={len(item['parent_attempts'])} "
                f"rewrite={len(item['rewrite_attempts'])}")
    argv = judge_argv(model, rubric)
    meta = {"model": model, "rubric_sha256": layout.sha256_file(rubric), "argv": argv}
    todo = []
    for item in items:
        vp = out / "verdicts" / f"{item['id']}.json"
        try:
            if not validate(json.loads(vp.read_text()), item["job"]):
                continue
        except (OSError, json.JSONDecodeError):
            pass
        todo.append(item)
    if not dry_run and todo:
        meta["cli_version"] = _cli_version()
        (out / "verdicts").mkdir(exist_ok=True)
        layout.write_json_atomic(out / "judge_run.json", {
            **meta, "concurrency": concurrency, "items": len(items),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        log(f"start items={len(todo)} of {len(items)} model={model} cli={meta['cli_version']}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(judge_one, i, out / "bundles" / i["id"], out / "verdicts" / f"{i['id']}.json",
                                   argv=argv, meta=meta, log=log, lock=lock) for i in todo]
            done = sum(f.result() for f in futures)
        log(f"finished done={done} failed={len(todo) - done}")
    rows = []
    for item in items:
        vp = out / "verdicts" / f"{item['id']}.json"
        verdict = json.loads(vp.read_text()) if vp.exists() else None
        rows.append(_verdict_row(item, verdict))
    path = out / "verdicts.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    judged = [r for r in rows if r["verdict"]]
    cats = collections.Counter(r["verdict"]["category"] for r in judged)
    print(f"{len(judged)}/{len(rows)} judged; worth {sum(r['verdict']['worth'] for r in judged)}; "
          f"categories {dict(cats)} -> {path}")
    return path


# ------------------------------------------------------------------------ score


def score(rows: list[dict]) -> dict:
    """Agreement with the expected verdicts. The miss rate is the share of
    rewrites expected not worth anything that the judge called worth."""
    rows = [r for r in rows if r.get("expect") is not None]
    judged = [r for r in rows if r.get("verdict")]
    agree = [r for r in judged if r["verdict"]["worth"] == r["expect"]["worth"]]
    neg = [r for r in judged if not r["expect"]["worth"]]
    pos = [r for r in judged if r["expect"]["worth"]]
    with_cat = [r for r in judged if r["expect"].get("category")]
    groups: dict[str, dict] = {}
    for g in sorted({r["expect"].get("group") or "-" for r in judged}):
        rs = [r for r in judged if (r["expect"].get("group") or "-") == g]
        groups[g] = {"n": len(rs), "agree": sum(r["verdict"]["worth"] == r["expect"]["worth"] for r in rs),
                     "categories": dict(collections.Counter(r["verdict"]["category"] for r in rs))}

    def rate(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    return {
        "items": len(rows), "judged": len(judged),
        "agreement": {"agree": len(agree), "n": len(judged), "rate": rate(len(agree), len(judged))},
        "miss": {"judged_worth": sum(r["verdict"]["worth"] for r in neg), "n": len(neg),
                 "rate": rate(sum(r["verdict"]["worth"] for r in neg), len(neg)),
                 "items": [r["name"] for r in neg if r["verdict"]["worth"]]},
        "false_reject": {"judged_not_worth": sum(not r["verdict"]["worth"] for r in pos), "n": len(pos),
                         "items": [r["name"] for r in pos if not r["verdict"]["worth"]]},
        "category": {"agree": sum(r["verdict"]["category"] == r["expect"]["category"] for r in with_cat),
                     "n": len(with_cat),
                     "items": {r["name"]: [r["expect"]["category"], r["verdict"]["category"]] for r in with_cat}},
        "groups": groups,
    }


# ------------------------------------------------------------------------- main


def _aliases(values: list[str]) -> dict[str, Path]:
    out = {}
    for v in values:
        name, sep, path = v.partition("=")
        if not sep:
            raise SystemExit(f"--alias {v}: expected NAME=PATH")
        out[name] = Path(path)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("judge", help="bundle accepted rewrites and judge each one")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--root", type=Path, help="an arm root or a training root (evolution/ and runs/)")
    src.add_argument("--spec", type=Path, help="a calibration spec (judge_calibration.json)")
    p.add_argument("--alias", action="append", default=[], help="with --spec: NAME=PATH for the spec's roots and evals")
    p.add_argument("--only", nargs="*", help="with --spec: item names or groups to judge")
    p.add_argument("--eval", type=Path, nargs="*", default=[], help="with --root: evals that scored the rewrites")
    p.add_argument("--label", default="{task}", help="with --eval: the eval label of a task (default {task})")
    p.add_argument("--groups", type=int, default=1,
                   help="without --eval: how many groups on the result revision the root's runs supply")
    p.add_argument("--task", nargs="*", help="with --root: only these tasks")
    p.add_argument("--job", choices=("harder", "easier"), help="with --root: only this direction")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--dry-run", action="store_true", help="write bundles and verdicts.jsonl, call no model")
    p = sub.add_parser("score", help="agreement of verdicts.jsonl with the spec's expected verdicts")
    p.add_argument("--verdicts", type=Path, required=True)
    p.add_argument("--out", type=Path)
    a = ap.parse_args()
    if a.cmd == "judge":
        if a.spec:
            items = items_from_spec(json.loads(a.spec.read_text()), _aliases(a.alias), set(a.only or []) or None)
        else:
            items = items_from_root(a.root, evals=a.eval, label=a.label, groups=a.groups,
                                    tasks=set(a.task) if a.task else None, job=a.job)
        if a.concurrency < 1:
            raise SystemExit("--concurrency must be at least 1")
        run_judge(items, out=a.out, model=a.model, concurrency=a.concurrency, dry_run=a.dry_run)
    else:
        rows = [json.loads(line) for line in a.verdicts.read_text().splitlines() if line.strip()]
        result = score(rows)
        if a.out:
            layout.write_json_atomic(a.out, result)
        print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
