#!/usr/bin/env python3
"""Stage RST (Harbor-format) tasks for the v13-rst judge, and extract per-task facts that need no judgment.

Staged tree, per task (three files, the names every v13 tool keys on):
  instruction.md   verbatim
  setup.sh         a two-line header, environment/Dockerfile verbatim, then every build-context file a COPY/ADD line
                   brings into the image, each under a header line. Context files no COPY/ADD names are NOT in the
                   image and are not staged. solution/, the generator's metadata JSONs and docker-compose files are
                   never staged: none of them reaches the training sandbox.
  tests/test.sh    the harness bootstrap (tests/test.sh) verbatim + one separator line + tests/test_state.py verbatim

Sidecar (sidecar/<task_id>.json): bootstrap facts (which fetches, set -e, fallback trap, the --with packages, the line
span), Dockerfile facts (FROM, WORKDIR, CMD/ENTRYPOINT, whether it installs curl), the runtime-gate outcome, the
static flags of the RST campaign and the two harness-compatibility classes. These are deterministic, so code reads
them, not the judge; lint_v13_rst.py checks the judge's bootstrap_fetch against them.

SECURITY: these files go to a third-party API. A task is excluded when ANY signal calls it security: the
security_union column (category/tag union, all 37,484 tasks), an is_security_shaped label from the three earlier LLM
labelling runs, or the dedicated instruction screen (security_screen.tsv in the run directory). The stager refuses to
stage anything when that screen is missing, and excludes every task the screen did not cover.

Usage: RST_DATA_ROOT=<campaign dir> stage_rst.py <run_dir>   (run_dir holds requested_ids.txt, security_screen.tsv)
"""
import csv
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
V13 = os.path.dirname(HERE)
OF = os.path.dirname(V13)
# The RST campaign directory: task trees, the security_union table, the static flags, the runtime-gate results.
RST = os.environ.get("RST_DATA_ROOT", "")
ROOTS = {"easy": RST + "/tasks_easy", "nonEasy": RST + "/tasks_nonEasy"}
DOMAIN_TSV = RST + "/inspect/v3/domain/rts_task_tb21_domain_v2.tsv"
LLM_LABELS = [RST + "/inspect/v3/domain/" + n for n in ("sample2k_labels.tsv", "topup_labels.tsv", "pilot_labels.tsv")]
GATES = {"easy": RST + "/gate_easy", "nonEasy": RST + "/gate"}
FLAGS = RST + "/inspect/flags"
HEADER = ("# This corpus ships no setup.sh. What the image bakes in is its Dockerfile, reproduced below\n"
          "# verbatim so the judge sees the shipped state. It is a Dockerfile, not a shell script.\n")
SEP = "# ---------- end of harness bootstrap; below is tests/test_state.py, the verifier it runs ----------\n"
MAX_SHOW_BYTES, MAX_SHOW_LINES = 65536, 1500     # a repo-building script of 12 KB must be shown whole
for _d in (os.path.join(OF, "v12"), os.path.join(V13, "tools")):    # the candidate extractor, wherever this checkout keeps it
    sys.path.insert(0, _d)
import candidates_extract as ce  # noqa: E402


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read(p):
    return open(p, errors="replace").read() if os.path.exists(p) else ""


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes")


def security_signals(ids, run_dir):
    sig = {}
    with open(DOMAIN_TSV) as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["task_id"] in ids and truthy(r.get("security_union")):
                sig.setdefault(r["task_id"], []).append("security_union")
    for p in LLM_LABELS:
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            rd = csv.DictReader(fh, delimiter="\t")
            for r in rd:
                tid = r.get("task_id") or r[rd.fieldnames[0]]
                if tid in ids and truthy(r.get("is_security_shaped")):
                    sig.setdefault(tid, []).append(os.path.basename(p) + ":is_security_shaped")
    screen = os.path.join(run_dir, "security_screen.tsv")
    if not os.path.exists(screen):
        log("FATAL: security_screen.tsv is missing. Nothing is staged without the instruction screen.")
        sys.exit(2)
    covered = set()
    for line in open(screen):
        p = line.rstrip("\n").split("\t")
        if len(p) < 2 or not p[0].startswith("rts_task_"):
            continue
        covered.add(p[0])
        if p[1].strip() != "0":
            sig.setdefault(p[0], []).append("instruction_screen:" + (p[2].strip() if len(p) > 2 else "1"))
    for tid in ids - covered:
        if tid not in sig:
            sig[tid] = ["not_covered_by_instruction_screen"]
    return sig


def instructions(df):
    """Dockerfile instructions as (first_line_no, text), heredoc bodies skipped, continuation lines joined."""
    lines, out, i = df.split("\n"), [], 0
    while i < len(lines):
        start, text = i + 1, lines[i]
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", text)
        j = i + 1
        if m:
            while j < len(lines) and lines[j].strip() != m.group(1):
                j += 1
        if m and j < len(lines):           # skip a heredoc body only when its terminator really exists
            i = j
        else:
            while text.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                text = text.rstrip()[:-1] + " " + lines[i].strip()
        out.append((start, text))
        i += 1
    return out


def copy_sources(ins):
    """[(line_no, [source, ...])] for COPY/ADD lines that read the build context."""
    out = []
    for n, text in ins:
        m = re.match(r"\s*(COPY|ADD)\s+(.*)$", text, re.I)
        if not m or "--from=" in m.group(2):
            continue
        rest = re.sub(r"--[a-z\-]+(=\S+)?\s+", "", m.group(2).strip())
        try:
            toks = json.loads(rest) if rest.startswith("[") else shlex.split(rest)
        except ValueError:
            toks = rest.split()
        srcs = [s for s in toks[:-1] if not re.match(r"https?://", s)]
        if srcs:
            out.append((n, srcs))
    return out


def context_files(env):
    out = []
    for d, _, fs in os.walk(env):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), env)
            if rel != "Dockerfile":
                out.append(rel)
    return sorted(out)


def matched(rel, src):
    s = src.strip()
    while s.startswith("./"):
        s = s[2:]
    s = s.rstrip("/")
    if s in ("", "."):
        return True
    return rel == s or rel.startswith(s + "/") or fnmatch.fnmatch(rel, s)


def stage_setup(src_dir):
    env = os.path.join(src_dir, "environment")
    df = read(os.path.join(env, "Dockerfile"))
    body = HEADER + df + ("" if df.endswith("\n") else "\n")
    ins = instructions(df)
    ctx = context_files(env)
    shown, stubbed, used = [], [], set()
    for n, srcs in copy_sources(ins):
        for rel in ctx:
            if rel in used or not any(matched(rel, s) for s in srcs):   # a COPY'd compose file IS shipped state
                continue
            used.add(rel)
            raw = open(os.path.join(env, rel), "rb").read()
            head = f"# ---------- build-context file environment/{rel}, brought into the image by setup.sh:{n + 2} "
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                body += head + f"[not shown: binary, {len(raw)} bytes] ----------\n"
                stubbed.append(rel)
                continue
            lines = text.split("\n")
            if len(raw) > MAX_SHOW_BYTES or len(lines) > MAX_SHOW_LINES:
                keep, size = [], 0
                for ln in lines[:MAX_SHOW_LINES]:
                    size += len(ln) + 1
                    if size > MAX_SHOW_BYTES:
                        break
                    keep.append(ln)
                body += head + f"[first {len(keep)} of {len(lines)} lines shown, {len(raw)} bytes in all] ----------\n"
                body += "\n".join(keep) + "\n"
                stubbed.append(rel)
            else:
                body += head + "----------\n" + text + ("" if text.endswith("\n") else "\n")
            shown.append(rel)
    facts = {"from_image": [m.group(1) for _, t in ins for m in [re.match(r"\s*FROM\s+(\S+)", t, re.I)] if m],
             "workdir": ([m.group(1) for _, t in ins for m in [re.match(r"\s*WORKDIR\s+(\S+)", t, re.I)] if m] or [None])[-1],
             "user": ([m.group(1) for _, t in ins for m in [re.match(r"\s*USER\s+(\S+)", t, re.I)] if m] or [None])[-1],
             "has_cmd": any(re.match(r"\s*CMD\b", t, re.I) for _, t in ins),
             "cmd": ([re.sub(r"\s+", " ", m.group(1))[:160] for _, t in ins for m in [re.match(r"\s*CMD\s+(.+)$", t, re.I)] if m] or [None])[-1],
             "has_entrypoint": any(re.match(r"\s*ENTRYPOINT\b", t, re.I) for _, t in ins),
             "curl_installed_by_dockerfile": any(re.search(r"(apt-get|apt|apk|yum|dnf)\s+(install|add)[^\n]*\bcurl\b", t)
                                                 for _, t in ins),
             "context": {"copied_shown": shown, "copied_stubbed_or_truncated": stubbed,
                         "present_but_never_copied": len([r for r in ctx if r not in used
                                                          and not r.startswith("docker-compose")]),
                         "compose_file": any(r.startswith("docker-compose") for r in ctx)}}
    return body, facts


FETCH = [("apt", re.compile(r"\bapt(-get)?\s+(update|install)\b")), ("astral.sh", re.compile(r"astral\.sh/uv")),
         ("pypi:uvx", re.compile(r"(^|[\s;&|])(uvx|uv\s+(run|tool|pip))\b")),
         ("pypi:pip", re.compile(r"\bpip3?\s+install\b")), ("git", re.compile(r"\bgit\s+clone\b")),
         ("other", re.compile(r"\b(curl|wget)\b[^\n]*https?://(?!astral\.sh)"))]


def bootstrap_facts(btext):
    lines = btext.split("\n")
    hits, kinds = [], []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("#"):
            i += 1
            continue
        found = [k for k, rx in FETCH if rx.search(ln)]
        if found:
            j = i
            while lines[j].rstrip().endswith("\\") and j + 1 < len(lines):
                j += 1
            hits += [i + 1, j + 1]
            kinds += [k for k in found if k not in kinds]
            i = j
        i += 1
    joined = re.sub(r"\\\n", " ", btext)
    specs = re.findall(r"(?:--with|-w)[ =]+('[^']*'|\"[^\"]*\"|[^\s\\]+)", joined)
    pk = [re.split(r"[=<>!~@ ]", s.strip("'\""), 1)[0] for s in specs]
    local = [s.strip("'\"") for s in specs if "file://" in s]
    pin = re.search(r"(?:^|\s)(?:-p|--python)[ =]+([0-9][0-9.]*)", joined)
    return {"fetches": kinds, "fetch_span": [min(hits), max(hits)] if hits else None,
            "with_packages": sorted(set(pk)), "with_local_path_specs": local, "python_pin": pin.group(1) if pin else None,
            "set_e": bool(re.search(r"(?m)^\s*set\s+-[a-zA-Z]*e", btext)),
            "fallback_trap": bool(re.search(r"(?m)^\s*trap\b", btext)),
            "cd": re.findall(r"(?m)^\s*cd\s+(\S+)", btext),
            "runs_pytest": bool(re.search(r"\bpytest\b", btext)),
            "sha256": hashlib.sha256(btext.encode()).hexdigest()}


def gate_outcome(tid, mix, cache={}):
    if mix not in cache:
        g = GATES[mix]
        passed = set(l.strip() for l in open(os.path.join(g, "pass_ids.txt")) if l.strip())
        dropped = {}
        for l in open(os.path.join(g, "dropped.tsv")):
            p = l.rstrip("\n").split("\t")
            if len(p) >= 2:
                dropped[p[0]] = p[1]
        cache[mix] = (passed, dropped)
    passed, dropped = cache[mix]
    return "pass" if tid in passed else dropped.get(tid, "not_in_gate_output")


def main():
    if not os.path.isdir(os.path.join(RST, "inspect")):
        log("FATAL: set RST_DATA_ROOT to the RST campaign directory (tasks_easy/, tasks_nonEasy/, inspect/, gate*/).")
        sys.exit(2)
    run = os.path.abspath(sys.argv[1])
    want = [l.strip() for l in open(os.path.join(run, "requested_ids.txt")) if l.strip()]
    ids = set(want)
    sec = security_signals(ids, run)
    loc = {t: k for t in want for k, r in ROOTS.items() if os.path.isdir(os.path.join(r, t))}
    allowed = [t for t in want if t in loc and t not in sec]
    log(f"requested {len(want)} | located {len(loc)} | excluded for security {len(sec)} | to stage {len(allowed)}")
    with open(os.path.join(run, "excluded_security.tsv"), "w") as fh:
        for t in sorted(sec):
            fh.write(t + "\t" + ",".join(sec[t]) + "\n")
    cats = {}
    with open(DOMAIN_TSV) as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["task_id"] in ids:
                cats[r["task_id"]] = r.get("rts_category", "")
    flags = {}
    for f in sorted(os.listdir(FLAGS)):
        if f.endswith(".txt"):
            for l in open(os.path.join(FLAGS, f)):
                t = l.split()[0] if l.strip() else ""
                if t in ids:
                    flags.setdefault(t, []).append(f[:-4])
    compat = {}
    for name, col, key in (("tmux_exposure.tsv", "exposed", "tmux_lt_2_3"), ("alpine_exposure.tsv", "predicted_exec_hang", "alpine_exec_hang")):
        p = os.path.join(RST, "inspect/v3", name)
        if os.path.exists(p):
            with open(p) as fh:
                for r in csv.DictReader(fh, delimiter="\t"):
                    if r["task_id"] in ids and truthy(r.get(col)):
                        compat.setdefault(r["task_id"], []).append(key)
    for sub in ("tasks", "sidecar", "candidates"):
        os.makedirs(os.path.join(run, sub), exist_ok=True)
    digests, n = {}, 0
    for tid in allowed:
        src = os.path.join(ROOTS[loc[tid]], tid)
        need = [os.path.join(src, p) for p in ("instruction.md", "environment/Dockerfile", "tests/test.sh", "tests/test_state.py")]
        if not all(os.path.exists(p) for p in need):
            log(f"SKIP {tid}: a required file is missing")
            continue
        dst = os.path.join(run, "tasks", tid)
        os.makedirs(os.path.join(dst, "tests"), exist_ok=True)
        open(os.path.join(dst, "instruction.md"), "w").write(read(need[0]))
        setup, dfacts = stage_setup(src)
        open(os.path.join(dst, "setup.sh"), "w").write(setup)
        btext = read(need[2])
        if not btext.endswith("\n"):
            btext += "\n"
        open(os.path.join(dst, "tests", "test.sh"), "w").write(btext + SEP + read(need[3]))
        boot = bootstrap_facts(btext)
        boot["lines"] = len(btext.splitlines())
        boot["separator_line"] = boot["lines"] + 1
        toml = read(os.path.join(src, "task.toml"))

        def tnum(section, key):
            m = re.search(r"\[" + section + r"\][^\[]*?" + key + r"\s*=\s*([0-9.]+)", toml, re.S)
            return float(m.group(1)) if m else None
        # The rollouter starts an ENTRYPOINT only; a CMD without one never runs. "idle" CMDs (a shell, sleep, tail -f)
        # lose nothing by that; any other CMD is a service or script the training sandbox never starts.
        cmd = dfacts.get("cmd") or ""
        idle = bool(re.fullmatch(r"""[\[\]"',\s]*(/bin/|/usr/bin/)?(bash|sh|zsh|sleep|tail)\b.*""", cmd)) and not re.search(r"&&|;|\.sh|\.py", cmd)
        dfacts["cmd_never_started"] = ("no_cmd" if not cmd else "entrypoint_runs_it" if dfacts["has_entrypoint"]
                                       else "idle_cmd" if idle else "service_or_script_cmd")
        # Generator leftovers: some instructions send the agent to a pipeline JSON ("the required artefacts are listed in
        # /app/rewrite_contract.json") that no Dockerfile line creates, so whatever it would have listed is unstated.
        named = sorted(set(re.findall(r"[\w./-]*(?:rewrite_[a-z_]+|[a-z_]*contract[a-z_]*)\.json", read(need[0]))))
        dfacts["instruction_names_pipeline_json"] = named
        dfacts["pipeline_json_in_image"] = [n for n in named if os.path.basename(n) in setup]
        side = {"task_id": tid, "mix": loc[tid], "rts_category": cats.get(tid, ""), "dockerfile": dfacts, "bootstrap": boot,
                "gate": gate_outcome(tid, loc[tid]), "static_flags": flags.get(tid, []),
                "harness_compat": compat.get(tid, []),
                "task_toml": {"agent_timeout_sec": tnum("agent", "timeout_sec"), "verifier_timeout_sec": tnum("verifier", "timeout_sec"),
                              "cpus": tnum("environment", "cpus"), "memory_mb": tnum("environment", "memory_mb")},
                "test_state_lines": len(read(need[3]).splitlines())}
        json.dump(side, open(os.path.join(run, "sidecar", f"{tid}.json"), "w"), indent=1)
        # candidate inventory: the v12 extractor, with this task's WORKDIR root added to the path pattern
        wd = (dfacts["workdir"] or "/app").strip("/").split("/")[0]
        roots = "app|home|opt|tmp|workspace|srv|var|root|usr/local|data|mnt"
        if wd and wd not in roots.split("|") and re.match(r"^[A-Za-z0-9_.\-]+$", wd):
            roots += "|" + re.escape(wd)
        ce.PATH_RE = re.compile(r"(?<![\w./-])(/(?:" + roots + r")(?:/[A-Za-z0-9_.+-]+)+)")
        c = ce.extract(dst)
        c["task_id"] = tid
        json.dump(c, open(os.path.join(run, "candidates", f"{tid}.json"), "w"), indent=1)
        digests[tid] = {p: hashlib.sha256(open(os.path.join(dst, p), "rb").read()).hexdigest()
                        for p in ("instruction.md", "setup.sh", "tests/test.sh")}
        n += 1
    man = {"run": os.path.basename(run), "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "requested": len(want), "located": len(loc), "excluded_security": len(sec), "staged": n,
           "not_found": sorted(t for t in want if t not in loc),
           "shape": "instruction.md verbatim; setup.sh = 2-line header + environment/Dockerfile verbatim + the build-context "
                    "files its COPY/ADD lines bring in; tests/test.sh = bootstrap verbatim + separator + tests/test_state.py "
                    "verbatim; solution/, generator metadata and compose files never staged",
           "security": "excluded on ANY of: security_union, an earlier LLM is_security_shaped label, the instruction screen, "
                       "or not covered by the screen",
           "file_sha256": digests}
    json.dump(man, open(os.path.join(run, "manifest.json"), "w"), indent=1)
    log(f"DONE: staged {n}; sidecars and candidate lists written; manifest {os.path.join(run, 'manifest.json')}")


if __name__ == "__main__":
    main()
