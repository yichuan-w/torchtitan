#!/usr/bin/env python3
"""Selftest for lint_v13_harbor2.py: a consistent SEED row is built for one non-security TerminalWorld task from its own
files and v21 candidate list, must validate and lint clean; each mutation must raise the lint id it targets; and the
one legitimate bootstrap-evidence shape (B1, an instruction that stops the bootstrap) must lint clean.

Needs the extracted packages (../tb21_agentic_top10/harbor/tw/tasks, not in git) and the v21 candidates. Prints lint
ids and schema messages only, never task text. Exit 1 on any miss.

    python3 lint_selftest_harbor2.py
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
MISSES = []
import json, copy, sys
sys.path.insert(0, '.')
import lint_v13_harbor2 as L, harbor_files as hf
B = '../tb21_agentic_top10/harbor'; T = 'tw_100135'; d = f'{B}/tw/tasks/{T}'
c = json.load(open(f'{B}/tw_v21/candidates/{T}.json')); x = hf.load_texts(d)
inv = [f['line'] for f in c['failing_statements']]
facts = L.bootstrap_facts(x[hf.BOOTSTRAP]); fl = facts['fetch_lines']
bf = f"tests/test.sh:{fl[0]}-{fl[-1]} apt-get -> distro mirrors: curl; curl -> astral.sh: uv {facts['uv_version']}; " \
     f"uvx -> pypi.org: {', '.join(facts['with'])}" + (f"; python {facts['pin']} -> github.com" if facts['pin'] else "")
first_assert = next(f['line'] for f in c['failing_statements'] if f['kind'] == 'assert')
row = {"task_id": T, "agent": "fixture", "rubric_version": "v13-harbor2", "core_step": "compute the requested values from the given data file",
 "assertions": [f"L{n}: fixture | {'fail' if n == first_assert else 'pass'}" for n in inv], "last_assert_line": max(inv),
 "expectation_source": "test_only", "literal_derivation": None,
 "excluded_material": [{"path": p["path"], "exclusion": "benign", "basis": p["seen_in"][0] if p["seen_in"][0].split(':')[0] in ("instruction.md","environment/Dockerfile","tests/test.sh","tests/test_state.py") else "environment/Dockerfile:1"} for p in c["programs"]],
 "oracle_reachable": None, "expectation_revealed": None, "value_derivable": None, "overspecific_check": None, "unstable_reward": None,
 "bootstrap_fetch": bf, "expectation_movable": None, "protected_paths": [], "weak_verifier_exploit": None, "core_clause_unenforced": None,
 "ambiguity": None, "uncertain_fact": None, "evidence": x[hf.VERIFIER].splitlines()[first_assert - 1].strip()[:200],
 "evidence_file": "tests/test_state.py", "evidence_line": first_assert, "reason": "fixture row for the lint's own test",
 "repro_cmd": None, "repro_expected": None, "exploit_status": "none_found",
 "pass_probe": f"tests/test_state.py:{first_assert} a stub output file of the right shape", "verdict": "CLEAN", "tier": "SEED"}
print("schema errors on the clean row:", len(L.schema_errors(row)))
print("clean row:", L.lint(d, row, c) or "ok")
if L.schema_errors(row) or L.lint(d, row, c):
    MISSES.append("clean row")
def mut(label, fn, expect):
    r = copy.deepcopy(row); fn(r); got = L.lint(d, r, c)
    print(f"{'OK ' if expect in got else 'MISS'} {label}: expected {expect}, got {got}")
    if expect not in got:
        MISSES.append(label)
mut("drop one assertions entry", lambda r: r["assertions"].pop(), "H2b")
mut("last_assert_line numbered in test.sh terms", lambda r: r.update(last_assert_line=inv[0]), "H2")
mut("assertion past the verifier's end", lambda r: r["assertions"].append(f"L{len(x[hf.VERIFIER].splitlines())+5}: x | pass"), "H12")
mut("evidence on a bootstrap line, no B1", lambda r: r.update(evidence_file="tests/test.sh", evidence_line=fl[0]), "H7")
mut("evidence line out of range", lambda r: r.update(evidence_line=10**4), "H7")
mut("pass_probe cites tests/test.sh", lambda r: r.update(pass_probe=f"tests/test.sh:{fl[0]} probe"), "H9")
mut("outcome suffix missing", lambda r: r["assertions"].__setitem__(0, r["assertions"][0].rsplit(" | ",1)[0]), "H9")
mut("bootstrap_fetch null", lambda r: r.update(bootstrap_fetch=None), "HB")
mut("bootstrap_fetch span short", lambda r: r.update(bootstrap_fetch=bf.replace(f"-{fl[-1]} ", f"-{fl[-1]-3} ", 1)), "HB")
mut("bootstrap_fetch omits a --with package", lambda r: r.update(bootstrap_fetch=bf.replace(", " + facts['with'][-1], "")), "HB")
mut("bootstrap_fetch wrong uv version", lambda r: r.update(bootstrap_fetch=bf.replace(facts['uv_version'], "0.0.1")), "HB")
mut("anchor in a file the task lacks", lambda r: r["excluded_material"][0].update(basis="tests/test.patch:3"), "H11")
mut("a program candidate undisposed", lambda r: r["excluded_material"].pop(), "H13")
mut("path named nowhere in the files", lambda r: r["excluded_material"].append({"path": "/opt/nowhere/zzqq.bin", "exclusion": "benign", "basis": "instruction.md:1"}), "H1")
mut("wrong rubric label", lambda r: r.update(rubric_version="v13-rst"), "H0")
# M1 of the v2.1 review: the bootstrap boundary (HL), each a probe the reviewer built
def boot_b3(r):
    r.update(unstable_reward=f"tests/test.sh:{fl[1]} the uv installer is fetched from astral.sh at grade time",
             repro_cmd="true", repro_expected="external-fetch", exploit_status="static_trace",
             verdict="UNSTABLE-REWARD", tier="FLAGGED", pass_probe=None)
def boot_leak(r):
    r.update(value_derivable=f"tests/test.sh:{fl[1]} V = f(A) with A = {c['programs'][0]['path']}, f read, core step skipped",
             repro_cmd="true", repro_expected="reward==1", exploit_status="static_trace",
             verdict="ANSWER-LEAK", tier="FLAGGED", pass_probe=None)
    r["assertions"] = [x.replace("| fail", "| pass") for x in r["assertions"]]
def boot_uncertain(r):
    r.update(uncertain_fact=f"tests/test.sh:{fl[1]} the grade-time fetch from astral.sh may fail", tier="UNSURE", pass_probe=None)
    r["assertions"] = [x.rsplit(" | ", 1)[0] for x in r["assertions"]]
mut("B3 anchored in the bootstrap", boot_b3, "HL")
mut("leak anchored in the bootstrap", boot_leak, "HL")
mut("uncertain_fact anchored in the bootstrap", boot_uncertain, "HL")
# m10 of the v2.1 review: the record's form
mut("bootstrap_fetch with no uvx entry", lambda r: r.update(bootstrap_fetch=bf.split("; uvx")[0] + "; pypi.org: " + ", ".join(facts['with'])), "HB")
mut("bootstrap_fetch without its '<command> ->' form", lambda r: r.update(bootstrap_fetch=f"tests/test.sh:{fl[0]}-{fl[-1]} curl, uv {facts['uv_version']}, " + ", ".join(facts['with'])), "HB")
def b1_boot(r):   # the legitimate case: B1 on a bootstrap line the instruction's state stops, evidence there too
    r.update(overspecific_check=f"tests/test.sh:{fl[0]} the instruction's firewall stops apt", evidence_file="tests/test.sh",
             evidence_line=fl[0], repro_cmd="true", repro_expected="reward==0", exploit_status="static_trace",
             verdict="INSTR-VERIFIER-MISMATCH", tier="FLAGGED", pass_probe=None)
    r["assertions"] = [a.replace("| pass", "| fail") if i == 0 else a for i, a in enumerate(r["assertions"])]
r = copy.deepcopy(row); r["bootstrap_fetch"] = bf.replace("uvx -> ", "uvx -p 3.13 --with x -> ")
print("record whose commands carry arguments:", L.lint(d, r, c) or "ok")
if L.lint(d, r, c):
    MISSES.append("record with arguments")
r = copy.deepcopy(row); b1_boot(r)
print("B1-bootstrap row, evidence in tests/test.sh:", L.lint(d, r, c) or "ok")
if L.lint(d, r, c) or L.schema_errors(r):
    MISSES.append("B1-bootstrap row")
print(f"{'PASS' if not MISSES else 'FAIL'}: {len(MISSES)} misses {MISSES}")
sys.exit(1 if MISSES else 0)
