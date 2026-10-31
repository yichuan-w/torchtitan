# Runs 1028-1031b: how each one died and what changed

Qwen3.5-9B tmax evolve-both runs, goal TB-2.1 avg@5 > .40 (best before: 1022).
Dates are PDT.

| Run | Ran | Ended at | Failure signature | Change for the next run |
|---|---|---|---|---|
| 1028 | 10-28 evening → 10-29 ~01:00 | step 11 (killed) | Rewriter on a local codex login: the provider's cybersecurity filter refused 11-21% of rewrite sessions (1-3% on the gateway path). | Rewrites back on the gateway path. |
| 1029 | 10-29 ~01:35 → ~11:25 | step 105 (killed) | Transient `EPERM` from the shared FUSE root ended evolve rounds and killed the fold thread: 595 rewrites, 331 interrupted, 197 accepted by step 100 (1022: 1070). Cause: another job's file-based message bus (0.1 s polling, create/delete churn) on the same mount. | 65bdce262 (refill skips `OSError`), 05c47cb9d (atomic writes retry `EPERM`). |
| 1030 | 10-29 ~12:45 → afternoon | step 59 (killed) | Same `EPERM`; accepted 438 by step 59 (1022: 716). | aaa76d3ce (`fuse_retry`: retry `EPERM` under the root), e98046ed8 (no retry for hard links); the other job stopped. |
| 1031 | 10-29 evening | prepare | `prepare` hit one more transient `EPERM` ("no valid live mix version"). | Fresh tag 1031b. |
| 1031b | 10-29 ~20:45 → 10-30 ~16:45 | step ~214 (stopped) | See below: token-level multilingual degeneration from ~step 170, eval back to the step-0 level. | `SWE_OFFPOLICY_STEPS` 7 → 5, active groups 200 → 160, rollout concurrency 2000 → 1920. |

## 1031b in detail

Infra incidents (all recovered, none killed the run):

- 10-29 22:46 → 10-30 00:29: evolve publish convoy. Three rewrites left git-tree
  scratch (5-15k files) under `package/run/`; copying them file by file to the FUSE
  root held all 16 publish threads. Fixed by 9e6e4758a (tar any `run/` dir over 200
  files) plus `EVOLVE_PUBLISH_WORKERS=48`. The restart marked 88 in-flight rewrites
  interrupted; their signals stayed pending and were re-done.
- 10-30 12:48 → 13:44 and 15:48 → 16:33: the W&B mirror loop stopped uploading;
  training was unaffected. A manual run of `wandb_history_mirror.py` caught it up.

The failure: token-level degeneration.

- Symptom: random CJK / Arabic / Hangul / Thai tokens inside English reasoning and
  code (`exp(upper恩velope(x))`, `tag_name的数字`), sometimes whole sentences switching
  to Chinese. Task prompts and all 2046 task revisions contain no CJK.
- Timeline (share of training turns with CJK): 0.3-0.9% up to pv160, 1.9% pv170,
  2.5% pv180, 9.8% pv190, 18-19% pv200-210. On eval: 0.5% at pv150, 2.3% pv175,
  24% pv200.
- Eval avg@5 (strict): pv150 .308 (best), pv175 .281, pv200 .249 (pv0 was .245).
- `rollout/format_error_frac/mean` .05 → .40 and `format_error_plain_text` .01 → .22
  from step ~185; avg_train_reward .55 → .40; turns per rollout 66 → 80.
- Not reward hacking: rollouts with negative advantage carry the garbage more often
  than positive ones; correlation with advantage is -0.2..0.
- 1022 showed the same degeneration at pv60-100 (peak 5.9% of turns) while its
  entropy was still high (~.125), and recovered when entropy contracted. 1026b
  never showed it in 300 steps.
- Last clean checkpoint: step-160. step-180 is at onset; step-200 onward is
  contaminated.

Suspected causes, by evidence:

1. `SWE_OFFPOLICY_STEPS=7` (1022 and 1026b: 5) is the only training setting that
   differs from both healthy runs; sampling, optimizer, loss and precision are
   identical. Mean policy age 4.7-5.0 vs ~3.5, and DPPO keeps 99.98% of tokens, so
   stale samples train unclipped.
2. Entropy collapsed by step 60 (.066; 1022 held ~.13 until step 90), then rebounded
   from that low base to .10-.15 right at the burst.
3. Trainer/generator log-prob gap grew (`bit_wise/logprob_diff/abs_mean` .013 → .021)
   with the garbage; more amplifier than cause.
4. Longer trajectories: the garbage rate rises with turn index (40% of turns past 80).
5. Low prior, cheap to rule out: numpy 2.3.5 → 2.0.0 / pandas downgrade in the
   training env on 10-26.

## Stop rules (backtested on 1022, 1026b, 1031b)

| Rule | Condition | 1031b | 1022 | 1026b |
|---|---|---|---|---|
| Warn | `debug/vllm_local_kl_k3_mean` rolling-10 > .0011 and `bit_wise/logprob_diff/abs_mean` rolling-10 > .019 for 3 steps | step ~172-176 | no | no |
| Stop | non-Latin chars per 1k completion chars rolling-10 > 0.5, or > 8% of turns with CJK for 5 steps | step ~195 | no | no |
| Late stop | `format_error_plain_text_frac` rolling-10 > .15 | step ~200 | no | step ~290 |

The warn rule has ~10% margin over 1022, so treat it as an alert, not a kill.
The non-Latin metric does not exist in W&B yet; add it before the next run.
