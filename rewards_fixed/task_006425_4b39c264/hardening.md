# Hardening rationale

## Evidence from attempts

All 12 attempts succeeded by loading the three columns, calling `np.linalg.cholesky` on the empirical covariance, and generally calling `np.linalg.inv` in the Metropolis-Hastings density. None inspected the covariance rank or handled a degenerate MVN. For example, `traces/attempt-01.jsonl` turn 4 computes `L = np.linalg.cholesky(cov)` and `np.linalg.inv(cov)`; `traces/attempt-06.jsonl` turn 3 does the same. The successful strategy was therefore spared a real numerical-linear-algebra decision because the original fixture was positive definite.

The accepted attempt in `traces/attempt-06.jsonl` also evaluates `-0.5 * x @ inv(cov) @ x` without centering at the empirical mean (turn 3), yet earned reward. That violates the requested MVN target and is recorded as a verifier repair in `run/repair.json`.

## Primary sources and pinned artifact

- NumPy 2.2.6 source, `numpy/linalg/_linalg.py`: https://raw.githubusercontent.com/numpy/numpy/v2.2.6/numpy/linalg/_linalg.py — the `cholesky` documentation says the input “must be Hermitian ... and positive-definite” and that it raises `LinAlgError` when decomposition fails because the matrix is not positive-definite.
- NumPy issue 8385: https://api.github.com/repos/numpy/numpy/issues/8385 and https://api.github.com/repos/numpy/numpy/issues/8385/comments — the reported real-world failure is `LinAlgError: Matrix is not positive definite`; maintainers explain that positive singular values do not prove positive definiteness because singular values are absolute eigenvalues.
- PyPI release metadata: https://pypi.org/pypi/numpy/2.2.6/json — identifies the pinned NumPy release as 2.2.6.

The environment will pin `numpy==2.2.6`. Its supplied data will have an exactly dependent feature, so its empirical covariance is positive semidefinite and rank deficient.

## Reproduction

Before editing, the current image reports NumPy 2.2.6. Running `np.linalg.cholesky` on the proposed covariance `[[1,.4,1.4],[.4,1.5,1.9],[1.4,1.9,3.3]]` produced:

```
LinAlgError: Matrix is not positive definite
```

After the environment edit, `./sandbox exec` loaded the actual CSV, computed its empirical covariance, and attempted both operations used throughout the traces. It reported NumPy `2.2.6`, rank `2`, eigenvalues `[2.59774841e-16, 1.04094489e+00, 5.72403367e+00]`, then:

```
LinAlgError: Matrix is not positive definite
LinAlgError: Singular matrix
```

## Proposed change and decision

The pipeline must continue to target the empirical MVN when the covariance is rank deficient. The proposal factor must therefore be obtained with a method valid for positive-semidefinite covariance, and target-density calculations must stay on the covariance support without an ordinary inverse. This is one modest step beyond the attempts: it preserves their MCMC, bootstrap, metric, JSON, and pytest workflow while requiring them to diagnose and correctly handle a common degenerate-covariance condition rather than mechanically applying Cholesky and inverse.

The planted error identifies only the numerical condition (`Matrix is not positive definite` or `Singular matrix`), not the correct factorization and density treatment. The output also lets an investigating student inspect covariance rank and eigenvalues.

## Plausible first fixes and replay results

From a fresh reset, force-reinstalling the pinned `numpy==2.2.6` succeeded but the same actual covariance still produced `LinAlgError: Matrix is not positive definite`; grading returned reward `0.0` because no deliverables existed. This is the attempts' ordinary reinstall/upgrade reaction, and the pinned version prevents an upgrade from silently changing the condition.

From another fresh reset, a complete pipeline using the common `cov + 1e-6 I` jitter workaround ran and produced metrics, but grading returned reward `0.0`: its generated/bootstrap distances no longer preserve the exact empirical support (`C = 2A`). Thus neither the error message nor the obvious numerical workaround announces a passing fix.

Direct replay of `traces/attempt-01.jsonl` from a fresh reset completed, but grading returned reward `0.0`; it failed the support check because its Cholesky factor follows a spurious floating-point third direction (distance relation error `1.60e-9`). Direct replay of `traces/attempt-06.jsonl` from a fresh reset also completed, but grading returned reward `0.0`; its uncentered target produced distances above the retained fidelity threshold (A `1.0023`, B `0.6936`, C `2.0046`). These replays started no service or setup by hand. All four public file/schema/test checks were reached in the first replay except the support assertion; the second failed the metric-fidelity assertion after the file check passed.

## Predicted next group

5 of 12. Every measured path mechanically used Cholesky/inverse and both representative replays now fail, but the covariance rank and eigenvalues make the required PSD-support treatment discoverable; stronger agents commonly know eigendecomposition/pseudoinverse handling for degenerate Gaussians.
