#!/usr/bin/env python3
"""Self-test of LR1-LR5 and of the stager's bootstrap reader. Portable: it builds its own tiny tasks from the public
harness bootstrap text, so it needs no staged task and no corpus. Only the LR ids are checked (the synthetic rows are
not complete v13 rows, so the inherited lints are ignored).

What it pins down:
  * stage_rst.bootstrap_facts reads the four bootstrap shapes the corpus has (uvx, uvx with -p/-w and quoted specs,
    pip + the image's Python, no fetch at all) the way the prompt's Step 4b describes them;
  * each LR lint fires on the row it is for and stays silent on a correct row.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lint_v13_rst as L  # noqa: E402
import stage_rst as S  # noqa: E402

TRAP = ("#!/bin/bash\n\nmkdir -p /logs/verifier\n_harbor_write_missing_reward() {\n"
        "  if [ ! -f /logs/verifier/reward.txt ] && [ ! -f /logs/verifier/reward.json ]; then\n"
        "    echo 0 > /logs/verifier/reward.txt\n  fi\n}\ntrap _harbor_write_missing_reward EXIT\n\n")
TAIL = ("\nif [ $? -eq 0 ]; then\n  echo 1 > /logs/verifier/reward.txt\nelse\n  echo 0 > /logs/verifier/reward.txt\nfi\n")
BOOT = {
    "usual": TRAP + "apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*\n\n"
                    "curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh\n\nsource $HOME/.local/bin/env\n\n"
                    "uvx \\\n  --with pytest==8.4.1 \\\n  --with pytest-json-ctrf==0.3.5 \\\n  --with requests \\\n"
                    "  pytest --ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA\n" + TAIL,
    "pinned": TRAP + "apt-get update && apt-get install -y curl\ncurl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh\n"
                     "source $HOME/.local/bin/env\nuvx -p 3.13 \\\n  -w pytest==8.4.1 \\\n  -w \"pypdf==5.1.0\" \\\n"
                     "  -w 'localpkg @ file:///tmp/localpkg' \\\n  pytest /tests/test_state.py -rA\n" + TAIL,
    "pip": "#!/bin/bash\nset -e\n" + TRAP + "pip install --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5\n\n"
           "python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_state.py -rA\n" + TAIL,
    "nofetch": TRAP + "source /app/env/bin/activate\npython -m pytest /tests/test_state.py -rA -q\n" + TAIL,
}
VERIFIER = ("import os\n\n\ndef test_report_exists():\n    assert os.path.exists('/app/report.txt')\n\n\n"
            "def test_report_value():\n    assert open('/app/report.txt').read().strip() == '42'\n")


def task(kind):
    boot = BOOT[kind]
    files = {"instruction.md": "Write the answer to /app/report.txt.\n",
             "setup.sh": S.HEADER + "FROM ubuntu:22.04\nWORKDIR /app\n",
             "tests/test.sh": boot + S.SEP + VERIFIER}
    facts = S.bootstrap_facts(boot)
    facts["lines"] = len(boot.splitlines())
    facts["separator_line"] = facts["lines"] + 1
    return files, {"bootstrap": facts}


def lr(kind, **kw):
    files, side = task(kind)
    sep = side["bootstrap"]["separator_line"]
    row = {"task_id": "rts_task_0123456789abcdef01234567", "assertions": [f"L{sep + 4}-{sep + 9}: the report | pass"],
           "excluded_material": [], "protected_paths": [], "bootstrap_fetch": None}
    row.update(kw)
    return [x for x in L.lint(files, row, None, side) if x.startswith("LR")]


def record(kind, drop=()):
    f = task(kind)[1]["bootstrap"]
    a, b = f["fetch_span"]
    parts = {"apt": "apt-get -> distro mirrors: curl", "astral.sh": "curl -> astral.sh: uv 0.9.7",
             "pypi:uvx": "uvx -> pypi.org: " + ", ".join(f["with_packages"]),
             "pypi:pip": "pip install -> pypi.org: pytest, pytest-json-ctrf"}
    text = "; ".join(parts[k] for k in f["fetches"] if k not in drop)
    if f["python_pin"] and "pin" not in drop:
        text += f"; python {f['python_pin']} -> github.com (uv downloads it unless the image has it)"
    return f"tests/test.sh:{a}-{b} {text}"


facts = {k: task(k)[1]["bootstrap"] for k in BOOT}
reader = [
    ("usual: three fetch kinds", facts["usual"]["fetches"], ["apt", "astral.sh", "pypi:uvx"]),
    ("usual: packages", facts["usual"]["with_packages"], ["pytest", "pytest-json-ctrf", "requests"]),
    ("usual: the span ends on the last continuation line of uvx", facts["usual"]["fetch_span"][1] - facts["usual"]["fetch_span"][0] >= 8, True),
    ("pinned: quoted and file:// specs are read", facts["pinned"]["with_packages"], ["localpkg", "pypdf", "pytest"]),
    ("pinned: the file:// spec is listed as in-image", facts["pinned"]["with_local_path_specs"], ["localpkg @ file:///tmp/localpkg"]),
    ("pinned: the interpreter pin", facts["pinned"]["python_pin"], "3.13"),
    ("pip: one fetch kind, set -e seen", (facts["pip"]["fetches"], facts["pip"]["set_e"]), (["pypi:pip"], True)),
    ("nofetch: nothing fetched, pytest still run", (facts["nofetch"]["fetches"], facts["nofetch"]["runs_pytest"]), ([], True)),
]
u = facts["usual"]
lints = [
    ("a correct record", lr("usual", bootstrap_fetch=record("usual")), []),
    ("the record is missing", lr("usual"), ["LR1"]),
    ("a record on a bootstrap that fetches nothing", lr("nofetch", bootstrap_fetch="tests/test.sh:11-12 pip -> pypi.org: pytest"), ["LR1"]),
    ("null on a bootstrap that fetches nothing", lr("nofetch"), []),
    ("anchored below the separator", lr("usual", bootstrap_fetch=record("usual").replace(
        f":{u['fetch_span'][0]}-{u['fetch_span'][1]} ", f":{u['separator_line'] + 2}-{u['separator_line'] + 5} ")), ["LR2"]),
    ("a fetch kind left out", lr("usual", bootstrap_fetch=record("usual", drop=("apt",))), ["LR4"]),
    ("a --with package left out", lr("usual", bootstrap_fetch=record("usual").replace(", requests", "")), ["LR4"]),
    ("the interpreter pin left out", lr("pinned", bootstrap_fetch=record("pinned", drop=("pin",))), ["LR4"]),
    ("pinned, complete", lr("pinned", bootstrap_fetch=record("pinned")), []),
    ("pip variant, complete", lr("pip", bootstrap_fetch=record("pip")), []),
    ("B3 anchored in the bootstrap", lr("usual", bootstrap_fetch=record("usual"),
                                        unstable_reward=f"tests/test.sh:{u['fetch_span'][0]} apt-get reaches the mirrors"), ["LR3"]),
    ("B1 anchored in the bootstrap is allowed", lr("usual", bootstrap_fetch=record("usual"),
                                                   overspecific_check=f"tests/test.sh:{u['fetch_span'][0]} the instruction blocks outbound traffic"), []),
    ("assertions mapped only in the bootstrap", lr("usual", bootstrap_fetch=record("usual"), assertions=["L3-5: reward trap | pass"]), ["LR5"]),
]
bad = [(n, got, want) for n, got, want in reader + lints if got != want]
print(f"{len(reader)} reader cases + {len(lints)} lint cases | wrong {len(bad)}")
for x in bad:
    print("  ", x)
sys.exit(1 if bad else 0)
