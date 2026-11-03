# Citation check: external reviewer's twelve claims

Read-only verification against the clone at
`<reaudit repo>/external/torchtitan-cotrain`.

- `H` = `ac7604b7284a1e6636ac6b52269ec14c70cb3e36` (branch `andy/proposer-drafts-checker`) — resolves as written.
- `B` = `16dc0a8df15bbcea85efd1ab542e261e6b586317` (branch `yichuan/qwen35-port-cotrain`) — resolves as written; `git rev-parse origin/yichuan/qwen35-port-cotrain` returns the same sha, so no substitution was needed.
- `T` = `torchtitan/experiments/rl/examples/tmax`.

Every cited file exists at the commit it is cited against. Line numbers below are the real
numbers in the blob at that commit.

## Summary

| # | Verdict | Note |
|---|---------|------|
| 1 | CONFIRMED | Docstring says the output "is not a root's live mix"; the only writes are `args.out` + its manifest, and only after `--apply`. |
| 2 | CONFIRMED | Release is 1,748 rows = 1,317 SWE-Rebench + 431 TMax `longlongcheck`; env has group size 12, hot reload 0, evolution signals 0. Same README elsewhere (:211-214) names a *separate* 3,321-row release that does contain the 452-task reaudit split. |
| 3 | CONFIRMED | Probe session gets no tests (stub) and no solution; the spec says derive the mistakes "from the public requirements", with no scoping to a changed clause — "ALL" is the reviewer's gloss, the text simply sets no narrower scope. |
| 4 | CONFIRMED | `REPAIR_ROUNDS = int(os.environ.get("EVOLVE_REPAIR_ROUNDS", "3"))`, author-then-verifier per iteration; the probe-miss path is `range(2 if allow_repair else 1)` = one repair then raise. |
| 5 | CONFIRMED | `status` is set purely from `test.exit_code == 0`; the string "reward" does not occur anywhere in the file. |
| 6 | CONFIRMED | The chain resolves end to end and grading passes `baseline_digests=baseline`; the "newer" (relative-age) part of the claim is not established by the cited lines, only the existence of the path. |
| 7 | CONFIRMED | All three parts hold: `-d` branch digests a recursive `find -type f` listing; `shlex.quote` makes a wildcard a literal path; `differences()` compares `ABSENT == ABSENT` as equal and never raises. Cited `:87-103` is the list-validation helper, not the directory logic — that sits at `:138-140`. |
| 8 | CONFIRMED | `setup.sh` is listed in the package layout, the extractor copies every tar member unconditionally, and `HIDDEN_FROM_VERIFIER` does not include it. |
| 9 | CONFIRMED | `_blind_layout(vpkg, probe)` then stub `tests/`; the claim omits that two of the four run/ allowlist files (`seed_size.json`, `seed_literals.json`) are explicitly unlinked afterwards, so a newly allowlisted file reaches the probe only if it is not added to that unlink list. |
| 10 | CONFIRMED | H suppresses the all-fail/zero-turn group (plus advisory); B removes that special case, adds `evolution_easier_ratio` (bounds-checked, `[0, harder)`), and `config_registry` defaults it to 0.0. `evolution_harder_ratio` already existed in H. |
| 11 | CONFIRMED | `--min-rate` is a bare `float`, default 0.9, no bounds check; selection is `rate >= a.min_rate`; `rev == 0` tasks are skipped; the staged signal's `direction` is hardcoded `"harder"`. |
| 12 | CONFIRMED | "the completed Qwen3.5-9B baseline", ReBench-only 1,317 tasks, 200 steps, `SWE_DATA_HOT_RELOAD=0`. |

---

## 1. `H:T/evolution/build_mix_v2.py:26-33`, `:368-376`

CONFIRMED.

```
30:failed to resolve. The output is a seed file for `new_root.py --mix`, written
31:through layout.write_mix; it is not a root's live mix. Run on della.
```

```
368:    if not args.apply:
369:        print("dry run -- pass --apply to write")
370:        return
371:    out = args.out
373:    layout.write_mix(out, [json.dumps(r) for r in rows])
```

A grep for writes in the whole file finds only lines 373 and 375 (`layout.write_json_atomic`
for `<out stem>.manifest.json`), both after the `--apply` guard — so the claim is right, with
the small omission that the manifest beside `--out` is a second written file.

## 2. `B:T/README_SEED_DATA.md:168-188`, `B:T/runbook/rebench_c1000_3t4g1e.b300.env:18-46`

CONFIRMED.

```
183:`release/mix.jsonl` is 1,748 rows: 1,317 SWE-Rebench and 431 TMax, the latter
184:from the `longlongcheck` split, whose tasks carry the repairs published on
185:2026-09-14. `fetch` checks every extracted file against the release manifest and
```

```
31:SWE_DATA_HOT_RELOAD=0
39:SWE_EVOLUTION_SIGNALS=0
46:SWE_GROUP_SIZE=12
```

Overstated/omitted: the parenthetical "(not a 452-task reaudit split)" is true of *this*
release, but the same README at `:211-214` says the repository "also holds a 3,321-row release
combining SWE-Rebench, the earlier 452-task TMax `reaudit` split and SWE-Smith", and the env
file's own header line 1 reads "train SWE-Rebench (+ TMax reaudit mix)" while line 18 says the
run reads `$TRL_BASE/data/mix/live.jsonl` rather than a pinned `RL_DATA`, so the overlay does
not by itself fix which corpus is trained.

## 3. `H:T/evolution/agents/independent_verifier_probes.md:1-20`, `:61-68`; `H:T/evolution/evolve_codex.py:1600-1625`

CONFIRMED.

```
 3:Design replay controls for the task described by `instruction.md` and the files
 4:available in its environment. The grading tests and reference solution are not
 5:provided. Work inside this package; use the container to investigate the public
 6:task and test your implementations. The file `tests/test.sh` is a placeholder,
```

The sentences that define what the negative controls must cover:

```
61:Then save `wrong-1.sh`, `wrong-2.sh`, and so on. Each wrong implementation should
62:make a specific semantic mistake while otherwise completing the task. Derive
63:these mistakes from the public requirements, including independently falsifiable
64:validity conditions. Identify what the public task requires the agent to leave:
65:a final artifact, or a program explicitly required to handle further inputs.
```

The harness side matches: a separate `session(rewrite, "probe", ...)` builds the package from
the blind package, deletes `tests/` and substitutes a stub, and swaps in this spec as
`AGENTS.md`:

```
1605:            _blind_layout(vpkg, probe)
1606:            shutil.rmtree(probe / "tests")
1609:                "#!/bin/sh\nmkdir -p /logs/verifier\necho 0 > /logs/verifier/reward.txt\nexit 2\n"
1613:            shutil.copy2(
1614:                VERIFIER_SPEC.with_name("independent_verifier_probes.md"),
```

Overstated only in wording: the spec never says "all" requirements and never contrasts with
"the clause a rewrite changed" — it says "the public requirements" with no narrowing, and it
does bound the evidence to this package (`:8-10`, "Use only this package as task evidence").

## 4. `H:T/evolution/evolve_codex.py:1821-1868`, `H:T/evolution/feedback_loop.py:920-1023`; probe-miss at `:1655-1705`

CONFIRMED.

```
 959:REPAIR_ROUNDS = int(os.environ.get("EVOLVE_REPAIR_ROUNDS", "3"))
```

```
1844:    for i in range(1, rounds + 1):
1852:        # 1. the author, without the blind verifier
1853:        task = _resume_author_blind(rewrite, task, text[-4000:], code, seq=i)
1859:        # 2. the verifier's author, with the run against the repaired solution
1860:        rel = _blind_repair(rewrite, vsession, fmap, text[-4000:], code)
```

`feedback_loop.py` runs the same bound on the post-revalidate oracle-repair loop:

```
 925:        rounds = int(os.environ.get("EVOLVE_REPAIR_ROUNDS", "3"))
 926:        for iteration in range(1, rounds + 1):
```

And the independent-probe path really is one repair then raise:

```
1655:    for attempt in range(2 if allow_repair else 1):
1663:        except SemanticProbeMisses as error:
1664:            if not allow_repair or attempt:
1665:                raise
```

Omitted: both loops are additionally capped by `rewrite_budget_sec()` (evolve_codex `:1845-1851`,
feedback_loop `:931-945`), so fewer than three iterations run on an old rewrite; and the
feedback_loop loop only engages for stages `daytona_oracle`/`step_size` (`:929-930`).

## 5. `H:T/evolution/oracle_validate_seeds.py:109-127`

CONFIRMED.

```
120:        test = sandbox.process.exec(
121:            "bash -lc 'cd /app 2>/dev/null || cd /; bash /oracle/tests/test.sh'",
122:            timeout=TEST_TIMEOUT)
123:        rec["test_exit"] = test.exit_code
124:        rec["status"] = "pass" if test.exit_code == 0 else "fail"
```

`git show H:<file> | grep -n reward` returns nothing: the file never mentions a reward file at
all, so the claim's consequence (a verifier writing reward 0 and exiting 0 records as pass)
follows directly. Note this script also uploads only `solution/` and `tests/` (`:109-111`) and
runs no pretest hook or integrity baseline — it is the older standalone oracle, not the
release path of claim 6.

## 6. Newer baseline-aware release path

CONFIRMED (chain), with the "newer" ordering unverified from these lines alone.

`run_release_oracle.sh`:

```
26:    exec "$oracle_python" "$oracle_here/environment_sweep.py" run \
27:      --output "$oracle_output/frozen" --label "$(basename "$oracle_output")" \
```

`prepare_release_oracle.py`:

```
107:    sweep.freeze(
108:        SimpleNamespace(
109:            output=frozen,
110:            mix=source / mix_name,
111:            packages=[packages],
```

`environment_sweep.py`:

```
186:                    result = await dr.probe(
187:                        args.output / "packages" / tid,
190:                        resources=resources,
191:                        prepared_row=payload["row"],
192:                        check_terminal=True,
```

`daytona_revalidate.py`:

```
239:    row = (
240:        prepared_row
241:        if prepared_row is not None
242:        else pack.to_row(str(pkg), pretest=pretest, protected=protected)
243:    )
```

```
329:        baseline = await capture_baseline(sb, tmax, workdir=workdir, timeout=120)
338:            await grade_tmax(
342:                baseline_digests=baseline,
```

`verify_provisioning.py`:

```
164:                baseline = await capture_baseline(
165:                    sb, tmax, workdir=workdir, timeout=120
200:                    await grade_tmax(
201:                        sb, tmax, workdir=workdir, baseline_digests=baseline
```

Also at `daytona_revalidate.py:249-253`: "A row with protected entries grades by the integrity
baseline (taken below, right before the run) and never consults the pin hook" — i.e. baseline
and pin hook are alternatives, not both. The cited lines establish that this path exists and
grades with the baseline; they say nothing about it being newer than
`oracle_validate_seeds.py`, which I did not check (no `git log` under the read-only rules).

## 7. `H:T/integrity_baseline.py:87-103`, `:126-142`

CONFIRMED, all three parts.

Directory support and literal quoting, in `_path_clause`:

```
133:def _path_clause(i: int, resolved: str) -> str:
134:    q = shlex.quote(resolved)
136:        f"if [ -f {q} ]; then "
138:        f"elif [ -d {q} ]; then "
139:        f"printf '%s {i}\\n' \"$(cd -- {q} 2>/dev/null && find . -type f -print0 2>/dev/null "
142:        f"else printf '{ABSENT} {i}\\n'; fi"
```

Absent-vs-absent comparing equal, in `differences`:

```
243:    return [k for k in keys if baseline.get(k) != current.get(k)]
```

Nothing in `differences` raises, and the module docstring at `:29` concedes the directory case
is unexercised: "none are pinned today; kept as specified". Omitted by the claim: the cited
`:87-103` range is `_string_list` / `protected_paths_of`, which is the *validation* helper (a
malformed list raises rather than reading as "none") — the directory behavior it is cited for
lives at `:138-140`.

## 8. `H:T/prepare_tmax_reaudit_data.py:16-21`, `:366-377`; `_blind_layout` at `H:T/evolution/evolve_codex.py:1439-1457`

CONFIRMED.

```
20:    tasks/<task_id>/solution/solve.sh
21:    tasks/<task_id>/setup.sh
```

```
366:            for m in members:
367:                dest = os.path.join(out_root, m.name)
373:                os.makedirs(os.path.dirname(dest), exist_ok=True)
374:                src = tar.extractfile(m)
377:                    shutil.copyfileobj(src, f)
```

The extraction loop is unconditional over every member of the package prefix, and the blind
layout hides only four names:

```
 946:HIDDEN_FROM_VERIFIER = (
 947:    "solution",
 948:    "traces",
 949:    "AGENTS.md",
 950:    "sandbox",
 951:)
```

`setup.sh` is a top-level file outside `run/`, so `_blind_layout`'s `ignore` leaves it in the
verifier's package. The claim is exact; it does not say whether any reaudit package's
`setup.sh` actually leaks solution-shaped content, which these lines cannot show.

## 9. `H:T/evolution/evolve_codex.py:1605-1612`

CONFIRMED.

```
1605:            _blind_layout(vpkg, probe)
1606:            shutil.rmtree(probe / "tests")
1607:            (probe / "tests").mkdir()
1611:            for name in ("seed_size.json", "seed_literals.json"):
1612:                (probe / "run" / name).unlink(missing_ok=True)
```

Omitted: the probe path deletes two of the four files `_blind_layout` keeps in `run/`
(the allowlist at `:1447-1452` is `seed_size.json`, `resources.json`, `seed_literals.json`,
`pretest.json`), so the claim's consequence holds only for a newly allowlisted file that is
not also added to this unlink list — the coupling the reviewer describes is real but is one
edit away from being broken deliberately.

## 10. `H:T/rollouter.py:1264-1289`; `B:T/rollouter.py:920-972`, `:1266-1295`; `B:T/config_registry.py:183-188`

CONFIRMED.

H suppresses the zero-turn all-fail group before any signal is written:

```
1264:            if all_failed and not any(len(r.turns) for r in rollouts):
1274:                logger.warning(
1276:                    f"{sample.instance_id}: all-fail group with zero turns"
1289:                return
```

B adds the configurable easier floor and validates it:

```
 925:        evolution_easier_ratio: float = 0.0
 926:        """Maximum solved fraction for simplification. Must be in
 927:        [0, evolution_harder_ratio).
 954:        if not 0 <= config.evolution_easier_ratio < config.evolution_harder_ratio:
```

and removes the zero-turn special case, replacing `all_failed` with the floor test:

```
1278:            at_floor = fraction <= self._evolution_easier_ratio
1287:            # A group that took no turn anywhere used to be suppressed here as an
1292:            # scored zero is a verdict on the task. Nothing left to special-case.
1295:            passed = not at_floor
```

`B:T/config_registry.py`:

```
186:        evolution_easier_ratio=float(
187:            os.environ.get("SWE_EVOLUTION_EASIER_RATIO", "0.0")
188:        ),
```

Overstated slightly: `evolution_harder_ratio` is not new in B — H already reads
`self._evolution_harder_ratio` at `:1260`. And B's stated justification for dropping the
zero-turn guard is that `is_scored` already filters NaN-reward (infra-failed) attempts
(`:1266-1268`, `:1288-1291`), which is a different filter from "no attempt took a turn": a
scored rollout with zero turns is no longer suppressed.

## 11. `H:T/evolution/offline_select_eval.py:60-71`, `:110-151`, `:190-207`

CONFIRMED.

```
65:    ap.add_argument(
66:        "--min-rate",
67:        type=float,
68:        default=0.9,
```

```
114:        rate = v["pass"] / v["graded"]
115:        if rate >= a.min_rate:
116:            picks.append((tid, v, rate))
```

```
147:        rev = _latest_rev(task_dir)
148:        if rev == 0:
150:            log_line(f"task={tid} skipped: still at r0, nothing to harden further")
193:            "direction": "harder",
```

No bounds check on `--min-rate` anywhere (`type=float` only), and `direction` is the literal
`"harder"` with no alternative, so any threshold the operator picks stages a hardening signal.
Omitted: the tool writes into a fresh `runs/<name>` and refuses to stage over an existing one
(`:127-128`), logs `min_rate` and each staged task (`:140-143`, `:205-208`), and is an offline
operator script — nothing in the training loop invokes it.

## 12. `B:T/runbook/README.md:32-65`

CONFIRMED.

```
32:### ReBench-only 200-step baseline
34:[`rebench_only_c1000_1t6g1e.h100.env`](rebench_only_c1000_1t6g1e.h100.env)
35:records the portable portion of the completed Qwen3.5-9B baseline: one 8-GPU
41:The fixed training input contains the 1,317 tasks from
64:training data is static (`SWE_DATA_HOT_RELOAD=0`), so an evolution service must
65:not alter this baseline.
```

The document records the run as completed and pins the input by dataset revision and JSONL
SHA-256 (`:41-48`); it deliberately contains no launcher, credentials or paths (`:37-39`), so
it is a recipe plus provenance, not a result artifact — no metrics appear in this section.
