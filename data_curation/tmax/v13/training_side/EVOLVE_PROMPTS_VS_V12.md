# The evolve loop's three agent prompts vs. our v12 audit prompt

Ref: `origin/andy/proposer-drafts-checker`, head `ac7604b7`. Third readout in the
series; companion to `ANDY_BRANCH_READOUT.md` (architecture, gates, pins) and
`ANDY_BRANCH_FOLLOWUP.md` (A1–A4 mapping, no-solution rows, harden trigger,
entry seam, pin safety). Those two are assumed, not repeated: where a gate is
named here it is named as a fact they established, and only the *prompt text* is
quoted at length.

Shorthand for the four sources, all read in full on the ref:

| tag | file (under `torchtitan/experiments/rl/examples/tmax/`) | role |
|---|---|---|
| **TE** | `evolution/agents/task_evolution.md` (336 lines) | the author / "proposer" session's AGENTS.md |
| **VA** | `evolution/agents/verifier_author.md` (246 lines) | the blind verifier session's AGENTS.md |
| **IP** | `evolution/agents/independent_verifier_probes.md` (107 lines) | the independent probe author's AGENTS.md |
| **EC** | `evolution/evolve_codex.py` | the per-job prompt constants: `_OPERATOR_HARDER_GUIDANCE` :1019, `_STUDENT_HARDER_GUIDANCE` :1047, `_CALIBRATION_GUIDANCE` :1121, `_HARDER_JOB` :1144, `_HARDER_JOB_BLIND` :1218, `_EASIER_JOB` :1294, `_REPAIR_JOB` :1298, `_REPAIR_BLIND_NOTE` :1318, `_SPEC_REPAIR_JOB` :1327, `_VERIFIER_JOB` :1359, `_VERIFIER_REPAIR_JOB` :1385, `_BUDGET` :1407 |
| **SO** | `evolution/simplify_operators.py` | the easier job's text: `CARDS` :14, `CALIBRATION_GUIDANCE` :65, `prompt()` :103-211 (this is the `{cards}` of `_EASIER_JOB`) |

**v12** = `data_curation/tmax/v12/v12_prompt.md`
(543 lines, not in the repository).

Enforcement labels used below: **GATE** = a downstream check rejects the rewrite
when the rule is broken; **ADVICE** = the loop computes it and records it without
rejecting; **STRUCTURAL** = the rule holds because of what the session is shown,
not because anything checks the output; **NONE** = only the sentence exists.

---

## 1. RULE MAP

### 1.0 Summary table

| v12 rule | their nearest rule | enforcement | verdict |
|---|---|---|---|
| Step 1 core step fixed from the instruction before the verifier | TE:61-64, VA:27, VA:49-50 | STRUCTURAL (blind split) | DUPLICATE in principle; CONFLICT on the ordering (VA:29 hands them the old verifier first) |
| Step 2 inventory of in-image material, nine exclusions | — | NONE | ABSENT |
| A1 oracle program reachable in the image | — | NONE | ABSENT (and EC:1093-1098 / TE:152-155 push material *into* the workspace) |
| A2 expectation taken from rollout-present material | VA:86-88 | NONE | CONFLICT (their sentence permits deriving from the original fixture) |
| A3 graded value derivable without the core work | VA:176-177 (self-inspection) | NONE | STATED-ONLY; CONFLICT with EC:1332-1333 / SO:141-142 / VA:69-70 |
| A4 expectation recomputed from a solver-writable file | TE:111-112 = VA:88-90, plus the replay control VA:181-185 | GATE (probe replay) where a control covers it | DUPLICATE; CONFLICT on the *pin* remedy (VA:90-91, :183-185) |
| A5 untouched image already passes | TE:225-227, TE:243-246, VA:203-204, EC:1377-1378 | GATE ×2 (null probe in `./sandbox check`; null probe in the caller's revalidate) | DUPLICATE |
| A6 / J1 a concrete complete wrong submission passes | VA:206-224, IP:61-87, VA:176-179 | GATE (replay: a `wrong-N.sh` that grades pass ⇒ one repair, then discard) | DUPLICATE, with a different search rule (per clause, not cheapest decoy) |
| A7 a core step's artefact never compared by content | VA:85-86 = TE:107-109; VA:91-94 = TE:114-117 | STATED-ONLY (`run/verifier-changes.md` is written and never read); GATE only for the *changed* clause via `wrong-N.sh` | DUPLICATE in statement, STATED-ONLY in enforcement |
| J2 secondary clauses unenforced (no effect) | VA:42-45, VA:66-70, TE:145-147 | NONE | DUPLICATE (inverse policy: they forbid *checking* them); EC:1101-1105 is stricter than J2 |
| B1 source-text grep | VA:178-180, VA:69-70 | incidental GATE (a differently-implemented `correct.sh` fails such a verifier) | STATED-ONLY, weak — no sentence says "do not grade the source text" |
| B1 exact format the instruction leaves open | VA:52-63, VA:95-153, VA:160-161, VA:238-240, TE:265-271 | GATE (`correct.sh` must pass; the control is required to use a permitted alternative representation) | DUPLICATE, and far more detailed than v12 |
| B1 wrong hardcoded literal | (no prompt sentence) EC:1390-1397 handles the disagreement | GATE (the oracle run: reference must score 1) | DUPLICATE for bred rewrites; ABSENT for seeds |
| B1 cwd-relative path, process liveness, executability/umask | not found in TE, VA, IP | NONE | ABSENT |
| B2 environment mismatch | TE:157-211, TE:329-331 | GATE (build / boot / oracle stages) | DUPLICATE |
| B2 unconfirmed verifier imports (the hold) | — | GATE by execution instead | ABSENT as a rule (they run the verifier; the hold is a static-audit-only device) |
| B3 wall-clock threshold | not found | NONE | ABSENT |
| B3 grade-time network | not found in VA or IP (zero occurrences of network/fetch/http/url); TE:324-327 covers the *task's* fetches | NONE | ABSENT on the verifier side |
| B3 flake | not found | NONE | ABSENT (each probe runs once) |
| C ambiguity | VA:39-42, IP:104-105 | GATE-as-exit (`BLOCKED:` ⇒ `Blocked` ⇒ task returns unchanged) | DUPLICATE, and their test is sharper |
| D trivial | SO:19-20, SO:38 | — | CONFLICT on disposition: a trivial task is 16/16, so the loop hardens it; EC:1332-1333 defends fixed-output tasks |
| reproduction (`repro_cmd`) | VA:206-240, IP:61-99 | GATE (every script replayed in a fresh Daytona container) | DUPLICATE, and theirs is executed rather than written |
| `verifier_fix` confined to tests/test.sh | VA:38-40 | GATE (package-hash guard around the probe author and the verifier repair) | DUPLICATE, better worded |
| four reject shapes | `pre_solved` ≈ null probe; `broken_premise` ≈ SO:143-145 + EC:1327-1357 | GATE / routed job | mixed: two DUPLICATE, `instruction_prints_answer` CONFLICT, `deliverable_is_leaked_material` ABSENT |
| tier (PASS/REPAIR/REVIEW/REJECT-PROVEN) | — | — | ABSENT (accept / reject / kept / blocked, no ladder) |

### 1.1 The rows that need the quote

**Step 1 — the instruction is the contract.** Both prompts state it, and on the
verifier side the blind layout makes it true by construction:

> "**`instruction.md`** | what the agent is told. Nothing else is shown to it.
> This is the contract you verify." — VA:27

> "Derive retained requirements from the original public instruction and files
> the solver can discover in the starting workspace, then apply the requested
> change. The reference solution and seed verifier are implementations to
> inspect, not authority for additional requirements." — TE:61-64

The narrow CONFLICT: v12 Step 1 forbids reading the verifier before fixing the
core step (v12:121-122 "From instruction.md alone, before looking at material or
assertions"), whereas VA:29 hands the verifier author the previous revision's
verifier as its starting point and tells it to "keep every inherited check whose
requirement remains in the instruction". Their anchor is the old verifier plus
the instruction; ours is the instruction alone. For a *seed* audit (no previous
revision) the two coincide.

**A1 — nothing, and two sentences that pull the other way.** No sentence in TE,
VA or IP mentions an answer-producing program left in the image (the follow-up
readout established this for the code; it holds for the prompts too). The two
sentences that make it more likely:

> "prefer a compact goal plus pointers to what is in the workspace, and put
> discoverable detail in the workspace itself." — TE:152-153

> "Adding artifacts to the workspace -- fixtures, decoy files, a second
> candidate, a stale copy, a log that contradicts a config -- is a legitimate
> way to remove such an assumption ... it counts only when the added material
> forces a decision judged by content" — EC:1093-1098

Neither is A1 (they are about discoverable *detail* and about decoy material
that must be judged by content), so this is coexistence, not contradiction — but
nothing in the loop asks whether the added material can be *invoked* to produce
the graded value, and the author is being actively encouraged to add material.

**A2 — the one real CONFLICT.** v12's A2 fires when the verifier's expectation
is the content of material present during rollout and test.sh neither hash-pins
nor regenerates it. VA offers exactly that as one of two permitted sources:

> "For supplied-data tasks, derive expectations from the original fixture or
> expected values prepared before solver execution and supplied with the
> grader." — VA:86-88

"expected values prepared before solver execution and supplied with the grader"
is our `test_only` shape and is fine. "the original fixture" is rollout-present
material and is our A2 unless test.sh pins it. The requirement that names be
agent-readable is a different axis and does **not** conflict with A2 — it
constrains names, not expected values:

> "**Every name you depend on -- a key, a label, a file name, a column --
> appears, spelled the same, in `instruction.md` or in a file under
> `environment/` that the instruction points at.**" — VA:52-54

So A1/A3 merely *coexist* with their discoverability rules (theirs is about
vocabulary, ours about answers); only A2 has a sentence pointing the other way,
and only for the supplied-data case.

**A3 — stated as self-inspection, contradicted twice elsewhere.**

> "Before finishing, inspect whether a no-op, a hardcoded answer or fabricated
> evidence could still pass, and whether an equivalent legal solution could
> fail." — VA:176-177 (verbatim twin at TE:128-130)

Nothing verifies that inspection. And two other jobs tell their agents the
opposite of our A3/`instruction_prints_answer` disposition:

> "An oracle-informed constant can satisfy a valid fixed-output task; do not add
> requirements merely to reject that possibility." — EC:1332-1333 (`_SPEC_REPAIR_JOB`)

> "An oracle-informed constant can pass many valid fixed-output tasks; this
> alone is not evidence of wrong grading or a reason to discard the task."
> — SO:141-142 (the easier job)

> "Do not infer a prohibition on copying or hardcoding." — VA:69-70

A v12 row that rejects a fixed-output task as `instruction_prints_answer` or
`deliverable_is_leaked_material` is, in their vocabulary, a task they would keep.

**A4 — duplicated, enforced, and with a forbidden remedy.**

> "Never use solver-writable replacement inputs as the authority for
> correctness; copying or hashing them at grading time does not recover the
> original data." — VA:88-90, verbatim also at TE:111-112

> "Where correctness depends on supplied data, include a replay control that
> replaces the working input and leaves an answer wrong for the original
> fixture. Reject it at the corresponding content check." — VA:181-183

That control is replayed by the caller in a fresh container and a miss is a
`SemanticProbeMisses` — so A4 is genuinely gated *for the clause the author
wrote a control for*. The CONFLICT is with v12's fix catalogue (v12:414-415
"hash-pin the material and assert it unchanged and non-empty before
recomputing"):

> "Input changes alone are not grounds for rejection unless the public task
> forbids them." — VA:90-91

> "Also execute a control that leaves a correct deliverable despite an input
> change when the public task permits that change; do not add an input
> immutability requirement to make the negative control fail." — VA:183-185

Their loop would reject our pin-the-input fix whenever the public task permits
changing the input; the acceptable form of the same fix is "derive the
expectation from a source the solver cannot rewrite", not "assert the input
unchanged".

**A5 — the most thoroughly duplicated rule on the ref.**

> "the caller re-runs the same checks afterwards from files you cannot reach — a
> fresh build, the reference solution against the verifier, and the verifier
> alone on an untouched workspace, which must fail. A verifier that passes
> without the solution pays for nothing" — TE:243-246

> "Then `reset` and `grade` the untouched workspace, which must fail. Do both
> before you finish; a verifier that was never run against a real container is a
> guess." — VA:203-204

Two independent gates run it (the author's own tool and the caller's Daytona
probe). For any task that has been through a rewrite, a v12 A5 finding is
redundant; for a seed it is not, because seed oracle validation never runs a
null probe.

**A7 — the statement is theirs, the enforcement is not.**

> "Map each retained or added requirement to a check of its promised behavior or
> result, naming the source of its expected answer." — VA:85-86 (twin at TE:107-109)

> "File existence, non-empty content, success words in a log, or agreement
> between two solver-written reports cannot alone establish correctness."
> — VA:91-93 (twin at TE:114-116)

> "Write `run/verifier-changes.md` as you go: one line per inherited check you
> kept, changed or removed and per check you added, each with the instruction
> sentence it rests on." — VA:35-37 (also `_VERIFIER_JOB`, EC:1372-1374)

`run/verifier-changes.md` is named in exactly two places on the ref, both of them
prompts (EC:1372 and VA:35); no module reads it. The mapping is therefore
STATED-ONLY. The only executed part is the changed clause, through `wrong-N.sh`.

**J2 — their policy is the inverse of ours and stricter in one place.** v12
records a non-core unenforced clause and lets it pass. VA tells the verifier not
to check such clauses at all:

> "Require a token log, saved workflow, provenance artifact, or evidence of a
> restricted method only when the public contract explicitly makes it part of
> acceptance." — VA:43-45

> "Do not add a report, execution log, or reusable-program requirement merely to
> satisfy a verifier role." — TE:145-146

But the author's hardening job is stricter than our J2 — an unenforceable method
clause must be reformulated, not left standing:

> "If correctness depends on a method or source restriction, make it enforceable
> in the runnable environment or checkable by the grader; otherwise reformulate
> it as an observable task condition." — EC:1101-1104

**B1 — their strongest area, and three shapes they have never heard of.** The
representation rules run VA:95-153 unbroken; the operative summary lines:

> "Compare content after normalizing representations the public task leaves
> open. ... Use byte equality only for content required to remain byte-for-byte
> unchanged. Normalization must preserve differences the public task forbids."
> — VA:102-108

> "Accept alternative paths, formats and implementations wherever the public
> task leaves them open." — VA:160-161

and the enforcement that makes it real:

> "If a plausible alternative implementation uses a different representation the
> instruction permits, use it for the correct control instead of requiring your
> preferred representation. The caller independently replays every saved
> script." — VA:238-240

Against that, v12's B1 list contains three shapes with no counterpart anywhere in
TE/VA/IP — a cwd-relative path in test.sh, a process-liveness assertion after a
stop, and an executability demand on a file written under the default umask. A
grep of the three prompts for `cwd|chmod|umask|executable|process` returns only
"Your working directory is the task package" (TE:11, VA:18) and the `./sandbox
exec` help lines. Not found.

**B3 — absent in all three prompts.** No occurrence of wall-clock, elapsed,
flake or nondeterminism-in-grading; `network|fetch|http|url|download` has **zero**
hits in VA and IP. The only adjacent sentences address the *solution*, not the
verifier:

> "Keep it deterministic, safe to run twice, and runnable non-interactively from
> any working directory." — TE:73-74

> "A full-state promise includes generated fields; if their defaults depend on
> the clock, compare executions at different times." — TE:80-82

**C — their ambiguity test is operational.**

> "If the public specification leaves the required outcome ambiguous, or
> explicitly requires a property the available artifacts and environment cannot
> establish, write `BLOCKED: <what, precisely>` to `run/verdict.txt` and stop."
> — VA:39-42

> "If the public specification is ambiguous enough that the two outputs cannot
> be distinguished, write `BLOCKED: <precise ambiguity>`" — IP:104-105

**D — a conflict of disposition.** A trivial task is solved 16/16, which is the
harden trigger, so the loop's response to triviality is a rewrite, not a drop;
and the simplify cards forbid *creating* one:

> "Retain a nontrivial operation, not only a constant or empty case."
> — SO:19-20 (`reduce_scale`)

> "Do not replace the task with an unrelated exercise or a trivial output."
> — SO:38 (`extract_subtask`)

**`verifier_fix` confinement — their sentence is the better one.**

> "Do not change other task files: the task is fixed, and a check that only
> passes because you changed the task is a check on nothing." — VA:38-40

**Bottom line.** Of v12's twenty-odd rules, four are fully duplicated *and*
gated on every rewrite (A5, A6/J1 through the controls, B1's representation
half, B2) and can be dropped from a bred-task audit; four are stated in their
prompts with nothing checking them (A3 self-inspection, A7's requirement→check
mapping, J2, the source-grep prohibition); seven are simply absent (A1, Step 2's
inventory, B1's cwd/liveness/umask shapes, all three B3 shapes, the tier ladder,
`deliverable_is_leaked_material`); and three actively conflict — A2's
supplied-data fixture (VA:86-88), A3/`instruction_prints_answer` versus the
oracle-informed-constant defence (EC:1332-1333, SO:141-142), and our
hash-pin-the-input fix versus VA:183-185. None of the four gated rules applies to
a seed that never entered the loop.

---

## 2. THEIR RULES WE LACK

Every requirement below is about verifier quality or task fairness, is stated in
one of the three prompts, and has no counterpart in v12. "Static-checkable"
means: decidable from `instruction.md` + `setup.sh` + `tests/test.sh` alone.

**1. Every name the verifier depends on must be agent-readable.**
> "**Every name you depend on -- a key, a label, a file name, a column --
> appears, spelled the same, in `instruction.md` or in a file under
> `environment/` that the instruction points at.**" — VA:52-54

Static-checkable: **yes, mechanically** (it is the dark-literal question), and
it is the single defect class the blind split was built for. Caveat the training
side already paid for: run as a gate it is almost all false positives — the loop
measured "the names audit flagged 130 literals without its seed baseline and
none with it, and every one of the 130 was a false positive"
(`evolution/feedback_loop.py:447-455`). Adopt it as a recorded observation, not
a floor.

**2. Where the instruction leaves a name open, check the value.**
> "Where the instruction leaves a name open, check the value instead: a report
> line that contains the commit's SHA, whichever label it is under; a field
> equal to the file's SHA-256, whichever key holds it." — VA:54-56

Static-checkable: **yes**. v12's B1 flags the name-pinning assertion but never
states the repair shape; this is the `verifier_fix` template for that whole
class.

**3. Bracketed format examples are ambiguous and both readings must pass.**
> "When brackets in a format example can denote either literal characters or
> placeholders, accept both readings unless the instruction explicitly resolves
> them; verify the enclosed values either way." — VA:60-63

Static-checkable: **yes**, and it is a concrete B1 sub-shape we do not list.

**4. Numeric comparison, as a nine-sentence catalogue.**
> "For every field the task defines numerically, compare its numeric value while
> preserving any explicitly required type. ... If a parsing task leaves a
> field's type open, a numeric string such as JSON `"1"` can preserve the same
> source value as `1` or `1.0`; accept these permitted encodings by comparing
> their numeric values. An explicit JSON-number requirement still excludes
> strings. Stringifying numbers does not normalize numeric equality. Do not
> truncate or round away a real difference." — VA:128-135

> "Preserve submitted precision from the first parse: converting an already
> rounded floating-point value to a decimal type cannot recover discarded
> digits." — VA:113-114

Static-checkable: **yes**. v12 mentions "float formatting" in one clause of B1
and nothing else. Both failure directions are here: rejecting a legal encoding
(our B1) and accepting a wrong value because the comparison normalized the
difference away — which v12 has **no rule for at all** on side A (it would reach
J1 only if we exhibit the complete wrong submission).

**5. Tolerance must be derived from the public requirement and its basis recorded.**
> "Derive any tolerance from the public requirements and permitted encoding
> error, and record the bound and its basis in the replay contract. Encoding
> resolution alone does not justify an error allowance. Unless the public task
> permits error, preserve an exact expected value when the required operation
> and chosen encoding introduce no error" — VA:135-140

> "Check whether an overly broad bound would accept that wrong value instead of
> choosing only a larger error the grader rejects." — VA:145-146

Static-checkable: **yes** — this is v12's `literal_derivation` extended from
"is the literal right?" to "is the *window* narrow enough to reject the adjacent
wrong value?", which v12 never asks. Strong candidate to adopt verbatim.

**6. Metadata and checksums do not establish content.**
> "A checksum matching a submitted file establishes their agreement, not that
> the file contains the required content. Output format, dimensions, channel
> count, or other metadata alone cannot establish that correspondence."
> — VA:99-102

Static-checkable: **yes**; it is an A7 sub-shape sharper than our (a)/(b)/(c).

**7. Boundary coverage, including the empty case, inside the verifier.**
> "For reusable programs, test empty inputs when permitted and nonempty inputs
> with no qualifying results; these exercise different behavior. In each case,
> check retained output requirements, such as required headers or schema even
> when there are no records. Check these properties before parsing or
> normalization discards them; an empty parsed collection alone does not prove
> that the required output structure exists. ... Include the empty-input
> invocation itself in the verifier, not only in an author check." — VA:163-172

Static-checkable: **partially** — we can see which cases test.sh exercises; we
cannot always tell which boundaries the public task permits. Worth a recorded
observation ("the verifier exercises one input shape only").

**8. The reusable-program contract: fresh grader inputs, expectations derived first.**
> "If the task requires a reusable program, run the submitted program through the
> specified entry point on fresh valid inputs prepared by the grader, with
> expected outputs derived before invoking the submitted program. Ensure
> retained outputs cannot let a no-op program pass, and restore inputs after the
> check." — VA:155-158 (twin at TE:119-123)

Static-checkable: **yes**. "expected outputs derived before invoking the
submitted program" is A4 stated as a *requirement on the verifier's order of
operations* rather than as a defect, and "fresh valid inputs prepared by the
grader" is v12's fix-catalogue entry promoted to a rule. v12 has no rule
distinguishing a deliverable that is exercised on grader-chosen inputs from one
merely read.

**9. Never invoke `solution/solve.sh` from the verifier.**
> "Never invoke `solution/solve.sh`: it is not there when the agent runs, and it
> is not there for you either. Invoke the workflow the way the instruction tells
> a user to." — VA:81-83 (twin at TE:124-125)

Static-checkable: **yes, trivially** (grep the verifier for the solution path).
v12 has no such rule; it would land as a B2/A1 hybrid. For the TMAX corpus this
matters less (no solution ships) but the seed corpus has them.

**10. Solver-written evidence proves nothing.**
> "Solver-written claims do not prove that a required execution, measurement or
> tool interaction occurred." — VA:93-94 (twin at TE:125-126)

Static-checkable: **yes**, and it names a shape v12's A7 does not enumerate:
the verifier reading the agent's own log/report as proof that the work happened.

**11. A passing reference proves nothing about rejection.**
> "A reference solution passing does not establish that incorrect solutions are
> rejected." — TE:116-117; and the simplify side, "An incorrect retained
> behavior accepted by the verifier requires repair, even if the reference also
> passes." — SO:207-208

Static-checkable: n/a (it is a principle, and it is exactly v12's reason to
exist). Worth quoting in v12 §1 as the justification for side A.

**12. Negative controls per independently falsifiable clause; positive control too.**
> "Save your public-instruction-only solution as `run/verifier-probes/correct.sh`
> ... Split the changed requirement into its independently falsifiable clauses.
> For each clause, save `wrong-1.sh`, `wrong-2.sh`, and so on: each script
> produces a nonempty, well-formed result but violates that clause alone. Check
> every clause of the changed dependency, including its validity conditions,
> rather than stopping after finding one error the verifier rejects. For
> example, selecting the latest valid result requires both choosing the latest
> result and rejecting invalid ones." — VA:206-213

Static-checkable: **as a design, yes; as a demonstration, no.** v12 writes one
`repro_cmd` for the highest-ranked fired rule and one `pass_probe`. Adopting the
clause decomposition would replace "the cheapest decoy" with "one probe per
falsifiable clause of the core step", which is a better instrument for A7.

**13. A control's claim must be justified against the public clause, not against the grader.**
> "Before saving a wrong control, identify the public clause its final
> deliverable violates after all permitted normalization. A type-only difference
> needs an explicit type requirement in that clause. Your grader rejecting an
> output does not establish that it is wrong; record the violated clause and the
> remaining value or structural difference in the contract before the caller
> freezes and replays the control." — VA:216-221

Static-checkable: **yes** — it is a discipline on how we *write* a `repro_cmd`
and a `pass_probe`, and v12 has no equivalent guard against a probe that "wins"
for an incidental reason.

**14. A control must not be defeated for the wrong reason.**
> "Ensure the distinguishing input affects the output: two identities that
> collapse to the same node cannot test an edge weight. When testing rejection
> of invalid inputs, include a case that violates the targeted validity
> condition while satisfying the others. An input with multiple defects can be
> rejected for the wrong reason." — VA:232-237

Static-checkable: **yes**, as a rule on our `pass_probe` and `repro_cmd`.

**15. A crash is not a semantic error.**
> "A script that crashes or never produces the requested artifact does not
> demonstrate a semantic error. A successful setup must exit zero; propagate
> setup errors instead of masking them with `exit 0`." — IP:85-87

Static-checkable: **yes** — v12's J1 says "complete wrong submission" without
saying what disqualifies one.

**16. Pin anything fetched; fail a failed fetch loudly.**
> "**The network is available and may be part of a task.** Pin anything fetched
> (URL plus checksum or version) so the reference solution is reproducible, and
> make a failed fetch fail loudly: non-zero exit, the error on stderr, no silent
> fallback. No credentials, nothing that only works through a proxy."
> — TE:324-327 (expanded at EC:1272-1279)

Static-checkable: **yes** for setup.sh and tests/test.sh (an unpinned fetch, a
bare URL without a checksum, an `except` branch that supplies the value the
`try` was fetching). v12's B3 covers only "test.sh reaches a host other than
loopback at grade time"; it has no rule for an unpinned fetch during *rollout*
and none for the silent-fallback shape — which is exactly
`evolution/scan_degenerate_graders.py`'s `fallback` pattern and one of the three
things that offline scanner looks for.

**17. Size and shape limits on the shipped package.**
> "COPY sources together stay under 1 MiB, and files under `tests/` are text and
> together stay under 1 MiB; a binary under `tests/` is refused by name"
> — TE:31-33 (twin at EC:1263-1268, which adds: "a large reference the verifier
> needs is checked by hash, not shipped")

Static-checkable: **yes** for the `tests/` half (we read that tree). v12 never
looks at the verifier's size or at whether it ships binary fixtures.

**18. The step-size rule.**
> "In student-guided mode, the reference solution may stay the same length or
> shrink, and may grow by at most 8 non-comment lines. In operator mode it must
> grow by 3 to 8 lines. The verifier may gain at most 5 assertions in either
> mode." — TE:257-260; and "Add what the new requirement needs, and no more: at
> most 5 assertions over the seed's count (`run/seed_size.json`). One
> requirement is two or three." — VA:64-65

Static-checkable: **no** from three files of one revision (it is a diff against
the seed). But v12 could record the assertion count, which is the input that
rule consumes.

**19. Solvability from the workspace alone, alternatives preserved.**
> "The task must stay solvable from the workspace alone. Someone reading only
> `instruction.md` and exploring the container must be able to get there. Never
> remove facts needed to satisfy a public requirement. Where the original task
> permits several correct outcomes, preserve those alternatives and check their
> shared requirements rather than choosing the reference implementation for the
> student." — TE:312-317

Static-checkable: **yes** as a reading of the instruction against the assertions;
it is the generative form of our B1 and reads better than our enumeration.

**20. An unstated exact-string requirement is a specification defect.**
> "A reasonable interpretation rejected by an unstated exact-string or
> formatting requirement is a specification defect, even when a hint could make
> the test pass." — SO:132-134

Static-checkable: **yes** — and note where it sits: in the *easier* job, i.e.
the loop treats this as grounds for repair rather than simplification.

**Bottom line.** The evolve prompts carry three things v12 does not check at
all: the numeric/representation catalogue (VA:95-153), the control-design
discipline (VA:206-240, IP:61-99), and the fetch-pinning rule (TE:324-327). Of
the twenty items above, sixteen are decidable from our three files; the four that
are not (step size, inherited-check retention, boundary permissibility, resource
sizing) are all diff- or environment-shaped. The highest-value adoptions are #5
(is the acceptance window narrow enough to reject the adjacent wrong value?), #8
("expected outputs derived before invoking the submitted program"), #9 (the
verifier invoking a path absent at grade time) and #16 (unpinned fetch / silent
fallback).

---

## 3. WORDING WE SHOULD BORROW

Ten sentences, theirs against ours, ours cited by v12 line.

**1. Fix confinement.**
- Theirs (VA:38-40): "Do not change other task files: the task is fixed, and a check that only passes because you changed the task is a check on nothing."
- Ours (v12:409-413): "`verifier_fix` is a change confined to tests/test.sh that closes a recorded mechanism while a faithful solution still scores 1 … A change *closes* a mechanism when, under the changed test.sh, your reproduction scores 0 (an A rule) or the faithful solution scores 1 (a B rule) while a faithful solution still scores 1".
- Theirs gives the *reason* in eleven words; ours gives the test. Keep ours, prepend theirs.

**2. The generative form of B1.**
- Theirs (VA:49-50): "Every check you add must be satisfiable by an agent that reads `instruction.md` and explores the container."
- Ours (v12:316-330): a fifteen-line enumeration of assertion shapes.
- Their sentence is the predicate our enumeration approximates; state it first, then enumerate as examples.

**3. Names versus values.**
- Theirs (VA:54-56): "Where the instruction leaves a name open, check the value instead: a report line that contains the commit's SHA, whichever label it is under; a field equal to the file's SHA-256, whichever key holds it."
- Ours (v12:321-323): "an exact string or byte comparison where the instruction permits other forms (key order, whitespace, float formatting, line endings, indentation, value types the instruction does not fix); a filename or path the instruction only suggested".
- Theirs states the fix and gives two examples; ours only lists the defect.

**4. What cannot establish correctness.**
- Theirs (VA:91-94): "File existence, non-empty content, success words in a log, or agreement between two solver-written reports cannot alone establish correctness. Solver-written claims do not prove that a required execution, measurement or tool interaction occurred."
- Ours (v12:271-274): "every assertion that touches S checks only (a) existence or executability, (b) a count, shape, format or regex, or (c) a value the instruction's own text supplies *in place of a result the agent must compute*".
- Theirs names two shapes ours misses (success words in a log; two solver-written reports agreeing) in one sentence.

**5. Why side A exists.**
- Theirs (TE:116-117): "A reference solution passing does not establish that incorrect solutions are rejected."
- Ours (v12:18-21): "A verifier is sound iff (A) the reward cannot be obtained without performing the core work the instruction asks for, and (B) a competent, faithful solution the author did not anticipate is not refused."
- Ours defines; theirs motivates. One line, worth adding to §1.

**6. Content versus agreement.**
- Theirs (VA:99-102): "A checksum matching a submitted file establishes their agreement, not that the file contains the required content. Output format, dimensions, channel count, or other metadata alone cannot establish that correspondence."
- Ours (v12:38-40): "**content comparison** — an assertion comparing a value by its content: equality, containment of a stated value, or a tolerance band around one; existence, executability, count, shape, format and regex checks are not."
- Ours defines the category; theirs supplies the two cases auditors actually miss (a self-consistent checksum, and metadata equality).

**7. What disqualifies a wrong submission.**
- Theirs (IP:85-87): "A script that crashes or never produces the requested artifact does not demonstrate a semantic error. A successful setup must exit zero; propagate setup errors instead of masking them with `exit 0`."
- Ours (v12:293-296): "You exhibit a concrete, complete wrong submission that satisfies EVERY assertion (a decoy of the right shape, a stub returning the right count, an existence-only artefact), built from rollout-visible facts only".
- Ours says what the submission must satisfy; theirs says what disqualifies it, which is the half we keep re-litigating.

**8. A probe must fail for the intended reason.**
- Theirs (VA:232-237): "Ensure the distinguishing input affects the output: two identities that collapse to the same node cannot test an edge weight. … An input with multiple defects can be rejected for the wrong reason."
- Ours (v12:443-448, the PASS conditions): "`pass_probe` is written — the strongest non-solution you considered … and the assertion (anchor first) that defeats it".
- Ours never asks *why* the assertion defeated it. Theirs supplies the missing clause.

**9. The ambiguity test.**
- Theirs (IP:104-105): "If the public specification is ambiguous enough that the two outputs cannot be distinguished, write `BLOCKED: <precise ambiguity>`".
- Ours (v12:363-364): "a necessary detail is missing, two equally reasonable readings produce different outputs, only one is accepted, and this prevents deciding A/B — several valid solution paths are not ambiguity."
- Ours is right and long; theirs gives the operational version: can you build two deliverables the spec cannot separate?

**10. The auditor is the agent the verifier must be fair to.**
- Theirs (VA:199-204): "**Do the task yourself, through `exec`, the way the instruction describes it, and then `grade`.** You are the agent this verifier has to be fair to: if you, reading only the instruction, cannot reach a state your own verifier accepts, neither can the policy, and the check that stopped you is the one to fix."
- Ours (v12:18-21 (B), 316-330): the B-side is defined but never given a procedure.
- We cannot execute, but "read only the instruction, write the deliverable you would write, and ask which assertion rejects it" is precisely a static B1 procedure and we do not state one. See also TE:298-300 for the counterweight — "The outcome that actually damages the pool is a task that passes because the check got weaker … nothing downstream can tell that the verifier used to demand more" — which is the crisp form of our v12:432-434 rule that dropping the assertion carrying the premise is not a fix.

**Bottom line.** Their prose is consistently better where a rule has a *reason*
or a *procedure* (1, 2, 5, 10) and where a defect has canonical examples (3, 4,
6, 7). Ours is better where a rule needs a decision boundary (the A1/A3 split,
the exclusion table, the tier logic) — theirs has no such machinery to compare.
Borrowing is one-directional: add their sentences as the lead-in to our
paragraphs, keep our predicates as the test.

---

## 4. WHAT THEIR VERIFIER AUTHOR WOULD DO WITH OUR FINDINGS

The blind split, as established in the earlier readouts: the verifier author's
whole world is `instruction.md`, `environment/`, the *previous* revision's
`tests/`, and four allowlisted files under `run/` (`seed_size.json`,
`resources.json`, `seed_literals.json`, `pretest.json`). Never the solution,
never the author's draft, never traces. Its prompt states the enumeration as its
world (VA:25-32) and the withholding as deliberate (VA:8-16).

One corpus-level caveat before the table. Our findings anchor in
`instruction.md`, `setup.sh` and `tests/test.sh`. The verifier author is shown
`environment/` — "the Dockerfile and every file the image ships" (VA:28) — but
for the TMAX half **setup.sh is not a file it can read**: the corpus ships it
"`setup.sh` (ignored -- already baked into the image)"
(`torchtitan/experiments/rl/examples/tmax/prepare_tmax_data.py:16`). A v12
finding whose only anchor is `setup.sh:<line>` therefore cites something outside
that session's view even though its *effects* are inside the container. Such a
finding must be restated in terms of the container state, or routed to the task
author.

| v12 field | who may see it | why | which paragraph it attaches to |
|---|---|---|---|
| `core_step` | **verifier author** (and the task author) | derived from `instruction.md` alone, which is the verifier author's contract (VA:27) | VA:85-86 "Map each retained or added requirement to a check of its promised behavior or result, naming the source of its expected answer" — the core step is the requirement that mapping must not miss. Also `_VERIFIER_JOB`, EC:1369-1372 |
| `core_clause_unenforced` (A7) | **verifier author** | both halves are inside its view: the clause is an instruction sentence, the unenforcing assertion is in the previous revision's `tests/`, which it is shown (VA:29) | VA:29 (the `tests/` row: "keep every inherited check whose requirement remains in the instruction, correct or remove one whose requirement changed or went") and VA:91-94. This is the single most natural insertion point of any v12 field |
| `oracle_reachable` (A1) | **task author only** | the path itself is in `environment/` and is visible, but the remedy — remove, neuter or `chmod -x` the program — is forbidden to the verifier author by VA:38-40 ("Do not change other task files"). Handing it the finding would be handing it a defect it cannot repair | TE has no paragraph for it either; the nearest home is the "Rules that always hold" section (TE:305-331) or `_HARDER_JOB_BLIND`'s environment bullets (EC:1257-1290). A new sentence is required |
| `expectation_revealed` (A2) | **verifier author** | the material is in `environment/`; the remedy is entirely inside `tests/` (pin it, regenerate it, or ship the expectation) | VA:86-90, the supplied-data paragraph — which is also the paragraph the finding contradicts, so it must be attached there or the two will disagree |
| `value_derivable` (A3) | **neither, as written** | the *path* is visible, but our A3 string is `V = f(A)`: it names the graded value and the function that produces it. Telling the verifier author the value is handing it the answer it was blinded from; telling the task author is fine | task author, TE:305-331. If it must reach the verifier author, strip V: "material at `<path>` yields the graded value without the core step" is admissible; "V = 42" is not |
| `expectation_movable` (A4) | **verifier author** | M is a rollout-present file inside `environment/`; the remedy is a change to how `tests/` derives its expectation | VA:86-90 and the replay-control paragraph VA:181-185. **Phrase it as "derive the expectation from a source the solver cannot rewrite", never as "assert the input unchanged"** — VA:183-185 forbids adding an input-immutability requirement |
| `overspecific_check` (B1) | **verifier author** | the line is in the previous revision's verifier, which it reads, and correcting it is literally its job description | VA:57-63 "Correct an inherited assertion that excludes a permitted representation while preserving its check of the required content", and `_VERIFIER_REPAIR_JOB` EC:1390-1393 "a check that demands something the instruction does not ask for, or a name the instruction leaves open: loosen that check to what the instruction actually promises, or check the value instead of the name" |
| `unstable_reward` (B3, timing) | **verifier author** (admissible) — but there is **nowhere to put it** | the assertion is in the previous verifier, so the fact is inside its view | not found: no paragraph in VA or IP mentions wall-clock, elapsed time, flake or the network. It would need a new bullet in "What the verifier has to hold" (VA:47-107). The nearest existing neighbours are the tolerance paragraph (VA:135-141) and the coverage paragraph (VA:163-172), neither of which is about stability |

Two structural notes for whoever wires this up. First, the provenance rule: the
seam exists (`run/` is the one-way tray, four names allowlisted for the blind
side) but nothing enforces where a `run/` file's *content* came from — that is
the writer's obligation, as the follow-up readout established. A findings file
that mixes A3's `V = f(A)` into the verifier author's copy silently breaks the
blind split the loop paid for. Second, every v12 field is derived from three
files the verifier author can reach in substance (instruction, image state, the
task's verifier), so the split is respected *by construction for all fields
except A3's value and any finding anchored only in setup.sh*.

**Bottom line.** Five of our seven finding types (core_step, A7, A2, A4, B1) are
inside the blind verifier's permitted view and land on paragraphs that already
exist — A7 on VA:29 and B1 on VA:57-63 need no new prose at all. A1 and A3
belong to the task author, because their remedies are edits the verifier author
is forbidden to make and because A3's text carries the answer. B3 is admissible
but homeless: their verifier prompt has no sentence about timing, flake or the
network, so a timing finding needs a new paragraph, not an attachment.

---

## 5. SEED-FACING CONSEQUENCES

Read from the prompts alone: what makes the author's and the verifier author's
jobs succeed or fail when the seed is what it is.

**Hidden vocabulary in the seed's solution or verifier — the failure the split
was built for.**
> "A verifier written beside the solution inherits the solution's private
> vocabulary: the key names of the report it happens to write, the label its
> regex anchors on, the file name it chose for an artifact. An agent that does
> every bit of the work and names one of those differently then scores zero, and
> the task is lost as "too hard" when it was unfair. Of eight hardened tasks
> reviewed that a policy failed 16 of 16 times, five failed on exactly this,
> three with all the work done." — VA:8-14

A seed whose instruction under-specifies names does not merely produce an unfair
verifier: on the blind path the requirement is *silently dropped*, because the
verifier author cannot check what it cannot name. The author is told so directly:

> "And a name only your solution knows will not be checked: state it in the
> instruction, or make the result checkable by value." — EC:1251-1252

**Fixed literals that a blind author cannot recompute.** The verifier author is
told to keep the seed's checks whose requirements survive (VA:57-59). If a seed's
assertion compares against a constant the instruction never states and the
environment never reveals, the blind author has two moves — keep it (inheriting
a literal it cannot verify) or drop it (losing the requirement). If it keeps a
literal the reference cannot satisfy, the loop's reconciliation catches it and
the repair job says to blame the solution, not the check:

> "If the run failed a check the instruction does require, the solution is what
> is wrong: write `BLOCKED: <which check, and what the run showed>` to
> `run/verdict.txt` and stop, and the caller sends the solution back."
> — EC:1394-1397

So a seed carrying an unverifiable literal costs a repair round at best and a
discarded rewrite at worst.

**Verifiers that grade source text.** The independent probe author is *required*
to vary the implementation:
> "Where permitted, use different internal names or equivalent data
> representations instead of relying on conventional defaults; preserve every
> explicitly required name, type, ordering rule, and interface." — IP:13-16

and the verifier author is required to accept such a control:
> "If a plausible alternative implementation uses a different representation the
> instruction permits, use it for the correct control instead of requiring your
> preferred representation." — VA:238-240

A seed whose verifier greps the submitted source therefore fails its own
positive control the first time it is bred, which is a `SemanticProbeMisses`:
one repair attempt, then the rewrite is discarded. Source-grep seeds are
expensive to breed, and the cost lands as a lost rewrite, not as a report.

**Services, live external systems and the network.** The spec-repair job is the
only place a live system is discussed, and its answer is to stop:
> "If a live external system cannot be provided faithfully, leave the task
> blocked and explain why." — EC:1339-1340

For the network the rule is pin-or-fail-loudly:
> "Pin anything fetched (URL plus checksum or version) so the reference solution
> is reproducible, and make a failed fetch fail loudly: non-zero exit, the error
> on stderr, no silent fallback. No credentials, nothing that only works through
> a proxy." — TE:324-327

A seed that fetches unpinned is inherited unpinned — nothing regenerates a pin
for a bred child — and the only instrumentation added on this branch is the
boot-time reachability probe, which is diagnostic, not a gate:
> "The harness records what each sandbox could reach at boot, so a task that
> needs the network is diagnosable when a cluster's egress differs."
> — EC:1276-1278

**Size: the seed's own dimensions are the budget for every child.**
> "The seed has {seed_lines} non-comment solution lines; the verifier may gain at
> most {max_asserts} assertions over the seed's {seed_asserts}. `./sandbox
> check` fails outside that and the caller rejects the rewrite. Measured on this
> corpus: rewrites that grew to 125 lines came back 0/16 five times in six,
> while the seed at its own size was solved every time." — EC:1150-1154

and the measured consequence of a *small* seed verifier:
> "Measured on the first paired round, that was the one repair of six blind
> rewrites: ten assertions against a seed's four, trimmed to pass."
> — EC:1402-1404

A seed with four assertions caps its child at nine; a seed whose solution is
already long has almost no growth room (+8 lines student-guided, +3..8 operator
mode, TE:257-260). Seeds that are already at the top of both budgets cannot be
hardened at all — the honest outcome is the give-up.

**Instruction shape, from both directions.** Too much detail teaches
shortcutting:
> "Dumping absolute paths, schema fields, exact formats or numbered operational
> steps is what teaches an agent to shortcut instead of work … Roughly three
> absolute paths is the budget — the entry point and the main deliverable — and
> never an inventory of intermediate artifacts." — TE:150-155

Too little makes the task uncheckable blind (VA:52-54, above). The seed property
that makes both jobs succeed is a *compact goal whose every graded name is
either stated or discoverable in the image* — and that is the same property v12's
A2/A3 push against from the other side, which is why the two instruments must be
read together rather than merged.

**Environment fragility, priced.**
> "End-of-life distributions (`vault.centos.org`, `archive.debian.org`, Ubuntu
> 14.04/16.04) serve from archive mirrors that are slow and intermittently gone.
> 113 tasks in this corpus are marked fragile for this alone." — TE:174-176
> "`:latest` or an untagged base moves under you and several are amd64-only. 26
> more." — TE:177-178
> "A rolling distribution upgraded at build time (`pacman -Syu`) fails every
> build for as long as any upstream breakage lasts. 7 more." — TE:179-180
> "The harness needs `tmux` inside the container. … an image with neither `tmux`
> in its repositories nor a C compiler leaves the agent unable to take a single
> turn — every rollout in the group scores zero having done nothing."
> — TE:164-168
> "25 tasks in one run built correctly and then never reached running state,
> costing 1,172 creates between them." — TE:204-205
> "five tasks declaring 16 GiB against an 8 GiB cap produced 704 refused creates
> before anyone noticed." — TE:190-192

The author is told to keep the seed's base image (TE:171-172, EC:1269-1271), so
every one of these is a property the seed hands down unchanged.

**Pins the seed should carry.**
> "If the public instruction requires files or command outputs to remain
> unchanged, list them in `tests/protected_paths.json` as `{"paths": [...],
> "cmds": [...]}`; the harness digests them before and after the episode and any
> change scores 0." — TE:35-38

This is the one per-task artefact an external audit can ship that the pipeline
reads back unchanged — and a seed that needs such a pin and lacks it has no
protection at all for its fixtures, which is the enforcement half missing from
A4.

**Bottom line.** Four seed properties decide whether the loop can do anything
with a task: every graded name stated or discoverable (or the requirement is
silently dropped by the blind author); a verifier that grades behaviour rather
than source text (or the positive control kills the first rewrite); headroom
under the seed's own line and assertion counts (or every hardening is a give-up);
and a base image that still builds (113 + 26 + 7 tasks already fail this). A v12
audit that reports these four for a seed is reporting exactly what the loop
cannot discover for itself — it only ever sees a task after the rewrite has
already been paid for.
