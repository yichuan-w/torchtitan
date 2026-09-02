# TerminalWorld seeds: contract-validity heuristic v1

Dark check = a filesystem path asserted by the verifier that appears nowhere
the agent can see (instruction, environment files, Dockerfile/entrypoint,
task.toml; parent-directory mentions count as discoverable).

- tasks with path-asserting verifiers: 1454/1530
- tasks with >=1 dark check: **312** (21.5% of path-asserting tasks)
- tasks where ALL asserted paths are dark: **56**

Worst offenders (most dark paths):
- tw_134374: 18/18 asserted paths dark
- tw_34188: 16/17 asserted paths dark
- tw_126506: 12/12 asserted paths dark
- tw_247715: 12/16 asserted paths dark
- tw_359207: 10/12 asserted paths dark
- tw_538643: 10/17 asserted paths dark
- tw_110130: 9/9 asserted paths dark
- tw_102926: 8/9 asserted paths dark
- tw_106848: 8/8 asserted paths dark
- tw_439619: 8/8 asserted paths dark

Caveat: lexical heuristic. Prose hints ('save the report next to the
input') and dynamically built paths evade it in both directions; treat
flagged tasks as candidates for manual/LLM review, not verdicts.
