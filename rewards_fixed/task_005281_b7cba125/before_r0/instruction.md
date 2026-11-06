I am a data scientist fitting a custom model, but I'm pulling my hair out trying to make my MCMC pipeline reproducible. 

I wrote a Metropolis-Hastings sampler in `/home/user/mcmc.py` to estimate the posterior of the mean (`mu`) and standard deviation (`sigma`) of a Normal distribution, given a dataset located at `/home/user/data.npy`. 
Even when I explicitly set the random seed via `numpy.random.seed(42)`, the chains diverge after a few hundred iterations across different runs. I suspect there is a floating-point reduction order issue or non-deterministic iteration happening in the log-likelihood calculation that slightly alters the acceptance probabilities.

Please perform the following steps to fix my pipeline:
1. Identify and fix the non-deterministic reduction bug in `/home/user/mcmc.py`. You should optimize the likelihood function to use vectorized numpy operations instead of loops where possible.
2. Write a script `/home/user/run_sampler.py` that loads `/home/user/data.npy`, runs the `sample` function from `mcmc.py` for 5000 iterations using `seed=42`, discards the first 1000 iterations as burn-in, and calculates the posterior means of `mu` and `sigma` from the remaining 4000 samples.
3. Save these two posterior means (comma-separated, `mu_mean,sigma_mean`) to `/home/user/results.csv`.
4. Create a scientific code regression test at `/home/user/test_reproducibility.py`. This script should run the `sample` function twice (both with `seed=42`, `iters=100`) and assert that the returned chains are exactly identical (using `np.testing.assert_array_equal` or similar). It should exit with code 0 if they match.

Do not use any external libraries other than `numpy`.