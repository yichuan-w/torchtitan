# Hardening rationale

## Attempt evidence and reward audit

All 12 successful attempts replace the likelihood loop with a NumPy reduction and retain the legacy process-global RNG pattern: `np.random.seed(seed)` followed by `np.random.normal` and `np.random.rand` (for example, `traces/attempt-01.jsonl`, turn 3, and `traces/attempt-12.jsonl`, turn 4). They then write the requested runner and same-process equality test. None checks whether calling this reusable sampler mutates a caller's random stream.

The rewards reflect completion of the public deliverables; I found no edited grader, hard-coded result, or skipped required artifact. However, the seed's stated nondeterminism is broken setup: `set(range(len(data)))` has a stable integer iteration order for this fixture, and two seeded calls already return equal chains. I will repair the planted likelihood defect so its iteration order actually varies between Python processes while preserving the same visible code-debugging goal.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/numpy/numpy/v1.26.4/doc/source/reference/random/index.rst — fetched 2026-11-05. It says users generally create a `Generator` with `default_rng`; seeded RNG sequences are reproducible; and the convenience functions in `numpy.random` are aliases to methods on a single global `RandomState` instance.
- https://raw.githubusercontent.com/numpy/numpy/v1.26.4/numpy/random/_generator.pyx — fetched 2026-11-05. The `default_rng` documentation says it constructs a new `Generator`, that `default_rng` is the recommended constructor, and that it "does not manage a default global instance."
- https://api.github.com/repos/numpy/numpy/releases/tags/v1.17.0 — fetched 2026-11-05. NumPy's release notes introduce the extensible random module, identify PCG64 as the new default, and state that MT19937 is retained for backward compatibility.
- https://pypi.org/pypi/numpy/1.26.4/json — fetched 2026-11-05. PyPI reports NumPy 1.26.4 supports Python >=3.9 and publishes the CPython 3.10 manylinux x86_64 wheel with SHA-256 `ffa75af20b44f8dba823498024771d5ac50620e6915abac414251bd971b4529f`.

The Dockerfile will pin the actual upstream package as `numpy==1.26.4` rather than relying on the base image's NumPy 1.21.5.

## Reproduction in this environment

On the untouched image, I ran a caller-state probe around `sample(data, iters=1, seed=42)`. With the caller seeded to 123, its expected first two draws were `[0.69646919, 0.28613933]`; around the sampler they were `[0.6964691855978616, 0.5986584841970366]`, and `global state preserved: False`. The sampler silently reseeds and consumes the singleton global `RandomState`.

## Proposed change

Keep the original likelihood repair, runner, CSV, and reproducibility-test requirements. Add the library requirement that `sample` must use a sampler-local seeded random generator and leave NumPy's process-global random state unchanged. This requires choosing and consistently using a local NumPy RNG API, then validating the resulting artifact rather than mechanically preserving the attempts' process-global pattern.

This is one modest step beyond the attempts: they already understand seeded pseudo-random draws and exact chain comparison, but now must isolate RNG ownership in reusable scientific code. The behavior is silent rather than an exception that names its fix; an investigating student can compare caller draws and inspect NumPy's documented `Generator`/`RandomState` APIs.

## Trial fixes and replay evidence

The planted reduction itself emits no exception and names no repair, so I tried the nearest API interpretation of the new requirement: replacing only `np.random.seed(seed)` with `rng = np.random.default_rng(seed)`. The remaining draws still call the global `np.random.normal`/`rand`; grading returned reward 0 (and the likelihood bug was also still present). This confirms the requirement does not announce a one-line fix.

I then replayed the complete commands from successful `attempt-01` on a fresh reset. Its first-reach fix vectorized the likelihood but retained `np.random.seed` and global draws. Grading returned reward 0: `test_sample_preserves_global_random_state` found that the MT19937 state array changed. All inherited artifact checks preceding it passed.

I separately replayed successful `attempt-12` from another fresh reset. It stopped at the same new state-isolation check with reward 0 after its likelihood repair passed. These replays show the change intersects the student's actual shared implementation path rather than adding unrelated work.

Expected next group: **6 of 12**. The additional decision is compact and well documented, but none of the 12 measured attempts considered RNG ownership; stronger attempts that inspect state or know `default_rng` should recover, while mechanical versions of the shared solution will fail.
