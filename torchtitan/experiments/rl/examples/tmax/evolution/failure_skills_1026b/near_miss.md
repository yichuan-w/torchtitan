# B — Near-miss deep dives, 1026b pv75 (avg@5 .310)

Task: on near-miss tasks (1–4/5 passed at pv75, or a pv75↔pv125 swing), compare passing and failing trials of the same task, find the decision that separates them, and turn it into a skill the teacher can train when rewriting tasks.

Source: `validation_traces/step-{75,125}/` of `agentpick-clean-evolve-both-1026b`. Test sources come from `tb2_1_eval.jsonl` fixtures. Passes are per 5 trials.

| task | pv75 | pv125 | separating decision |
|---|---|---|---|
| pytorch-model-cli | 1 | 3 | stdout = only the digit; check more than the single sample image |
| bn-fit-modify | 1 | 3 | CSV header `to,from` means child first; the test reads columns by name |
| pytorch-model-recovery | 1 | 3 | save `/app/model.pt` early; don't run unbounded foreground training |
| openssl-selfsigned-cert | 4 | 2 | check script imported a pip package the grader's Python doesn't have |
| git-multibranch | 3 | 2 | tested a push to the local path, never the `git clone git@localhost` the grader uses |
| build-cython-ext | 4 | 2 | blind `sed` replaced `n.` with `np.` in a module that imports numpy as `n` |
| custom-memory-heap-crash | 4 | 1 | one root-cause read then one fix (pass) vs ~15 rewrites of user.cpp until context ran out |
| count-dataset-tokens | 4 | 4 | had the right number, then flip-flopped and overwrote it at the "Are you sure?" prompt |
| portfolio-optimization | 4 | 5 | speedup margin too thin for the grader's machine |

Infra noise, excluded from the skills: two pytorch-model-recovery failures were verifier pip timeouts (900s spent downloading nvidia-* wheels).

---

## Skill 1 — Match the output contract the grader parses, exactly

What the grader reads is a contract: stdout, column names, the file path. "Mostly right, plus a friendly message" scores 0.

- **pytorch-model-cli s4 (fail).**
  - The spec says "The output of the tool should only be the predicted digit."
  - The C++ tool printed `Prediction: 2 | Prediction written to prediction.txt`.
  - It failed `test_cli_tool_executable` (`stdout.strip()=="2"`). `test_cli_tool_output` failed with `ValueError: invalid literal for int()`.
  - Passing s0 ran `/app/cli_tool /app/weights.json /app/image.png` and checked that the output was exactly `2`.
- **bn-fit-modify s0 (fail).**
  - The spec says save edges "in the format: to,from".
  - s0 found the right DAG but wrote rows parent-first under that header: `to,from / U,R / U,M / U,Y / U,D / Y,D / R,M`.
  - The test reads `(row["from"], row["to"])`, so every edge came out reversed. It failed `test_learned_dag_structure` and `test_intervened__data_structure`.
  - Passing s4 wrote `to,from / M,U / Y,U / D,U / D,Y / R,U`.

**Trainable behavior:** before submitting, print each deliverable raw (`cat`, `head`, `./tool | od -c`). Read it the way a parser would against the spec's literal wording: header semantics, nothing extra on stdout, exact path.

**Teacher hints:**
- Write tasks whose spec states a non-default or counter-intuitive format (reversed column order, digit-only stdout, a fixed key order).
- Have the test parse strictly by name or exact match.
- Tempt the model with a "helpful" log line that breaks the contract.

## Skill 2 — Verify through the grader's entry path and environment, not your own shortcut

The grader runs a fresh process, often a different Python environment and a different user or transport. "It works in my shell" is not evidence.

- **openssl-selfsigned-cert s2 (fail, 13 turns).**
  - `check_cert.py` did `from cryptography import x509`. When that failed, the agent's own reasoning was: "The cryptography module is not installed. Let me install it first using pip".
  - The verifier runs `python /app/check_cert.py` in its own uv Python without that package. It got `returncode=1`, stdout `Error: cryptography module not installed.`, and failed `test_python_verification_script`.
  - Passing s0 used only the standard library (`subprocess.run(["openssl","x509",...])`).
- **git-multibranch s2 (fail).**
  - The agent validated with `git remote add origin /git/project` (local filesystem path) and curl, then submitted.
  - The grader does `spawn git clone git@localhost:/git/project project-test` over SSH with a password. Clone failed: `bash: line 1: cd: project-test: No such file or directory`, then `AssertionError: HTTPS multi-branch deploy test failed`.
  - The spec lists the 6 test steps verbatim, and the agent never ran step 1 as written.
  - Passing trials (s1, s3, s4) ran the full flow: SSH clone, then push, then curl.
- **pytorch-model-cli s1 (fail).**
  - Validated only on the single provided `image.png`, which gave `2`.
  - `test_cli_tool_output` compares predictions on 50 MNIST images; a preprocessing mismatch never showed on the one sample.
  - Passing s0 reused model.py's exact transforms.

**Trainable behavior:**
- When the spec says "the system will be tested by…", script exactly those steps (same user, same transport, same command line).
- Run deliverables in a clean interpreter (`env -i`, a fresh venv, or `python3 -I`) using only the stdlib or preinstalled packages.
- Test on more than the single example that was given.

**Teacher hints:**
- The verifier uses a different interpreter or user than the agent shell, and tests install nothing.
- The spec gives the exact test procedure, and a convenient shortcut exists that passes locally but not via that procedure (local path vs SSH, root vs git user).
- Provide one sample input while the hidden test uses many.

## Skill 3 — Secure a valid deliverable early; keep each command bounded

Long budgets are lost to foreground loops and endless polishing. A saved, valid artifact earns partial-to-full credit; none earns 0.

- **pytorch-model-recovery s2 (fail; 110 turns, `hit_time_budget`).**
  - Repeated long foreground training runs, each followed by `<wait>` then `C-c`.
  - Errors along the way: `AttributeError: 'RecoveredModel' object has no attribute 'save'` and `OSError: could not get source code` (it called `torch.jit.script` on a class defined via stdin).
  - It never wrote `/app/model.pt`. Tests raised `ValueError: The provided filename … does not exist`.
  - The task only needed *any* MSE decrease from tuning `output_layer`.
  - Passing s4 froze the other layers, ran a short training, called `torch.jit.save(model2,'/app/model.pt')` immediately, then iterated.
- **git-multibranch s0 (fail; `hit_max_turns`, 300 turns, never submitted).** It kept reconfiguring sshd/nginx with no checkpoint of a working state.
- **custom-memory-heap-crash s4 (fail; 277 turns, `hit_context_limit`).** See skill 4.

**Trainable behavior:**
- After the first plausible solution, write the required artifact to the required path at once, then improve it.
- Put any command that may run longer than about 60s behind `timeout`, or run it in the background with logs.
- Never loop through `<wait>` + `C-c`.
- Write scripts to a file, not stdin, so TorchScript and inspect can find the source.

**Teacher hints:**
- Tasks where a cheap baseline already meets the threshold ("any decrease", "under N"), but a perfectionist path is open-ended.
- Tasks where the artifact path is checked first.
- Training or compile steps that take minutes on 1–2 vCPU.

## Skill 4 — Read the provided root-cause material, then make one targeted fix (no edit-and-retry)

- **custom-memory-heap-crash.**
  - Need: fix only `/app/user.cpp` so the release build (custom libstdc++) doesn't crash and valgrind reports no leaks.
  - Passing s2 (60 turns): build both modes, reproduce the crash, `cat /build/patches/locale_init.cc.patch`, `cat /build/patches/locale_classes.tcc.patch`, grep the libstdc++ source, run valgrind, write user.cpp once, rebuild both, run, valgrind, submit.
  - Failing s4 read the same patches by T17, then spent T20–T39 on about 15 one-off `gdb -batch -ex "break _Fac_tidy_reg_t::~_Fac_tidy_reg_t" …` probes.
  - From T72 on it alternated `cat > /app/user.cpp << 'EOF'` with a release rebuild about 15 times (T72, 88, 96, 104, 128, 144, 160, 176, 192, 216, 232, 264…), until the context ran out.
  - Result: `test_release_build_runs_without_crash` failed.
  - Most pv125 trials failed the same way (179–300 turns), which explains 4/5 dropping to 1/5.
- **pytorch-model-recovery s2.** The same pattern: re-run, hit an error, tweak one thing, re-run, with no step back to read the `torch.jit` error.

**Trainable behavior:**
- After two failed attempts at the same fix, stop editing.
- Write down the causal chain (what is freed or destroyed and when, and who touches it afterwards) from the patches or sources provided.
- Make the fix that chain implies, then validate once.

**Teacher hints:**
- Tasks shipping a patch or diff that is the explanation (a planted bug in a library or build).
- Fixes confined to one file, where blind variations rarely work.
- Turn-budget pressure.

## Skill 5 — Make precise, alias-aware bulk edits, then rerun the whole check

- **build-cython-ext s1 (fail, 10/11).**
  - To fix NumPy 2 removed aliases, it ran global seds such as `sed -i 's/n\.float/np.float64/g' pyknotid/make/periodic_knot.py pyknotid/invariants.py` and `sed -i 's/np\.int/np.int64/g' …`.
  - `invariants.py` does `import numpy as n`, so the edit produced `n.zeros(..., dtype=np.longlong)`.
  - Result: `NameError: name 'np' is not defined` in `test_cinvariants_python_vs_cython`.
  - `s/np\.int/np.int64/` also rewrites an existing `np.int64` into `np.int6464`.
  - Passing trials (s0, s2, s3, s4) checked each file's import alias and used word-bounded patterns.
- (Related, pv125) Other build-cython-ext failures: `AttributeError: module 'numpy' has no attribute …` from aliases missed in `.pyx` files. The same missing step: no post-edit `grep -rn` for every remaining old alias.

**Trainable behavior:**
- Before a regex rewrite, `grep -n "^import numpy\|^from numpy"` each file to learn its alias.
- Anchor patterns (`\bnp\.int\b`).
- Afterwards, grep for both leftovers and over-replacements, and exercise every module path (pure Python and the Cython fallback).

**Teacher hints:**
- Repos where the same library is imported under different aliases across files.
- Deprecations that are prefixes of valid names (`int` vs `int64`, `float` vs `float_`).
- Tests that exercise the less-traveled fallback path.

## Skill 6 — Commit to the evidence-backed answer; leave margin on thresholds

- **count-dataset-tokens s4 (fail).**
  - At T33 it computed the README-guided `deepseek_reasoning + deepseek_solution: 79586`, which is correct.
  - It then explored alternatives (224655 from all assistant messages, 79566 from another tokenization path).
  - At the "Are you sure?" prompt it rewrote `/app/answer.txt` three times (79566, then 224655, then 79566) and submitted 79566. The test expects `79586`.
  - Passing s3 also wobbled (it briefly wrote 63841), but went back to the number justified by the README field definitions.
- **portfolio-optimization s3 (fail).**
  - `test_performance_and_scalability[8000]` failed.
  - The agent's own benchmark showed only 1.3–1.7× against the required 1.2×, on a noisy 1–2 vCPU sandbox, and it submitted anyway.
  - Passing trials vectorized further and had clear headroom.

**Trainable behavior:**
- When computations disagree, find *why* (which field, which tokenizer) and choose using the spec or README definition. Don't take the latest number.
- Treat the confirm prompt as a time to check, not to re-decide: change the answer only if there is new evidence.
- For speed or accuracy thresholds, aim for at least 2× margin, or re-measure several times.

**Teacher hints:**
- Tasks with several plausible interpretations, where documentation in the task picks one.
- Thresholds just above what a naive optimization reaches.

---

### Ranking for the teacher (by near-miss trials lost at pv75 + pv125)
1. Skill 2: grader path/env (openssl, git-multibranch, pytorch-model-cli)
2. Skill 3: early deliverable/bounded commands, together with Skill 4: root cause before retry (heap-crash 4→1, recovery, git-multibranch)
3. Skill 1: output contract (pytorch-model-cli, bn-fit-modify)
4. Skill 5: precise bulk edits (build-cython-ext 4→2)
5. Skill 6: commit and margin (count-dataset-tokens, portfolio)

Caveat: skills 5 and 6 rest on one clear pass/fail contrast per task, so they are less broadly evidenced than skills 1–4.
