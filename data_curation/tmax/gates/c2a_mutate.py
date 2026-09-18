"""C2a mutation harness: revert each consumer site to its literal, prove RED."""
import hashlib, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path("/homes/zhaofan/terminalworld-openai-switch")
PY = str(ROOT / ".venv/bin/python")
NC = "scripts/selfcheck/nc_c2a_station_constants_selfcheck.py"

# (label, file, current_text, mutated_text)
MUT = [
 ("A1 stages:release-gate-candidates", "scripts/run_rebench_agentic_stages.py",
  "    if policy.stage_id in RELEASE_GATE_CANDIDATES:",
  '    if policy.stage_id in {"solution", "instruction"}:'),
 ("A2 stages:acquisition-check", "scripts/run_rebench_agentic_stages.py",
  "                and request.policy.stage_id in AUTHORING_STAGES):",
  '                and request.policy.stage_id in {"solution", "instruction"}):'),
 ("A3 executor:provider+parent_release", "scripts/rebench_scale300_live_executor.py",
  "        if stage in core.AUTHORING_STAGES:",
  '        if stage in {"solution", "instruction"}:'),
 ("A4 plumbing:post-release-dispatch", "scripts/rebench_agentic_live_plumbing.py",
  """    _gated_after_release = _plumbing_release_gated()
    stage_names = tuple(name for name in core.MODEL_STAGE_NAMES
                        if name in _gated_after_release)""",
  """    stage_names = (("instruction",) if core.AUTHOR_MODE in core.RST_MODES
                   else ("solution", "instruction"))"""),
 ("B1 provider:1157 four-stage guard", "scripts/codex_stage_provider.py",
  "    if policy.stage_id not in core.MODEL_STAGE_NAMES:",
  '    if policy.stage_id not in {"contract", "solution", "verifier", "instruction"}:'),
 ("B2 provider:1264 policy loop", "scripts/codex_stage_provider.py",
  "    for name in core.MODEL_STAGE_NAMES:",
  '    for name in ("contract", "solution", "verifier", "instruction"):'),
 ("B3 provider:1549 capability stage id", "scripts/codex_stage_provider.py",
  "    if stage_id not in core.MODEL_STAGE_NAMES:",
  '    if stage_id not in {"contract", "solution", "verifier", "instruction"}:'),
 ("B4 provider:2963 all-stages-ephemeral", "scripts/codex_stage_provider.py",
  "        for stage in core.MODEL_STAGE_NAMES_BY_MODE[author_mode])",
  '        for stage in ("contract", "solution", "verifier", "instruction"))'),
 ("B5 provider:3017 posture map", "scripts/codex_stage_provider.py",
  "        for stage in core.MODEL_STAGE_NAMES_BY_MODE[mode]:",
  '        for stage in ("contract", "solution", "verifier", "instruction"):'),
 ("B6 provider:4752 session-slot shape", "scripts/codex_stage_provider.py",
  "                != set(core.MODEL_STAGE_NAMES)",
  '                != {"contract", "verifier", "solution", "instruction"}'),
 ("B7 provider:5016 dispatch guard", "scripts/codex_stage_provider.py",
  "        if request.policy.stage_id not in core.MODEL_STAGE_NAMES:",
  '        if request.policy.stage_id not in {\n                "contract", "solution", "verifier", "instruction"}:'),
 ("B8 stages:4895 candidate receipts", "scripts/run_rebench_agentic_stages.py",
  "    required = STAGE_RECEIPT_NAMES",
  '    required = ("contract", "environment", "solution", "verifier", "instruction")'),
 ("B9 stages:4910 live_stage_chain", "scripts/run_rebench_agentic_stages.py",
  """        stage_receipts[name].get("authority") == "live_dispatch_capture"
        for name in MODEL_STAGE_NAMES)""",
  """        stage_receipts[name].get("authority") == "live_dispatch_capture"
        for name in ("contract", "solution", "verifier", "instruction"))"""),
 ("B13 stages:5456 plan stage list", "scripts/run_rebench_agentic_stages.py",
  "            for name in STAGE_RECEIPT_NAMES\n",
  '            for name in ("contract", "environment", "solution", "verifier",\n                         "instruction")\n'),
 ("B10 stages:4972 stage ready", "scripts/run_rebench_agentic_stages.py",
  "                   for name in MODEL_STAGE_NAMES):",
  '                   for name in ("contract", "solution", "verifier", "instruction")):'),
 ("B11 factory:753 slot projection", "scripts/codex_full300_factory.py",
  "        if (set(slots) != set(core.MODEL_STAGE_NAMES)",
  '        if (set(slots) != {"contract", "verifier", "solution", "instruction"}'),
 ("B12 executor:286 recovery stages", "scripts/rebench_scale300_live_executor.py",
  "        if stage in core.MODEL_STAGE_NAMES:",
  '        if stage in {"contract", "verifier", "solution", "instruction"}:'),
 ("C-a scale300 MODEL_STAGES not rebound", "scripts/run_rebench_scale300.py",
  "MODEL_STAGES = MODEL_STAGES_BY_MODE[core.AUTHOR_MODE]",
  'MODEL_STAGES = MODEL_STAGES_BY_MODE["split5_legacy"]'),
 ("C-b scale300 STAGE_LIMITS not rebound", "scripts/run_rebench_scale300.py",
  "STAGE_LIMITS = STAGE_LIMITS_BY_MODE[core.AUTHOR_MODE]",
  'STAGE_LIMITS = STAGE_LIMITS_BY_MODE["split5_legacy"]'),
 ("C-c T5 second name list (not derived)", "scripts/run_rebench_scale300.py",
  """MODEL_STAGES_BY_MODE = {
    mode: frozenset(names)
    for mode, names in core.MODEL_STAGE_NAMES_BY_MODE.items()
}""",
  """MODEL_STAGES_BY_MODE = {
    mode: frozenset({"contract", "verifier", "solution", "instruction"})
    for mode in core.AUTHOR_MODES
}"""),
 ("C-d rst_merged value moved early (C2b leak)", "scripts/run_rebench_agentic_stages.py",
  '    "rst_merged": ("solution", "instruction"),   # C2b: ("author",)\n}\nAUTHORING_STAGES = ',
  '    "rst_merged": ("author",),\n}\nAUTHORING_STAGES = '),
 ("C-e STAGE_LIMITS value drift", "scripts/run_rebench_scale300.py",
  '    "rst_merged": dict(_SPLIT_STAGE_LIMITS),',
  '    "rst_merged": {"author": 390},'),
 ("C-g B9 guard stops deriving (literal restored)", "scripts/run_rebench_agentic_stages.py",
  """    live_stage_chain = all(
        stage_receipts[name].get("authority") == "live_dispatch_capture"
        for name in MODEL_STAGE_NAMES)""",
  """    live_stage_chain = all(
        stage_receipts[name].get("authority") == "live_dispatch_capture"
        for name in ("contract", "solution", "verifier", "instruction"))"""),
 ("C-i B9 guard silently rebound to T2 (no literal)", "scripts/run_rebench_agentic_stages.py",
  """        stage_receipts[name].get("authority") == "live_dispatch_capture"
        for name in MODEL_STAGE_NAMES)""",
  """        stage_receipts[name].get("authority") == "live_dispatch_capture"
        for name in STAGE_RECEIPT_NAMES)"""),
 ("C-h a gate refusal string duplicated", "scripts/rebench_agentic_live_plumbing.py",
  '        raise ValueError("assembly candidate invalid: " + candidate_issue)',
  '        raise ValueError("assembly candidate invalid: " + candidate_issue)\n'
  '    _unused = "assembly candidate invalid: "'),
 ("C-f candidate set silently widened", "scripts/run_rebench_agentic_stages.py",
  '''RELEASE_GATE_CANDIDATES_BY_MODE = {
    "split5_legacy": ("solution", "instruction"),''',
  '''RELEASE_GATE_CANDIDATES_BY_MODE = {
    "split5_legacy": ("solution", "instruction", "verifier"),'''),
]

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def purge():
    for p in ROOT.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)

def run_nc(mode="split5_legacy"):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TW_AUTHOR_MODE=mode)
    r = subprocess.run([PY, NC], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=600)
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1][:110] if (r.stdout+r.stderr).strip() else ""

purge()
code, line = run_nc()
print(f"BASELINE exit={code} :: {line}")
assert code == 0, "baseline must be green"

rows = []
for label, rel, cur, mut in MUT:
    path = ROOT / rel
    before = path.read_text()
    before_sha = sha(path)
    if before.count(cur) != 1:
        rows.append((label, "SKIP", f"anchor count={before.count(cur)}"))
        continue
    path.write_text(before.replace(cur, mut, 1))
    purge()
    code, line = run_nc()
    path.write_text(before)
    purge()
    assert sha(path) == before_sha, "restore not byte-identical: " + rel
    rows.append((label, "RED" if code != 0 else "GREEN(!)", line))

print()
for label, verdict, detail in rows:
    print(f"{verdict:9s} | {label:44s} | {detail[:88]}")
print()
print("leaks:", sum(1 for r in rows if r[1] == "RED"), "/", len(rows))
purge()
code, line = run_nc()
print(f"RESTORED exit={code} :: {line}")
