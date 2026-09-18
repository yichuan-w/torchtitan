#!/usr/bin/env python3
"""gold_rollout.py — build gold from a model rollout for the parked no-gold tasks (design: reviews/gold_rollout_design_20260903.md).

A coding-agent rollout (Vanillux2Agent, OpenAI GPT via litellm) solves a parked task IN its Daytona sandbox under
training conditions (root, in-place, network allow-list to the model endpoint only, no image entrypoint, instruction as
the prompt). The sandbox END STATE is snapshotted into a gold_static-shaped member — the exec-side runtime effects that
an apply_patch/trace snapshot cannot capture (socket perms, renames, builds), which is why batch-G gate-1 parked these.

NEW file (does not touch existing tools/). No Claude API: provider is OpenAI, key read at RUNTIME from --key-file and
NEVER printed, logged, or committed. Model id + reasoning_effort are CLI parameters (the exact GPT id is resolved by the
lead from /v1/models once the key lands). --dry-run stops before the API call / any sandbox creation.

Only the ORIGINAL-verifier reward is recorded, as an INFORMATIONAL provenance field — never a gate here (the promotion
gates in the design note are run by the validation pipeline, not this tool).

Testable/pure helpers (no litellm/daytona/network): load_parked_ids, read_openai_key, snapshot_tar_cmd,
solution_member_name, pack_solution_into_gold_tar, task_hash, provenance_record, build_plan. The live rollout path is
lazily imported and clearly delimited (LIVE ROLLOUT below); it is not exercised without a key + uv + Daytona.
"""
from __future__ import annotations
import argparse, asyncio, base64, contextlib, contextvars, fnmatch, hashlib, io, json, os, pathlib, re, signal, sys, tarfile, time

_REASONING = contextvars.ContextVar('gold_reasoning', default=None)          # per-rollout reasoning-token accumulator (Vanillux2Agent's usage.json reports 0 for the Responses bridge; the real count is in completion_tokens_details, so the shim accumulates it per asyncio task/rollout)
_USAGE = contextvars.ContextVar('gold_usage', default=None)                   # per-rollout per-call token log: list of {prompt, completion, reasoning} appended by the shim per litellm.completion (62-cohort provenance carried no token counts; the 282 record needs per-step + totals)

RA = pathlib.Path('/data/users/zekaili/fangzhou/tb_check/reaudit_398')
BASE = pathlib.Path('/data/users/zekaili/fangzhou/tb_check/rebench_tmax_recovery_2026-09-02/work')
KEY_FILE_DEFAULT = '/home/zekaili/fangzhou/tb_check/moai.txt'                 # OpenAI key; user-filled; NEVER print/log/commit
API_BASE_DEFAULT = 'https://us.api.openai.com/v1'                             # lead-resolved: plain api.openai.com rejects this key ("incorrect regional hostname")
PARKED_TSV_DEFAULT = str(RA / 'repairs' / 'parked_no_gold.tsv')
REVIEW_SRC = str(BASE / 'tmax-a12-tmp-snapshot' / 'tmax-review-source')       # harbor/rl_data/Vanillux2Agent live here (read-only; its uv lock is never touched)
VANILLUX_DIR = str(pathlib.Path(REVIEW_SRC) / 'Vanillux2Agent')
GOLD_MEMBER_PREFIX = 'gold_static'                                            # outer member: gold_static/<task_id>.solution.tar.gz
SNAPSHOT_ROOTS = ('app', 'home', 'tmp')                                       # /app /home /tmp — relative to / so `tar -xzpf -C /` (gold mode) restores them
WHOLE_FS_PRUNE = ('proc', 'sys', 'dev', 'run', 'var/cache', 'var/lib/apt/lists')   # pruned from the whole-fs changed-set: kernel fs + volatile caches
WHOLE_FS_SKIP_RE = r'(^|/)(__pycache__|\.git)(/|$)|(^|/)\.(gold_snapshot|manifest|clean_manifest|end_manifest)|\.b64$'   # this tool's own scratch + pycache
DEFAULT_EFFORT = 'xhigh'
DAYTONA_KEY_FILE_DEFAULT = '/home/zekaili/fangzhou/tb_check/mdaytona.txt'     # Daytona key; NEVER print/log/commit
# litellm Responses-bridge fix (upstream PR #33931) applied to the runtime venv's litellm. Without it, a reasoning model
# that emits a THOUGHT message + a tool call is split into two choices and Vanillux2Agent reads choice[0] (the message)
# only -> zero commands. The patched transformation.py must hash to LITELLM_PATCH_SHA256 or we refuse the Responses model
# (a venv rebuild reverts to the split form silently). See reviews/litellm_c_venv_patch_20260904.txt.
LITELLM_PATCH_TAG = 'PR33931@b3df2a7'
LITELLM_PATCH_SHA256 = '8ee19535eb61b1ff6cde5925feb78774d8dade62c5cb8b10cf87f41bca278328'
LITELLM_TRANSFORM_RELPATH = 'completion_extras/litellm_responses_transformation/transformation.py'


def _litellm_transform_sha() -> str | None:
    """sha256 of the active litellm install's Responses-bridge transformation.py (the file PR #33931 patches), or None if
    litellm/the file is absent (pure/dry-run path)."""
    try:
        import litellm
        f = pathlib.Path(litellm.__file__).parent / LITELLM_TRANSFORM_RELPATH
        return hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    except Exception:  # noqa: BLE001
        return None


def responses_guard_check(model: str, actual_sha: str | None) -> tuple[bool, str]:
    """Fail-closed gate for the Responses bridge. A model routed through openai/responses/ needs PR #33931 or it splits
    [message, tool_call] into two choices and the agent reads the wrong one; require the patched transformation.py sha.
    Non-Responses (chat/completions) models are unaffected — the guard does not apply. Returns (ok, message)."""
    if 'responses/' not in model:
        return True, 'chat/completions path: PR #33931 not required'
    if actual_sha is None:
        return False, f'litellm transformation.py not found (is litellm installed in the runtime venv?); need {LITELLM_PATCH_TAG}'
    if actual_sha != LITELLM_PATCH_SHA256:
        return False, f'litellm transformation.py sha {actual_sha} != expected {LITELLM_PATCH_SHA256} ({LITELLM_PATCH_TAG})'
    return True, f'patched ({LITELLM_PATCH_TAG})'
IMAGE_MAP = str(RA / 'unified' / 'image_map_398.json')                        # task_id -> {image, gold_type, ...}
IMAGE_MAP_ALL = str(RA / 'unified' / 'image_map_all.json')
IMAGE_MAP_15K = str(RA / 'unified' / 'image_map_15k.json')                    # the training dataset's
                                                                              # env_config: every task

# Self-contained agent harness: one bash tool + a submit tool. The model solves the task in the sandbox; this tool never
# prints or inspects the instruction, the transcript, or tool outputs (the Opus honesty gate reads those).
_SYSTEM_PROMPT = (
    'You are an autonomous software engineer working as root inside a Linux sandbox. '
    'Solve the task described by the user by running shell commands with the run_bash tool. '
    'The environment PERSISTS across your commands: files you create and state you change remain for later commands. '
    'Work through EVERY requirement stated in the instruction, one by one; do not skip any. '
    'Before you call submit you MUST VERIFY your own work: re-read the instruction, and for each stated requirement run '
    'the checks or tests the instruction describes (or, if none is given, construct a direct check) and confirm it passes. '
    'Do NOT call submit until every requirement is met and you have verified it — a premature submit scores zero. '
    'If a check fails, fix it and re-verify. Do not ask the user questions; act. Keep going until the task is fully done.'
)
_TOOLS = [
    {'type': 'function', 'function': {'name': 'run_bash', 'description': 'Run a bash command as root in the sandbox and return its combined stdout/stderr.',
                                      'parameters': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': 'the bash command'}}, 'required': ['command']}}},
    {'type': 'function', 'function': {'name': 'submit', 'description': 'Call when the task is complete and verified.',
                                      'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
]
_MAX_TOOL_OUTPUT = 8000                                                       # truncate tool output fed back to the model (never logged here)
AGENT_LOOP_LABEL = {'self_contained': 'self_contained_litellm_loop', 'vanillux2': 'vanillux2-agent'}   # --agent choice -> provenance agent_loop
AGENT_LOOP = AGENT_LOOP_LABEL['self_contained']                               # default; the swappable agent implementation (recorded so a switch to vanillux2-agent is auditable)
ROLLOUTER = f'gold_rollout.{AGENT_LOOP} 0.1'                                  # what actually ran (NOT vanillux2-agent — the scribe caught the hardcoded mislabel)
# 'vanillux2-agent' names a VENDORED sibling of the trainer's loop, not the trainer's loop: Vanillux2Agent/agent.py in
# the review-source snapshot (344 lines, its own prompts) vs torchtitan examples/tmax/vanillux_loop.py (452 lines,
# registered at examples/tmax/rollouter.py:101-103), 736 diff lines apart. Measured by harness-reader 2026-09-06.
# Five swaps against the training path, of which TWO were the named allowed ones -- external model via litellm (:516)
# and the turn cap lifted to --max-steps (:658) -- and THREE were not: the scaffold, its prompts, and the grading
# path (this file grades through repro_exec's nonce read at :1565-1570; grade_tmax and pre_test_sh occur 0 times
# here, so no training pre-verify hook runs). Recorded as a known divergence, not silently; a calibration re-roll
# through the real loop is the user's open decision, and until it happens no gold row may be described as having
# gone through the training stack as-is.


def _labels(agent: str) -> tuple[str, str]:
    """(rollouter, agent_loop) provenance labels for the --agent choice — records the real loop that produced the gold."""
    loop = AGENT_LOOP_LABEL.get(agent, agent)
    return f'gold_rollout.{loop} 0.1', loop


# ---------------------------------------------------------------- pure helpers
def load_parked_ids(tsv_path: str | pathlib.Path) -> list[str]:
    """Task ids from parked_no_gold.tsv: first tab-column, must start with task_. Skips blank lines, `#` comment lines
    (seats prepend dated gate-3c notes), and the `task_id` header wherever it sits — so leading comments can't smuggle the
    header in as an id (was: header-skip keyed on line 0 only)."""
    out = []
    for ln in pathlib.Path(tsv_path).read_text().splitlines():
        s = ln.strip()
        if not s or s.startswith('#'):
            continue
        tid = ln.split('\t', 1)[0].strip()
        if tid == 'task_id' or not tid.startswith('task_'):                  # skip the header row and any non-task row
            continue
        out.append(tid)
    return out


def load_plan(plan_path: str | pathlib.Path) -> dict:
    """Load the gate1_282 plan: bucketed ids (buckets[].ids + security.ids), per-task image_digest, and the rung ladder.
    REFUSES (loader contract, per_task_contract.loader_must / Fable B5) if any bucketed id lacks per_task.image_digest — a
    null digest would silently degenerate the per-image keying. Returns {ids, digests{id:digest}, env{id:{kind,identity}}, rungs}."""
    d = json.loads(pathlib.Path(plan_path).read_text())
    ids: list[str] = []
    for b in d.get('buckets', []):
        ids += list(b.get('ids', []))
    ids += list((d.get('security') or {}).get('ids', []))
    ids = list(dict.fromkeys(ids))                                           # dedupe, preserve order
    per = d.get('per_task') or {}
    missing = [t for t in ids if not (per.get(t) or {}).get('image_digest')]
    if missing:
        raise SystemExit(f'plan {plan_path}: {len(missing)} bucketed id(s) lack per_task.image_digest (e.g. {missing[:3]}); '
                         'refusing to load (a null digest degenerates per-image keying). See per_task_contract.loader_must.')
    digests = {t: per[t]['image_digest'] for t in ids}
    env = {t: {'kind': per[t].get('env_kind'), 'identity': per[t].get('env_identity')} for t in ids}
    sizes = {t: {'compressed_bytes': per[t].get('image_size_bytes'),          # ops stamps these into the plan; absent is allowed
                 'uncompressed_bytes': per[t].get('image_size_uncompressed_bytes')} for t in ids}
    start_rungs = {t: int(per[t]['start_rung']) for t in ids if per[t].get('start_rung') is not None}   # ops derives from env_baseline_by_image; absent => rung 0
    rolls = {t: int(per[t]['rolls']) for t in ids if per[t].get('rolls') is not None}   # per-task roll count for --parallel-rolls plan (absent => run.parallel_rolls default, else 1)
    eval_timeouts = {t: int(per[t]['eval_timeout_sec']) for t in ids if per[t].get('eval_timeout_sec') is not None}   # per-task grade-time cap override (absent => --grade-timeout / TMAX_EVAL_TIMEOUT_SEC / 900)
    _pr_default = (d.get('run') or {}).get('parallel_rolls')
    rungs = None
    _raw = ((d.get('run') or {}).get('ladder') or {}).get('rungs') or d.get('rungs')   # plan keeps rungs at run.ladder.rungs (Fable); top-level is a legacy fallback
    if _raw:                                                                 # rung objects {cpu, memory_gib, disk_gib}; None => the hardcoded RAM_LADDER
        rungs = [(int(r['cpu']), int(r.get('memory_gib') or r.get('mem_gib')), int(r['disk_gib'])) for r in _raw]
    return {'ids': ids, 'digests': digests, 'env': env, 'sizes': sizes, 'start_rungs': start_rungs, 'rungs': rungs,
            'rolls': rolls, 'parallel_rolls_default': (int(_pr_default) if _pr_default is not None else None),
            'eval_timeouts': eval_timeouts}


# Every path the harness writes by default (cfg key -> its default). --isolate-root relocates each under one dir so a probe
# cannot leak into a shared file (incident: flag-by-flag isolation still duplicated the gate-2 queue row and OVERWROTE
# repairs/gold_rollout_replay/<id>.sh). None-default keys relocate to a fixed basename.
SHARED_WRITE_PATHS = {
    'replay_dir': str(RA / 'repairs' / 'gold_rollout_replay'),
    'gate2_queue': str(RA / 'repairs' / 'gold_rollout_gate2_queue.tsv'),
    'provenance_out': str(RA / 'repairs' / 'gold_rollout_provenance.jsonl'),
    'sweep_log': str(RA / 'reviews' / 'gold_rollout_sweeps_20260903.tsv'),
    'out_gold_tar': str(BASE / 'tmax-release-products' / 'gold_static_rollout.tar'),
    'transcript_dir': str(RA / 'scratch' / 'gold_rollout_transcripts'),
    'sandbox_log': str(RA / 'scratch' / 'gold_rollout_sandboxes.jsonl'),
    'clean_manifest_cache_dir': str(RA / 'scratch' / 'gold_rollout_clean_manifests'),
    'retention_root': None,
    'retention_manifest': None,
}
_ISOLATE_FALLBACK_BASENAME = {'retention_root': 'retention', 'retention_manifest': 'retention_manifest.tsv'}


def resolve_isolated_paths(values: dict, root: str) -> dict:
    """Place EVERY shared write path under root: a default (or unset) value is relocated by basename; an explicit value must
    already resolve inside root or ValueError (so a probe can't half-isolate and clobber a shared file). Returns {key: path}."""
    root = os.path.abspath(root)
    out = {}
    for key, dflt in SHARED_WRITE_PATHS.items():
        val = values.get(key)
        if val is None or val == dflt:                                       # default/unset -> under the root
            base = os.path.basename(dflt) if dflt else _ISOLATE_FALLBACK_BASENAME[key]
            out[key] = os.path.join(root, base)
        else:                                                               # explicit -> must be inside the root
            av = os.path.abspath(val)
            if av != root and not av.startswith(root + os.sep):
                raise ValueError(f'explicit --{key.replace("_", "-")} {val} is outside --isolate-root {root}; refusing')
            out[key] = val
    return out


def image_size_mib_and_known(cfg: dict, task_id: str) -> tuple[float | None, bool]:
    """Image size in MiB for the create-failure size arm — UNCOMPRESSED ONLY (what actually lands on disk). Ruling: the
    compressed registry size cannot decide disk pressure (ops: task_000016 is 422 MiB compressed vs a 1816 MB measured
    baseline, 4.3x; no image of 156 reached 95% of the 1 GiB rung), and registry manifests carry compressed sizes only, so
    known is almost always False today and the unknown-size default (retry + one escalation) governs. compressed_bytes is
    kept as data but never feeds the decision; image_size_known == uncompressed known. bytes/1048576."""
    s = (cfg.get('image_sizes') or {}).get(task_id) or {}
    b = s.get('uncompressed_bytes')                                          # uncompressed ONLY; compressed never predicts disk pressure
    return (round(int(b) / 1048576, 1), True) if b else (None, False)


def instruction_for(task_id: str) -> str:
    """The task instruction from the LOCAL task dir (scratch/tasks/<id>/instruction.md) — the source the training data
    pipeline uses. Opaque: callers pass it to the agent, never inspect or log it. Returns '' if missing/empty."""
    p = RA / 'scratch' / 'tasks' / task_id / 'instruction.md'
    return p.read_text() if p.exists() else ''


def read_openai_key(path: str | pathlib.Path) -> str:
    """Return the OpenAI key text, or '' if the file is missing/empty. The RESULT MUST NEVER be printed, logged, or
    committed — callers pass it only into os.environ for the model client. An empty return means the user has not filled
    the key yet (the file ships 0 bytes)."""
    p = pathlib.Path(path)
    if not p.exists():
        return ''
    return p.read_text().strip()


def solution_member_name(task_id: str) -> str:
    """Outer gold-tar member for a task, matching the existing layout (verified: gold_static/<id>.solution.tar.gz)."""
    return f'{GOLD_MEMBER_PREFIX}/{task_id}.solution.tar.gz'


def snapshot_tar_cmd(dest: str = '/tmp/.gold_snapshot.tar.gz', paths: list[str] | None = None, paths_file: str | None = None) -> str:
    """The in-sandbox command that packs a gzip tar rooted at / (relative paths, perms preserved) — the inner shape
    `gold` mode extracts with `tar -xzpf -C /`. paths_file (a NUL-free newline path list read with -T) is preferred for
    large changed-sets (whole-fs installs blow ARG_MAX); `paths` inlines a small set; neither packs all of /app /home /tmp
    (--full-dump). Missing roots/paths are skipped (--ignore-failed-read)."""
    if paths_file:
        return f'tar -czpf {dest} -C / --ignore-failed-read -T {paths_file}'
    if paths:
        return f'tar -czpf {dest} -C / --ignore-failed-read -- ' + ' '.join(paths)
    return f'tar -czpf {dest} -C / --ignore-failed-read ' + ' '.join(SNAPSHOT_ROOTS)


def manifest_cmd(dest: str = '/tmp/.manifest.txt', whole_fs: bool = False) -> str:
    """The in-sandbox command that writes a `<sha256>  <relative path>` manifest (rooted at /, no leading slash) — one
    side of the changed-set diff. whole_fs=True scans the WHOLE root filesystem (find / -xdev) minus /proc /sys /dev,
    volatile caches, and this tool's own scratch files — so the agent's out-of-root changes (apt->/usr,/var, compiled
    binaries, services) are captured; the default scans only /app /home /tmp (the old overlay)."""
    if whole_fs:
        prune = ' '.join(f"-path './{p}' -prune -o" for p in WHOLE_FS_PRUNE)
        return (f"cd / && find . -xdev {prune} -type f -printf '%P\\0' 2>/dev/null "
                f"| grep -zvE '{WHOLE_FS_SKIP_RE}' | xargs -0 sha256sum 2>/dev/null > {dest} || true")
    roots = ' '.join(SNAPSHOT_ROOTS)
    return f"cd / && find {roots} -type f -print0 2>/dev/null | xargs -0 sha256sum 2>/dev/null > {dest} || true"


async def _pull_tar(sb, dest: str, timeout: int = 300) -> bytes:
    """Pull a sandbox tar as bytes WITHOUT exec-stdout truncation (exec stdout is capped ~15KB, which corrupted large
    members): base64 it to a FILE, read that file via read_file (fs object-storage download, not exec stdout), decode."""
    await sb.exec(f'base64 -w0 {dest} > {dest}.b64', user='root', timeout=timeout)
    b64 = await sb.read_file(f'{dest}.b64')
    return base64.b64decode(b64.strip()) if b64 and b64.strip() else b''


async def sandbox_absent_paths(sb, rel_paths: list) -> list:
    """Of these overlay-relative paths (rooted at /, no leading slash), which no longer EXIST in the sandbox NOW — i.e.
    vanished between the end manifest and tar. One exec over a fed file list (avoids ARG_MAX). Called only when the member
    is short, and while the sandbox is still up (before teardown), to classify a vanished race vs a genuine partial member."""
    if not rel_paths:
        return []
    await sb.write_file('/tmp/.gold_miss.txt', ('\n'.join(rel_paths) + '\n').encode(), user='root')   # /tmp/.gold_miss.txt is itself a HARNESS_SANDBOX_PATH (tmp/.gold*), and written after the manifest, so it never enters a member
    _, out, _ = await sb.exec('cd / && while IFS= read -r p; do [ -e "$p" ] || printf "%s\\n" "$p"; done < /tmp/.gold_miss.txt',
                              user='root', timeout=120)
    return sorted(l for l in (out or '').splitlines() if l.strip())


def parse_sha_manifest(text: str) -> dict[str, str]:
    """Parse `<sha256>  <path>` lines (sha256sum format, two-space separator) into {path: sha}. A path with a leading
    backslash (sha256sum escapes names containing backslash/newline) keeps the escaped form verbatim on both sides, so
    the diff is still consistent."""
    out: dict[str, str] = {}
    for ln in text.splitlines():
        if not ln.strip():
            continue
        sha, sep, path = ln.partition('  ')
        if sep and path:
            out[path] = sha.strip()
    return out


def diff_changed_set(clean: dict[str, str], dirty: dict[str, str]) -> dict[str, list[str]]:
    """Changed set of the end-state (dirty) vs a clean sandbox of the same image (both path->sha manifests).
    added/modified go into the overlay tar; deleted cannot be expressed by an overlay tar (noted in the sidecar)."""
    added = [p for p in dirty if p not in clean]
    modified = [p for p in dirty if p in clean and dirty[p] != clean[p]]
    deleted = [p for p in clean if p not in dirty]
    return {'added': sorted(added), 'modified': sorted(modified), 'deleted': sorted(deleted)}


def overlay_paths(changed: dict[str, list[str]]) -> list[str]:
    """The paths an overlay solution.tar.gz must carry: added + modified (never deleted — an overlay tar can only add)."""
    return sorted(changed['added'] + changed['modified'])


# The measuring instrument lives inside the filesystem being measured: the changed set is diffed over a sandbox that also
# holds (a) the training clone's own per-command exec spill (external/torchtitan-cotrain/.../sandbox/daytona.py:49-51
# _EXEC_OUTPUT_DIR=/tmp/.torchtitan_exec + /dev/shm variant) and (b) gold_rollout's own snapshot/manifests/paths-list/
# pollers/replay scratch (this file + repro_exec.py). Ops's probe: the pack refused with missing
# ['tmp/.torchtitan_exec/<id>.raw'] (the spill raced away between the end manifest and tar), and 60 of 78 shipped members
# carried tmp/.end_manifest*, 23 carried tmp/.gold* — the instrument packed INTO the gold. Every harness-owned sandbox
# path (relative to /, sha256sum's %P form: no leading slash) is stripped from the changed set BEFORE overlay_paths and
# the snapshot. fnmatch '*' spans '/', so a '<dir>/*' glob covers the whole subtree.
HARNESS_SANDBOX_PATHS = (
    'tmp/.torchtitan_exec', 'tmp/.torchtitan_exec/*',                         # training clone per-command exec spill (.raw/.fifo)
    'dev/shm/.torchtitan_exec', 'dev/shm/.torchtitan_exec/*',                 # its /dev/shm result dir (dev is pruned in whole-fs + unscanned in the app/home/tmp live manifest, listed defensively)
    'tmp/.gold_*',                                                            # gold_rollout: .gold_snapshot.tar.gz(.b64), .gold_paths.txt, .gold_fast.sh/.gold_fast_samples/.gold_fast.pid, .gold_du.sh/.gold_du_samples/.gold_du.pid, .gold_miss.txt (Fable nit: .gold_ underscore, not a bare .gold prefix -> an agent dotfile named .gold or .goldfish under tmp is NOT silently dropped; every harness file starts with .gold_)
    'tmp/.clean_manifest.txt', 'tmp/.end_manifest.txt', 'tmp/.manifest.txt',  # the manifests themselves (the .end_manifest race ops saw in 60 members)
    'tmp/.procgold', 'tmp/.procgold/*',                                       # repro_exec proc-gold scratch
    'tmp/.vanillux2', 'tmp/.vanillux2/*',                                     # the Vanillux2Agent agent-loop state (its own scratch under the sandbox /tmp) -- harness, never the solution
    'tmp/.prep.sh', 'tmp/.cmdrc', 'tmp/.paths.txt',                           # prepare_runtime + rebuild command-rc + rebuild -T paths list
    'tmp/.replay_instr.sh', 'tmp/.replay_plain.sh', 'tmp/.replay_*.sh',       # rebuild replay scripts run in-sandbox
    'tmp/.entrypoint*',                                                       # entrypoint log/marker (run_one_live never starts the entrypoint; defensive for the shared repro_exec paths)
)


def is_harness_path(rel_path: str) -> bool:
    """True if a changed-set path (sha256sum %P: relative to /, no leading slash) is one the harness itself creates in the
    sandbox (HARNESS_SANDBOX_PATHS) — never the agent's solution, so it must not enter a gold member."""
    p = rel_path[2:] if rel_path.startswith('./') else rel_path
    p = p.lstrip('/')
    return any(fnmatch.fnmatchcase(p, g) for g in HARNESS_SANDBOX_PATHS)


def strip_harness_paths(changed: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drop every harness-owned sandbox path from the changed set BEFORE overlay_paths/snapshot, so the measuring
    instrument never lands in a gold member nor causes a member-name mismatch (ops finding). Pure -> unit-tested."""
    return {k: [p for p in v if not is_harness_path(p)] for k, v in changed.items()}


def classify_member_gap(missing: list, absent: list) -> tuple[str, str]:
    """Name the member-gap CLASS so a vanished race and a genuine partial member never look alike (lead finding 2). Given
    the overlay paths MISSING from the packed member and (of those) the ones that no longer EXIST in the sandbox at
    snapshot time: all missing vanished -> 'vanished_before_snapshot' (a race between the end manifest and tar); any
    missing path still present -> 'partial_member' (tar --ignore-failed-read dropped a present/readable file). Pure."""
    aset = set(absent); still = [p for p in missing if p not in aset]
    if still:
        return 'partial_member', (f'partial_member: {len(still)} overlay path(s) present but unpacked {still[:10]}'
                                  + (f'; {len(aset)} also vanished' if aset else ''))
    return 'vanished_before_snapshot', f'vanished_before_snapshot: {len(missing)} overlay path(s) gone before tar {missing[:10]}'


def build_solution_gz(files: dict[str, bytes]) -> bytes:
    """Build an inner solution.tar.gz from {relative path: bytes} — the gold member's inner shape (relative paths rooted
    at /, extracted with `tar -xzpf -C /`). Used for the changed-set overlay."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for path, data in sorted(files.items()):
            ti = tarfile.TarInfo(path); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def clean_manifest_cache_path(image_id: str, cache_dir: str | pathlib.Path) -> pathlib.Path:
    """Where the clean-sandbox manifest for an image id is cached (computed once per image, reused across tasks)."""
    safe = ''.join(c if (c.isalnum() or c in '._-') else '_' for c in image_id)
    return pathlib.Path(cache_dir) / f'{safe}.manifest.txt'


def gate_reward(stdout_reward: str | None):
    """Interpret a verifier's reward.txt content as an int reward, or None when absent/unreadable (a void measurement)."""
    if stdout_reward is None:
        return None
    s = stdout_reward.strip()
    if s in ('0', '1'):
        return int(s)
    try:
        return int(float(s))
    except ValueError:
        return None


def append_gate2_queue(queue_path: str | pathlib.Path, task_id: str, sandbox_id: str | None, transcript_path: str) -> None:
    """Append a LABELS-ONLY handoff row (task_id, sandbox_id, transcript_path) for the Opus gate-2 honesty read. Holds no
    task content — the transcript itself lives in untracked scratch and is never inspected by this tool. Writes a header
    on first creation."""
    q = pathlib.Path(queue_path); newq = not q.exists()
    with q.open('a') as fh:
        if newq:
            fh.write('task_id\tsandbox_id\ttranscript_path\n')
        fh.write(f'{task_id}\t{sandbox_id}\t{transcript_path}\n')


def append_sweep_log(sweep_path: str | pathlib.Path, sandbox_id: str | None, source: str, task_id: str, outcome: str) -> None:
    """Append one sweep row (sandbox_id, source, task_id, outcome, timestamp) — the authoritative record the scribe reads
    to verify teardown. outcome is deleted/gone/error. Labels only; no task content. Header on first creation."""
    if not sandbox_id:
        return
    p = pathlib.Path(sweep_path); new = not p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a') as fh:
        if new:
            fh.write('sandbox_id\tsource\ttask_id\toutcome\ttimestamp\n')
        fh.write(f'{sandbox_id}\t{source}\t{task_id}\t{outcome}\t{time.strftime("%Y-%m-%dT%H:%M:%S")}\n')


def pack_solution_into_gold_tar(task_id: str, solution_gz: bytes, gold_tar_path: str | pathlib.Path) -> str:
    """Write/replace the member gold_static/<task_id>.solution.tar.gz (bytes = the inner solution.tar.gz pulled from the
    sandbox) inside gold_tar_path, matching the release layout. Rewrites the tar so a re-run replaces (never duplicates)
    the member. Returns the member name. Never touches the release gold_static.tar unless the caller points here."""
    gold_tar_path = pathlib.Path(gold_tar_path)
    member = solution_member_name(task_id)
    keep: list[tuple[str, bytes]] = []
    if gold_tar_path.exists():
        with tarfile.open(gold_tar_path, 'r') as tf:
            for m in tf.getmembers():
                if m.name == member:
                    continue                                                 # drop the old copy of this task
                if m.isfile():
                    keep.append((m.name, tf.extractfile(m).read()))
    tmp = gold_tar_path.with_suffix(gold_tar_path.suffix + '.tmp')
    with tarfile.open(tmp, 'w') as tf:
        for name, data in keep:
            ti = tarfile.TarInfo(name); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        ti = tarfile.TarInfo(member); ti.size = len(solution_gz)
        tf.addfile(ti, io.BytesIO(solution_gz))
    tmp.replace(gold_tar_path)
    return member


def read_gold_member_bytes(gold_tar_path: str | pathlib.Path, task_id: str) -> bytes | None:
    """The current bytes of gold_static/<task_id>.solution.tar.gz inside the gold tar, or None if absent — read before a
    re-roll replaces the member (latest-wins release layout) so the outgoing bytes can be retained."""
    gp = pathlib.Path(gold_tar_path); member = solution_member_name(task_id)
    if not gp.exists():
        return None
    with tarfile.open(gp, 'r') as tf:
        for m in tf.getmembers():
            if m.name == member and m.isfile():
                return tf.extractfile(m).read()
    return None


def task_hash(task_id: str, tasks_root: str | pathlib.Path | None = None) -> str:
    """A stable task fingerprint for provenance: sha256 of the ORIGINAL tests/test.sh bytes when available (identifies
    the task version the gold was produced against), else sha256 of the task id."""
    root = pathlib.Path(tasks_root) if tasks_root else (RA / 'scratch' / 'tasks')
    ts = root / task_id / 'tests' / 'test.sh'
    if ts.exists():
        return hashlib.sha256(ts.read_bytes()).hexdigest()
    return hashlib.sha256(task_id.encode()).hexdigest()


def provenance_record(task_id: str, *, model: str, reasoning_effort: str, max_steps: int, cost_usd: float | None,
                      sandbox_id: str | None, task_hash_hex: str, orig_verifier_reward, date: str,
                      steps: int | None = None, changed_set_count: int | None = None, deleted_paths: list[str] | None = None,
                      member_size_bytes: int | None = None, full_dump: bool = False, repaired_verifier_reward=None,
                      packed: bool = False, rollouter: str = ROLLOUTER, agent_loop: str = AGENT_LOOP,
                      replay_path: str | None = None, replay_sha256: str | None = None,
                      effective_max_steps=None, effective_cost_limit=None,
                      reasoning_tokens: int | None = None, invalid_reason: str | None = None,
                      litellm_patch: str | None = None) -> dict:
    """The provenance sidecar for a model-rollout gold. gate 1 (orig_verifier_reward) and gate 3
    (repaired_verifier_reward) are recorded; orig is INFORMATIONAL (never a promotion gate in this tool). deleted_paths
    are files removed vs the clean image — an overlay tar cannot express a deletion, so they are noted here."""
    return {
        'task_id': task_id,
        'gold_source': 'model_rollout',
        'model': model,
        'reasoning_effort': reasoning_effort,
        'rollouter': rollouter,                                             # the runner + agent loop that actually produced this
        'agent_loop': agent_loop,                                            # swappable agent implementation (records a vanillux2-agent switch)
        'replay_path': replay_path,                                          # the trajectory replay script (gitignored; gate-3 replay for runtime-effects tasks)
        'replay_sha256': replay_sha256,                                      # sha256 of that replay script (gate-3 contract)
        'max_steps': max_steps,                                             # CLI value (self_contained uses it; vanillux2 ignores it)
        'effective_max_steps': effective_max_steps,                         # the cap the agent ACTUALLY ran under (vanillux2 = training config)
        'effective_cost_limit': effective_cost_limit,                       # the cost cap the agent actually ran under (vanillux2 training config: 0.0 = unbounded)
        'steps': steps,
        'rollout_cost_usd': cost_usd,
        'sandbox_id': sandbox_id,
        'task_hash': task_hash_hex,
        'orig_verifier_reward': orig_verifier_reward,                        # gate 1 — INFORMATIONAL, not a promotion gate here
        'repaired_verifier_reward': repaired_verifier_reward,               # gate 3 — where a validated spec exists, else None
        'changed_set_count': changed_set_count,                             # added + modified files in the overlay
        'deleted_paths': deleted_paths or [],                              # deletions vs the clean image (overlay tar can't express them)
        'member_size_bytes': member_size_bytes,
        'full_dump': full_dump,
        'packed': packed,                                                    # entered the gold tar only when gate 1 == 1
        'reasoning_tokens': reasoning_tokens,                                # REAL reasoning tokens (completion_tokens_details) summed over the rollout; 0 => model did not reason => invalid
        'invalid_reason': invalid_reason,                                    # set when this row must not be packed (no_reasoning / empty_instruction / provider_refusal_cyber_policy / exception)
        'litellm_patch': litellm_patch,                                      # the litellm Responses-bridge fix in force for this roll (PR33931@<sha>), or None on chat/completions

        'date': date,
    }


def build_plan(task_id: str, *, model: str, reasoning_effort: str, max_steps: int, cost_limit: float,
               command_timeout: int, image: str | None, gold_tar: str, provenance_out: str, key_present: bool,
               api_base: str | None = None, full_dump: bool = False) -> dict:
    """The dry-run plan for one task — everything the live run would use, and nothing secret. No API, no sandbox."""
    return {
        'task_id': task_id,
        'provider': 'openai',
        'model': model,
        'api_base': api_base,
        'snapshot_mode': 'full-dump' if full_dump else 'changed-set',
        'reasoning_effort': reasoning_effort,
        'max_steps': max_steps,
        'cost_limit_usd': cost_limit,
        'command_timeout_s': command_timeout,
        'start_entrypoint': False,                                           # TMAX corpus rule: no image entrypoint
        'image': image or 'derive-per-task',
        'network': 'daytona allow-list = model endpoint only',
        'snapshot_cmd': snapshot_tar_cmd(),
        'gold_member': solution_member_name(task_id),
        'gold_tar': gold_tar,
        'provenance_out': provenance_out,
        'task_hash': task_hash(task_id),
        'key_present': key_present,
    }


# ---------------------------------------------------------------- reasoning_effort wiring (item 1), non-invasive
def install_reasoning_effort_shim(effort: str, force_tool_choice: bool = False) -> None:
    """Wire reasoning_effort (and, with force_tool_choice, tool_choice="required") into the litellm completion path WITHOUT
    editing Vanillux2Agent/agent.py (which builds its completion_kwargs inline). Wraps litellm.completion so every call
    carries reasoning_effort, and — when tools are present and force_tool_choice — tool_choice="required" so the model MUST
    emit a tool_call instead of answering in prose (the format-error cause: gpt-5.6-sol returned commands as prose that the
    tool extractor rejected). Leaves the training config untouched. Called on the live path after the key/env are set."""
    import litellm  # lazy: not importable in the pure/dry-run path
    if getattr(litellm.completion, '_gold_effort_wrapped', False):
        return
    _orig = litellm.completion

    def _wrapped(*a, **kw):
        kw.setdefault('reasoning', {'effort': effort})                      # RESPONSES form: reasoning_effort=<x> is a no-op with tools on /v1/chat/completions (0 reasoning tokens); reasoning={'effort':<x>} via the openai/responses/ bridge actually reasons (probe: xhigh -> 44 reasoning tokens + a tool_call)
        if force_tool_choice and kw.get('tools') and 'tool_choice' not in kw:
            kw['tool_choice'] = 'required'
        resp = _orig(*a, **kw)
        try:                                                                # accumulate the REAL reasoning tokens (completion_tokens_details) into the current rollout's counter + log per-call usage
            u = getattr(resp, 'usage', None)
            det = getattr(u, 'completion_tokens_details', None)
            rt = int(getattr(det, 'reasoning_tokens', 0) or 0)
            box = _REASONING.get()
            if box is not None:
                box[0] += rt
            ulog = _USAGE.get()
            if ulog is not None:
                ulog.append({'prompt': int(getattr(u, 'prompt_tokens', 0) or 0),
                             'completion': int(getattr(u, 'completion_tokens', 0) or 0),
                             'reasoning': rt})
        except Exception:  # noqa: BLE001
            pass
        return resp

    _wrapped._gold_effort_wrapped = True                                     # idempotent
    litellm.completion = _wrapped


def resolve_image(task_id: str, override: str | None, plan_identity: str | None = None) -> str | None:
    """The task's container image: the --image override, else the PLAN's per-task env_identity, else the maps.

    The plan arm exists because gate1_pass36 lost all 108 of its rollouts to a map miss while the plan it ran from
    carried the right image for every task. The loader already parses env_identity into cfg['image_env'] and only
    stamps it into a resources record — a field the plan sets and nothing reads, which is B5 in reverse. The maps
    cover the 1246 published audit tasks; the never-judged pool is disjoint from all of them, so any rollout of a
    pool task hits this.

    A DISAGREEMENT between the plan and a map raises rather than picking a winner. The two naming different images
    for one task means one of them is wrong, and a run that silently prefers either would produce a gold built in
    an environment nobody chose.
    """
    if override:
        return override
    mapped = None
    for mp in (IMAGE_MAP, IMAGE_MAP_ALL, IMAGE_MAP_15K):
        p = pathlib.Path(mp)
        if p.exists():
            m = json.loads(p.read_text())
            e = m.get(task_id)
            if isinstance(e, dict) and e.get('image'):
                mapped = e['image']; break
            if isinstance(e, str) and e:
                mapped = e; break
    if plan_identity and mapped and plan_identity != mapped:
        raise ValueError(f'{task_id}: the plan says image {plan_identity} and the image map says {mapped}. '
                         f'One of them is wrong and neither is safe to prefer silently — fix the plan or the map.')
    return plan_identity or mapped


async def _agent_loop(sb, instruction: str, cfg: dict, log) -> tuple[int, float, str, list]:
    """Self-contained loop: drive the model (litellm, reasoning shim already installed) to solve the task via the run_bash
    tool in the sandbox. Returns (steps, cost_usd, transcript_json, executed commands). NEVER logs the instruction, tool
    arguments, or tool outputs — only step counts. The transcript is returned opaquely for the gate-2 (Opus) read."""
    import litellm
    messages = [{'role': 'system', 'content': _SYSTEM_PROMPT}, {'role': 'user', 'content': instruction}]
    steps = 0; cost = 0.0; commands: list[str] = []
    for step in range(cfg['max_steps']):
        if cfg['cost_limit'] and cost >= cfg['cost_limit']:
            log(f'  cost_limit reached at step {step} (${cost:.2f})'); break
        resp = await asyncio.to_thread(litellm.completion, model=cfg['model'], messages=messages, tools=_TOOLS,
                                       api_base=cfg.get('api_base'), timeout=cfg['command_timeout'] + 60, num_retries=2)
        steps = step + 1
        try:
            cost += float(litellm.completion_cost(resp) or 0.0)
        except Exception:  # noqa: BLE001 — cost accounting best-effort
            pass
        msg = resp.choices[0].message
        messages.append(msg.model_dump() if hasattr(msg, 'model_dump') else dict(msg))
        tcs = getattr(msg, 'tool_calls', None) or []
        if not tcs:
            break                                                            # model produced a final message with no tool call
        done = False
        for tc in tcs:
            name = tc.function.name
            if name == 'submit':
                done = True
                messages.append({'role': 'tool', 'tool_call_id': tc.id, 'content': 'submitted'})
                continue
            try:
                args = json.loads(tc.function.arguments or '{}')
            except Exception:  # noqa: BLE001
                args = {}
            command = args.get('command', '')
            rc, out, _ = await sb.exec(command, user='root', timeout=cfg['command_timeout'])
            result = (out or '')[-_MAX_TOOL_OUTPUT:]                          # opaque; never logged here
            messages.append({'role': 'tool', 'tool_call_id': tc.id, 'content': f'[rc={rc}]\n{result}'})
        if done:
            break
    log(f'  agent finished: steps={steps} cost=${cost:.4f}')
    return steps, cost, json.dumps(messages), commands, {'max_steps': cfg['max_steps'], 'cost_limit': cfg['cost_limit']}


async def _agent_loop_vanillux(sb, instruction: str, cfg: dict, log) -> tuple[int, float, str, list]:
    """Option C: drive a VENDORED Vanillux2Agent (mini-SWE-agent prompting, one bash command per turn) over the reaudit
    DaytonaSandbox via a thin duck-typed BaseEnvironment adapter (only .exec is used).

    NOT the training rollouter, and this docstring said it was until harness-reader measured both sides (2026-09-06).
    What runs is Vanillux2Agent/agent.py out of the review-source snapshot (REVIEW_SRC, :31; sys.path insert below),
    344 lines with its own prompts — a SIBLING of the trainer's loop, not the trainer's loop. The trainer drives
    torchtitan .../examples/tmax/vanillux_loop.py (452 lines), imported for its registration side effect at
    examples/tmax/rollouter.py:101-103; the two differ by 736 diff lines. Grading is ours too: this file reaches the
    reward through repro_exec's nonce path (:1565-1570), and `grade_tmax` and `pre_test_sh` appear 0 times in it, so
    no training pre-verify hook runs over a gold rollout.

    Say "vendored sibling", never "the training rollouter": the golds are strong evidence about the task, and a
    weaker claim about the training stack than the old wording implied. See AGENT_LOOP_LABEL (:94) for the three
    unlisted swaps this makes.

    Same return shape as _agent_loop. Vanillux2Agent writes trajectory.json/timing.json/usage.json to its logs_dir —
    steps=len(timing), cost=context.cost_usd, transcript = trajectory.json (opaque, for gate 2). NEVER inspects the
    instruction/transcript/tool outputs."""
    import sys, tempfile, litellm
    sys.path.insert(0, REVIEW_SRC)                                            # for Vanillux2Agent + rl_data (read-only; review-source lock untouched)
    from Vanillux2Agent.agent import Vanillux2Agent
    from harbor.environments.base import ExecResult
    from harbor.models.agent.context import AgentContext
    litellm.drop_params = True                                                # gpt-5.x reasoning models reject top_p/temperature alongside reasoning_effort; drop the unsupported ones (only the MODEL is swapped vs training)
    raw_commands: list[str] = []
    cmd_records: list[dict] = []                                             # per-command {command, exit_code, output} for commands.jsonl retention
    _pending = [None]                                                        # the raw command about to be exec'd, set by _wrap_command; None => a non-agent exec (setup's state-dir init) that must not pair with a raw command

    class _RecordingAgent(Vanillux2Agent):                                   # capture the RAW command per step for the gate-3 replay script (cwd/env persist naturally when replayed in one bash)
        def _wrap_command(self, command):
            raw_commands.append(command); _pending[0] = command
            return super()._wrap_command(command)

    class _Env:                                                              # duck-typed: Vanillux2Agent.run only calls environment.exec
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            rc, out, _ = await sb.exec(command, user='root', timeout=timeout_sec or cfg['command_timeout'])
            raw = _pending[0]; _pending[0] = None
            if raw is not None:                                              # skip setup()'s state-dir exec (no _wrap_command) so records pair 1:1 with agent commands
                cmd_records.append({'command': raw, 'exit_code': rc, 'output': out or ''})
            return ExecResult(stdout=out or '', stderr='', return_code=rc)

        async def start(self): return None
        async def stop(self): return None

    logs = pathlib.Path(tempfile.mkdtemp(prefix='vanillux_logs_'))
    agent = _RecordingAgent(logs_dir=logs, model_name=cfg['model'], api_base=cfg.get('api_base'),
                            max_steps=cfg['max_steps'],                       # USER DECISION (22:50): lift the 64-step training cap -> pass the CLI --max-steps (e.g. 100000) into the agent; without this the agent silently runs its training default of 64
                            temperature=None, top_p=None, top_k=None)        # the Responses model (gpt-5.6-sol) REJECTS sampling params (top_p/temperature) and the bridge does not drop them; cost cap stays the training default (0.0 = unbounded)
    ctx = AgentContext()
    _tok = _REASONING.set([0])                                               # per-rollout reasoning-token counter (this asyncio task's context; propagates through asyncio.to_thread)
    _utok = _USAGE.set([])                                                    # per-call token log for this rollout
    try:
        await agent.run(instruction, _Env(), ctx)
    finally:
        _rt = (_REASONING.get() or [0])[0]; _REASONING.reset(_tok)
        _usage = list(_USAGE.get() or []); _USAGE.reset(_utok)
    cost = float(getattr(ctx, 'cost_usd', 0) or getattr(agent, 'cost', 0) or 0)
    steps = 0
    try:
        steps = len(json.loads((logs / 'timing.json').read_text()))
    except Exception:  # noqa: BLE001
        pass
    transcript = ''
    try:
        transcript = (logs / 'trajectory.json').read_text()                  # opaque; for gate 2
    except Exception:  # noqa: BLE001
        pass
    log(f'  vanillux2 finished: steps={steps} cost=${cost:.4f}')
    eff = {'max_steps': getattr(agent, 'max_steps', None), 'cost_limit': getattr(agent, 'cost_limit', None), 'reasoning_tokens': _rt,
           'commands': cmd_records,                                          # per-command {command, exit_code, output} -> commands.jsonl
           'usage': _usage}                                                  # per-call {prompt, completion, reasoning} -> token totals + per-step
    return steps, cost, transcript, raw_commands, eff


_OOM_MARKERS = ('out of memory', 'killed process', 'oom-kill', 'oomkilled', 'exit code 137', 'rc=137', 'signal 9')   # ops's set (rc 137 / oom text); disk-full is NOT here (record, no bump)
_QUOTA_MARKERS = ('quota', '429', 'too many requests', 'rate limit', 'ratelimit', 'exceeded')                       # ops's classify_error quota set


def _is_oom(text: str) -> bool:
    t = (text or '').lower()
    return any(m in t for m in _OOM_MARKERS)


def _is_platform_deleted(err: str) -> bool:
    """A sandbox the PLATFORM deleted under us mid-rollout, which is not a create-time refusal and must not be counted
    as one. The wave of 2026-09-04 lost 213 of 277 tasks this way and the summary line still read "daytona-refusals
    0", because that counter only ever meant quota/429 at create. Every one of the 175 bodies said the same thing:
    "not found: sandbox <id> not found (it has been deleted)". Root cause was ours -- auto_stop_interval 10 minutes
    with auto_delete 0, and the keep-alive heartbeat starved by pooled-client timeouts under wide fan-out -- but the
    fact to surface is that a live sandbox went away, whoever caused it."""
    e = (err or '').lower()
    return 'has been deleted' in e or ('not found' in e and 'sandbox' in e)


def _is_quota(text: str) -> bool:
    t = (text or '').lower()
    return any(m in t for m in _QUOTA_MARKERS)


_DISK_MARKERS = ('no space left', 'disk', 'space left on device', 'enospc', 'exceeds', 'too large', 'image size')


def create_failure_is_disk(error: str, image_size_mib, disk_cap_gib) -> bool:
    """A CREATE failure is disk_at_ceiling (escalate the ladder) ONLY when the error names disk/space OR a known UNCOMPRESSED
    image size exceeds the rung's disk cap. image_size_mib must be the uncompressed size (see image_size_mib_and_known) —
    the compressed registry size cannot decide this and is never passed here. With no uncompressed size (the common case)
    this returns False and the unknown-size default governs (same-rung retry, then one escalation, then a harness error),
    so a pull flake never climbs all four rungs."""
    t = (error or '').lower()
    if any(m in t for m in _DISK_MARKERS):
        return True
    if image_size_mib and disk_cap_gib and image_size_mib > disk_cap_gib * 1024:
        return True
    return False


class _ConcurrencyGate:
    """An asyncio concurrency gate that can SHRINK but never grow — mirrors ops's quota back-off (one fewer slot per
    quota error, floor 1)."""
    def __init__(self, cap: int):
        self._cap = max(1, cap); self._active = 0; self._cond = asyncio.Condition()

    async def __aenter__(self):
        async with self._cond:
            while self._active >= self._cap:
                await self._cond.wait()
            self._active += 1
        return self

    async def __aexit__(self, *exc):
        async with self._cond:
            self._active -= 1; self._cond.notify_all()

    async def shrink(self):
        async with self._cond:
            if self._cap > 1:
                self._cap -= 1
            self._cond.notify_all()


async def _attempt_with_quota(task_id: str, cfg: dict, gate, log) -> dict:
    """One run_one_live attempt with the quota/429 back-off (ops: up to create_retries, shrink the gate + sleep 60s each)."""
    rec: dict = {}; refusals = 0
    retries = cfg.get('create_retries', 0)
    deleted = 0
    for attempt in range(retries + 1):
        rec = await run_one_live(task_id, cfg, log)
        err = rec.get('error') or ''
        if err and _is_platform_deleted(err):                               # the platform deleted a LIVE sandbox: a
            deleted += 1                                                    # different fact from a create-time refusal
        if err and _is_quota(err) and attempt < retries:                    # a real Daytona refusal (quota/429): back off + shrink
            refusals += 1
            if gate is not None:
                await gate.shrink()
            log(f'{task_id}: Daytona refusal (quota/429) -> shrink + sleep 60s ({attempt + 1}/{retries})')
            await asyncio.sleep(60); continue
        break
    rec['daytona_refusals'] = refusals; rec['platform_deleted'] = deleted
    if rec.get('create_transient'):                                         # a non-disk create failure (e.g. pull flake): ONE same-rung retry (sub-attempt r1)
        log(f'{task_id}: transient create failure -> one retry at the same rung (r1)')
        rcfg = dict(cfg); rcfg['_attempt_suffix'] = (cfg.get('_attempt_suffix') or '') + 'r1'   # COMPOSE with any existing x<sub> from the parallel path (attempt_<n>x2r1_<sandbox>), never overwrite it (Fable F/f); distinct retention key, no dup manifest key
        rec = await run_one_live(task_id, rcfg, log)
        rec['daytona_refusals'] = refusals; rec['platform_deleted'] = deleted
        if rec.get('create_transient'):                                     # persisted after the retry
            if not rec.get('image_size_known') and not cfg.get('_create_escalated'):
                rec['disk_at_ceiling'] = True; rec['escalated_unknown_size'] = True   # unknown size: escalate ONCE at the FIRST persistent create failure (whatever rung it happens at, still only once via _create_escalated)
                log(f'{task_id}: persistent create failure, size unknown -> escalate once (disk_at_ceiling)')
            else:
                rec['harness_error'] = True                                 # known size (<=cap) OR already escalated once -> harness error, never a further climb
                log(f'{task_id}: persistent create failure -> harness error (no ladder climb)')
    return rec


async def _rollout_with_retries(task_id: str, cfg: dict, gate, log) -> dict:
    """SIZING (--sizing): the resource LADDER (user ruling). Boot 1/1/1; if the finished attempt is at a ceiling
    (ram_at_ceiling: oom_kill/rc137/cumulative peak >=95% memory.max, or disk_at_ceiling: ENOSPC/du >=95% cap) re-run once
    at the next rung 1/1/1 -> 2/2/2 -> 4/4/4 -> 4/8/10, until un-pressured or the top rung also ceilings. Every attempt
    keeps its own retention dir + resources.json; the returned (last) rec is the canonical one the provenance points at.
    LEGACY (no --sizing): the prior quota + single OOM bump behaviour, unchanged."""
    if cfg.get('sizing'):
        ladder = cfg.get('ladder') or RAM_LADDER                             # rung objects from the plan when present, else the user's fixed ladder
        prev_flag = None; rec = {}; create_escalated = False; skipped: list = []
        i = min(max(int((cfg.get('start_rungs') or {}).get(task_id, 0)), 0), len(ladder) - 1)   # per-task start_rung hint (clamped), else rung 0
        while i < len(ladder):
            rung = ladder[i]
            acfg = dict(cfg); acfg.update(cpu=rung[0], mem_gb=rung[1], disk_gb=rung[2], _attempt=i + 1, _oom_rerun=(i > 0), _bump_flag=prev_flag, _is_top_rung=(i == len(ladder) - 1), _create_escalated=create_escalated, _skipped_rungs=list(skipped))
            rec = await _attempt_with_quota(task_id, acfg, gate, log)
            if rec.get('baseline_over_cap'):                                 # BASELINE GATE fired: the env alone is >=95% of this rung -> jump to the first rung that fits BOTH resources (skip the intermediate rungs), no agent ran
                fit = first_fitting_rung(ladder, rec.get('env_ram_mib'), rec.get('env_disk_mib'), start=i + 1)
                if fit is None:
                    rec['status'] = 'parked_at_ceiling'                      # nothing on the ladder fits the environment
                    log(f'{task_id}: baseline over cap at rung {rung} and no larger rung fits -> parked')
                    if cfg.get('provenance_out'):                            # Fable minor: PERSIST parked_at_ceiling (the per-attempt rows say baseline_over_cap; without this the park is only on the returned rec)
                        _rl, _al = _labels(cfg.get('agent', 'self_contained'))
                        _prow = parked_at_ceiling_row(task_id, model=cfg['model'], reasoning_effort=cfg['reasoning_effort'],
                                                      max_steps=cfg['max_steps'], date=cfg.get('date', ''), rollouter=_rl, agent_loop=_al,
                                                      rung={'cpu': rung[0], 'mem_gib': rung[1], 'disk_gib': rung[2]},
                                                      env_ram_mib=rec.get('env_ram_mib'), env_disk_mib=rec.get('env_disk_mib'),
                                                      skipped_rungs=skipped, litellm_patch=cfg.get('litellm_patch'))
                        _lk = cfg.get('_write_lock')
                        async with (_lk if _lk is not None else contextlib.nullcontext()):
                            with open(cfg['provenance_out'], 'a') as fh:
                                fh.write(json.dumps(_prow) + '\n')
                    break
                skipped = skipped + list(range(i + 1, fit))                  # rungs jumped over (never attempted)
                log(f'{task_id}: baseline over cap at rung {rung} (env ram {rec.get("env_ram_mib")}MiB / disk {rec.get("env_disk_mib")}MiB) -> jump to rung {ladder[fit]} (skipped {skipped})')
                i = fit; continue
            if acfg.get('agent') == 'vanillux2' and rec.get('reasoning_tokens') == 0 and not rec.get('no_reasoning_rerun'):
                log(f'{task_id}: reasoning_tokens=0 at rung {rung} -> re-roll once at the same rung')
                rec = await _attempt_with_quota(task_id, acfg, gate, log); rec['no_reasoning_rerun'] = True
            pressured = bool(rec.get('ram_at_ceiling') or rec.get('disk_at_ceiling'))
            if not pressured or i == len(ladder) - 1:
                if pressured:
                    log(f'{task_id}: at-ceiling at TOP rung {rung} -> parked, no further attempt')
                break
            prev_flag = 'ram_at_ceiling' if rec.get('ram_at_ceiling') else 'disk_at_ceiling'
            if rec.get('escalated_unknown_size'):                            # an unknown-size create escalation is spent once -> the next rung must not escalate a create failure again
                create_escalated = True
            log(f'{task_id}: {prev_flag} at rung {rung} -> bump to next rung')
            i += 1
        return rec
    # LEGACY path (non-sizing runs, e.g. the earlier 42/62 cohorts)
    rec = await _attempt_with_quota(task_id, cfg, gate, log)
    if _is_oom(rec.get('error') or '') and not rec.get('oom_rerun'):
        log(f'{task_id}: OOM-suspect -> re-run once at {cfg["oom_cpu"]}cpu/{cfg["oom_mem_gb"]}GB/{cfg["oom_disk_gb"]}GB (memory bump)')
        bumped = dict(cfg); bumped.update(cpu=cfg['oom_cpu'], mem_gb=cfg['oom_mem_gb'], disk_gb=cfg['oom_disk_gb'], _oom_rerun=True)
        rec = await _attempt_with_quota(task_id, bumped, gate, log); rec['oom_rerun'] = True
    if cfg.get('agent') == 'vanillux2' and rec.get('reasoning_tokens') == 0 and not rec.get('no_reasoning_rerun'):
        log(f'{task_id}: completed with reasoning_tokens=0 (model did not reason) -> re-roll once')
        rec = await _attempt_with_quota(task_id, cfg, gate, log); rec['no_reasoning_rerun'] = True
    return rec


async def _parallel_rollout(task_id: str, roll_index: int, cfg: dict, gate, log) -> dict:
    """ONE independent rollout for --parallel-rolls (user ruling: N per task run CONCURRENTLY, each its own sandbox id /
    retention dir <sandbox_id>/ / provenance row; the packing rollout writes the single replay pointer). Sizing = the
    UNCONDITIONAL --caps-from-peak-plus-gib form (the seven re-roll tasks have no recorded peak, so the measured baseline is
    the only peak available): create at --cpu-fixed on the start_rung hint, MEASURE the baseline, tear down, ALWAYS recreate
    at ceil(baseline)+G GiB (the extra create is cheaper than a wrong cap), run the agent; if the agent phase hits a ceiling,
    recreate ONCE at ceil(run-peak)+G. The 95% baseline gate is kept as a SAFETY for the case even baseline+G does not fit.
    All caps clamped to the platform max (top ladder rung). Returns the run rec (its status/gate1 drives pass/pack/park)."""
    ladder = cfg.get('ladder') or RAM_LADDER
    plat_mem, plat_disk = ladder[-1][1], ladder[-1][2]                        # platform max = the top ladder rung (8 GiB / 10 GiB)
    start = min(max(int((cfg.get('start_rungs') or {}).get(task_id, 0)), 0), len(ladder) - 1)
    _hm, _hd = ladder[start][1], ladder[start][2]                             # the hint rung's caps = the fallback when a sample is missing (Fable F4: never shrink to a bare G)
    C = cfg['cpu_fixed']; G = cfg['caps_from_peak_plus_gib']
    # (1) BASELINE PROBE: create at cpu C on the hint rung ONLY to measure the baseline (no agent, no tokens, no retention)
    pcfg = dict(cfg); pcfg.update(cpu=C, mem_gb=_hm, disk_gb=_hd,
                                  _attempt=roll_index, _roll_index=roll_index, _baseline_probe=True)
    probe = await _attempt_with_quota(task_id, pcfg, gate, log)
    if probe.get('env_ram_mib') is None:                                      # the probe create failed (env never measured) -> that failed attempt IS the rollout's outcome
        probe['roll_index'] = roll_index; return probe
    mem = max(_hm, caps_plus_gib(probe.get('env_ram_mib'), G, plat_mem, fallback_gib=_hm))   # UNCONDITIONAL resize to ceil(baseline)+G (clamped); floored at the hint rung so a tiny/missing baseline never drops the run below it (Fable F4)
    disk = max(_hd, caps_plus_gib(probe.get('env_disk_mib'), G, plat_disk, fallback_gib=_hd))
    log(f'{task_id} roll {roll_index}: baseline ram {probe.get("env_ram_mib")}MiB / disk {probe.get("env_disk_mib")}MiB -> agent caps {mem}/{disk} GiB (baseline+{G})')
    # (2) RUN at cpu C, caps = baseline+G; the 95% gate stays as a safety; one peak+G recreate on an agent ceiling
    sub = 2; recreated_peak = False; rec: dict = {}
    while True:
        acfg = dict(cfg); acfg.update(cpu=C, mem_gb=mem, disk_gb=disk, _attempt=roll_index,
                                      _attempt_suffix=f'x{sub}', _roll_index=roll_index,
                                      _oom_rerun=(sub > 2), _is_top_rung=(mem >= plat_mem and disk >= plat_disk))
        rec = await _attempt_with_quota(task_id, acfg, gate, log)
        if rec.get('baseline_over_cap'):                                      # SAFETY: even baseline+G was <5% headroom -> grow to baseline+G of the observed env, else park
            nm = caps_plus_gib(rec.get('env_ram_mib'), G, plat_mem, fallback_gib=mem); nd = caps_plus_gib(rec.get('env_disk_mib'), G, plat_disk, fallback_gib=disk)
            if nm <= mem and nd <= disk:
                rec['status'] = 'parked_at_ceiling'
                log(f'{task_id} roll {roll_index}: baseline over cap at clamp {mem}/{disk} GiB -> parked'); break
            mem, disk = max(mem, nm), max(disk, nd); sub += 1; continue
        pressured = bool(rec.get('ram_at_ceiling') or rec.get('disk_at_ceiling'))
        if pressured and not recreated_peak:                                  # agent-phase ceiling -> recreate ONCE at run-peak+G
            nm = caps_plus_gib(rec.get('peak_ram_mb'), G, plat_mem, fallback_gib=mem); nd = caps_plus_gib(rec.get('peak_disk_mb'), G, plat_disk, fallback_gib=disk)
            if nm <= mem and nd <= disk:                                      # can't grow (clamp reached) -> park at ceiling
                log(f'{task_id} roll {roll_index}: agent ceiling at clamp {mem}/{disk} GiB -> parked'); break
            log(f'{task_id} roll {roll_index}: agent ceiling -> recreate once at peak+{G} GiB ({nm}/{nd})')
            mem, disk = max(mem, nm), max(disk, nd); recreated_peak = True; sub += 1; continue
        break
    rec['roll_index'] = roll_index
    return rec


# The trainer bounds EVERY command it runs in-sandbox: harness/sandbox/daytona.py:166-171 wraps each one in
# `timeout --signal=TERM --kill-after=10s <N>s` with N = 120 (_COMMAND_KILL_GRACE_SEC = 10), and rc 124 is the
# kill. The gold rollouts ran through that same class (tools/daytona_backend.py:25-36 loads it unmodified;
# gold_rollout.py's own rollout path passes command_timeout 120). The replay had NO per-command bound, so one
# hung command ate the whole-script cap -- three shipped solves run past 1800 s for exactly that. These two
# numbers are the trainer's, cited rather than guessed, and they are constants: reading them from the
# environment would let a replay be measured under a bound the training run never had.
# ONE definition of a form-B invocation line, used by the writer below and by the reader further down, and
# pinned by the fork's counter patch (tools/patches/torchtitan_oracle_commands_harness_form_1.patch) whose
# regex a test asserts equal to this string. Three readers of one shape is how a writer drifts unnoticed.
GR_INVOCATION_RE = r"^_gr '[A-Za-z0-9+/=]+'$"
# ...and the widest thing that is still an INVOCATION rather than the definition or a `_gr_run=`-style variable:
# `\b` does not fire inside a word, and the lookahead drops `_gr(){`. Any line this finds that the strict shape
# does not is drift, and both the fork's counter and the reader below refuse on exactly that difference.
GR_INVOCATION_LOOSE_RE = r"^\s*_gr\b(?!\()"
GR_DEFINITION_RE = r"^_gr\(\)\{"
_GR_INVOCATION = re.compile(GR_INVOCATION_RE, re.M)
_GR_INVOCATION_LOOSE = re.compile(GR_INVOCATION_LOOSE_RE, re.M)
_GR_DEFINITION = re.compile(GR_DEFINITION_RE, re.M)
REPLAY_COMMAND_TIMEOUT_SEC = 120
REPLAY_KILL_GRACE_SEC = 10


def render_replay_script(commands: list[str], *,
                         command_timeout_sec: int = REPLAY_COMMAND_TIMEOUT_SEC) -> tuple[str, str]:
    """Build the form-B replay script BODY + its sha256 WITHOUT writing it, so the same exact bytes can be both the
    latest-wins pointer (replay_dir/<task_id>.sh, packer-written) and the per-rollout retained copy
    (retention_root/<task_id>/<sandbox_id>/replay.sh). write_replay_script wraps this. See write_replay_script's docstring
    for the form-B contract (no set -e, per-command bash -c with cwd/env statefile, EXIT-trap REPLAY sidecar, exit 0).

    Each command is bounded the way the trainer bounds every command it runs (REPLAY_COMMAND_TIMEOUT_SEC): a
    hung one is killed at that bound instead of eating the whole-script cap, and its rc 124 goes to the
    commands_timed_out counter rather than to commands_nonzero. That counter counts an rc, NOT a cause: 124 is
    equally producible by a task-authored `timeout` inside the command -- 305 of the 453 shipped replays carry one,
    across 767 commands (measured 2026-09-06) -- and the wrapper cannot tell the two apart. Reading
    commands_timed_out as "the bound fired" produced a wrong diagnosis the day the bound landed; the honest reading
    is "this many commands exited 124", and duration is what distinguishes a kill from a task's own timeout. The bound is applied only where GNU timeout
    exists -- an image without it runs exactly as before and the sidecar prints a REPLAY-NOTE saying the bound
    was not applied, rather than failing every command with rc 127 in exchange for a guarantee. ``command_timeout_sec`` exists so a test can
    reach the mechanism in seconds; a script rendered with anything but the trainer's number says so in a
    comment line, because a replay measured under a different bound is not the run it claims to reproduce."""
    b64 = [base64.b64encode(c.encode()).decode() for c in commands]          # base64 => no quoting/heredoc hazards in the emitted script
    if command_timeout_sec != REPLAY_COMMAND_TIMEOUT_SEC:                    # only a test may move it, and it says so in the script
        lines_note = f'# NOTE: per-command bound {command_timeout_sec}s, not the trainer\'s {REPLAY_COMMAND_TIMEOUT_SEC}s'
    else:
        lines_note = None
    # Each command runs in its OWN `bash -c` with cwd/env carried through a statefile — exactly mirroring Vanillux2Agent's
    # per-command exec (review-source agent.py:315). This makes an `exit` INSIDE a command end only that command's shell
    # (in a single persistent bash, an eval'd `exit` killed the replay before the sidecar => the gold-replay-no-sidecar
    # failures rc127/rc1). cwd/env still persist across commands via the statefile, so the reproduced end state is faithful.
    # Every external tool the wrapper needs is resolved to an ABSOLUTE path HERE, before any recorded command
    # runs, and invoked by that path afterwards. A replayed command that destroys PATH used to take the decoder
    # with it: `bash "$0/dec"` then failed, the command substitution yielded empty, `eval ""` returned 0, and the
    # sidecar reported a clean run in which nothing had executed -- three commands, {run 3, nonzero 0}, two of
    # them never run. The same disease reached `cat` for the cwd and `bash`/`timeout` for the command itself.
    lines = ['#!/usr/bin/env bash',
             '# gold-rollout form-B replay: no set -e; each command in its OWN bash -c with cwd/env via a statefile so an',
             '# `exit` in a command ends only that command (not the replay); the REPLAY sidecar is emitted from an EXIT trap',
             '# so even a partial/killed run reports commands_run; every tool below is resolved to an absolute path before',
             '# the first command runs, so a command that destroys PATH cannot silently disable the ones after it.',
             '_gr_run=0; _gr_nz=0; _gr_to=0; _gr_und=0',
             '_GR_SH=$(command -v bash); _GR_B64=$(command -v base64); _GR_PY=$(command -v python3)',
             # The bound needs GNU timeout. Some task images do not ship it, and a replay that simply invoked it
             # there would fail EVERY command with rc 127 and reach no end state -- a silent catastrophe in
             # exchange for a bound. Resolved once, absolute: present, every command is bounded; absent, the
             # replay runs as it always did and the sidecar says the bound was not applied, because "nothing
             # timed out" and "nothing could time out" are different facts.
             f'_GR_TOBIN=$(command -v timeout); _GR_TO=""; [ -n "$_GR_TOBIN" ] && '
             f'_GR_TO="$_GR_TOBIN --signal=TERM --kill-after={REPLAY_KILL_GRACE_SEC}s {command_timeout_sec}s"',
             '_gr_sidecar(){ printf \'REPLAY {"commands_run": %d, "commands_nonzero": %d, "commands_timed_out": %d, "commands_undecoded": %d}\\n\' "$_gr_run" "$_gr_nz" "$_gr_to" "$_gr_und"; '
             '[ -z "$_GR_TO" ] && printf \'REPLAY-NOTE no per-command bound: timeout(1) absent\\n\'; return 0; }',
             'trap _gr_sidecar EXIT',                                        # sidecar always prints (normal exit, exit-in-command, or kill) => never gold-replay-no-sidecar
             '_GR_ST=$(mktemp -d)',
             # The decoder file holds ONE absolute command (base64 where present, python3 otherwise) and is fed
             # to the interpreter by pathname. Not an executable: that would need `chmod` at start and an exec
             # bit on /tmp, two more ways to lose the decoder. The interpreter arrives as a POSITIONAL argument
             # ($2 below), so neither PATH nor the replayed command's own environment can take it away.
             'if [ -n "$_GR_B64" ]; then printf \'%s\\n\' "exec $_GR_B64 -d" > "$_GR_ST/dec"; '
             'else printf \'%s\\n\' "exec $_GR_PY -c \\"import sys,base64; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))\\"" > "$_GR_ST/dec"; fi',
             'printf %s "$PWD" > "$_GR_ST/cwd"',
             'export -p > "$_GR_ST/env" 2>/dev/null || true',
             # Inner shell, single-quoted, therefore free of single quotes: read the cwd with a BUILTIN (`cat`
             # would need PATH too), source the carried env, decode by pathname, and refuse to eval an empty
             # decode of a non-empty payload -- that is the shape that used to look like a successful no-op.
             '_gr(){ _gr_run=$((_gr_run+1)); : > "$_GR_ST/dec_out"; '
             '$_GR_TO "$_GR_SH" -c \'IFS= read -r _c < "$0/cwd"; [ -n "$_c" ] && cd "$_c" 2>/dev/null; '
             '. "$0/env" 2>/dev/null || true; _d=$(printf %s "$1" | "$2" "$0/dec"); printf %s "$_d" > "$0/dec_out"; '
             '[ -n "$1" ] && [ -z "$_d" ] && exit 127; eval "$_d"; _rc=$?; '
             'printf %s "$PWD" > "$0/cwd"; export -p > "$0/env" 2>/dev/null || true; exit $_rc\' '
             '"$_GR_ST" "$1" "$_GR_SH"; '
             # 124 is timeout's kill; a non-zero with nothing decoded is the decoder having failed, not the
             # command; anything else non-zero is the command's own, tolerated, failure.
             'local _rc=$?; if [ "$_rc" -eq 124 ]; then _gr_to=$((_gr_to+1)); '
             'elif [ "$_rc" -ne 0 ] && [ ! -s "$_GR_ST/dec_out" ]; then _gr_und=$((_gr_und+1)); '
             'elif [ "$_rc" -ne 0 ]; then _gr_nz=$((_gr_nz+1)); fi; return 0; }']
    if lines_note:
        lines.append(lines_note)
    lines += [f"_gr {chr(39)}{b}{chr(39)}" for b in b64]                     # base64 alphabet has no single-quote, so the quoting is safe
    for l in lines[-len(b64):] if b64 else []:                               # the writer is held to the shared shape too
        if not _GR_INVOCATION.match(l):
            raise ValueError(f'emitted an invocation line the pinned shape does not match: {l[:60]!r}')
    lines.append('exit 0')                                                   # the EXIT trap prints the sidecar
    body = '\n'.join(lines) + '\n'
    return body, hashlib.sha256(body.encode()).hexdigest()


def write_replay_script(path: str | pathlib.Path, commands: list[str]) -> str:
    """Save the agent's executed RAW commands as an ordered replay script that MIRRORS the agent's session: every command
    in order, per-command failures TOLERATED (NO set -e — the agent ran each command independently and tolerated
    exploratory non-zeros; set -e aborted faithful replays at the first tolerated failure), cwd/env carried because the
    commands run in ONE bash. Runnable as root in a fresh sandbox with the same no-entrypoint start; background services
    the agent started with nohup/disown are preserved. gate-3c grades the reproduced END STATE (it does not gate on an
    intermediate non-zero). Returns the script's sha256. Holds command CONTENT -> its directory must be gitignored.

    FORM B + sidecar: each raw command is base64-encoded and run via `eval` inside a shell FUNCTION (not a subshell), so
    cwd/env persist across commands and heredocs/quotes survive intact; per-command rc is counted; a `REPLAY {json}` sidecar
    line (commands_run/commands_nonzero) is emitted for the gate-3c executor (daytona_backend.parse_replay_summary), and the
    script `exit 0`s so a tolerated nonzero command never makes the executor void a good end state. commands_run==0 (empty
    replay) is the only shape the executor reads as "did not run"."""
    body, sha = render_replay_script(commands)
    pathlib.Path(path).write_text(body)
    return sha


def replay_commands_from_script(path: str | pathlib.Path) -> list[str]:
    """Recover the raw command list from a form-B replay script (decode the base64 in each `_gr '<b64>'` line). Lets a
    replay be regenerated in a new form-B shape WITHOUT re-rolling the model. Returns [] for a plain/old script.

    A form-B script whose invocation lines do not all match the pinned shape RAISES, naming the line: this reader used
    to skip anything it could not parse, so a writer that drifted (a trailing comment, double quotes, an unquoted
    `$var`, a payload wrapped across lines) would have yielded a SHORTER command list and the caller would have
    regenerated a replay missing commands, with nothing anywhere saying so. A script with no `_gr()` definition is a
    plain/old one and still returns [] as documented."""
    text = pathlib.Path(path).read_text()
    if not _GR_DEFINITION.search(text):
        return []                                                            # plain/old script: nothing to recover
    out = []
    for n, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if not _GR_INVOCATION_LOOSE.match(ln):
            continue                                                         # the preamble's own `_gr_*` lines
        if not _GR_INVOCATION.match(ln):
            raise ValueError(f'{pathlib.Path(path).name}:{n}: `_gr` line does not match the pinned invocation '
                             f'shape {GR_INVOCATION_RE!r}, so the recovered command list would be short')
        b = ln.strip()[len("_gr '"):-1]
        try:
            out.append(base64.b64decode(b).decode())
        except Exception as e:  # noqa: BLE001
            raise ValueError(f'{pathlib.Path(path).name}:{n}: payload does not decode ({type(e).__name__})') from None
    return out


# ---- resource sizing (SANDBOX_SIZING.md + tmax-log-reviewer layout + lead/user rulings) ----
# Daytona is cgroup v2 (kernel 6.8) but writing memory.peak returns EACCES, so the FALLBACK is the live path:
# env-baseline peak + cumulative run peak, task figure a lower bound (ram_lower_bound). memory.current is polled at 1s
# during the agent to record a sampled agent peak (labelled); the provisioning number stays the cumulative memory.peak.
CG2 = {'peak': '/sys/fs/cgroup/memory.peak', 'cur': '/sys/fs/cgroup/memory.current', 'max': '/sys/fs/cgroup/memory.max',
       'events': '/sys/fs/cgroup/memory.events', 'stat': '/sys/fs/cgroup/memory.stat', 'cpu': '/sys/fs/cgroup/cpu.stat', 'cpumax': '/sys/fs/cgroup/cpu.max'}
CG1 = {'peak': '/sys/fs/cgroup/memory/memory.max_usage_in_bytes', 'cur': '/sys/fs/cgroup/memory/memory.usage_in_bytes',
       'max': '/sys/fs/cgroup/memory/memory.limit_in_bytes', 'events': '/sys/fs/cgroup/memory/memory.oom_control',
       'stat': '/sys/fs/cgroup/memory/memory.stat', 'cpu': '/sys/fs/cgroup/cpuacct/cpuacct.usage', 'cpumax': '/sys/fs/cgroup/cpu/cpu.cfs_quota_us'}
_FAST_LOG = '/tmp/.gold_fast_samples'; _FAST_PID = '/tmp/.gold_fast.pid'      # 1s: memory.current, df used, cpu usage_usec
_DU_LOG = '/tmp/.gold_du_samples'; _DU_PID = '/tmp/.gold_du.pid'             # du_interval: du -sx -m /


def _method_string(cgv: str, du_interval_s) -> str:
    counters = 'cgroup v2 memory.peak/current/max/stat/events, cpu.stat usage_usec' if cgv == 'v2' else \
               'cgroup v1 memory.max_usage_in_bytes/usage_in_bytes/limit_in_bytes/stat/oom_control, cpuacct.usage'
    return (f'{counters}; memory.peak reset attempted (EACCES on Daytona -> fallback: env-baseline + cumulative run peak, '
            f'task figure a lower bound); memory.current polled 1s (sampled agent peak); disk block-based du -sx -m / '
            f'(default, not --apparent-size) every {du_interval_s}s (max, grid-bounded lower bound) + df -m used 1s for the '
            f'ceiling (df is INFORMATIONAL on Daytona: df -m --output=used / reports the writable overlay layer only, not '
            f'image occupancy, so disk_at_ceiling is decided by du + ENOSPC, never df); anon+shmem from memory.stat; tmpfs '
            f'from findmnt; oom_kill from memory.events; avg_cpu_cores is a '
            f'window average (cpu.stat usage_usec delta / sample span), not an instantaneous peak; units MiB')


def _mib(nbytes) -> float | None:
    return round(int(nbytes) / 1048576, 1) if nbytes is not None else None


async def _read_int_file(sb, path: str):
    rc, out, _ = await sb.exec(f'cat {path} 2>/dev/null', user='root', timeout=30)
    s = (out or '').strip()
    return int(s) if (rc == 0 and s.lstrip('-').isdigit()) else None          # 'max' / missing -> None


async def detect_cgroup(sb) -> str:
    rc, out, _ = await sb.exec('test -f /sys/fs/cgroup/cgroup.controllers && echo v2 || echo v1', user='root', timeout=30)
    return (out or '').strip() or 'v2'


async def sample_mem_peak_bytes(sb, cgv='v2'): return await _read_int_file(sb, (CG2 if cgv == 'v2' else CG1)['peak'])
async def sample_mem_max_bytes(sb, cgv='v2'): return await _read_int_file(sb, (CG2 if cgv == 'v2' else CG1)['max'])
async def sample_mem_current_bytes(sb, cgv='v2'): return await _read_int_file(sb, (CG2 if cgv == 'v2' else CG1)['cur'])


async def sample_mem_stat(sb, cgv='v2') -> dict:
    """anon (un-reclaimable working set, the hard floor) + shmem (tmpfs lands in RAM as shmem, not on disk), bytes."""
    p = (CG2 if cgv == 'v2' else CG1)['stat']
    rc, out, _ = await sb.exec(f"awk '/^anon /{{a=$2}} /^shmem /{{s=$2}} END{{print a\" \"s}}' {p} 2>/dev/null", user='root', timeout=30)
    parts = (out or '').split()
    to = lambda x: int(x) if x.isdigit() else None
    return {'anon': to(parts[0]) if len(parts) > 0 else None, 'shmem': to(parts[1]) if len(parts) > 1 else None}


async def reset_mem_peak(sb, cgv='v2') -> bool:
    """Attempt to reset the mem high-water (kernel >=6.8 allows writing memory.peak). On Daytona this is EACCES as root, so
    it returns False and the caller uses the cumulative-peak fallback. Kept for a future kernel/platform that allows it."""
    if cgv != 'v2':
        return False
    rc, _, _ = await sb.exec(f'echo 0 > {CG2["peak"]} 2>/dev/null', user='root', timeout=30)
    return rc == 0


async def read_oom_kill(sb, cgv='v2'):
    if cgv == 'v2':
        rc, out, _ = await sb.exec(f"awk '/^oom_kill /{{print $2}}' {CG2['events']} 2>/dev/null", user='root', timeout=30)
    else:
        rc, out, _ = await sb.exec(f"awk '/oom_kill /{{print $2}}' {CG1['events']} 2>/dev/null", user='root', timeout=30)
    s = (out or '').strip()
    return int(s) if s.isdigit() else None


async def sample_du_mib(sb):
    rc, out, _ = await sb.exec('du -sx -m / 2>/dev/null | cut -f1', user='root', timeout=300)
    s = (out or '').strip()
    return int(s) if s.isdigit() else None


async def measure_du_cost_s(sb) -> float:
    """Time one du -sx / so the poll interval can be max(2, 2x cost): du on a large image can exceed 1s."""
    rc, out, _ = await sb.exec("s=$(date +%s.%N); du -sx / >/dev/null 2>&1; e=$(date +%s.%N); awk -v a=$s -v b=$e 'BEGIN{printf \"%.2f\", b-a}'", user='root', timeout=300)
    try:
        return float((out or '0').strip())
    except Exception:  # noqa: BLE001
        return 0.0


async def sample_tmpfs_mounts(sb) -> list:
    rc, out, _ = await sb.exec('findmnt -t tmpfs -n -o TARGET 2>/dev/null', user='root', timeout=30)
    return [l.strip() for l in (out or '').splitlines() if l.strip()]


async def sample_root_fstype(sb):
    """The / filesystem type, so the df arm's informational status (df reports the writable overlay layer on an overlay/
    fuse fs) is explainable from the record. findmnt, falling back to stat -f."""
    rc, out, _ = await sb.exec('findmnt -no FSTYPE / 2>/dev/null || stat -f -c %T / 2>/dev/null', user='root', timeout=30)
    return (out or '').strip() or None


async def sample_cpu_max_cores(sb, cgv='v2'):
    if cgv != 'v2':
        return None
    rc, out, _ = await sb.exec(f'cat {CG2["cpumax"]} 2>/dev/null', user='root', timeout=30)
    parts = (out or '').split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) > 0:
        return round(int(parts[0]) / int(parts[1]), 2)
    return None                                                              # 'max <period>' => uncapped


def _fast_poller_body(cur: str, cpu_cmd: str) -> str:
    """The fast (1s) sampler loop: 'ts cur df cpu' per line. Written to a FILE (base64) and run as `bash <file>` so the
    awk single-quotes in cpu_cmd never collide with an outer bash -c quote (the bug that left these fields None)."""
    return ("while :; do\n"
            "  printf '%s %s %s %s\\n' \"$(date +%s)\" \"$(cat " + cur + " 2>/dev/null)\" "
            "\"$(df -m --output=used / 2>/dev/null | tail -1 | tr -d ' ')\" \"$(" + cpu_cmd + " 2>/dev/null)\"\n"
            "  sleep 1\ndone\n")


def _du_poller_body(du_interval_s) -> str:
    return ("while :; do\n"
            "  printf '%s %s\\n' \"$(date +%s)\" \"$(du -sx -m / 2>/dev/null | cut -f1)\"\n"
            f"  sleep {du_interval_s}\ndone\n")


async def poller_start(sb, cgv: str, du_interval_s):
    """Two in-sandbox background pollers: fast (1s: memory.current, df -m used /, cpu usage_usec) and du (du_interval). Each
    loop body is base64-written to a file then `nohup bash <file>` — no nested-quote collisions (an inline awk '...' inside
    a bash -c '...' silently broke the fast poller and left sampled_agent_ram/cpu None on the whole 26-row bucket-0 run)."""
    cur = (CG2 if cgv == 'v2' else CG1)['cur']
    cpu_cmd = f"awk '/usage_usec/{{print $2}}' {CG2['cpu']}" if cgv == 'v2' else f"cat {CG1['cpu']}"
    fast_b64 = base64.b64encode(_fast_poller_body(cur, cpu_cmd).encode()).decode()
    du_b64 = base64.b64encode(_du_poller_body(du_interval_s).encode()).decode()
    fast = (f"printf %s '{fast_b64}' | base64 -d > /tmp/.gold_fast.sh; rm -f {_FAST_LOG}; "
            f"nohup bash /tmp/.gold_fast.sh > {_FAST_LOG} 2>/dev/null & echo $! > {_FAST_PID}")
    du = (f"printf %s '{du_b64}' | base64 -d > /tmp/.gold_du.sh; rm -f {_DU_LOG}; "
          f"nohup bash /tmp/.gold_du.sh > {_DU_LOG} 2>/dev/null & echo $! > {_DU_PID}")
    await sb.exec(fast, user='root', timeout=30)
    await sb.exec(du, user='root', timeout=30)


async def poller_stop_read(sb) -> dict:
    """Stop both pollers; return sampled_agent_ram_bytes (max memory.current), df_max_used_mib, du_max_mib, cpu deltas."""
    await sb.exec(f'kill "$(cat {_FAST_PID} 2>/dev/null)" "$(cat {_DU_PID} 2>/dev/null)" 2>/dev/null; true', user='root', timeout=30)
    rc, fast, _ = await sb.exec(f'cat {_FAST_LOG} 2>/dev/null', user='root', timeout=30)
    rc2, du, _ = await sb.exec(f'cat {_DU_LOG} 2>/dev/null', user='root', timeout=30)   # lines are "ts mib"; take max mib (not sort, which would order by ts)
    cur_max = df_max = None; cpu_first = cpu_last = None; ts_first = ts_last = None; n = 0
    for ln in (fast or '').splitlines():
        p = ln.split()                                                       # ts cur df cpu (each sample timestamped, Fable B6)
        if len(p) >= 1 and p[0].isdigit():
            t = int(p[0]); ts_first = t if ts_first is None else ts_first; ts_last = t
        if len(p) >= 2 and p[1].isdigit():
            v = int(p[1]); cur_max = v if cur_max is None else max(cur_max, v)
        if len(p) >= 3 and p[2].isdigit():
            v = int(p[2]); df_max = v if df_max is None else max(df_max, v)
        if len(p) >= 4 and p[3].isdigit():
            c = int(p[3]); cpu_first = c if cpu_first is None else cpu_first; cpu_last = c
        n += 1
    span = (ts_last - ts_first) if (ts_first is not None and ts_last is not None) else max(n - 1, 1)
    cpu_core_seconds = round((cpu_last - cpu_first) / 1_000_000, 2) if (cpu_first is not None and cpu_last is not None) else None
    avg_cpu_cores = round((cpu_last - cpu_first) / 1_000_000 / max(span, 1), 2) if (cpu_core_seconds is not None and span > 0) else None
    du_max = None; du_n = 0                                                   # du lines are "ts mib"; max over the mib column
    for ln in (du or '').splitlines():
        p = ln.split()
        v = p[-1] if p else ''
        if v.isdigit():
            du_max = int(v) if du_max is None else max(du_max, int(v)); du_n += 1
    return {'sampled_agent_ram_bytes': cur_max, 'df_max_used_mib': df_max, 'du_max_mib': du_max,
            'cpu_core_seconds': cpu_core_seconds, 'avg_cpu_cores': avg_cpu_cores,
            'fast_samples': n, 'du_samples': du_n, 'sample_span_s': span, 'ts_first': ts_first, 'ts_last': ts_last}


async def resource_identity(sb, cfg: dict, image: str, cgv: str, reset_supported: bool, du_interval_s) -> dict:
    rc, kern, _ = await sb.exec('uname -r', user='root', timeout=30)          # in-sandbox kernel; NEVER nproc (returns host 64)
    cpu_cap = await sample_cpu_max_cores(sb, cgv)                             # enforced cpu cap from cpu.max (the rung is the request)
    root_fstype = await sample_root_fstype(sb)                               # explains the informational df arm (overlay/fuse => df sees the writable layer only)
    return {'task_id': None, 'run_id': cfg.get('run_id'), 'image_id': cfg.get('image_digest'), 'root_fstype': root_fstype,   # digest only; NO silent fallback to the image tag (Fable B5) — run_one_live fails loudly if the plan lacks it
            'sandbox_size': {'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')},
            'cpu_max_cores': cpu_cap, 'kernel': (kern or '').strip(), 'cgroup_version': cgv, 'reset_supported': bool(reset_supported),
            'du_interval_s': du_interval_s, 'method': _method_string(cgv, du_interval_s), 'trace_ref': cfg.get('trace_ref')}


def compute_resources(*, identity: dict, env_phase: dict, run_phase: dict, mem_max_bytes, disk_cap_gib,
                      ram_reset_applied: bool, is_oom_rerun: bool, rc137: bool, enospc_seen: bool,
                      attempt: int, rung: dict, bump_flag=None) -> dict:
    """Assemble the nested resources record (identity/phases/derived/flags). Pure -> unit-tested. env_phase/run_phase carry
    raw bytes; run_phase also carries the cumulative run peak, the 1s-sampled agent peak, df/du maxima and cpu. Without a
    reset (the Daytona reality) the task figures are lower bounds (ram_lower_bound=true) and the delta is never a peak."""
    def _ph(p, agent=False):
        d = {'peak_ram_mb': _mib(p.get('peak_ram_bytes')), 'peak_ram_anon_mb': _mib(p.get('anon_bytes')),
             'shmem_mb': _mib(p.get('shmem_bytes')), 'peak_disk_mb': p.get('disk_mib')}
        if agent:
            d.update({'sampled_agent_ram_peak_mb': _mib(p.get('sampled_agent_ram_bytes')),
                      'df_max_used_mib': p.get('df_max_used_mib'),           # persisted: it feeds disk_at_ceiling's third arm + disk_spike_risk, so the ceiling call is auditable from the record
                      'avg_cpu_cores': p.get('avg_cpu_cores'), 'cpu_core_seconds': p.get('cpu_core_seconds'),
                      'oom_kill': p.get('oom_kill'), 'ram_reset_applied': bool(ram_reset_applied)})
        return d
    env = _ph(env_phase); run = _ph(run_phase, agent=True)
    def _mx(a, b): return max(a, b) if (a is not None and b is not None) else (a if a is not None else b)
    prov_ram = _mx(env['peak_ram_mb'], run['peak_ram_mb'])                    # env-inclusive = what to provision
    prov_disk = _mx(env['peak_disk_mb'], run['peak_disk_mb'])
    ram_task = (round(run['peak_ram_mb'] - env['peak_ram_mb'], 1) if (run['peak_ram_mb'] is not None and env['peak_ram_mb'] is not None) else None)
    disk_task = (run['peak_disk_mb'] - env['peak_disk_mb'] if (run['peak_disk_mb'] is not None and env['peak_disk_mb'] is not None) else None)
    disk_cap_mib = disk_cap_gib * 1024 if disk_cap_gib else None
    run_peak_bytes = run_phase.get('peak_ram_bytes')
    ram_at_ceiling = bool(run_phase.get('oom_kill')) or bool(rc137) or (
        run_peak_bytes is not None and mem_max_bytes not in (None, 0) and run_peak_bytes >= 0.95 * mem_max_bytes)
    df_max = run_phase.get('df_max_used_mib')
    # disk_at_ceiling is decided by du (prov_disk) + ENOSPC only. The df arm is INFORMATIONAL: on Daytona `df -m --output=used /`
    # reports the writable overlay layer (~1 MiB) not the image occupancy (du ~303 MB), so a df-based arm can never trip and
    # df > du never holds. df_max is still recorded (+ disk_spike_risk kept as a df-based informational hint).
    disk_at_ceiling = bool(enospc_seen) or bool(prov_disk is not None and disk_cap_mib is not None and prov_disk >= 0.95 * disk_cap_mib)
    disk_spike_risk = bool(df_max is not None and run['peak_disk_mb'] is not None and df_max > run['peak_disk_mb'])  # informational only (df = writable layer on Daytona)
    derived = {'peak_ram_mb': prov_ram, 'peak_disk_mb': prov_disk, 'peak_disk_task_mb': disk_task}
    if ram_reset_applied:                                                     # reset isolated the run -> a real task figure
        derived['peak_ram_task_mb'] = ram_task
    else:                                                                     # Daytona: no reset -> the name itself marks it a lower bound (Fable minor)
        derived['peak_ram_task_mb_lower_bound'] = ram_task
    return {**identity, 'attempt': attempt, 'rung': rung, 'is_oom_rerun': bool(is_oom_rerun), 'bump_flag': bump_flag,
            'phases': {'env_baseline': env, 'agent_run': run}, 'derived': derived,
            'flags': {'ram_at_ceiling': ram_at_ceiling, 'disk_at_ceiling': disk_at_ceiling,
                      'ram_lower_bound': not bool(ram_reset_applied), 'disk_spike_risk': disk_spike_risk},
            'flags_inputs': {'mem_max_mib': _mib(mem_max_bytes), 'disk_cap_mib': disk_cap_mib, 'rc137': bool(rc137),
                             'enospc_seen': bool(enospc_seen)}}          # every arm of the ceiling/spike flags is now recomputable from resources.json alone


RAM_LADDER = [(1, 1, 1), (2, 2, 2), (4, 4, 4), (4, 8, 10)]                   # cpu/mem_gib/disk_gib rungs (user ruling); boot 1/1/1, bump on a ceiling


import math as _math


def resolve_task_rolls(task_id: str, parallel_rolls_arg, plan) -> int:
    """How many rollouts a task gets (F#7-4). --parallel-rolls N (int) -> N for EVERY task (today's meaning). The literal
    "plan" -> per_task[<id>].rolls if present, else run.parallel_rolls (the plan default), else 1 — so one plan file can
    send the seven at 3 and 000048 + the platform leftovers at 1 in a single invocation. Always >= 1."""
    if parallel_rolls_arg == 'plan':
        if not plan:
            return 1
        r = (plan.get('rolls') or {}).get(task_id)
        if r is None:
            r = plan.get('parallel_rolls_default')
        return max(1, int(r)) if r is not None else 1
    return max(1, int(parallel_rolls_arg))


CREATE_CAP_DEFAULT = 60                                                        # semaphore around sandbox create + upload (the phase that starved heartbeats at 277 wide)
LIVE_CAP_DEFAULT = 300                                                        # concurrent LIVE sandboxes (the outer gate). User rule: 300 (was 120); ops runs the 35's re-roll at an explicit --live-cap 300 --create-cap 60
AUTO_STOP_MIN_DEFAULT = '60'                                                  # TT_DAYTONA_AUTO_STOP_MIN the harness exports (setdefault) so a direct launch matches the plan's env
EVAL_TIMEOUT_DEFAULT_S = 900                                                  # TMAX_EVAL_TIMEOUT_SEC: grade-time cap for the WHOLE verifier run, aligned with the training side


def resolve_live_cap(workers_arg, live_cap_arg) -> int:
    """The outer gate (concurrent live sandboxes). --live-cap is the knob (default LIVE_CAP_DEFAULT); --workers is kept as
    the legacy name and WINS whenever a launcher passes it at all (its argparse default is None, so an explicit 8 is
    distinguishable and means 8 -- Fable residual), so existing launch scripts keep their meaning; when absent, --live-cap
    applies."""
    if workers_arg is not None:
        return max(1, int(workers_arg))
    return max(1, int(live_cap_arg if live_cap_arg is not None else LIVE_CAP_DEFAULT))


def grade_timeout_default(env=None) -> int:
    """TMAX_EVAL_TIMEOUT_SEC from the environment, else EVAL_TIMEOUT_DEFAULT_S (900). The training side grades a task's
    whole test under this cap; gold and validate must not grade under the 120 s per-command cap."""
    env = os.environ if env is None else env
    try:
        v = int(str(env.get('TMAX_EVAL_TIMEOUT_SEC', '')).strip() or EVAL_TIMEOUT_DEFAULT_S)
    except ValueError:
        v = EVAL_TIMEOUT_DEFAULT_S
    return max(1, v)


def apply_grade_timeout_env(flag_value, resolved: int, env=None) -> int:
    """ONE RULE for the grade-time cap (Fable residual on f67f9bb): an EXPLICIT --grade-timeout is the cap everywhere -- it
    OVERWRITES TMAX_EVAL_TIMEOUT_SEC for this process so the shared backend (daytona_backend, validate) grades under the
    same number as the gold gates; when the flag is absent, the env value (else 900) is the cap everywhere (setdefault).
    Returns the cap the process now runs under."""
    env = os.environ if env is None else env
    if flag_value is not None:
        env['TMAX_EVAL_TIMEOUT_SEC'] = str(int(resolved))
    else:
        env.setdefault('TMAX_EVAL_TIMEOUT_SEC', str(int(resolved)))
    return int(env['TMAX_EVAL_TIMEOUT_SEC'])


def grade_timeout_for(cfg: dict, task_id: str) -> int:
    """Per-task grade-time cap: the plan's per_task[<id>].eval_timeout_sec when present, else cfg['grade_timeout'], else
    the default. Never the 120 s agent-command cap."""
    per = (cfg.get('eval_timeouts') or {}).get(task_id)
    if per is not None:
        return max(1, int(per))
    return max(1, int(cfg.get('grade_timeout') or grade_timeout_default()))


def sandbox_issue_counts(sb) -> dict:
    """The clone sandbox's SandboxIssueTracker counts (heartbeat_retry = a heartbeat that had to retry, i.e. arrived late;
    sandbox_lost = the platform took the sandbox away), read BEFORE teardown. Robust to an older clone without the
    tracker (all zeros). Pure over the object."""
    tr = getattr(sb, 'issue_tracker', None)
    counts = getattr(tr, 'counts', None) if tr is not None else None
    if callable(counts):                                                      # the clone exposes counts as a @property (base.py:88) today; a method form is tolerated too (Fable) -- never call a dict
        try:
            counts = counts()
        except Exception:  # noqa: BLE001
            counts = None
    counts = dict(counts) if isinstance(counts, dict) else {}
    # kinds the clone records (daytona.py _record_issue literals at HEAD): heartbeat_retry (:699), create_retry, delete_retry,
    # session_create_retry, poll_transient, command_timeout, command_status_*, execute_*, *_disk_exhausted, *_failed, sandbox_lost (:618)
    return {'heartbeat_late': int(counts.get('heartbeat_retry', 0)),        # a heartbeat that had to retry (arrived late) -- a REAL kind, daytona.py:699
            'transport_retry': int(sum(v for k, v in counts.items() if str(k).endswith('_retry'))),   # every *_retry kind (heartbeat/create/delete/session_create): the broader transport-pressure signal
            'sandbox_lost': int(counts.get('sandbox_lost', 0)),
            'issue_events': int(sum(counts.values())) if counts else 0}


def preflight_images(ids, image_override, plan_env: dict | None) -> dict:
    """Resolve every selected id's image BEFORE any sandbox exists and refuse the whole run on any miss (Fable on e7e81f8 /
    7680c7e: a disagreement raised inside a roll vanished under gather(return_exceptions=True) with no row). Shared by the
    run path AND the rebuild path (the rebuild used to skip it). Returns {id: image}; raises SystemExit(2) listing the
    problems."""
    env = plan_env or {}
    problems, resolved = [], {}
    for t in ids:
        try:
            im = resolve_image(t, image_override, (env.get(t) or {}).get('identity'))
        except ValueError as e:
            problems.append(str(e)); continue
        if not im:
            problems.append(f'{t}: no image in the plan per_task.env_identity and none in any image map'); continue
        resolved[t] = im
    if problems:
        print(f'refusing to run: {len(problems)} of {len(ids)} selected task(s) have no usable image:')
        for pb in problems[:10]:
            print(f'  {pb}')
        raise SystemExit(2)
    return resolved


def rebuild_ledger_row(rec: dict, *, date: str, image: str | None) -> dict:
    """The provenance-ledger row for one replay REBUILD (Fable on 7680c7e: rebuild recs were only printed). status
    'rebuild', gold_source 'replay_rebuild', canonical_pressured True so no aggregator ever selects it as a rollout gold."""
    return {'task_id': rec.get('task_id'), 'gold_source': 'replay_rebuild', 'status': 'rebuild', 'canonical_pressured': True,
            'date': date, 'image': image, 'sandbox_id': rec.get('sandbox_id'), 'replay_rc': rec.get('replay_rc'),
            'commands_run': rec.get('commands_run'), 'commands_nonzero': rec.get('commands_nonzero'),
            'commands_timed_out': rec.get('commands_timed_out'), 'commands_undecoded': rec.get('commands_undecoded'),
            'changed_set_count': rec.get('changed_set_count'), 'deleted_count': rec.get('deleted_count'),
            'member_size_bytes': rec.get('member_size_bytes'), 'member_replaced': bool(rec.get('member_size_bytes') and rec.get('replay_rc') == 0),
            'error': rec.get('error'), 'heartbeat_late': rec.get('heartbeat_late'), 'sandbox_lost': rec.get('sandbox_lost')}


def caps_plus_gib(peak_mib, plus_gib: int, clamp_gib: int, fallback_gib=None) -> int:
    """A resource cap in GiB for the --parallel-rolls sizing mode: ceil(peak_mib/1024) + plus_gib, at least 1, clamped to
    the platform max clamp_gib. peak_mib None (the sampler never produced a figure) -> fallback_gib, the hint rung's own cap
    (clamped), NOT a bare G floor (Fable F4: a missing sample must not shrink the sandbox to G GiB); if no fallback is given
    it still floors at plus_gib for back-compat."""
    if peak_mib is None:
        base_gib = int(fallback_gib) if fallback_gib is not None else int(plus_gib)
        return max(1, min(base_gib, int(clamp_gib)))
    return max(1, min(_math.ceil(peak_mib / 1024) + int(plus_gib), int(clamp_gib)))


def claim_pack(packed_tasks: set, task_id: str) -> bool:
    """--parallel-rolls arbiter (Fable hazard 2): the FIRST rollout of a task to reach the pack decision under the shared
    write lock wins (returns True, records the task as packed); every later concurrent rollout of the same task gets False
    (it is retained + recorded passed_not_packed, never a second pack / second gate-2 row). Caller MUST hold the write lock,
    and only call this for a rollout that is otherwise a clean packable pass. Pure -> unit-tested."""
    if task_id in packed_tasks:
        return False
    packed_tasks.add(task_id)
    return True


def parked_after_n_row(task_id: str, *, model: str, reasoning_effort: str, max_steps: int, date: str, rollouter: str,
                       agent_loop: str, n: int, reasons: list, litellm_patch=None) -> dict:
    """--parallel-rolls: the ONE task-level provenance row written when ALL N rollouts of a task fail (user ruling: all N
    reasons in the provenance row). packed False, status parked_after_n_rolls, canonical_pressured True so no aggregator
    ever selects it as gold. The N per-rollout rows are still written separately (each failing attempt keeps its own)."""
    prov = provenance_record(task_id, model=model, reasoning_effort=reasoning_effort, max_steps=max_steps, cost_usd=None,
                             sandbox_id=None, task_hash_hex=task_hash(task_id), orig_verifier_reward=None, date=date,
                             rollouter=rollouter, agent_loop=agent_loop, packed=False, litellm_patch=litellm_patch)
    prov['status'] = 'parked_after_n_rolls'; prov['canonical_pressured'] = True
    prov['parallel_rolls'] = int(n); prov['roll_reasons'] = list(reasons)
    return prov


def task_passed(rrecs: list) -> bool:
    """--parallel-rolls task-level pass (pure -> tested): a rollout that PACKED, or a sibling recorded passed_not_packed
    because another rollout packed = the task passed. A gate-1 pass REFUSED at member verification (pack_failed, packed
    False) does NOT count as passed — else a task whose only passing roll was pack-refused would be neither packed nor
    parked, and nothing would record its task-level outcome (Fable)."""
    return any(r.get('packed') or r.get('status') == 'passed_not_packed' for r in rrecs)


def parked_at_ceiling_row(task_id: str, *, model: str, reasoning_effort: str, max_steps: int, date: str, rollouter: str,
                          agent_loop: str, rung: dict, env_ram_mib, env_disk_mib, skipped_rungs: list, litellm_patch=None) -> dict:
    """The baseline-gate park record (Fable minor on a405b29): when NO ladder rung fits the environment baseline, the
    parked_at_ceiling status must be PERSISTED to provenance, not only set on the returned rec (the per-attempt rows the
    gate wrote said baseline_over_cap). packed False, canonical_pressured True so no aggregator selects it."""
    prov = provenance_record(task_id, model=model, reasoning_effort=reasoning_effort, max_steps=max_steps, cost_usd=0.0,
                             sandbox_id=None, task_hash_hex=task_hash(task_id), orig_verifier_reward=None, date=date,
                             rollouter=rollouter, agent_loop=agent_loop, packed=False, litellm_patch=litellm_patch)
    prov['status'] = 'parked_at_ceiling'; prov['canonical_pressured'] = True
    prov['parked_reason'] = 'baseline_over_cap_no_fitting_rung'; prov['rung'] = rung
    prov['env_ram_mib'] = env_ram_mib; prov['env_disk_mib'] = env_disk_mib; prov['skipped_rungs'] = list(skipped_rungs)
    return prov


def replay_pointer_path(replay_dir, task_id: str) -> pathlib.Path:
    """The SINGLE latest-wins replay pointer in replay_dir, for EVERY mode: <task_id>.sh, written only by the rollout that
    wins the pack claim. No keyed (roll<n>) file may live in replay_dir — pretest_check.replay_inputs digests every *.sh
    there (a keyed file flips the collision gate STALE) and pin_gold_collision keys replays by p.stem (a roll<n> stem reads
    as a new task id); four consumers read replay_dir/<task_id>.sh by exact name (repair_tally :276/:448, daytona :341,
    rebuild :1620). Per-rollout copies are retained under retention instead."""
    return pathlib.Path(replay_dir) / f'{task_id}.sh'


def retention_dir_key(sandbox_id, attempt=1, suffix: str = '') -> str:
    """The retention subdirectory / manifest-key segment under <task_id>/: attempt_<n>[<r1|x<sub>>]_<sandbox_id> (lead
    ruling). The attempt-index PREFIX + r1/x<sub> marker keep tools/retention_audit.py's '*/attempt_*' glob (:74) and its
    manifest-key->directory walk (:76) intact; the SANDBOX-ID SUFFIX makes the key unique per create, so a later batch can
    never overwrite an earlier attempt of the same task (the roll index restarts at 1 each batch). A create-failure has no
    sandbox id -> attempt_<n>[<suffix>]_nosandbox (still attempt_*-matching and unique within a run)."""
    return f'attempt_{int(attempt)}{suffix or ""}_{sandbox_id if sandbox_id else "nosandbox"}'


def retain_replaced_file(current_bytes: bytes, task_id: str, retention_root, provenance_out, sha_field: str,
                         dest_basename: str) -> tuple[str | None, int | None]:
    """Before a latest-wins shared artifact (the replay pointer or a gold-tar member) is REPLACED by a newer attempt, copy
    the OUTGOING bytes into the outgoing attempt's retention dir so nothing packed is ever lost (lead ruling). The owning
    attempt is found by matching the outgoing bytes' sha256 to a provenance row's <sha_field>. Owner-directory resolution
    order (NEVER create a directory -- a lone one-file dir reads UNMANIFESTED to the audit): (1) the rebuilt post-rework key
    attempt_<n>[<suffix>]_<sandbox_id> if it exists; (2) else the PRE-rework layout attempt_<n>[<suffix>] if it exists
    (bucket 0 + the 94cdfb4 wave carry sandbox_id + sha on rows whose dir is the old attempt_<n> name -- reroll7's 000048
    re-pack hits this first); (3) else retention_root/<task_id>/_replaced/<sha>.<ext>. Returns the outgoing (sha256, size),
    or (None, None) when there is nothing to replace. fs read/write only."""
    if not current_bytes:
        return None, None
    sha = hashlib.sha256(current_bytes).hexdigest(); size = len(current_bytes)
    owner_key = owner_old_key = None
    pv = pathlib.Path(provenance_out) if provenance_out else None
    if pv and pv.exists():
        for l in pv.read_text().splitlines():          # last matching row wins = the currently-packed owner
            if not l.strip():
                continue
            try:
                r = json.loads(l)
            except Exception:  # noqa: BLE001
                continue
            if r.get('task_id') == task_id and r.get(sha_field) == sha and r.get('sandbox_id'):
                _at = r.get('attempt')
                if _at is None:                                                        # 566/589 pre-rework rows have no top-level attempt; the sizing rows carry it under resources.attempt (else default 1). Without this a ladder-path attempt_2/attempt_3 owner defaults to attempt_1 and step 2 mis-attributes the bytes (Fable).
                    _at = (r.get('resources') or {}).get('attempt')
                _at = int(_at) if _at is not None else 1                               # guard the int() against a null
                _sf = r.get('attempt_suffix') or ''
                owner_key = retention_dir_key(r['sandbox_id'], _at, _sf)               # post-rework: attempt_<n>[<suffix>]_<sandbox_id>
                owner_old_key = f'attempt_{_at}{_sf}'                                  # pre-rework: attempt_<n>[<suffix>] (no sandbox suffix)
    if retention_root:
        base = pathlib.Path(retention_root) / task_id
        new_dir = (base / owner_key) if owner_key else None
        old_dir = (base / owner_old_key) if owner_old_key else None
        if new_dir is not None and new_dir.exists():       # (1) the owner's post-rework dir
            dest = new_dir / dest_basename
        elif old_dir is not None and old_dir.exists():     # (2) the owner's PRE-rework dir (bucket 0 / 94cdfb4 wave) -- write beside its existing files, do not create a new sandbox-suffixed dir
            dest = old_dir / dest_basename
        else:                                              # (3) neither exists -> _replaced fallback (never create an attempt_* owner dir the audit would flag)
            ext = dest_basename.split('.', 1)[1] if '.' in dest_basename else 'bin'
            dest = base / '_replaced' / f'{sha}.{ext}'
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():                          # idempotent: never overwrite an already-retained copy
            dest.write_bytes(current_bytes)
    return sha, size


def baseline_over_cap(env_ram_mib, env_disk_mib, mem_gib, disk_gib) -> bool:
    """After the baseline phase (setup done, no agent): is the environment itself already at/over 95% of this rung's RAM or
    disk cap? If so the agent must not run here — no headroom (user ruling)."""
    ram_over = env_ram_mib is not None and mem_gib and env_ram_mib >= 0.95 * mem_gib * 1024
    disk_over = env_disk_mib is not None and disk_gib and env_disk_mib >= 0.95 * disk_gib * 1024
    return bool(ram_over or disk_over)


def first_fitting_rung(ladder, env_ram_mib, env_disk_mib, start=0):
    """Smallest rung index >= start whose RAM and disk caps BOTH exceed baseline/0.95 (baseline < 95% of cap), so the agent
    has headroom. None if no rung fits (-> parked_at_ceiling)."""
    for i in range(max(start, 0), len(ladder)):
        _cpu, mem_gib, disk_gib = ladder[i]
        ram_ok = env_ram_mib is None or env_ram_mib < 0.95 * mem_gib * 1024
        disk_ok = env_disk_mib is None or env_disk_mib < 0.95 * disk_gib * 1024
        if ram_ok and disk_ok:
            return i
    return None


def should_pack(gate1, no_reasoning: bool, pressured: bool) -> bool:
    """A member enters the gold tar / gate-2 queue only for a gate-1 pass that reasoned AND was NOT resource-pressured — a
    pressured attempt is a sizing signal to bump the rung, never a clean gold (Fable (c))."""
    return gate1 == 1 and not no_reasoning and not pressured


def verify_member(member_size, snapshot_rc, member_names, expected_names) -> tuple[bool, str | None]:
    """Gate a member before packing (pure -> testable): the snapshot tar must have exited 0, the archive must be non-empty
    and readable, and for a changed-set the SET OF MEMBER NAMES must equal the overlay path set. Compare by NAME, not a
    file-type count: tar stores a hardlink/symlink overlay entry as a link member (isfile() False), so a count of regular
    files under-counts and would falsely refuse a legitimate pass (probe: member_count 14 vs overlay 15 on one link). A
    real partial member (tar --ignore-failed-read dropped unreadable paths, exit 0) shows up as missing names, which go
    into the reason (capped) so it is diagnosable from the record. member_names == -1 => unreadable archive. expected_names
    None => full_dump (no per-path expectation)."""
    if snapshot_rc not in (0, None):
        return False, f'snapshot_tar_rc={snapshot_rc}'
    if not member_size:
        return False, f'zero_byte_member ({member_size or 0} bytes)'
    if member_names == -1:
        return False, 'unreadable_member (archive would not open)'
    if expected_names is not None and member_names is not None:
        got = set(member_names); exp = set(expected_names)
        if got != exp:
            missing = sorted(exp - got); extra = sorted(got - exp)
            return False, (f'member_names != overlay: missing {len(missing)} {missing[:10]}'
                           + (f' extra {len(extra)} {extra[:10]}' if extra else ''))
    return True, None


def pack_and_queue_if_clean(cfg: dict, task_id: str, sandbox_id, solution_gz: bytes, transcript: str,
                            gate1, no_reasoning: bool, pressured: bool) -> tuple[bool, str | None, str | None]:
    """The should_pack-gated pack actions, factored so the real pack path is unit-testable: an un-pressured gate-1 pass
    (that reasoned) with a NON-EMPTY member packs it, writes the transcript and appends the gate-2 queue; anything
    pressured/failed/zero-byte does none of these. A zero-byte or unreadable member is REFUSED (it reproduces nothing, and
    packed=True would feed gate 2 + gate-3 replays a green empty gold — task_000048). Returns (packed, transcript_path,
    pack_failed_reason); pack_failed_reason is set only when a would-be-clean pack was refused for an empty member."""
    if not should_pack(gate1, no_reasoning, pressured):
        return False, None, None
    n = len(solution_gz or b'')
    if n == 0:                                                              # empty/unreadable member -> refuse, do not queue gate 2
        return False, None, f'zero_byte_member ({n} bytes)'
    pack_solution_into_gold_tar(task_id, solution_gz, cfg['out_gold_tar'])
    tdir = pathlib.Path(cfg['transcript_dir']); tdir.mkdir(parents=True, exist_ok=True)   # gate-2 handoff: transcript CONTENT -> scratch (untracked, never inspected here)
    tpath = tdir / f'{task_id}.{sandbox_id}.json'
    tpath.write_text(transcript or '')
    append_gate2_queue(cfg['gate2_queue'], task_id, sandbox_id, str(tpath))
    return True, str(tpath), None


def attempt_status(pressured: bool, is_top_rung: bool, errored: bool = False) -> str:
    """ok = un-pressured clean; error = un-pressured but errored (timeout / refusal / exception / harness_error);
    at_ceiling_bumped = pressured with a higher rung to try; parked_at_ceiling = pressured at the top rung (no further
    attempt). Aggregators select only status==ok rows (canonical_pressured==False AND not an error)."""
    if pressured:
        return 'parked_at_ceiling' if is_top_rung else 'at_ceiling_bumped'
    return 'error' if errored else 'ok'


NON_ATTEMPT_STATUSES = ('baseline_probe', 'parked_after_n_rolls', 'parked_at_ceiling', 'baseline_over_cap')   # rows that are not a scoreable rollout attempt


def selectable_gold_row(row: dict) -> bool:
    """The EXPLICIT filter an aggregator uses to pick a clean-gold provenance row: status == 'ok' AND not
    canonical_pressured (F#7-3). Keying on status this way excludes the baseline-probe row by its own status
    ('baseline_probe'), not merely as a side effect of canonical_pressured=True (which is kept on the probe/park rows for
    back-compat with aggregators that still read it). park/error/passed_not_packed rows are excluded by status != 'ok'."""
    return row.get('status') == 'ok' and not row.get('canonical_pressured')


def token_totals(usage: list[dict]) -> dict:
    """Per-step (the list) + totals from the shim's per-call usage log."""
    return {'prompt_tokens': sum(int(u.get('prompt', 0)) for u in usage),
            'completion_tokens': sum(int(u.get('completion', 0)) for u in usage),
            'reasoning_tokens': sum(int(u.get('reasoning', 0)) for u in usage),
            'n_calls': len(usage), 'per_step': usage}


def write_task_retention(task_dir: str | pathlib.Path, *, transcript: str, commands: list[dict], replay_body,
                         events: list[dict], resources: dict, provenance: dict) -> pathlib.Path:
    """Write the 6 per-task retention files under retention_root/<task_id>/<sandbox_id>/ (gitignored). replay_body is the
    rendered replay-script TEXT for THIS rollout (not a src path): each rollout retains its OWN script here even though only
    the packing rollout writes the shared replay_dir/<task_id>.sh pointer."""
    d = pathlib.Path(task_dir); d.mkdir(parents=True, exist_ok=True)
    (d / 'transcript.json').write_text(transcript or '')
    with (d / 'commands.jsonl').open('w') as fh:
        for c in commands:
            fh.write(json.dumps(c) + '\n')
    (d / 'replay.sh').write_text(replay_body or '')
    with (d / 'sandbox_events.jsonl').open('w') as fh:
        for e in events:
            fh.write(json.dumps(e) + '\n')
    (d / 'resources.json').write_text(json.dumps(resources, indent=1))
    (d / 'provenance.json').write_text(json.dumps(provenance, indent=1))
    return d


def append_retention_manifest(manifest_path: str | pathlib.Path, task_id: str, task_dir: str | pathlib.Path) -> None:
    """Append sha256 + bytes for each retention file to the COMMITTED manifest (task_id, file, sha256, bytes)."""
    d = pathlib.Path(task_dir); m = pathlib.Path(manifest_path); new = not m.exists()
    m.parent.mkdir(parents=True, exist_ok=True)
    with m.open('a') as fh:
        if new:
            fh.write('task_id\tfile\tsha256\tbytes\n')
        for f in sorted(d.iterdir()):
            if f.is_file():
                b = f.read_bytes()
                fh.write(f'{task_id}\t{f.name}\t{hashlib.sha256(b).hexdigest()}\t{len(b)}\n')


async def _grade_in_place(sb, R, DB, tests_dir: pathlib.Path, timeout: int):
    """Run a verifier against the sandbox's current state using the executor's exact torchtitan grade sequence
    (tests -> /tests, nonce reset, verifier as root, read back). Returns an int reward or None (void)."""
    import uuid as _uuid
    await sb.exec(f'mkdir -p /logs/verifier {R.TT_TESTS_DIR}', user='root', timeout=60)
    for src, dst in DB.tests_upload_plan(tests_dir, R.TT_TESTS_DIR):
        await sb.write_file(dst, src, user='root')
    nonce = _uuid.uuid4().hex
    await sb.exec(R.nonce_pre_grade_script(R.TT_REWARD_PATH, nonce), user='root', timeout=60)
    sentinel = await sb.read_file(R.TT_REWARD_PATH)
    await sb.exec(R.verifier_exec_script(R.TT_TESTS_DIR), user='root', timeout=timeout)
    raw = await sb.read_file(R.TT_REWARD_PATH)
    reward, note = R.nonce_verdict(sentinel, raw if raw != '' else None, nonce)
    if note == 'verifier_wrote_reward':
        return gate_reward(R.parse_reward_clamped(raw))
    return None                                                              # verifier did not write a fresh reward -> void


async def run_one_live(task_id: str, cfg: dict, log=print) -> dict:
    """Solve ONE parked task with a GPT rollout in its Daytona sandbox, snapshot the changed-set end-state into the gold
    tar, grade gate 1/gate 3 in place, and append provenance. Runs under a Python with litellm + the daytona SDK and both
    keys in env. NEVER logs secrets, the instruction, the transcript, or sandbox file contents."""
    import base64
    sys.path.insert(0, str(RA / 'tools'))
    import repro_exec as R, daytona_backend as DB
    rec = {'task_id': task_id, 'image': None, 'steps': None, 'cost_usd': None, 'gate1': None, 'gate3': None,
           'member_size_bytes': None, 'changed_set_count': None, 'deleted_count': None, 'sandbox_id': None,
           'transcript_path': None, 'replay_path': None, 'replay_sha256': None, 'error': None}
    try:                                                                      # rec FIRST: the first cut of this
        image = resolve_image(task_id, cfg.get('image'),                      # guard wrote rec['error'] one line
                              ((cfg.get('image_env') or {}).get(task_id) or {}).get('identity'))   # before rec
    except ValueError as _ie:                                                 # existed, so the handler for a
        rec['error'] = str(_ie)                                               # swallowed error raised
        rec['invalid_reason'] = 'image_plan_map_disagreement'; rec['status'] = 'error'   # UnboundLocalError of its own
        return rec
    rec['image'] = image
    _transcript = ''; _commands: list = []; _eff: dict = {}
    _events: list = []; _resources: dict = {}                                 # sandbox_events.jsonl + resources.json (retention/sizing)
    _sizing = bool(cfg.get('sizing'))                                         # resource sampling on only for the sizing/282 run (keeps the normal path fast + tests unchanged)
    _cgv = 'v2'; _du_interval = 2                                             # cgroup version + du poll interval (both recomputed live under _sizing)
    _env_phase: dict = {}; _identity: dict = {}; _reset_ok = False            # _reset_ok hoisted so the except path can build a partial resources record even if an exception precedes agent-start

    def _ev(name, **kw):
        _events.append({'ts': round(time.time(), 3), 'event': name, **kw})
    if not image:                                                             # gate1_pass36: 108 rolls died here and
        rec['error'] = ('no image for task: not in the plan per_task.env_identity and not in any image map '   # the
                        f'({IMAGE_MAP}, {IMAGE_MAP_ALL}, {IMAGE_MAP_15K})')                                    # parked
        rec['invalid_reason'] = 'no_image_resolved'                           # row said only "unknown", 108 times,
        rec['status'] = 'error'                                               # because this early return set neither
        return rec
    DaytonaSandbox = DB.load_daytona_sandbox_class()
    sb = DaytonaSandbox(image, cpu=cfg.get('cpu', 1), memory=cfg.get('mem_gb', 2), disk_gb=cfg.get('disk_gb', 8))
    dest = '/tmp/.gold_snapshot.tar.gz'
    _torn = {'v': False}

    async def _teardown(outcome: str = 'deleted'):                            # idempotent: tears the sandbox down AND records the sweep row; runs on the normal path (before pack), on any exception, and on CancelledError/SIGTERM via the finally backstop, so a killed driver never leaks a sandbox unswept
        if _torn['v']:
            return
        _torn['v'] = True
        oc = outcome
        rec.update(sandbox_issue_counts(sb))                                  # heartbeat_late (heartbeat retries) + sandbox_lost from the clone's issue tracker, read BEFORE the sandbox goes away -> per-run counters a canary can prove the split with
        try:
            await sb.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            oc = 'error'
        _ev('teardown', outcome=oc, sandbox_id=rec.get('sandbox_id'), heartbeat_late=rec.get('heartbeat_late'), sandbox_lost=rec.get('sandbox_lost'))
        if cfg.get('sweep_log') and rec.get('sandbox_id'):
            _lk = cfg.get('_write_lock')
            async with (_lk if _lk is not None else contextlib.nullcontext()):
                append_sweep_log(cfg['sweep_log'], rec.get('sandbox_id'), cfg.get('sweep_source', 'reroll'), task_id, oc)
    try:
        _csem = cfg.get('_create_sem')                                       # the concurrency SPLIT: only create + upload (prepare_runtime) run under the create semaphore (default 60); the agent/grade/snapshot phases run under the live-cap gate only
        async with (_csem if _csem is not None else contextlib.nullcontext()):
            _t_create = time.time()
            await sb.__aenter__()
            rec['sandbox_id'] = getattr(sb, 'sandbox_id', None)
            if rec['sandbox_id'] and cfg.get('sandbox_log'):                 # record the id AT CREATE (durable) so a killed run can be swept by recorded id
                _lk = cfg.get('_write_lock')
                async with (_lk if _lk is not None else contextlib.nullcontext()):
                    pathlib.Path(cfg['sandbox_log']).parent.mkdir(parents=True, exist_ok=True)
                    with open(cfg['sandbox_log'], 'a') as fh:
                        fh.write(json.dumps({'task_id': task_id, 'backend': 'daytona', 'container_id': rec['sandbox_id']}) + '\n')
            _ev('sandbox_create', sandbox_id=rec['sandbox_id'])
            await sb.exec(R.prepare_runtime_script(), user='root', timeout=120)   # no entrypoint started (TMAX rule)
            rec['create_upload_s'] = round(time.time() - _t_create, 2)      # how long this rollout held a create slot (canary evidence for the split)
            _ev('prepare_runtime_done', create_upload_s=rec['create_upload_s'])
        if _sizing:                                                          # BASELINE phase: image booted + setup done, no agent (SANDBOX_SIZING.md); same sandbox as the run
            _cgv = await detect_cgroup(sb)
            _du_cost = await measure_du_cost_s(sb); _du_interval = max(2, int(round(2 * _du_cost)))   # du on a big image can exceed 1s
            _reset_supported = await reset_mem_peak(sb, _cgv)                 # probe once (EACCES on Daytona -> False); result recorded, run uses the fallback
            _stat = await sample_mem_stat(sb, _cgv)
            _env_phase = {'peak_ram_bytes': await sample_mem_peak_bytes(sb, _cgv), 'anon_bytes': _stat.get('anon'),
                          'shmem_bytes': _stat.get('shmem'), 'disk_mib': await sample_du_mib(sb)}
            _digest = (cfg.get('image_digests') or {}).get(task_id) or cfg.get('image_digest')
            if cfg.get('image_digests') is not None and not _digest:          # B5: fail loudly rather than write a null-digest record (per-image keying would degenerate)
                raise RuntimeError(f'{task_id}: no image_digest in the plan per_task map — refusing to record a null digest')
            _identity = await resource_identity(sb, cfg, image, _cgv, _reset_supported, _du_interval)
            _identity['task_id'] = task_id; _identity['image_id'] = _digest   # strictly the plan digest (fail-loud above if missing); no tag fallback
            _identity['env'] = (cfg.get('image_env') or {}).get(task_id); _identity['tmpfs_mounts'] = await sample_tmpfs_mounts(sb)
            _ev('baseline_sample', env_ram_peak_mib=_mib(_env_phase['peak_ram_bytes']), env_disk_mib=_env_phase['disk_mib'], cgroup=_cgv, du_interval_s=_du_interval, reset_supported=_reset_supported)
            _env_ram_mib = _mib(_env_phase.get('peak_ram_bytes')); _env_disk_mib = _env_phase.get('disk_mib')
            if cfg.get('_baseline_probe'):                                     # --parallel-rolls: this create measured the baseline; the user wants that environment RECORDED, so it gets its own minimal attempt (provenance + retention + manifest, keyed like any attempt), then we return so the caller recreates at ceil(baseline)+G (F1)
                rec['status'] = 'baseline_probe'; rec['env_ram_mib'] = _env_ram_mib; rec['env_disk_mib'] = _env_disk_mib
                _ev('baseline_probe', env_ram_mib=_env_ram_mib, env_disk_mib=_env_disk_mib)
                _pres = compute_resources(identity=_identity, env_phase=_env_phase, run_phase={}, mem_max_bytes=None,
                                          disk_cap_gib=cfg.get('disk_gb'), ram_reset_applied=False, is_oom_rerun=False,
                                          rc137=False, enospc_seen=False, attempt=int(cfg.get('_attempt', 1)),
                                          rung={'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')}, bump_flag=None)
                _pres['baseline_probe'] = True; _pres['tokens'] = token_totals([])
                await _teardown('deleted')
                _rl, _al = _labels(cfg.get('agent', 'self_contained'))
                _pprov = provenance_record(task_id, model=cfg['model'], reasoning_effort=cfg['reasoning_effort'], max_steps=cfg['max_steps'],
                                           cost_usd=0.0, sandbox_id=rec.get('sandbox_id'), task_hash_hex=task_hash(task_id),
                                           orig_verifier_reward=None, date=cfg['date'], rollouter=_rl, agent_loop=_al, packed=False, litellm_patch=cfg.get('litellm_patch'))
                _pprov['status'] = 'baseline_probe'; _pprov['canonical_pressured'] = True
                _pprov['rung'] = {'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')}
                _pprov['env_ram_mib'] = _env_ram_mib; _pprov['env_disk_mib'] = _env_disk_mib
                _pprov['attempt'] = int(cfg.get('_attempt', 1)); _pprov['roll_index'] = cfg.get('_roll_index'); _pprov['attempt_suffix'] = cfg.get('_attempt_suffix') or ''
                _pprov['resources'] = _pres
                _pkey = retention_dir_key(rec.get('sandbox_id'), cfg.get('_attempt', 1), cfg.get('_attempt_suffix', ''))
                _lk = cfg.get('_write_lock')
                async with (_lk if _lk is not None else contextlib.nullcontext()):
                    if cfg.get('provenance_out'):
                        with open(cfg['provenance_out'], 'a') as fh:
                            fh.write(json.dumps(_pprov) + '\n')
                    if cfg.get('retention_root'):
                        _ptd = pathlib.Path(cfg['retention_root']) / task_id / _pkey
                        write_task_retention(_ptd, transcript='', commands=[], replay_body='', events=_events, resources=_pres, provenance=_pprov)
                        if cfg.get('retention_manifest'):
                            append_retention_manifest(cfg['retention_manifest'], f'{task_id}/{_pkey}', _ptd)
                        rec['retention_dir'] = str(_ptd)
                return rec
            if baseline_over_cap(_env_ram_mib, _env_disk_mib, cfg.get('mem_gb'), cfg.get('disk_gb')):   # BASELINE GATE (kept as a SAFETY under --parallel-rolls): env already >=95% of this rung's cap -> do NOT run the agent here (user ruling), no tokens
                rec['baseline_over_cap'] = True; rec['status'] = 'baseline_over_cap'
                rec['env_ram_mib'] = _env_ram_mib; rec['env_disk_mib'] = _env_disk_mib
                _resources = compute_resources(identity=_identity, env_phase=_env_phase, run_phase={}, mem_max_bytes=None,
                                               disk_cap_gib=cfg.get('disk_gb'), ram_reset_applied=False, is_oom_rerun=bool(cfg.get('_oom_rerun')),
                                               rc137=False, enospc_seen=False, attempt=int(cfg.get('_attempt', 1)),
                                               rung={'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')}, bump_flag=cfg.get('_bump_flag'))
                _resources['baseline_over_cap'] = True; _resources['skipped_rungs'] = cfg.get('_skipped_rungs') or []
                _resources['tokens'] = token_totals([])
                _ev('baseline_over_cap', env_ram_mib=_env_ram_mib, env_disk_mib=_env_disk_mib, rung={'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')})
                await _teardown('deleted')                                   # free the sandbox; no agent, no tokens
                _prov = provenance_record(task_id, model=cfg['model'], reasoning_effort=cfg['reasoning_effort'], max_steps=cfg['max_steps'],
                                          cost_usd=0.0, sandbox_id=rec.get('sandbox_id'), task_hash_hex=task_hash(task_id),
                                          orig_verifier_reward=None, date=cfg['date'], rollouter=_labels(cfg.get('agent', 'self_contained'))[0],
                                          agent_loop=_labels(cfg.get('agent', 'self_contained'))[1], packed=False, litellm_patch=cfg.get('litellm_patch'))
                _prov['status'] = 'baseline_over_cap'; _prov['canonical_pressured'] = True; _prov['resources'] = _resources
                _prov['skipped_rungs'] = _resources['skipped_rungs']; _prov['rung'] = {'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')}
                _prov['attempt'] = int(cfg.get('_attempt', 1)); _prov['roll_index'] = cfg.get('_roll_index'); _prov['attempt_suffix'] = cfg.get('_attempt_suffix') or ''
                _lk = cfg.get('_write_lock')
                async with (_lk if _lk is not None else contextlib.nullcontext()):
                    if cfg.get('provenance_out'):
                        with open(cfg['provenance_out'], 'a') as fh:
                            fh.write(json.dumps(_prov) + '\n')
                    if cfg.get('retention_root'):
                        _rkey = retention_dir_key(rec.get('sandbox_id'), cfg.get('_attempt', 1), cfg.get('_attempt_suffix', ''))
                        _td = pathlib.Path(cfg['retention_root']) / task_id / _rkey
                        write_task_retention(_td, transcript='', commands=[], replay_body='', events=_events, resources=_resources, provenance=_prov)
                        if cfg.get('retention_manifest'):
                            append_retention_manifest(cfg['retention_manifest'], f'{task_id}/{_rkey}', _td)
                        rec['retention_dir'] = str(_td)
                return rec
        # baseline (clean) manifest, cached per image id
        cache = clean_manifest_cache_path(image, cfg['clean_manifest_cache_dir']); cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            clean = parse_sha_manifest(cache.read_text())
        else:
            await sb.exec(manifest_cmd('/tmp/.clean_manifest.txt'), user='root', timeout=300)
            clean_txt = await sb.read_file('/tmp/.clean_manifest.txt'); cache.write_text(clean_txt); clean = parse_sha_manifest(clean_txt)
        # ROOT-CAUSE FIX: the instruction is delivered from the LOCAL task dir (scratch/tasks/<id>/instruction.md), the way
        # the training data pipeline sources it — NOT by cat-ing the sandbox (the image has no instruction.md there, which
        # left the prompt BLANK for all prior rolls). Read opaquely (never inspected/logged); refuse an empty instruction.
        instr = instruction_for(task_id)
        if not instr.strip():
            rec['error'] = f'instruction missing/empty for {task_id} — refusing to roll a blank task'
            return rec                                                        # finally tears down + logs the sweep
        _loop = _agent_loop_vanillux if cfg.get('agent') == 'vanillux2' else _agent_loop   # Option C swap behind the one seam
        _ep = cfg.get('episode_timeout')                                     # lead's per-episode wall cap; None => unlimited
        _reset_ok = False
        if _sizing:                                                          # RUN phase begins: attempt reset (EACCES->fallback), start the 1s + du pollers
            _reset_ok = await reset_mem_peak(sb, _cgv); await poller_start(sb, _cgv, _du_interval)
            _ev('agent_start', peak_reset_ok=_reset_ok)
        try:
            rec['steps'], rec['cost_usd'], _transcript, _commands, _eff = await asyncio.wait_for(_loop(sb, instr, cfg, log), timeout=_ep)
        except (asyncio.TimeoutError, TimeoutError):                         # the episode ran past the cap -> cancel + record timed_out (no manual kill)
            rec['error'] = f'episode_timeout after {_ep}s'; raise
        if _sizing:                                                          # RUN peaks sampled NOW, before the end-state manifest/snapshot churn memory + disk
            _poll = await poller_stop_read(sb)
            _stat_r = await sample_mem_stat(sb, _cgv)
            _run_phase = {'peak_ram_bytes': await sample_mem_peak_bytes(sb, _cgv),   # cumulative (no reset on Daytona) = the provisioning number
                          'anon_bytes': _stat_r.get('anon'), 'shmem_bytes': _stat_r.get('shmem'),
                          'disk_mib': _poll.get('du_max_mib'), 'df_max_used_mib': _poll.get('df_max_used_mib'),
                          'sampled_agent_ram_bytes': _poll.get('sampled_agent_ram_bytes'), 'oom_kill': await read_oom_kill(sb, _cgv),
                          'avg_cpu_cores': _poll.get('avg_cpu_cores'), 'cpu_core_seconds': _poll.get('cpu_core_seconds')}
            _mmax = await sample_mem_max_bytes(sb, _cgv)
            _rc137 = any(c.get('exit_code') == 137 for c in _eff.get('commands', [])) or 'rc=137' in (rec.get('error') or '')
            _enospc = any('no space left on device' in (c.get('output') or '').lower() for c in _eff.get('commands', []))
            _resources = compute_resources(identity=_identity, env_phase=_env_phase, run_phase=_run_phase, mem_max_bytes=_mmax,
                                           disk_cap_gib=cfg.get('disk_gb'), ram_reset_applied=_reset_ok, is_oom_rerun=bool(cfg.get('_oom_rerun')),
                                           rc137=_rc137, enospc_seen=_enospc, attempt=int(cfg.get('_attempt', 1)),
                                           rung={'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')},
                                           bump_flag=cfg.get('_bump_flag'))
            _resources['tokens'] = token_totals(_eff.get('usage', []))       # per-step + total prompt/completion/reasoning (62-cohort had none)
            _resources['rollout_cost_usd'] = rec.get('cost_usd')
            rec['ram_at_ceiling'] = _resources['flags']['ram_at_ceiling']; rec['disk_at_ceiling'] = _resources['flags']['disk_at_ceiling']
            rec['peak_ram_mb'] = _resources['derived'].get('peak_ram_mb'); rec['peak_disk_mb'] = _resources['derived'].get('peak_disk_mb')   # env-inclusive provisioning peaks -> --parallel-rolls sizes the peak+G recreate from these
            _ev('agent_end', derived=_resources.get('derived'), flags=_resources.get('flags'))
        # end-state manifest -> changed set
        await sb.exec(manifest_cmd('/tmp/.end_manifest.txt'), user='root', timeout=300)
        dirty = parse_sha_manifest(await sb.read_file('/tmp/.end_manifest.txt'))
        changed = strip_harness_paths(diff_changed_set(clean, dirty)); paths = overlay_paths(changed)   # drop the harness's own sandbox scratch before overlay/snapshot (ops finding)
        rec['changed_set_count'] = len(paths); rec['deleted_count'] = len(changed['deleted'])
        # snapshot the gold member (changed set, or --full-dump) and pull it as bytes via the non-truncating file pull.
        # Use a -T paths FILE for the changed set (an inline arg list blew ARG_MAX on a 20305-path task -> tar failed ->
        # 0-byte member packed as gold; task_000048). full_dump keeps the roots form.
        _expected = None
        if cfg.get('full_dump'):
            _snap_rc, _, _ = await sb.exec(snapshot_tar_cmd(dest, paths=None), user='root', timeout=300)
        else:
            await sb.write_file('/tmp/.gold_paths.txt', ('\n'.join(paths) + '\n').encode(), user='root')
            _snap_rc, _, _ = await sb.exec(snapshot_tar_cmd(dest, paths_file='/tmp/.gold_paths.txt'), user='root', timeout=600)
            _expected = len(paths)                                          # a changed-set member must carry exactly the overlay files
        solution_gz = await _pull_tar(sb, dest)
        rec['member_size_bytes'] = len(solution_gz); rec['snapshot_rc'] = _snap_rc; rec['expected_member_count'] = _expected
        rec['member_count'] = None; _member_names = None                    # open the pulled archive and take member NAMES -> a name-set diff vs the overlay catches a partial member (--ignore-failed-read dropped paths, rc 0) without false-refusing hardlink/symlink members
        if solution_gz and _expected is not None:
            try:
                with tarfile.open(fileobj=io.BytesIO(solution_gz), mode='r:gz') as _tf:
                    _member_names = [n[2:] if n.startswith('./') else n for n in _tf.getnames() if n not in ('', '.')]
                    rec['member_count'] = len(_member_names)
            except Exception:  # noqa: BLE001
                _member_names = -1; rec['member_count'] = -1               # unreadable archive
        if _expected is not None and isinstance(_member_names, list):      # a member-name gap: re-check the missing paths WHILE the sandbox is up so pack can name vanished-race vs genuine-partial (finding 2)
            _miss = sorted(set(paths) - set(_member_names))
            if _miss:
                rec['member_missing'] = _miss; rec['member_missing_absent'] = await sandbox_absent_paths(sb, _miss)
        # gates AFTER the snapshot so grading never pollutes the gold
        _gt = grade_timeout_for(cfg, task_id); rec['grade_timeout_s'] = _gt    # grade-time cap for the WHOLE verifier run (TMAX_EVAL_TIMEOUT_SEC / --grade-timeout / per-task plan override; default 900), NOT the 120 s agent-command cap
        ot = RA / 'scratch' / 'tasks' / task_id / 'tests'
        if (ot / 'test.sh').exists():
            rec['gate1'] = await _grade_in_place(sb, R, DB, ot, _gt)          # original verifier — informational
        rt = RA / 'scratch' / 'repairs' / task_id / 'tests'
        if (rt / 'test.sh').exists():
            rec['gate3'] = await _grade_in_place(sb, R, DB, rt, _gt)          # repaired verifier
    except Exception as e:  # noqa: BLE001
        rec['error'] = rec.get('error') or f'{type(e).__name__}: {e}'[:300]   # keep a pre-set reason (episode_timeout) instead of the bare exception str
        if _sizing and not _resources:                                        # B2: build a PARTIAL resources record from the samples taken so far, before the sandbox is torn down
            try:
                _poll = await poller_stop_read(sb)
                _rp = {'peak_ram_bytes': await sample_mem_peak_bytes(sb, _cgv), 'disk_mib': _poll.get('du_max_mib'),
                       'df_max_used_mib': _poll.get('df_max_used_mib'), 'sampled_agent_ram_bytes': _poll.get('sampled_agent_ram_bytes'),
                       'oom_kill': await read_oom_kill(sb, _cgv), 'avg_cpu_cores': _poll.get('avg_cpu_cores'), 'cpu_core_seconds': _poll.get('cpu_core_seconds')}
                _st = await sample_mem_stat(sb, _cgv); _rp['anon_bytes'] = _st.get('anon'); _rp['shmem_bytes'] = _st.get('shmem')
                _resources = compute_resources(identity=_identity, env_phase=_env_phase, run_phase=_rp, mem_max_bytes=await sample_mem_max_bytes(sb, _cgv),
                                               disk_cap_gib=cfg.get('disk_gb'), ram_reset_applied=_reset_ok, is_oom_rerun=bool(cfg.get('_oom_rerun')),
                                               rc137=_is_oom(rec['error']), enospc_seen='no space left on device' in rec['error'].lower(),
                                               attempt=int(cfg.get('_attempt', 1)), rung={'cpu': cfg.get('cpu'), 'mem_gib': cfg.get('mem_gb'), 'disk_gib': cfg.get('disk_gb')}, bump_flag=cfg.get('_bump_flag'))
                _resources['partial'] = True; _resources['tokens'] = token_totals(_eff.get('usage', []))
            except Exception:  # noqa: BLE001
                pass
        await _teardown('deleted')                                            # free the sandbox now; the finally below is an idempotent backstop
        # record the failed roll in provenance so the final summary separates provider-refusal / timeout from gate-1 failures
        _el = rec['error'].lower()
        _reason = ('provider_refusal_cyber_policy' if 'cyber_policy' in _el
                   else 'timed_out' if 'episode_timeout' in _el or 'timeouterror' in _el
                   else 'exception')
        rec['invalid_reason'] = _reason
        if _is_oom(rec['error']):                                            # a hard OOM crash (rc137/oom text) never reached the sizing sample -> still flag it so the ladder bumps a rung
            rec['ram_at_ceiling'] = True
        if _sizing and rec.get('sandbox_id') is None and not _is_quota(rec['error']):   # a non-quota CREATE failure
            rec['create_failed'] = True
            _img_mib, _known = image_size_mib_and_known(cfg, task_id); rec['image_size_known'] = _known   # uncompressed if stamped, else compressed
            if create_failure_is_disk(rec['error'], _img_mib, cfg.get('disk_gb')):   # disk/space error or KNOWN size>cap -> disk pressure now -> ladder escalates
                rec['disk_at_ceiling'] = True
            else:                                                           # else transient: _attempt retries once same rung, then decides escalate-once-if-unknown vs harness error
                rec['create_transient'] = True
        _rl, _al = _labels(cfg.get('agent', 'self_contained'))
        prov = provenance_record(task_id, model=cfg['model'], reasoning_effort=cfg['reasoning_effort'], max_steps=cfg['max_steps'],
                                 cost_usd=rec.get('cost_usd'), sandbox_id=rec.get('sandbox_id'), task_hash_hex=task_hash(task_id),
                                 orig_verifier_reward=None, date=cfg['date'], steps=rec.get('steps'), rollouter=_rl, agent_loop=_al, packed=False,
                                 litellm_patch=cfg.get('litellm_patch'))
        prov['invalid_reason'] = _reason
        prov['retries'] = 5 if _reason == 'provider_refusal_cyber_policy' else 0   # cyber_policy exhausts Vanillux2Agent's 5 retries before raising
        _pressured = bool(rec.get('ram_at_ceiling') or rec.get('disk_at_ceiling'))   # e.g. OOM crash / create-failure=disk_at_ceiling on this exit path
        rec['status'] = attempt_status(_pressured, bool(cfg.get('_is_top_rung')), errored=True)   # un-pressured errored (timeout/refusal/exception/harness_error) -> 'error', not 'ok'
        prov['status'] = rec['status']; prov['canonical_pressured'] = _pressured
        prov['attempt'] = int(cfg.get('_attempt', 1)); prov['roll_index'] = cfg.get('_roll_index'); prov['attempt_suffix'] = cfg.get('_attempt_suffix') or ''   # attempt/roll index inside the record (dir is keyed by sandbox id)
        prov['heartbeat_late'] = rec.get('heartbeat_late'); prov['transport_retry'] = rec.get('transport_retry'); prov['sandbox_lost'] = rec.get('sandbox_lost'); prov['create_upload_s'] = rec.get('create_upload_s')   # the errored path carries the same canary counters (a platform-deleted sandbox lands here)
        if _sizing and _resources:
            prov['resources'] = _resources; prov['tokens'] = _resources.get('tokens')
        if cfg.get('provenance_out'):
            _lk = cfg.get('_write_lock')
            async with (_lk if _lk is not None else contextlib.nullcontext()):
                with open(cfg['provenance_out'], 'a') as fh:
                    fh.write(json.dumps(prov) + '\n')
        if cfg.get('retention_root'):                                        # B2: retention on the error/timeout/refusal path too, with whatever exists so far
            _rkey = retention_dir_key(rec.get('sandbox_id'), cfg.get('_attempt', 1), cfg.get('_attempt_suffix', ''))   # sandbox-id keyed (a later batch can't overwrite)
            _tdir = pathlib.Path(cfg['retention_root']) / task_id / _rkey
            try:
                write_task_retention(_tdir, transcript=_transcript, commands=_eff.get('commands', []),
                                     replay_body=(render_replay_script(_commands)[0] if _commands else ''), events=_events, resources=_resources, provenance=prov)
                if cfg.get('retention_manifest'):
                    _lk = cfg.get('_write_lock')
                    async with (_lk if _lk is not None else contextlib.nullcontext()):
                        append_retention_manifest(cfg['retention_manifest'], f'{task_id}/{_rkey}', _tdir)
                rec['retention_dir'] = str(_tdir)
            except Exception:  # noqa: BLE001
                pass
        return rec
    finally:
        await _teardown('deleted')                                            # NORMAL path: frees the sandbox before packing; also the backstop that sweeps on CancelledError / SIGTERM
    # pack ONLY when gate 1 == 1 (valid gold); a failed attempt keeps its provenance sidecar but never enters the tar
    rec['packed'] = False
    _rollouter, _agent_loop_label = _labels(cfg.get('agent', 'self_contained'))
    if rec['member_size_bytes'] is not None:                                  # a rollout+snapshot completed (pass or gate-fail)
        # trajectory: render the form-B replay ONCE. Each rollout RETAINS its own copy (retention/<sandbox_id>/replay.sh);
        # replay_dir/<task_id>.sh is the SINGLE latest-wins pointer, written only by the rollout that wins the pack claim.
        _rbody, rec['replay_sha256'] = render_replay_script(_commands)
        _rkey = retention_dir_key(rec.get('sandbox_id'), cfg.get('_attempt', 1), cfg.get('_attempt_suffix', ''))   # retention dir/key = the sandbox id (a later batch's roll-1 can't overwrite an earlier attempt)
        _pointer = replay_pointer_path(cfg['replay_dir'], task_id)
        rec['replay_path'] = (str(pathlib.Path(cfg['retention_root']) / task_id / _rkey / 'replay.sh')
                              if cfg.get('retention_root') else str(_pointer))   # provenance points at the retained copy (consumers build the pointer path by name); the pointer when there is no retention
        _lock = cfg.get('_write_lock')                                        # serialize the SHARED writes (gold tar, gate-2 queue, replay pointer, provenance jsonl) across concurrent workers
        _cm = _lock if _lock is not None else contextlib.nullcontext()
        rec['reasoning_tokens'] = _eff.get('reasoning_tokens')
        _no_reasoning = cfg.get('agent') == 'vanillux2' and (rec['reasoning_tokens'] == 0)   # completed but the model didn't reason -> invalid (do not pack), re-rolled once
        _pressured = bool(rec.get('ram_at_ceiling') or rec.get('disk_at_ceiling'))           # resource-pressured attempt: a sizing signal, not clean gold
        rec['status'] = attempt_status(_pressured, bool(cfg.get('_is_top_rung')))            # ok / at_ceiling_bumped / parked_at_ceiling
        _member_ok, _member_reason = verify_member(rec['member_size_bytes'], rec.get('snapshot_rc'), _member_names, paths if _expected is not None else None)
        if not _member_ok and rec.get('member_missing'):                       # a member-name gap -> replace the generic reason with the vanished-race vs genuine-partial class (finding 2)
            rec['pack_failed_class'], _member_reason = classify_member_gap(rec['member_missing'], rec.get('member_missing_absent') or [])
        _arbiter = cfg.get('_packed_tasks')                                    # --parallel-rolls: per-task first-passing-wins claim, held under the write lock (None on the default one-roll path)
        _clean_pack = bool(should_pack(rec['gate1'], _no_reasoning, _pressured) and _member_ok)   # a verified, un-pressured, reasoning gate-1 pass = a would-be pack
        _member_sha = _replaced_member_sha = _replaced_member_size = None
        async with _cm:
            if _arbiter is not None and _clean_pack and not claim_pack(_arbiter, task_id):   # another rollout of this task packed first -> retain + record, never a 2nd pack / 2nd gate-2 row (hazard 2)
                rec['packed'] = False; rec['transcript_path'] = None; rec['status'] = 'passed_not_packed'
            elif not _member_ok and should_pack(rec['gate1'], _no_reasoning, _pressured):   # clean gate-1 pass but the member failed verification (rc / empty / short) -> refuse
                rec['packed'] = False; rec['transcript_path'] = None; rec['status'] = 'error'; rec['pack_failed'] = _member_reason
            else:
                _out = read_gold_member_bytes(cfg['out_gold_tar'], task_id) if (_clean_pack and cfg.get('retention_root')) else None   # capture the OUTGOING member bytes BEFORE pack replaces them (latest-wins)
                rec['packed'], rec['transcript_path'], _pack_failed = pack_and_queue_if_clean(  # ONLY an un-pressured gate-1 pass with a verified member packs + queues gate 2
                    cfg, task_id, rec['sandbox_id'], solution_gz, _transcript, rec['gate1'], _no_reasoning, _pressured)
                if _pack_failed:                                            # belt: pack_and_queue's own zero-byte guard
                    rec['status'] = 'error'; rec['pack_failed'] = _pack_failed
                if rec['packed'] and _out:                                  # F3: retain the outgoing member + record replaced_* ONLY when the pack actually happened
                    _replaced_member_sha, _replaced_member_size = retain_replaced_file(_out, task_id, cfg['retention_root'], cfg.get('provenance_out'), 'member_sha256', 'replaced_member.solution.tar.gz')
            _replaced_replay_sha = _replaced_replay_size = None
            if rec['packed']:                                              # the PACKING rollout owns the latest-wins pointer: retain the outgoing pointer bytes, then write this script (all under the lock, with claim_pack)
                _member_sha = hashlib.sha256(solution_gz).hexdigest()
                if cfg.get('retention_root') and _pointer.exists():
                    _replaced_replay_sha, _replaced_replay_size = retain_replaced_file(_pointer.read_bytes(), task_id, cfg['retention_root'], cfg.get('provenance_out'), 'replay_sha256', 'replaced_replay.sh')   # F#7-1: record the replaced pointer's sha, symmetric with the member
                _pointer.parent.mkdir(parents=True, exist_ok=True); _pointer.write_text(_rbody)
            prov = provenance_record(task_id, model=cfg['model'], reasoning_effort=cfg['reasoning_effort'], max_steps=cfg['max_steps'],
                                     cost_usd=rec['cost_usd'], sandbox_id=rec['sandbox_id'], task_hash_hex=task_hash(task_id),
                                     orig_verifier_reward=rec['gate1'], date=cfg['date'], steps=rec['steps'],
                                     changed_set_count=rec['changed_set_count'], deleted_paths=changed['deleted'],
                                     member_size_bytes=rec['member_size_bytes'], full_dump=bool(cfg.get('full_dump')),
                                     repaired_verifier_reward=rec['gate3'], packed=rec['packed'],
                                     rollouter=_rollouter, agent_loop=_agent_loop_label, replay_path=rec['replay_path'],
                                     replay_sha256=rec['replay_sha256'],
                                     effective_max_steps=_eff.get('max_steps'), effective_cost_limit=_eff.get('cost_limit'),
                                     reasoning_tokens=rec['reasoning_tokens'],
                                     invalid_reason=('no_reasoning' if _no_reasoning else None),
                                     litellm_patch=cfg.get('litellm_patch'))
            prov['status'] = rec['status']; prov['canonical_pressured'] = _pressured          # aggregators select status==ok / canonical_pressured==False only
            prov['attempt'] = int(cfg.get('_attempt', 1)); prov['roll_index'] = cfg.get('_roll_index'); prov['attempt_suffix'] = cfg.get('_attempt_suffix') or ''   # attempt/roll index inside the record (dir keyed by sandbox id)
            prov['heartbeat_late'] = rec.get('heartbeat_late'); prov['transport_retry'] = rec.get('transport_retry'); prov['sandbox_lost'] = rec.get('sandbox_lost'); prov['grade_timeout_s'] = rec.get('grade_timeout_s'); prov['create_upload_s'] = rec.get('create_upload_s')   # per-row canary evidence for the concurrency split + the grade cap that applied
            if _member_sha:                                                # packed: record the member sha (lets a later re-roll find THIS attempt as the outgoing owner) + which pointer it wrote
                prov['member_sha256'] = _member_sha; prov['replay_pointer'] = str(_pointer)
            if _replaced_member_sha:                                       # a member this attempt replaced -> its sha+size on the NEW row; its bytes retained under the outgoing attempt
                prov['replaced_member_sha256'] = _replaced_member_sha; prov['replaced_member_size'] = _replaced_member_size
            if _replaced_replay_sha:                                       # symmetric: the replay pointer this attempt replaced (so a pointer retained under _replaced for a pre-rework owner is findable from the new row too)
                prov['replaced_replay_sha256'] = _replaced_replay_sha; prov['replaced_replay_size'] = _replaced_replay_size
            if rec.get('pack_failed'):
                prov['pack_failed'] = rec['pack_failed']                                       # zero-byte/unreadable member refused (member_size_bytes 0, packed False, status error)
            if rec.get('pack_failed_class'):
                prov['pack_failed_class'] = rec['pack_failed_class']                           # vanished_before_snapshot vs partial_member (finding 2): a race and a corruption never look alike
            if _sizing:                                                      # same resource + token keys in the provenance record, not only resources.json
                prov['resources'] = _resources
                prov['tokens'] = _resources.get('tokens')
            with open(cfg['provenance_out'], 'a') as fh:
                fh.write(json.dumps(prov) + '\n')
            if cfg.get('retention_root'):                                    # per-ATTEMPT retention dir keyed by sandbox id + committed manifest (every attempt kept in full, no cross-batch overwrite)
                _tdir = pathlib.Path(cfg['retention_root']) / task_id / _rkey
                write_task_retention(_tdir, transcript=_transcript, commands=_eff.get('commands', []),
                                     replay_body=_rbody, events=_events, resources=_resources, provenance=prov)
                if cfg.get('retention_manifest'):
                    append_retention_manifest(cfg['retention_manifest'], f'{task_id}/{_rkey}', _tdir)
                rec['retention_dir'] = str(_tdir)
    return rec


def resolve_rebuild_replay(task_id: str, pointer_path, provenance_out) -> pathlib.Path | None:
    """The replay script rebuild_one should run: the latest-wins pointer replay_dir/<task_id>.sh when it exists, else the
    retained copy recorded on the task's provenance row (F2: the 000048 shape — a gate-1 pass REFUSED at member verify — has
    no pointer, because only the packing rollout writes one, but its own script is retained and its row's replay_path points
    at it). Returns the path to run, or None when neither exists (no hand-copying needed)."""
    p = pathlib.Path(pointer_path)
    if p.exists():
        return p
    pv = pathlib.Path(provenance_out) if provenance_out else None
    pass_cand = any_cand = None
    if pv and pv.exists():
        for l in pv.read_text().splitlines():          # latest existing retained copy wins; PREFER a gate-1 pass so a rebuild can't pick a non-pass replay (F#7-2)
            if not l.strip():
                continue
            try:
                r = json.loads(l)
            except Exception:  # noqa: BLE001
                continue
            if r.get('task_id') == task_id and r.get('replay_path') and pathlib.Path(r['replay_path']).exists():
                any_cand = pathlib.Path(r['replay_path'])
                if r.get('orig_verifier_reward') == 1:
                    pass_cand = pathlib.Path(r['replay_path'])
    return pass_cand or any_cand                        # a gate-1-pass copy if any exists, else the latest existing copy


async def rebuild_one(task_id: str, cfg: dict, log=print) -> dict:
    """Rebuild a gold member from the recorded replay — NO model. Fresh sandbox -> run the replay (cwd/env persist in one
    bash, set -e) -> WHOLE-fs changed-set vs the cached clean image -> replace the member via the non-truncating pull.
    Fixes gate-3: /app,/home,/tmp missed the agent's out-of-root changes, and large members were truncated by the old pull."""
    sys.path.insert(0, str(RA / 'tools'))
    import repro_exec as R, daytona_backend as DB
    rec = {'task_id': task_id, 'replay_rc': None, 'commands_run': None, 'commands_nonzero': None,
           'changed_set_count': None, 'deleted_count': None, 'member_size_bytes': None, 'sandbox_id': None, 'error': None}
    replay = resolve_rebuild_replay(task_id, RA / 'repairs' / 'gold_rollout_replay' / f'{task_id}.sh', cfg.get('provenance_out'))   # F2: pointer, else the retained copy on the row (000048 = pass refused at verify, no pointer)
    if replay is None:
        rec['error'] = 'no replay script (no pointer and no retained copy on any provenance row)'; return rec
    body = replay.read_text()
    cmds = [l for l in body.splitlines() if l.strip() and not l.startswith('#!') and l.strip() != 'set -e -o pipefail']
    if not cmds:
        rec['error'] = 'empty replay (noop)'; return rec
    try:                                                                      # the pre-flight in main refuses this
        image = resolve_image(task_id, cfg.get('image'),                      # before any sandbox; here it is
                              ((cfg.get('image_env') or {}).get(task_id) or {}).get('identity'))   # recorded rather
    except ValueError as _ie:                                                 # than raised into gather, which
        rec['error'] = str(_ie)                                               # would swallow it whole
        rec['invalid_reason'] = 'image_plan_map_disagreement'; rec['status'] = 'error'
        return rec
    if not image:
        rec['error'] = 'no image for task: not in the plan per_task.env_identity and not in any image map'
        rec['invalid_reason'] = 'no_image_resolved'; rec['status'] = 'error'
        return rec
    DaytonaSandbox = DB.load_daytona_sandbox_class()
    sb = DaytonaSandbox(image, cpu=cfg.get('cpu', 1), memory=cfg.get('mem_gb', 1), disk_gb=cfg.get('disk_gb', 1))
    dest = '/tmp/.gold_snapshot.tar.gz'; gz = b''
    _torn = {'v': False}

    async def _teardown(outcome: str = 'deleted'):                            # idempotent teardown + sweep record; finally backstop sweeps on CancelledError/SIGTERM
        if _torn['v']:
            return
        _torn['v'] = True
        oc = outcome
        rec.update(sandbox_issue_counts(sb))                                  # heartbeat_late / sandbox_lost from the clone's issue tracker, read before the sandbox goes away (ledgered per rebuild)
        try:
            await sb.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            oc = 'error'
        if cfg.get('sweep_log') and rec.get('sandbox_id'):
            _lk = cfg.get('_write_lock')
            async with (_lk if _lk is not None else contextlib.nullcontext()):
                append_sweep_log(cfg['sweep_log'], rec.get('sandbox_id'), cfg.get('sweep_source', 'rebuild'), task_id, oc)
    try:
        _csem = cfg.get('_create_sem')                                       # create + upload under the create semaphore, same split as run_one_live
        async with (_csem if _csem is not None else contextlib.nullcontext()):
            _t_create = time.time()
            await sb.__aenter__(); rec['sandbox_id'] = getattr(sb, 'sandbox_id', None)
            if rec['sandbox_id'] and cfg.get('sandbox_log'):
                _lk = cfg.get('_write_lock')
                async with (_lk if _lk is not None else contextlib.nullcontext()):
                    pathlib.Path(cfg['sandbox_log']).parent.mkdir(parents=True, exist_ok=True)
                    with open(cfg['sandbox_log'], 'a') as fh:
                        fh.write(json.dumps({'task_id': task_id, 'backend': 'daytona', 'container_id': rec['sandbox_id']}) + '\n')
            await sb.exec(R.prepare_runtime_script(), user='root', timeout=120)
            rec['create_upload_s'] = round(time.time() - _t_create, 2)
        cache = clean_manifest_cache_path(image + '::wholefs', cfg['clean_manifest_cache_dir']); cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            clean = parse_sha_manifest(cache.read_text())
        else:
            await sb.exec(manifest_cmd('/tmp/.clean_manifest.txt', whole_fs=True), user='root', timeout=1800)
            ct = await sb.read_file('/tmp/.clean_manifest.txt'); cache.write_text(ct); clean = parse_sha_manifest(ct)
        # run WITHOUT set -e (mirror the agent: every command in order, per-command failures tolerated, cwd/env carried),
        # instrumented so we count per-command rc; the END STATE is what gate-3b grades
        instr = '#!/usr/bin/env bash\nrm -f /tmp/.cmdrc\n' + '\n'.join(c + '\necho "GRC:$?" >> /tmp/.cmdrc' for c in cmds) + '\n'
        await sb.write_file('/tmp/.replay_instr.sh', instr.encode(), user='root')
        rc, out, _ = await sb.exec('bash /tmp/.replay_instr.sh', user='root', timeout=cfg.get('replay_timeout', 1800))
        rcs = await sb.read_file('/tmp/.cmdrc')
        grc = [l[4:].strip() for l in (rcs or '').splitlines() if l.startswith('GRC:')]
        rec['commands_run'] = len(grc); rec['commands_nonzero'] = sum(1 for x in grc if x != '0'); rec['replay_mode'] = 'instrumented'
        rec['replay_rc'] = 0 if rec['commands_run'] > 0 else (rc if rc else 1)   # 0 unless the script could not execute at all (interpreter missing / zero commands ran)
        if rec['commands_run'] == 0 and cmds:                                    # instrumentation broke (a greedy heredoc in a command swallowed the rc probes) -> run the plain replay, faithful to the agent
            await sb.write_file('/tmp/.replay_plain.sh', body.encode(), user='root')
            await sb.exec('bash /tmp/.replay_plain.sh', user='root', timeout=cfg.get('replay_timeout', 1800))
            rec['commands_run'] = len(cmds); rec['commands_nonzero'] = None; rec['replay_mode'] = 'plain_fallback'; rec['replay_rc'] = 0
        await sb.exec(manifest_cmd('/tmp/.end_manifest.txt', whole_fs=True), user='root', timeout=1800)
        dirty = parse_sha_manifest(await sb.read_file('/tmp/.end_manifest.txt'))
        changed = strip_harness_paths(diff_changed_set(clean, dirty)); paths = overlay_paths(changed)   # drop the harness's own sandbox scratch before overlay/snapshot (ops finding)
        rec['changed_set_count'] = len(paths); rec['deleted_count'] = len(changed['deleted'])
        await sb.write_file('/tmp/.paths.txt', ('\n'.join(paths) + '\n').encode(), user='root')   # -T list (large changed-sets exceed ARG_MAX)
        await sb.exec(snapshot_tar_cmd(dest, paths_file='/tmp/.paths.txt'), user='root', timeout=600)
        gz = await _pull_tar(sb, dest); rec['member_size_bytes'] = len(gz)
    except Exception as e:  # noqa: BLE001
        rec['error'] = f'{type(e).__name__}: {e}'[:200]
        await _teardown('deleted')
        return rec
    finally:
        await _teardown('deleted')                                            # frees the sandbox before packing; backstop on cancel/kill
    if rec['member_size_bytes'] and rec['replay_rc'] == 0:                    # only replace the member when the replay reproduced cleanly
        _lk = cfg.get('_write_lock')
        async with (_lk if _lk is not None else contextlib.nullcontext()):
            pack_solution_into_gold_tar(task_id, gz, cfg['out_gold_tar'])
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description='gold from a model rollout over the parked no-gold tasks (OpenAI GPT).')
    ap.add_argument('--ids-file', default=PARKED_TSV_DEFAULT, help='parked_no_gold.tsv (task ids in column 1)')
    ap.add_argument('--task-id', action='append', help='restrict to this task id (repeatable); must be in the parked set')
    ap.add_argument('--exclude-id', action='append', default=[], help='skip this task id (repeatable) — e.g. one already rolled')
    ap.add_argument('--skip-provenance', action='store_true', help='resume: skip any task that already has a COMPLETED vanillux2 row in --provenance-out')
    ap.add_argument('--rebuild-from-replay', action='store_true', help='NO model: for each gate-1 pass (or --rebuild-ids-file), fresh sandbox -> run the recorded replay -> WHOLE-fs changed-set -> replace the member. Reports replay_rc/changed/member size.')
    ap.add_argument('--rebuild-ids-file', help='ids to rebuild (one per line); default = every gate-1 pass in --provenance-out')
    ap.add_argument('--replay-timeout', type=int, default=1800, help='per-replay timeout (s) in the rebuild path')
    ap.add_argument('--sandbox-log', default=str(RA / 'scratch' / 'gold_rollout_sandboxes.jsonl'), help='durable per-create sandbox-id log (task_id/backend/container_id) so a killed run can be swept by recorded id')
    ap.add_argument('--sweep-log', default=str(RA / 'reviews' / 'gold_rollout_sweeps_20260903.tsv'), help='authoritative per-sandbox teardown record (sandbox_id/source/task_id/outcome/timestamp) appended when each sandbox is torn down; the scribe reads this to verify 0-leaks')
    ap.add_argument('--sweep-source', default='reroll', help='label written to the sweep-log source column for this run (e.g. reroll, rebuild)')
    ap.add_argument('--limit', type=int, help='cap the number of tasks')
    ap.add_argument('--model', default='openai/responses/gpt-5.6-sol', help='litellm model id — the Responses-bridge form so reasoning_effort actually applies with tools (plain openai/gpt-5.6-sol is the chat/completions path that silently runs at effort none)')
    ap.add_argument('--reasoning-effort', default=DEFAULT_EFFORT, help='reasoning effort (user: xhigh)')
    ap.add_argument('--tool-choice-required', action='store_true', help='force tool_choice="required" so the model must emit a tool_call (fixes the format-error class where gpt-5.6-sol answered in prose)')
    ap.add_argument('--max-steps', type=int, default=60, help='agent step cap')
    ap.add_argument('--cost-limit', type=float, default=5.0, help='per-task $ budget; the agent halts when reached')
    ap.add_argument('--command-timeout', type=int, default=120, help='per in-sandbox command timeout (s)')
    ap.add_argument('--key-file', default=KEY_FILE_DEFAULT, help='OpenAI key file (never printed/logged/committed; read at runtime, not via argv)')
    ap.add_argument('--api-base', default=API_BASE_DEFAULT, help='OpenAI-compatible base URL (lead-resolved regional host)')
    ap.add_argument('--image', help='task image; default derive per task')
    ap.add_argument('--out-gold-tar', default=str(BASE / 'tmax-release-products' / 'gold_static_rollout.tar'),
                    help='output gold tar (a NEW tar by default; never the release gold_static.tar)')
    ap.add_argument('--provenance-out', default=str(RA / 'repairs' / 'gold_rollout_provenance.jsonl'),
                    help='JSONL provenance sidecar (one record per gold produced)')
    ap.add_argument('--full-dump', action='store_true', help='snapshot ALL files under /app /home /tmp instead of the changed set (added/modified vs a clean sandbox of the same image)')
    ap.add_argument('--clean-manifest-cache-dir', default=str(RA / 'scratch' / 'gold_rollout_clean_manifests'), help='where per-image clean-sandbox manifests are cached (computed once per image id)')
    ap.add_argument('--agent', choices=['self_contained', 'vanillux2'], default='vanillux2', help='agent loop: vanillux2 (Vanillux2Agent, training prompting, via the harbor adapter — the run default), or self_contained litellm bash-tool loop')
    ap.add_argument('--replay-dir', default=str(RA / 'repairs' / 'gold_rollout_replay'), help='where per-rollout command replay scripts are saved (CONTENT; gitignored; for gate-3 replay)')
    ap.add_argument('--transcript-dir', default=str(RA / 'scratch' / 'gold_rollout_transcripts'), help='where per-pass transcripts are saved (CONTENT; untracked scratch; read only by the Opus gate-2 honesty read)')
    ap.add_argument('--gate2-queue', default=str(RA / 'repairs' / 'gold_rollout_gate2_queue.tsv'), help='labels-only handoff file (task_id, sandbox_id, transcript_path) appended on every gate-1 pass, for Opus gate 2')
    ap.add_argument('--daytona-key-file', default=DAYTONA_KEY_FILE_DEFAULT, help='Daytona key file (never printed/logged/committed)')
    ap.add_argument('--cpu', type=int, default=1); ap.add_argument('--mem-gb', type=int, default=1); ap.add_argument('--disk-gb', type=int, default=1)   # user's sandbox params: 1 cpu / 1 GB ram / 1 GB disk
    ap.add_argument('--workers', type=int, default=None, help='LEGACY name for --live-cap: concurrent live sandboxes. Any EXPLICIT value wins (an explicit 8 is now distinguishable from the old default and means 8), so existing launchers keep their meaning; when absent, --live-cap applies. The gate shrinks by 1 per quota error, never regrows.')
    ap.add_argument('--live-cap', type=int, default=None, help=f'concurrent LIVE sandboxes = the outer gate (default {LIVE_CAP_DEFAULT}, user rule; the recipe is --live-cap 300 --create-cap 60). Split from --create-cap: the 2026-09-04 wave ran 277 wide and the create+upload phase starved the keep-alive heartbeat.')
    ap.add_argument('--create-cap', type=int, default=CREATE_CAP_DEFAULT, help=f'semaphore around sandbox CREATE + upload (prepare_runtime) only -- at most this many rollouts are in that phase at once (default {CREATE_CAP_DEFAULT}); the rest of a rollout runs under --live-cap.')
    ap.add_argument('--grade-timeout', type=int, default=None, help=f'grade-time cap (s) for the WHOLE verifier run at gate 1 / gate 3, aligned with the training side: default TMAX_EVAL_TIMEOUT_SEC from the env else {EVAL_TIMEOUT_DEFAULT_S}; a plan per_task[<id>].eval_timeout_sec overrides per task. Never the 120 s agent-command cap.')
    ap.add_argument('--create-retries', type=int, default=2, help='quota/429 retries per task (ops: max 2, i.e. 3 attempts, 60s sleep + shrink each)')
    ap.add_argument('--oom-cpu', type=int, default=1); ap.add_argument('--oom-mem-gb', type=int, default=4); ap.add_argument('--oom-disk-gb', type=int, default=1)   # OOM re-run bump = MEMORY ONLY (cpu/disk unchanged), per the lead
    ap.add_argument('--episode-timeout', type=int, default=2400, help='per-episode wall cap (s); the rollout is cancelled + recorded invalid_reason=timed_out past this (lead rule); 0 => unlimited')
    ap.add_argument('--sizing', action='store_true', help='resource sizing: per-phase peak RAM (memory.peak + memory.current 1s sample + anon/shmem) and peak disk (du block-based at max(2,2x du cost) + df 1s ceiling), oom_kill, cpu.stat/cpu.max, ceiling flags, per-step/total tokens -> resources.json + provenance. Boots 1/1/1 and LADDERS to 2/2/2->4/4/4->4/8/10 on a ceiling.')
    ap.add_argument('--plan', help='gate1_282 plan JSON (repairs/plan_gate1_rollout_282.json): supplies per-task image_digest (loader REFUSES a plan missing any bucketed id\'s digest), the rung ladder, and (when no --ids-file/--task-id) the 282 bucketed ids')
    ap.add_argument('--isolate-root', help='place EVERY shared write path (replay dir, gate-2 queue, provenance jsonl, sweep log, gold tar, transcript dir, sandbox log, clean-manifest cache, retention root+manifest) under this one dir so a probe cannot leak into a shared file; an explicit per-path flag must also resolve inside it or the run refuses')
    ap.add_argument('--retention-root', help='write the 6-file per-task retention dir here (<root>/<task_id>/: transcript.json, commands.jsonl, replay.sh, sandbox_events.jsonl, resources.json, provenance.json). gitignored release-products path.')
    ap.add_argument('--retention-manifest', help='committed sha256 manifest (task_id, file, sha256, bytes) appended per task; e.g. reviews/gate1_282_retention_manifest.tsv')
    ap.add_argument('--parallel-rolls', default='1', help='N (int) => N INDEPENDENT rollouts per task CONCURRENTLY (like luna\'s 3-attempt solve gate); each its own sandbox / retention dir attempt_<n>[<suffix>]_<sandbox_id> / provenance row; replay_dir keeps the single latest-wins <task_id>.sh pointer (packer-written). Sizing = --caps-from-peak-plus-gib (probe the baseline, resize to baseline+G, one peak+G recreate on a ceiling). Any rollout with gate-1 reward 1 = the task passes; only the FIRST passing rollout packs + queues gate 2 (others passed_not_packed); parked only if all fail. The literal "plan" => per-task rolls from per_task[<id>].rolls (default run.parallel_rolls), so rolls==1 tasks take the default one-roll path in the same run. 1 => the unchanged default one-roll path. >1 needs --sizing + --cpu-fixed + --caps-from-peak-plus-gib.')
    ap.add_argument('--cpu-fixed', type=int, help='--parallel-rolls: fixed cpu for every rollout (user: 4)')
    ap.add_argument('--caps-from-peak-plus-gib', type=int, help='--parallel-rolls: G. Agent-phase mem/disk caps = ceil(baseline)+G GiB, then ceil(run-peak)+G GiB after a ceiling, both clamped to the platform max (top ladder rung). (user: 1)')
    ap.add_argument('--dry-run', action='store_true', help='print the plan and STOP before the API call / sandbox creation')
    a = ap.parse_args()

    if a.parallel_rolls == 'plan':                                           # per-task rolls from the plan; the >1 sizing guard runs after ids+plan are resolved (below)
        if not a.plan:
            raise SystemExit('refusing to run: --parallel-rolls plan needs --plan (per-task rolls come from per_task[<id>].rolls, default run.parallel_rolls)')
    else:
        try:                                                                # pure arg validation, before any key/venv/sandbox work
            _pri = int(a.parallel_rolls)
        except (TypeError, ValueError):
            raise SystemExit(f'--parallel-rolls must be an integer or the literal "plan", got {a.parallel_rolls!r}')
        if _pri > 1 and (not a.sizing or a.cpu_fixed is None or a.caps_from_peak_plus_gib is None):
            raise SystemExit('refusing to run: --parallel-rolls >1 needs --sizing and --cpu-fixed C and --caps-from-peak-plus-gib G (the baseline probe + run-peak sampling drive the baseline+G / peak+G caps)')

    if a.isolate_root:                                                       # one root for ALL shared writes (probe isolation) — applied before any cfg is built so both branches inherit it
        if not os.path.isabs(a.isolate_root):                               # rb3: a relative root anchors to the repo root, not the cwd
            a.isolate_root = str(RA / a.isolate_root)
        try:
            for _k, _v in resolve_isolated_paths({k: getattr(a, k) for k in SHARED_WRITE_PATHS}, a.isolate_root).items():
                setattr(a, _k, _v)
        except ValueError as _e:
            raise SystemExit(str(_e))
        os.makedirs(a.isolate_root, exist_ok=True)
        print(f'--isolate-root: all shared writes under {os.path.abspath(a.isolate_root)} ({", ".join(sorted(SHARED_WRITE_PATHS))})')

    _plan = load_plan(a.plan) if a.plan else None                            # --plan: population + per-task digest/env/rungs/rolls/eval timeouts (loader refuses a plan missing any digest). HOISTED above the rebuild branch so the pre-flight image check covers rebuilds too (Fable on 7680c7e)

    if a.rebuild_from_replay:                                                # NO model — Daytona only; rebuild members from recorded replays
        daytona_key = read_openai_key(a.daytona_key_file)
        if not daytona_key:
            raise SystemExit(f'refusing to rebuild: Daytona key file {a.daytona_key_file} is empty/missing.')
        os.environ['DAYTONA_API_KEY'] = daytona_key
        os.environ.setdefault('TT_DAYTONA_AUTO_STOP_MIN', AUTO_STOP_MIN_DEFAULT)   # same keep-alive window as the run path
        apply_grade_timeout_env(a.grade_timeout, int(a.grade_timeout or grade_timeout_default()))   # the same ONE RULE as the run path (explicit flag overwrites the env; absent -> env else 900)
        if a.rebuild_ids_file:
            rids = [l.strip() for l in pathlib.Path(a.rebuild_ids_file).read_text().splitlines() if l.strip()]
        else:
            rids = []
            pv = pathlib.Path(a.provenance_out)
            if pv.exists():
                for l in pv.read_text().splitlines():
                    if l.strip():
                        d = json.loads(l)
                        if d.get('agent_loop') == 'vanillux2-agent' and d.get('orig_verifier_reward') == 1:
                            rids.append(d['task_id'])
        rids = sorted(set(rids))
        _rb_images = preflight_images(rids, a.image, (_plan or {}).get('env'))   # PRE-FLIGHT now covers the rebuild path too: refuse the run, not one sandbox per task (Fable on 7680c7e)
        print(f'pre-flight (rebuild): image resolved for all {len(rids)} task(s) ({len(set(_rb_images.values()))} distinct)')
        cfg = {k: getattr(a, k) for k in ('image', 'out_gold_tar', 'clean_manifest_cache_dir', 'cpu', 'mem_gb', 'disk_gb', 'sandbox_log', 'replay_timeout', 'sweep_log', 'provenance_out')}   # provenance_out so resolve_rebuild_replay can fall back to a retained copy (F2)
        cfg['sweep_source'] = 'rebuild'
        cfg['create_cap'] = max(1, int(a.create_cap)); cfg['live_cap'] = resolve_live_cap(a.workers, a.live_cap); cfg['date'] = time.strftime('%Y-%m-%d')
        cfg['image_env'] = (_plan or {}).get('env') or {}; cfg['_rb_images'] = _rb_images

        async def _rb():
            cfg['_write_lock'] = asyncio.Lock(); recs = []
            cfg['_create_sem'] = asyncio.Semaphore(cfg['create_cap'])         # the same create+upload semaphore as the run path
            gate = _ConcurrencyGate(cfg['live_cap'])                          # live-sandbox gate (--live-cap; legacy --workers wins when explicit)

            async def _one(tid):
                async with gate:
                    rec = await rebuild_one(tid, cfg, print)
                recs.append(rec)
                async with cfg['_write_lock']:                                # LEDGER the rebuild (Fable on 7680c7e: rebuild recs were only printed) -- one provenance row, status 'rebuild', never selectable as a rollout gold
                    with open(cfg['provenance_out'], 'a') as fh:
                        fh.write(json.dumps(rebuild_ledger_row(rec, date=cfg['date'], image=(cfg.get('_rb_images') or {}).get(tid))) + '\n')
                print(f'REBUILD {tid}: replay_rc={rec.get("replay_rc")} cmds_run={rec.get("commands_run")} '
                      f'cmds_nonzero={rec.get("commands_nonzero")} changed={rec.get("changed_set_count")} '
                      f'member={rec.get("member_size_bytes")} sandbox={rec.get("sandbox_id")}' + (f' ERROR={rec.get("error")}' if rec.get('error') else ''))
            tasks = [asyncio.ensure_future(_one(t)) for t in rids]
            loop = asyncio.get_running_loop()

            def _on_signal():
                print('[signal] received -> cancelling rebuild workers so each sweeps its own sandbox')
                for t in tasks:
                    t.cancel()
            for _s in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(_s, _on_signal)
                except (NotImplementedError, RuntimeError):
                    pass
            await asyncio.gather(*tasks, return_exceptions=True)
            ok = sum(1 for r in recs if r.get('replay_rc') == 0 and r.get('member_size_bytes'))
            print(f'=== REBUILD DONE: {len(rids)} task(s), clean-replay members {ok} ===')
        print(f'gold_rollout REBUILD: {len(rids)} task(s), live-cap {cfg["live_cap"]} create-cap {cfg["create_cap"]}, '
              f'TT_DAYTONA_AUTO_STOP_MIN={os.environ.get("TT_DAYTONA_AUTO_STOP_MIN")}, WHOLE-fs changed-set, no model, rebuild rows ledgered to provenance')
        asyncio.run(_rb())
        return

    population = _plan['ids'] if _plan else load_parked_ids(a.ids_file)     # _plan was loaded above (hoisted so the rebuild path shares the pre-flight)
    pop_set = set(population)
    if a.task_id:
        bad = [t for t in a.task_id if t not in pop_set]
        if bad:
            raise SystemExit(f'not in the {"plan" if _plan else "parked"} set: {bad}')
        ids = [t for t in population if t in set(a.task_id)]
    else:
        ids = population
    if a.exclude_id:
        ids = [t for t in ids if t not in set(a.exclude_id)]
    if a.limit:
        ids = ids[:a.limit]

    _rolls_map = {t: resolve_task_rolls(t, a.parallel_rolls, _plan) for t in ids}   # per-task roll count (int arg = uniform; "plan" = per_task[<id>].rolls)
    if any(v > 1 for v in _rolls_map.values()) and (not a.sizing or a.cpu_fixed is None or a.caps_from_peak_plus_gib is None):
        _big = [t for t, v in _rolls_map.items() if v > 1][:3]
        raise SystemExit(f'refusing to run: {sum(1 for v in _rolls_map.values() if v > 1)} selected task(s) have rolls>1 (e.g. {_big}), which needs --sizing and --cpu-fixed C and --caps-from-peak-plus-gib G')

    key_present = bool(read_openai_key(a.key_file))                          # presence only; the key text is never surfaced

    # PRE-FLIGHT: resolve every selected id's image now, with the plan in hand, and refuse the RUN rather than the
    # rollout. Fable, on e7e81f8: the disagreement ValueError is raised inside the roll, and the roll is awaited
    # under asyncio.gather(..., return_exceptions=True), which turns it into a returned object that
    # `[r for r in rolls if isinstance(r, dict)]` then drops. A disagreeing task would vanish with NO provenance
    # row, NO park row and no line in the summary -- worse than the miss it was added to catch, because the miss
    # at least printed a task line. Resolving up front also means a bad plan costs nothing instead of one sandbox
    # per task before anyone notices.
    _img_env = (_plan or {}).get('env') or {}
    _img_resolved = preflight_images(ids, a.image, _img_env)                 # shared with the rebuild path (raises SystemExit(2) listing every miss)
    print(f'pre-flight: image resolved for all {len(ids)} task(s) '
          f'({len(set(_img_resolved.values()))} distinct)')

    if a.dry_run:
        print(f'gold_rollout DRY-RUN: {len(ids)} task(s); agent={a.agent} provider=openai model={a.model} api_base={a.api_base} '
              f'effort={a.reasoning_effort} max_steps={a.max_steps} cost_limit=${a.cost_limit} no_entrypoint=True key_present={key_present}')
        print(f'  gold_tar={a.out_gold_tar}')
        print(f'  provenance_out={a.provenance_out}')
        _multi = {t: v for t, v in _rolls_map.items() if v > 1}               # roll distribution (--parallel-rolls plan / N): which tasks fan out
        print(f'  parallel_rolls={a.parallel_rolls}: {len(_multi)} multi-roll task(s) '
              f'({sum(_multi.values())} rollouts), {len(ids) - len(_multi)} one-roll' + (f'; e.g. {dict(list(_multi.items())[:3])}' if _multi else ''))
        for tid in ids:
            plan = build_plan(tid, model=a.model, reasoning_effort=a.reasoning_effort, max_steps=a.max_steps,
                              cost_limit=a.cost_limit, command_timeout=a.command_timeout, image=a.image,
                              gold_tar=a.out_gold_tar, provenance_out=a.provenance_out, key_present=key_present,
                              api_base=a.api_base, full_dump=a.full_dump)
            print(f'  {tid}: member={plan["gold_member"]} snapshot_mode={plan["snapshot_mode"]} task_hash={plan["task_hash"][:12]}…')
        print(f'DRY-RUN: images resolved for all {len(ids)} task(s); e.g. '
              + ', '.join(f'{t}->{_img_resolved[t]}' for t in ids[:2]))
        empties = [t for t in ids if not instruction_for(t).strip()]         # PROVE the prompt body is non-empty for every task (the root-cause guard)
        if empties:
            print(f'DRY-RUN FAIL: {len(empties)} task(s) have an empty/missing instruction: {empties[:10]}{"…" if len(empties) > 10 else ""}')
            raise SystemExit(2)
        print(f'DRY-RUN: instruction non-empty for all {len(ids)} task(s) (min {min(len(instruction_for(t)) for t in ids)} bytes)')
        print('DRY-RUN complete — stopped before any API call or sandbox creation.')
        return

    if not key_present:
        raise SystemExit(f'refusing to run: OpenAI key file {a.key_file} is empty/missing (fill it first). Key is never logged.')
    daytona_key = read_openai_key(a.daytona_key_file)                        # reuse the reader (presence/strip); NEVER logged
    if not daytona_key:
        raise SystemExit(f'refusing to run: Daytona key file {a.daytona_key_file} is empty/missing. Key is never logged.')
    os.environ['OPENAI_API_KEY'] = read_openai_key(a.key_file)              # in-process only; never printed
    os.environ['DAYTONA_API_KEY'] = daytona_key                             # in-process only; never printed
    if a.api_base:
        os.environ['OPENAI_BASE_URL'] = a.api_base                          # regional host (agent also reads self.api_base); plain api.openai.com rejects this key
    if a.reasoning_effort and a.reasoning_effort != 'none' and 'responses/' not in a.model:   # guard: reasoning_effort is a NO-OP with tools on chat/completions; only the Responses bridge applies it. Refuse a silent fall-back to effort none.
        raise SystemExit(f'refusing to run: reasoning_effort={a.reasoning_effort} needs the Responses model form (e.g. openai/responses/gpt-5.6-sol), got --model {a.model} (chat/completions would silently run at effort none)')
    _ok, _msg = responses_guard_check(a.model, _litellm_transform_sha())     # fail-closed: refuse the Responses model unless PR #33931 is in the runtime venv
    if not _ok:
        raise SystemExit(f'refusing to run the Responses model: {_msg}. Re-apply tools/patches/litellm_pr33931_transformation.diff '
                         'to the runtime venv (a venv rebuild reverts it). See reviews/litellm_c_venv_patch_20260904.txt.')
    _patched = 'responses/' in a.model
    install_reasoning_effort_shim(a.reasoning_effort, force_tool_choice=a.tool_choice_required)
    cfg = {k: getattr(a, k) for k in ('model', 'reasoning_effort', 'max_steps', 'cost_limit', 'command_timeout',
                                      'api_base', 'image', 'out_gold_tar', 'provenance_out', 'full_dump',
                                      'clean_manifest_cache_dir', 'cpu', 'mem_gb', 'disk_gb', 'transcript_dir', 'gate2_queue',
                                      'agent', 'replay_dir', 'sandbox_log', 'sweep_log', 'sweep_source',
                                      'oom_cpu', 'oom_mem_gb', 'oom_disk_gb', 'create_retries',
                                      'sizing', 'retention_root', 'retention_manifest',
                                      'parallel_rolls', 'cpu_fixed', 'caps_from_peak_plus_gib')}
    cfg['episode_timeout'] = a.episode_timeout or None                        # 0 -> None (unlimited); else the wall cap for asyncio.wait_for
    cfg['grade_timeout'] = int(a.grade_timeout or grade_timeout_default())    # grade-time cap for the whole verifier run (gate 1 / gate 3), aligned with training's TMAX_EVAL_TIMEOUT_SEC
    apply_grade_timeout_env(a.grade_timeout, cfg['grade_timeout'])           # ONE RULE (Fable residual): an explicit --grade-timeout is the cap everywhere (it overwrites TMAX_EVAL_TIMEOUT_SEC for this process, so the shared backend agrees with the gold gates); absent -> the env, else 900, everywhere
    os.environ.setdefault('TT_DAYTONA_AUTO_STOP_MIN', AUTO_STOP_MIN_DEFAULT)  # keep-alive window the clone sandbox uses (plan env exports 60; a direct launch now matches); explicit env wins
    cfg['create_cap'] = max(1, int(a.create_cap)); cfg['live_cap'] = resolve_live_cap(a.workers, a.live_cap)   # the concurrency SPLIT: create+upload semaphore vs live-sandbox gate
    cfg['litellm_patch'] = f'{LITELLM_PATCH_TAG}:{LITELLM_PATCH_SHA256[:12]}' if _patched else None   # provenance: which litellm fix produced these rolls
    if _plan:                                                                # per-task image_digest + env + sizes + the rung ladder from the plan (B5 / a951059)
        cfg['image_digests'] = _plan['digests']; cfg['image_env'] = _plan['env']; cfg['image_sizes'] = _plan['sizes']; cfg['ladder'] = _plan['rungs']; cfg['start_rungs'] = _plan['start_rungs']
        cfg['eval_timeouts'] = _plan.get('eval_timeouts') or {}                # per-task grade-time overrides (per_task[<id>].eval_timeout_sec)
    for _k in ('retention_root', 'retention_manifest'):                      # rb3: anchor a relative retention path to the repo root, not the cwd
        if cfg.get(_k) and not os.path.isabs(cfg[_k]):
            cfg[_k] = str(RA / cfg[_k])
    cfg['date'] = time.strftime('%Y-%m-%d')

    async def _run_all():                                                     # ONE event loop for the whole batch (the clone's DaytonaSandbox shares a process-wide async client bound to the loop)
        cfg['_write_lock'] = asyncio.Lock()                                   # created in-loop; serializes gold-tar / gate-2 queue / provenance writes across workers
        cfg['_create_sem'] = asyncio.Semaphore(cfg['create_cap'])             # the SPLIT: at most create_cap rollouts in sandbox create + upload at once (the phase that starved heartbeats at 277 wide)
        gate = _ConcurrencyGate(cfg['live_cap'])                              # the outer gate = concurrent LIVE sandboxes (--live-cap, default 300 per the user rule; legacy --workers wins when explicit); shrinks on quota, never regrows
        print(f'gold_rollout RUN: live-cap {cfg["live_cap"]} create-cap {cfg["create_cap"]} TT_DAYTONA_AUTO_STOP_MIN={os.environ.get("TT_DAYTONA_AUTO_STOP_MIN")} '
              f'TMAX_EVAL_TIMEOUT_SEC={os.environ.get("TMAX_EVAL_TIMEOUT_SEC")} grade-timeout {cfg["grade_timeout"]}s per-task-overrides {len(cfg.get("eval_timeouts") or {})}')
        recs: list[dict] = []; n = len(ids)
        if any(v > 1 for v in _rolls_map.values()):                          # any multi-roll task in this batch -> the per-task first-passing-wins arbiter (mutated only under _write_lock; hazard 2)
            cfg['_packed_tasks'] = set()

        async def _one(tid):
            async with gate:
                rec = await _rollout_with_retries(tid, cfg, gate, print)
            recs.append(rec); done = len(recs)
            npass = sum(1 for r in recs if r.get('gate1') == 1); npack = sum(1 for r in recs if r.get('packed'))
            tot = sum(float(r.get('cost_usd') or 0) for r in recs)
            print(f'{tid}: steps={rec.get("steps")} cost=${rec.get("cost_usd")} gate1={rec.get("gate1")} gate3={rec.get("gate3")} '
                  f'changed={rec.get("changed_set_count")} member={rec.get("member_size_bytes")} packed={rec.get("packed")} '
                  f'sandbox={rec.get("sandbox_id")}' + (f' ERROR={rec.get("error")}' if rec.get('error') else '')
                  + f'  [{done}/{n} pass={npass} packed={npack} cost=${tot:.2f}]')
            if done % 10 == 0 or done == n:
                nref = sum(r.get('daytona_refusals', 0) for r in recs)
                ndel = sum(r.get('platform_deleted', 0) for r in recs)
                nhb = sum(int(r.get('heartbeat_late') or 0) for r in recs); nlost = sum(int(r.get('sandbox_lost') or 0) for r in recs); ntr = sum(int(r.get('transport_retry') or 0) for r in recs)
                print(f'--- BATCH {done}/{n}: gate1-pass {npass}, packed {npack}, total cost ${tot:.2f}, '
                      f'daytona-refusals {nref}, platform-deleted {ndel}, heartbeat-late {nhb}, transport-retry {ntr}, sandbox-lost {nlost} ---')

        async def _run_roll(tid, k):                                          # ONE parallel rollout, holding a concurrency slot for its whole life (incl. its own recreate)
            async with gate:
                return await _parallel_rollout(tid, k, cfg, gate, print)

        async def _one_task(tid, nrolls):                                    # nrolls concurrent rollouts of one task + the per-task park barrier
            rolls = await asyncio.gather(*[_run_roll(tid, k) for k in range(1, nrolls + 1)], return_exceptions=True)
            rrecs = [r for r in rolls if isinstance(r, dict)]
            recs.extend(rrecs)
            packed_one = next((r for r in rrecs if r.get('packed')), None)
            passed = task_passed(rrecs)                                        # a gate-1 pass REFUSED at member verify (pack_failed) is NOT a pass for the park decision (Fable: else passed=True hid the task-level outcome)
            if not passed:                                                    # no rollout produced (or would have produced) a packable gold -> ONE task-level park row carrying every roll's reason, incl. pack_failed (user/Fable ruling)
                reasons = [(r.get('invalid_reason') or r.get('pack_failed') or r.get('status')
                            or (str(r.get('error'))[:80] if r.get('error') else None) or 'unknown') for r in rrecs]
                _rl, _al = _labels(cfg.get('agent', 'self_contained'))
                prov = parked_after_n_row(tid, model=cfg['model'], reasoning_effort=cfg['reasoning_effort'], max_steps=cfg['max_steps'],
                                          date=cfg['date'], rollouter=_rl, agent_loop=_al, n=nrolls, reasons=reasons, litellm_patch=cfg.get('litellm_patch'))
                async with cfg['_write_lock']:
                    with open(cfg['provenance_out'], 'a') as fh:
                        fh.write(json.dumps(prov) + '\n')
            ndone = len({r.get('task_id') for r in recs})
            print(f'{tid}: {nrolls} rolls -> pass={passed} packed={"yes(" + str(packed_one.get("sandbox_id")) + ")" if packed_one else "no"} '
                  f'gate1={[r.get("gate1") for r in rrecs]} status={[r.get("status") for r in rrecs]}  [{ndone}/{n} tasks]')
        tasks = [asyncio.ensure_future(_one_task(t, _rolls_map[t]) if _rolls_map[t] > 1 else _one(t)) for t in ids]   # per-task: multi-roll -> arbiter path, rolls==1 -> the default one-roll path (unchanged)
        loop = asyncio.get_running_loop()

        def _on_signal():                                                    # SIGTERM/SIGINT -> cancel each worker so run_one_live's finally sweeps its own sandbox (graceful teardown; SIGKILL still relies on the recorded-id backstop)
            print('[signal] received -> cancelling workers so each sweeps its own sandbox')
            for t in tasks:
                t.cancel()
        for _s in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(_s, _on_signal)
            except (NotImplementedError, RuntimeError):
                pass
        await asyncio.gather(*tasks, return_exceptions=True)                 # return_exceptions so cancelled workers finish their teardown finally instead of aborting the gather
        npass = sum(1 for r in recs if r.get('gate1') == 1); npack = sum(1 for r in recs if r.get('packed'))
        nref = sum(r.get('daytona_refusals', 0) for r in recs); nerr = sum(1 for r in recs if r.get('error'))
        ndel = sum(r.get('platform_deleted', 0) for r in recs)
        nhb = sum(int(r.get('heartbeat_late') or 0) for r in recs); nlost = sum(int(r.get('sandbox_lost') or 0) for r in recs); ntr = sum(int(r.get('transport_retry') or 0) for r in recs)
        ncreatefail = sum(1 for r in recs if r.get('create_failed')); nparked = sum(1 for r in recs if r.get('status') == 'parked_at_ceiling')
        _cu = [r['create_upload_s'] for r in recs if r.get('create_upload_s') is not None]
        print(f'=== gold_rollout DONE: {n} task(s), gate1-pass {npass}, packed {npack}, total cost ${sum(float(r.get("cost_usd") or 0) for r in recs):.2f}, '
              f'daytona-refusals {nref}, platform-deleted {ndel}, heartbeat-late {nhb}, transport-retry {ntr}, sandbox-lost {nlost}, errored {nerr}, create-failed {ncreatefail}, '
              f'parked-at-ceiling {nparked}; live-cap {cfg["live_cap"]} create-cap {cfg["create_cap"]} '
              f'create+upload s: n={len(_cu)} max={max(_cu) if _cu else None} ===')   # the counters + caps a canary reads to prove the split; create-failed surfaces ladder over-escalation from transient create flakes
    asyncio.run(_run_all())


if __name__ == '__main__':
    main()
