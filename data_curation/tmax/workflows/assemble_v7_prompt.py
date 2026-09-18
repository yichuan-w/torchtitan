#!/usr/bin/env python3
"""assemble_v7_prompt.py — deterministic assembler for the V7 judge prompt.

Builds rubrics/v7_prompt.md from
  rubrics/reference/v6_core.md   (V6 base, line ranges cited below)
  rubrics/reference/v6_prompt.md (V6 preamble, lines 1-12)
  rubrics/v7_rubric_delta.md             (sections D0..D9, parsed by their `## Dn.` headers)
following reviews/notes/v7_readiness_fable_20260902.md §1 steps 1-5:
  1. D1 first; then v6_prompt.md:1-12 with task paths rewritten to scratch/tasks/<id>/.
  2. v6_core.md:11-23; D3 in place of 25-57 and 249-260; 85-95 and 165-171 rewritten to
     "privileged-test-only no longer changes the tier; record it in `exploitability`".
  3. 324-329 (`reference_guarded`) replaced by the D1(b) definition + wrapper caveat.
  4. ONE merged JSON block = v6_core.md:313-364 ∪ D6 fields; "exactly this" reissued after it.
  5. D2, D4, D5, D8, D7, then a recording section with the V7 output path.
D9 (routing / quarantine) binds the workflow, not the judge — referenced, not inlined.
Re-run after ANY change to the delta; never edit rubrics/v7_prompt.md by hand.
Rubric text only — no task content is read or written.
"""
import json, hashlib, pathlib, re, sys

RA = pathlib.Path(__file__).resolve().parents[1]
CORE = RA / 'rubrics/reference/v6_core.md'
PROMPT6 = RA / 'rubrics/reference/v6_prompt.md'
DELTA = RA / 'rubrics/v7_rubric_delta.md'
OUT = RA / 'rubrics/v7_prompt.md'
CAL_OUT = RA / 'rubrics/v7_calibration.md'        # D7 lives here: scorer-only, NEVER in the judge prompt
TASK_ROOT = '{TASK_ROOT}/<task_id>'                # workflow substitutes {TASK_ROOT} (absolute path of the staged tasks)
V7_OUT = '{V7_OUTPUT_PATH}'   # set by the workflow, e.g. reaudit_398/judge/v7_<batch>_<agent>.jsonl


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


# F9 (v3.18): a section header's trailing editorial parenthetical — (NEW …) / (replaces …) / (adds …) / (extends …) — is
# dropped even when an earlier parenthetical precedes it (e.g. "… (v3.17 C1); … (replaces v6_core.md …)").
HEADER_STRIP_RE = re.compile(r'(?m)^(## D\d\. .*?)\s*\((?:NEW|replaces|adds|extends)[^()\n]*\)\s*$')


def rubric_version() -> str:
    """Single source: the newest `## V7-delta vX.Y[.Z]` heading in rubrics/CHANGELOG.md."""
    heads = re.findall(r'^## V7-delta v(\d+)\.(\d+)(?:\.(\d+))?', (RA / 'rubrics/CHANGELOG.md').read_text(), re.M)
    assert heads, 'no V7-delta version heading in rubrics/CHANGELOG.md'
    a, b, c = max((int(x), int(y), int(z or 0)) for x, y, z in heads)
    return f'V7-delta v{a}.{b}' + (f'.{c}' if c else '')


def lines(p: pathlib.Path) -> list[str]:
    return p.read_text().splitlines()


def L(src: list[str], a: int, b: int) -> str:
    """1-indexed inclusive line range, as in the review's citations."""
    return '\n'.join(src[a - 1:b])


def delta_sections(text: str) -> dict[str, str]:
    parts = re.split(r'(?m)^(?=## D\d\.)', text)
    out = {}
    for part in parts:
        m = re.match(r'## (D\d)\.', part)
        if m:
            out[m.group(1)] = part.rstrip('\n')
    return out


RESIDUE_RE = re.compile(r'\.append|\.replace\(|""|\."')   # D-99(h): edit-script verbs / quote residue an applier can paste into prose


def applier_residue(text: str, json_block: str = '') -> list[str]:
    """D-99(h) "no applier residue" (Fable S-06, v3.18 :272-274): lines of PROSE containing `.append`, `.replace(`, `""`,
    or a double quote directly after a sentence-ending period. Prose = the prompt minus the emitted JSON block and minus
    fenced code (```…```), where `."` is legitimate. Returns "line_no: text" hits (empty = clean)."""
    body = text.replace(json_block, '\n' * json_block.count('\n')) if json_block else text
    hits, in_fence = [], False
    for i, ln in enumerate(body.split('\n'), 1):
        if ln.startswith('```'):
            in_fence = not in_fence; continue
        if not in_fence and RESIDUE_RE.search(ln):
            hits.append(f'{i}: {ln.strip()[:80]}')
    return hits


def main() -> None:
    core, p6 = lines(CORE), lines(PROMPT6)
    D = delta_sections(DELTA.read_text())
    for k in ('D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9'):
        assert k in D, f'delta section {k} missing'
    def judge_facing(txt: str) -> str:
        """The judge never saw V6: drop editorial header suffixes and V6-directed sentences."""
        txt = HEADER_STRIP_RE.sub(r'\1', txt)                       # F9: strips the LAST keyword parenthetical even after an earlier one
        txt = re.sub(r' Drop the V6 rule that `accepts-wrong` \+ `privileged-test-only`\nroutes to REVIEW purely on exploitability; under D1 almost nothing is truly\nprivileged, because the agent is root\.', '', txt)
        txt = txt.replace('Keep hunting `rejects-right` (V6 section\n"Look for BOTH directions") unchanged — it is still the most-missed defect.', 'Keep hunting `rejects-right` ("Look for BOTH directions" below) — it is still the most-missed defect.')
        txt = txt.replace(' (NEW sub-rule)', '').replace('Tier table (replaces the V6 tier table):', 'Tier table:').replace('(the V6 nine labels, bound by `defects`)', '(the nine verdict labels, bound by `defects`)')
        return txt
    for k in ('D1', 'D2', 'D3', 'D4', 'D5', 'D8'):
        D[k] = judge_facing(D[k])
    # sanity anchors on the V6 line ranges the assembly depends on
    assert core[24].startswith('## Two axes'), core[24]
    assert core[84].startswith('## The rule that prevents over-rejection'), core[84]
    assert core[164].lstrip().startswith('**But set the exploitability honestly.**'), core[164]
    assert core[248].startswith('## Tier'), core[248]
    assert core[310].startswith('Return exactly this JSON'), core[310]
    assert core[323].lstrip().startswith('"reference_guarded"'), core[323]
    assert core[378].startswith('## Recording your work'), core[378]

    preamble = (L(p6, 1, 12).replace('/tmp/tmax_gate_work/<task_id>', TASK_ROOT)
                .replace('Audit exactly the task ids listed at the end, ONE AT A TIME.',
                         'Audit exactly the task ids in {TASK_IDS} (substituted by the workflow), ONE AT A TIME.'))

    # FIX 4 (log-reviewer): v6_core.md:61-64 asserts "578 judgements … recorded exactly once";
    # the archive cannot reproduce that number (mismatch_direction is empty in every V2-V4
    # record). Keep the instruction, drop the unsourced statistic.
    both_dirs = L(core, 59, 83)
    old_stat = ('Almost every rubric, including the four earlier versions of this one, hunts only\n'
                'for *wrong solutions passing*. Across 578 judgements those versions produced,\n'
                '**`rejects-right` was recorded exactly once** — not because it is rare, but\n'
                'because nobody was asked to look.')
    assert both_dirs.count(old_stat) == 1, 'rejects-right statistic anchor moved'
    both_dirs = both_dirs.replace(old_stat,
        'Almost every rubric, including the four earlier versions of this one, hunts only\n'
        'for *wrong solutions passing*. Across the V2–V4 judgements the archive keeps, the\n'
        '`mismatch_direction` field that would record `rejects-right` is empty in every\n'
        'record — not because it is rare, but because nobody was asked to look.\n'
        '(Statistic withdrawn: the earlier "578 judgements, recorded exactly once" is not\n'
        'reproducible from the archived records.)')

    exploit_note = (
        '## Exploitability no longer changes the tier\n\n'
        'Under D1 the agent is root in the image, so almost nothing is truly\n'
        '`privileged-test-only`. Still record WHO could trigger a bypass in `exploitability`\n'
        '(rollout-visible / grading-runtime-visible / feedback-learnable /\n'
        'privileged-test-only / n/a) — it is triage information — but it does not route\n'
        'the record: soundness plus the D8 exploit cost set the tier (D3). What protects\n'
        'against over-rejection is now the reproduction gate (D4): a defect you cannot\n'
        'drive with a runnable repro is REVIEW, not REJECT. `rejects-right` is REPAIR\n'
        'whatever its visibility — a verifier that fails correct work corrupts the reward\n'
        'with no agent involvement at all.')

    mgt_note = (
        '  Record who could trigger it in `exploitability`. Knowing that the verifier\n'
        '  recomputes from a given file is often knowledge only the verifier\'s source\n'
        '  carries — but under D1 the agent is root and can read and write that file, so\n'
        '  this is usually `rollout-visible`. Either way exploitability does not change the\n'
        '  tier (D3); rate the reproduction cost (D8) and attach the repro (D4).')

    tier_pointer = (
        '## Tier — read it off D3 above (soundness + D8 exploit cost)\n\n'
        '**REJECT-PROVEN is the only tier\n'
        'that discards a task, so it carries the burden of proof**: it requires a SIMPLE\n'
        'reproduction (phase 3 runs it — a REJECT-PROVEN whose repro scores 0 re-tiers to REVIEW, D4) and no\n'
        'bounded verifier change that repairs it (D5).\n'
        'If you cannot produce the repro, the tier is REVIEW, not REJECT.')

    reference_guarded = (
        '    "reference_guarded":      <true ONLY if the verifier is protected per D1(b)/(c):\n'
        '                               it recomputes the answer from the task input at grade\n'
        '                               time, the reference exists only at grade time\n'
        '                               (test.sh writes it), or it checks provenance\n'
        '                               (islink/realpath/inode/hash/recompile-from-source).\n'
        '                               CAVEAT: a symlink-only guard (islink/realpath) is\n'
        '                               PARTIAL — a wrapper script that exec\'s the reference\n'
        '                               at run time is a genuine file and passes it. Set true\n'
        '                               only when the reference is absent at grade time or\n'
        '                               the answer is recomputed; otherwise false and name\n'
        '                               the surviving vector in `who_could_trigger`>,')

    d6_fields = (
        '\n  "repro_cmd":       "<shell; null ONLY for PASS or REVIEW — in the REVIEW case state why in who_could_trigger or evidence>",\n'
        '  "repro_expected":  "reward==1 | reward==0 | reward==1-unenforced | no-reward-timeout | reward-varies | external-fetch | none: <reason> (REVIEW) | null (PASS)",\n'
        '  "repro_cost":      "simple | medium | hard | null",\n'
        '  "repro_lines":     <integer number of shell statements, or null>,\n'
        '  "shortcut_tag":    "shortcut_medium | shortcut_hard | null",\n'
        '  "verifier_fix":    "<concrete test.sh change for REPAIR, else null>",\n'
        '  "unenforced_cosmetic": "<a cosmetic instruction requirement the verifier skips (PASS case), else null>",\n'
        '  "cleared_by_probe": "<for a REVIEW cleared to PASS by phase 3: probe kind, runs, cpu limit, contention, reward vector — else null>",\n'
        '  "honest_fix_lines": <integer: minimal lines an honest solution must change/add to pass the verifier legitimately; null unless accepts-wrong>,\n'
        '  "honest_fix_sketch": "<AT MOST 40 words: what that honest solution does (the agent\'s own work on the task, not a test.sh change — that is verifier_fix); null unless accepts-wrong>",\n'
        '  "soundness_probe": "<C2: for a sound call when verifier_expected_answer_from is hardcoded-literal — the cheapest wrapper/stub/passthrough you considered AND the assertion that defeats it; null otherwise>",\n'
        '  "agent_is_root":   true,\n'
        '  "reference_paths": ["<in-image paths the verifier trusts, or []>"]\n')

    tail = L(core, 330, 363).rstrip()
    # FIX 1 (log-reviewer): checks key renamed to match the defects flag in name AND polarity
    old_aib = ('    "artifact_identity_bound":<true if the verifier ties what it grades to the\n'
               '                               work it claims to test; false if a decoy of the\n'
               '                               right name and shape would satisfy it>,')
    assert tail.count(old_aib) == 1, 'artifact_identity_bound anchor moved'
    tail = tail.replace(old_aib,
        '    "artifact_identity_unbound": <true if a decoy of the right name and shape would\n'
        '                               satisfy the verifier (it does NOT tie what it grades to\n'
        '                               the work it claims to test); false if it does. Same name\n'
        '                               and polarity as the defects flag>,')
    head = L(core, 313, 323)
    old_ev = ('  "evidence": "<the exact line that decides the verdict — quote it, with its\n'
              '                file and line number>",')
    assert head.count(old_ev) == 1, 'evidence anchor moved'
    head = head.replace(old_ev,
        '  "evidence": "<AT MOST 200 characters: the verbatim deciding line, with file and line number>",\n'
        '  "reason": "<AT MOST 40 words, your own words: why that line decides the verdict>",')
    head = head.replace('{\n  "verdict":', '{\n  "task_id": "<the task id>",\n  "agent": "<your agent label>",\n  "rubric_version": "v7",\n  "revised_after_priors": null,\n  "verdict":')
    assert head.count('"rubric_version"') == 1
    json_block = head + '\n' + reference_guarded + '\n' + tail + ',' + d6_fields + '}'
    # sanity: the v6 tail we kept ends at the closing brace line 364 which we re-add above
    assert core[363].strip() == '}', core[363]

    recording = (
        '## Recording your work — the FILE is the deliverable, not your reply\n\n'
        f'Append ONE line of JSON to `{V7_OUT}` (substituted by the workflow) the moment you\n'
        'finish each task. Never batch: write task 1\'s line before you start task 2. Every\n'
        'line carries `"task_id"`, `"agent"`, `"rubric_version": "v7"` and `"revised_after_priors": null`.\n'
        '**The FIRST line written for a task_id is its verdict of record.** Before starting, grep\n'
        'that file for your ids and skip any already present. Your final reply: one line with\n'
        'counts — never a verdict, never a quotation.\n\n'
        f'`{V7_OUT}` is QUARANTINED: its `evidence` fields quote task text verbatim, so the file is\n'
        'never committed to the repo and is read only by the Opus log-reviewer seat (D9).\n\n'
        'Message 2 — prior verdicts (`audit_v4_verdict`, V5, V6 tier) and phase-3 results where\n'
        'they exist — arrives ONLY after your first line is on disk (D2). If they change your\n'
        'mind, append a SECOND line with the same `task_id` whose `"revised_after_priors"` object\n'
        'names only the fields you would now change and cites the first line; never overwrite it.\n\n'
        'Routing and content quarantine (which model judges which task, what may be quoted\n'
        'where) are the workflow\'s duty — delta D9 — not yours.')

    parts = [
        '# TMAX audit — V7 judge prompt\n',
        D['D1'],                                  # step 1: harness contract first
        preamble,                                 # v6_prompt.md:1-12, paths rewritten
        D['D2'],                                  # what the judge is given (8 columns, nothing else)
        L(core, 11, 23),                          # step 2: framing + "Assign exactly one verdict"
        D['D3'],                                  # replaces 25-57 (axes) and 249-260 (tier table)
        both_dirs,                                # 59-83 with the unsourced statistic footnoted (FIX 4)
        exploit_note,                             # replaces 85-95
        L(core, 97, 164),                         # verdicts … MGT body
        mgt_note,                                 # replaces 165-171
        L(core, 173, 247),                        # OTHER, provenance, preplaced value, requirements, how to answer
        tier_pointer,                             # replaces 249-260
        L(core, 262, 309),                        # defects binding + two-question procedure
        'Return exactly this JSON, nothing else:\n\n' + json_block,   # step 4: ONE merged block
        'Return exactly this JSON — nothing else. `tier` is authoritative; `verdict` is a\n'
        'descriptive label bound by `defects` (D3).',
        L(core, 366, 375),                        # checks vs defects; CLEAN self-check
        D['D4'], D['D5'], D['D8'],                # step 5 (D7 is scorer-only — see CAL_OUT)
        recording,                                # replaces 379-384
    ]
    body = '\n\n'.join(p.rstrip('\n') for p in parts) + '\n'      # judge-facing text: everything after the header comment
    ASM_PAIRS = [["P07", "  setup.sh         what is built into the container before the agent starts", "replace", "  setup.sh         what the image bakes in; in training, environment/seeds (→ /workspace)\n                   and the image ENTRYPOINT also run before the agent starts"], ["P12", "  services the environment does not provide, so the task cannot be completed as\n  written.", "append", " (A service the image ENTRYPOINT starts counts as provided; one that neither setup.sh,\n  the seeds nor the ENTRYPOINT starts does not.)"], ["P13", "  a readable reference implementation, a ground-truth output file, an embedded\n  solution, or — see below — an executable reference the agent can simply run.", "replace", "  a readable reference implementation or ground-truth output file (a reference file —\n  gold itself is never shipped), an embedded solution, an executable reference the agent\n  can simply run (see below), or an answer obtainable by a network fetch during rollout."], ["P14", "attach the repro (D4).", "append", " A seed file under /workspace\n  is agent-writable: a verifier that recomputes from it is MUTABLE-GROUND-TRUTH."], ["P15", "  a task needing network, GPU, GUI or hardware the container lacks;\n  non-determinism; a required background service that is never started; a", "replace", "  a task needing GPU, GUI or hardware the container lacks (network is open during\n  rollout and is never a defect by itself); non-determinism; a required background\n  service that neither setup.sh, the seeds nor the ENTRYPOINT starts; a"], ["P16", "\"It fuzzes 1000 random inputs against a correct oracle, so nothing", "replace", "\"It fuzzes 1000 random inputs against a correct in-image reference program, so nothing"], ["P17", "count — and search `setup.sh` and the environment for it. If it is there, decide", "replace", "count — and search `setup.sh`, the environment/seeds and the environment for it. If it is there, decide"], ["P18", "  \"confidence\": <integer 1-10>,", "replace", "  \"confidence\": <integer 0-10 = 2 × the number of these you actually did: (1) read all three files in full; (2) verified verifier_expected_answer_from against test.sh; (3) traced repro_cmd (or soundness_probe) through the decisive assertion; (4) tested the shortcut against the D1 protections; (5) walked 'Look for BOTH directions' — nothing else moves it>,"], ["P20", "    \"reachable_reference\":    \"<path of a reference program the agent can read\n                                or execute, or null>\",", "replace", "    \"reachable_reference\":    \"<path of an in-image or seed-provided (/workspace) reference\n                                program the agent can read or execute, or null>\","], ["P21", "  \"who_could_trigger\": \"<one concrete sentence naming the actor and the path, or null if sound>\",", "replace", "  \"who_could_trigger\": \"<one concrete sentence naming the actor, the path, and the decisive assertion the non-solution satisfies; null if sound>\","], ["P22", "    \"nondeterministic_reward\":<true if the reward can differ for the same\n                               correct answer — wall-clock, ordering, network>", "replace", "    \"nondeterministic_reward\":<true if the reward can differ for the same\n                               correct answer — wall-clock, ordering, network (an external host reached by test.sh at grade time; loopback to the agent's own service is readiness, not nondeterminism — set verifier_network either way)>"], ["P23", "  \"agent_is_root\":", "prepend", "  \"verifier_network\": \"<none | loopback | external — what tests/test.sh connects to at grade time: loopback = 127.0.0.1/localhost, the service the agent was asked to run; external = any other host (urlopen/requests/socket, curl/wget, pip/apt/npm install, git fetch, NTP); a label, quote no URL>\",\n"], ["P24", "  \"reference_paths\":", "prepend", "  \"harness_class\": \"<reward_rewrite ONLY if this task's design leaves an agent-spawned process running into grading (a service the instruction asks for; usually with needs: entrypoint) \u2014 else null; the class itself is filed once by the harness reviewer, not per task>\",\n"], ["P25", "  \"reference_paths\": [\"<in-image paths the verifier trusts, or []>\"]\n", "replace", "  \"reference_paths\": [\"<in-image or seed-provided paths the verifier trusts, or []>\"]\n"], ["P26", "checks, run it now. Lower your confidence when the verifier's logic is long\nenough that you could have missed something in it.", "replace", "checks, run it now. Confidence is the check count above; do not adjust it for feel — a\nverifier long enough to hide something means check (3) or (4) is missing, so the count\nalready drops."], ["P25", "__WHOLE_LINE__:  \"soundness_probe\":", "replace", "  \"soundness_probe\": \"<C2: for a sound call when verifier_expected_answer_from is hardcoded-literal — the cheapest wrapper/stub/passthrough you considered from rollout-visible state only (the instruction, the image and seed contents, directory listings — never test.sh-only constants) AND the assertion that defeats it; null otherwise>\",\n"]]
    ASM_PAIRS += [["M-03", "Report as\nINSTR-VERIFIER-MISMATCH, and say in the evidence that the requirement is\nunenforced.", "replace", "If the two-question procedure below finds the clause load-bearing, report INSTR-VERIFIER-MISMATCH with soundness\n`accepts-wrong` (D3) and say in the evidence that the requirement is unenforced; if it is cosmetic, the verdict\nstays CLEAN and the clause goes in `unenforced_cosmetic`."], ["M-04a", "non-determinism; a required background\n  service that neither setup.sh, the seeds nor the ENTRYPOINT starts; a", "replace", "non-determinism; a required background service the task needs only IMPLICITLY and that\n  neither setup.sh, the seeds nor the ENTRYPOINT starts (a service or dependency the instruction or\n  verifier NAMES but the environment omits is INSTR-ENV-MISMATCH, not OTHER); a"], ["M-04b", "(A service the image ENTRYPOINT starts counts as provided; one that neither setup.sh,\n  the seeds nor the ENTRYPOINT starts does not.)", "replace", "(A service the image ENTRYPOINT starts counts as provided; one that the instruction or verifier\n  names but neither setup.sh, the seeds nor the ENTRYPOINT starts is this verdict — a service the task\n  needs only implicitly is OTHER.)"], ["M-06", "checks, run it now. Confidence is the check count above; do not adjust it for feel — a\nverifier long enough to hide something means check (3) or (4) is missing, so the count\nalready drops.", "replace", "checks, run it now. Confidence is 2 × the number of the FIVE checks named in the `confidence` field (not\nthis CLEAN list); do not adjust it for feel — a verifier long enough to hide something is one whose decisive assertion you have not\ntraced (check 3) or whose shortcut you have not tested against the D1 protections (check 4), so the count\nalready drops."], ["M-10b", "  \"verdict\": \"<one of the eight labels>\",", "replace", "  \"verdict\": \"<one of the nine labels>\","], ["M-11", "(network is open during\n  rollout and is never a defect by itself)", "replace", "(network is open during\n  rollout and an AGENT's network use is never a defect by itself; a VERIFIER that reaches an external\n  host at grade time is `unstable` — D3)"], ["M-16", "  output shape, format, types or count; trusts a self-reported value; greps the", "replace", "  output shape, format, types or count; trusts a self-reported value (if that value comes from an\n  agent-OWNED program the verifier runs, it is MUTABLE-GROUND-TRUTH, not this); greps the"]]   # v3.18.1 (contradiction sweeps)
    for _pid, _anchor, _mode, _new in ASM_PAIRS:
        if _anchor.startswith('__WHOLE_LINE__:'):
            _pfx = _anchor[len('__WHOLE_LINE__:'):]
            _ls = [ln for ln in body.split('\n') if ln.startswith(_pfx)]
            assert len(_ls) == 1, f'v3.18 {_pid}: whole-line anchor count {len(_ls)} != 1'
            body = body.replace(_ls[0] + '\n', _new if _new.endswith('\n') else _new + '\n', 1); continue
        _a = _anchor.strip('…')
        assert body.count(_a) == 1, f'v3.18/v3.18.1 {_pid}: anchor count {body.count(_a)} != 1'
        body = body.replace(_a, {'replace': _new, 'append': _a + _new, 'prepend': _new + _a}[_mode], 1)
    content_sha16 = hashlib.sha256(body.encode()).hexdigest()[:16]  # stable across key-only / stamp-only edits
    version = rubric_version()
    header = (
        '<!-- GENERATED by tools/assemble_v7_prompt.py — do not edit by hand.\n'
        f'     version: {version}   content_sha256_16: {content_sha16} (sha256 of everything after this comment; tools/prompt_sha.py recomputes it)\n'
        f'     inputs: v6_core.md@{sha(CORE)}  v6_prompt.md@{sha(PROMPT6)}  rubrics/v7_rubric_delta.md@{sha(DELTA)}\n'
        '     construction: reviews/notes/v7_readiness_fable_20260902.md §1 steps 1-5 (see script docstring). -->\n')
    text = header + '\n' + body
    cal_header = ('<!-- GENERATED by tools/assemble_v7_prompt.py from rubrics/v7_rubric_delta.md D7 — SCORER-ONLY.\n'
                  '     This file is the calibration answer key (expected tiers + phase-3 citations). It is NEVER\n'
                  f'     part of rubrics/v7_prompt.md and never shown to a judge. delta@{sha(DELTA)} -->\n\n'
                  '# TMAX audit — V7 calibration expectations (scorer-only)\n\n')
    cal_text = cal_header + D['D7'] + '\n'
    # ---- ALL checks run on in-memory text BEFORE any artefact is written (a failed check leaves the tree untouched)
    m = re.search(r'```calibration-key\n(.*?)```', cal_text, re.S)
    assert m, 'calibration-key block missing from D7'
    key_lines = [ln for ln in m.group(1).splitlines() if ln.strip()]
    assert key_lines[0].split('\t')[0] == 'task_id', 'calibration-key header'
    key_ids = [ln.split('\t')[0] for ln in key_lines[1:]]
    md_ids = set(re.findall(r'task_\d{6}_[0-9a-f]{8}', cal_text.replace(m.group(0), '')))
    missing_in_md = [k for k in key_ids if k not in md_ids]
    assert not missing_in_md, f'answer-key ids absent from the calibration prose: {missing_in_md}'
    pending = sorted(md_ids - set(key_ids))
    assert text.count('Return exactly this JSON') == 2, 'JSON block must be issued once and reissued once'
    assert '/tmp/tmax_gate_work' not in text, 'stale task path survived'
    assert '/tmp/v4_batch2.jsonl' not in text and '/tmp/v6_cal.jsonl' not in text, 'stale output path survived'
    assert 'privileged-test-only` and belongs in REVIEW' not in text, 'V6 165-171 survived'
    assert 'do NOT qualify' not in text, 'V6 reference_guarded 324-329 survived'
    assert text.count('"reference_guarded"') == 1 and text.count('"repro_cost"') == 1
    assert text.count('"soundness_probe"') == 1 and 'MEDIUM/HARD accepts-wrong never reach REJECT-PROVEN' in text, 'v3.17 C2 field / anti-over-rejection guard'
    for stale in ('stays PASS with a tag', 'MEDIUM/HARD (D8) — then', 'a PASS with shortcut_tag null', 'PASS without shortcut_tag', 'PASS that carries `shortcut_tag`', 'agent-supplied graded artifact', 'graded artifact is supplied by the agent'):
        assert stale not in text, f'pre-C1/D-57 wording survived: {stale}'
    assert '"artifact_identity_bound"' not in text and text.count('"artifact_identity_unbound"') == 2, 'checks/defects polarity'
    assert '`rejects-right` was recorded exactly once' not in text, 'unsourced statistic survived'
    assert 'Statistic withdrawn' in text, 'withdrawal footnote missing'
    assert not re.search(r'task_0|\b0\d{5}\b', text), 'task id / prefix leaked into the judge prompt'
    assert '## D7.' not in text and 'v6_cal' not in text, 'D7 leaked into the judge prompt'
    assert '{TASK_ROOT}' in text and '{TASK_IDS}' in text and 'QUARANTINED' in text, 'placeholders/quarantine missing'
    assert text.count('"reason"') == 1 and text.count('"rubric_version": "v7",') == 1, 'reason/rubric_version keys in the JSON block'
    # ---- D-99 semantic asserts (§Pass 15 lesson: mechanical checks passed while pasted cells broke the JSON block)
    _blk_start = text.index('Return exactly this JSON, nothing else:')
    _open = text.index('\n{\n', _blk_start) + 1; _close = text.index('\n}\n', _open) + 2
    _block = text[_open:_close]
    _norm = re.sub(r'//[^\n]*', '', _block)                          # trailing // comments
    def _in_string(m):                                                # inside a quoted string: placeholders -> PH, raw newlines -> space
        return '"' + re.sub(r'<[^<>]*>', 'PH', m.group(1), flags=re.S).replace('\n', ' ') + '"'
    _norm = re.sub(r'"((?:[^"\\]|\\.)*)"', _in_string, _norm, flags=re.S)
    _norm = re.sub(r'<[^<>]*>', '"PH"', _norm, flags=re.S)            # bare placeholders outside strings (<integer 1-10>, <true if …>)
    _norm = re.sub(r',(\s*[}\]])', r'\1', _norm)                    # tolerate a trailing comma before a closing brace
    try:
        _parsed = json.loads(_norm)
    except json.JSONDecodeError as e:
        raise AssertionError(f'D-99(a): the emitted JSON block does not parse after placeholder normalisation: {e}')
    assert isinstance(_parsed, dict) and len(_parsed) >= 20, 'D-99(a): JSON block lost its keys'
    assert '\u2026' not in _block, 'D-99(e): literal ellipsis (U+2026) inside the JSON block — a keep-context notation leaked into field text'
    assert not re.search(r'<[^<>]*"[^<>]*>', _block), 'D-99(g): a double quote inside a <…> placeholder in the JSON block (use single quotes)'
    _ver = tuple(int(x) for x in re.search(r'v(\d+)\.(\d+)', version).group(1, 2))
    _residue = applier_residue(text, _block)                          # D-99(h): enforced since v3.18.1
    if '--no-strict-residue' not in sys.argv:                       # v3.18.1: permanently ON (D-99(h)); the flag is the escape hatch
        assert not _residue, f'D-99(h): applier residue in the prompt prose ({len(_residue)} line(s)): {_residue}'
    elif _residue:
        print(f'WARNING D-99(h) (not enforced without --strict-residue): applier residue on {len(_residue)} line(s): {_residue}', file=sys.stderr)
    if _ver >= (3, 18):
        assert 'the uploads precede the reset' in text, 'D-99(f, v3.18+): D1 grade-order sentence must state that the uploads precede the reset'
    # the second "Return exactly this JSON" occurrence is the reissue sentence, not a block (count asserted above)
    for _ln in text.split('\n'):
        assert 'CONDITIONAL' not in _ln and '[CONFIRM' not in _ln, f'D-99(b): editorial marker pasted into the prompt: {_ln[:60]!r}'
        assert not _ln.rstrip().endswith('.;') and not re.search(r'\S  \.', _ln), f'D-99(d): ".;" or double space before a period: {_ln[:60]!r}'
    for _para in re.split(r'\n\s*\n', text):                       # bold spans may wrap across lines: parity per paragraph
        assert _para.count('**') % 2 == 0, f'D-99(b): unmatched ** in paragraph: {_para.strip()[:60]!r}'
    _headers = [ln for ln in text.split('\n') if ln.startswith('## ')]
    assert len(_headers) == len(set(_headers)), f'D-99(c): duplicated section header(s): {[h for h in _headers if _headers.count(h) > 1]}'
    for _d in ('## D1.', '## D2.', '## D3.', '## D4.', '## D5.', '## D8.'):
        assert sum(1 for h in _headers if h.startswith(_d)) == 1, f'D-99(c): {_d} header count != 1'
    # ---- writes happen only now
    OUT.write_text(text)
    CAL_OUT.write_text(cal_text)
    (RA / 'rubrics/v7_calibration.tsv').write_text('\n'.join(key_lines) + '\n')
    print(f'answer key: rubrics/v7_calibration.tsv emitted, {len(key_ids)} rows; {len(pending)} calibration ids not in the key (pending/candidates): {pending}')
    n = text.count('\n')
    print(f'wrote {OUT} ({n} lines) from v6_core@{sha(CORE)} delta@{sha(DELTA)}; {version} content_sha256_16 {content_sha16}')
    # post-conditions the review asked for
    # Fable pass-2 P1: no calibration answer key and no task id (full or six-digit prefix) in the judge prompt
    print(f'wrote {CAL_OUT} (scorer-only, {CAL_OUT.read_text().count(chr(10))} lines)')
    print('post-conditions ok')


if __name__ == '__main__':
    main()
