# Rewrite agent `./sandbox check` verdicts (run 1030c)

## easier

- rewrites: 1537 (with ≥1 agent session: 1535; with ≥1 check: 1474)
- `./sandbox check` runs: 1595, failed: 63 (3.9%)
- rewrites with ≥1 failed check: 45; of those, ended accepted: 39; ended {'failed': 6}
- accepted rewrites whose last check passed: 1376 / 1378
- failures by component:
  - oracle fails (reference solution scores <1): 50
  - stage:null_probe: 8
  - stage:boot: 5
- checks per rewrite (6 = 6+): 0: 63, 1: 1378, 2: 80, 3: 12, 4: 1, 5: 1, 6: 2

## harder

- rewrites: 1179 (with ≥1 agent session: 1169; with ≥1 check: 1093)
- `./sandbox check` runs: 1249, failed: 88 (7.0%)
- rewrites with ≥1 failed check: 70; of those, ended accepted: 67; ended {'failed': 3}
- accepted rewrites whose last check passed: 1019 / 1029
- failures by component:
  - oracle fails (reference solution scores <1): 66
  - stage:boot: 20
  - stage:step_size: 2
- checks per rewrite (6 = 6+): 0: 86, 1: 968, 2: 100, 3: 21, 4: 3, 6: 1

## Example fail lines

**easier / oracle fails (reference solution scores <1)**
- `VERDICT: fail   reward=0.0   solve_exit=0   null_reward=0.0   took=31s   box=[cpu=1 mem_gb=1 disk_gb=8] || measured: mem_peak_mb=247.4 cpu_seconds=10.9 df_used_mb=12.3 solve_secs=6.8 | --- what the run printed (tail) --- | [?2004hroot@eaa4bdc8-c980-409a-8e31-ac0a37c1c3a9:/evalml# bash -c 'bash <path> printf '\r\nTT_REFERENCE_EXIT_d40879b47bd648598094d226e`
- `VERDICT: fail   reward=0.0   solve_exit=0   null_reward=0.0   took=779s   box=[cpu=1 mem_gb=1 disk_gb=1] || measured: mem_peak_mb=317.6 cpu_seconds=19.0 df_used_mb=235.9 solve_secs=422.5 | recorded solution replay: waited 363s of 362s recorded between turns (recorded timing) | (the fast replay got reward=0.0 solve_exit=0; this result is the recorded-timing rerun's) | --- what the run printed (tail) ---`
**easier / stage:boot**
- `VERDICT: fail   stage=boot (the environment did not come up) || hook: PostToolUse | hook: PostToolUse | hook: PostToolUse`
- `VERDICT: fail   stage=boot (the environment did not come up) || hook: PostToolUse | hook: PostToolUse | hook: PostToolUse`
**easier / stage:null_probe**
- `VERDICT: fail   stage=null_probe   null_reward=1.0 || The verifier passes on the untouched workspace: it pays for nothing. | Make it depend on work the solution has to do, then check again. | hook: PreToolUse`
- `VERDICT: fail   stage=null_probe   null_reward=1.0 || The verifier passes on the untouched workspace: it pays for nothing. | Make it depend on work the solution has to do, then check again. | hook: PostToolUse`
**harder / oracle fails (reference solution scores <1)**
- `VERDICT: fail   reward=0.0   solve_exit=128   null_reward=0.0   took=15s   box=[cpu=1 mem_gb=2 disk_gb=4] || measured: mem_peak_mb=54.7 cpu_seconds=3.4 df_used_mb=9.5 solve_secs=8.0 | --- what the run printed (tail) --- | [?2004hroot@24f9efdb-b0ea-401a-b0f6-b059ff0c5649:/pelita# bash -c 'bash <path> printf '\r\nTT_REFERENCE_EXIT_dcf0e9ab2e0945f79fb0161aed03`
- `VERDICT: fail   reward=0.0   solve_exit=0   null_reward=0.0   took=70s   box=[cpu=1 mem_gb=1 disk_gb=4] || measured: mem_peak_mb=72.1 cpu_seconds=6.2 df_used_mb=12.3 solve_secs=7.7 | --- what the run printed (tail) --- | [?2004hroot@ee0e0783-097b-479c-8d4a-1276905efa50:/icalendar# bash -c 'bash <path> printf '\r\nTT_REFERENCE_EXIT_4a34ed1544d841a9bc9fa82b`
**harder / stage:boot**
- `VERDICT: fail   stage=boot (the environment did not come up) || hook: PostToolUse Completed | hook: PostToolUse Completed | hook: PostToolUse Completed`
- `VERDICT: fail   stage=boot (the environment did not come up) || hook: PostToolUse | hook: PostToolUse | hook: PostToolUse`
**harder / stage:step_size**
- `VERDICT: fail   stage=step_size   reward=1.0   took=16s   box=[cpu=1 mem_gb=2 disk_gb=2] || measured: mem_peak_mb=52.0 cpu_seconds=3.1 df_used_mb=4.8 solve_secs=8.7 | recorded solution replay: waited 1s of 0s recorded between turns (recorded timing) | The reference solution passes, but The rewrite violates the configured size bounds: the verifier has 12 assertions against the seed's 6; one`
- `VERDICT: fail   stage=step_size   reward=1.0   took=16s   box=[cpu=1 mem_gb=2 disk_gb=2] || measured: mem_peak_mb=52.0 cpu_seconds=3.1 df_used_mb=4.8 solve_secs=8.7 | recorded solution replay: waited 1s of 0s recorded between turns (recorded timing) | The reference solution passes, but The rewrite violates the configured size bounds: the verifier has 12 assertions against the seed's 6; one`
