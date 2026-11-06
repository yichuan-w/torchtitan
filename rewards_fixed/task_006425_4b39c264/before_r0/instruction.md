You are a machine learning engineer preparing synthetic training data that mimics a sensitive original dataset. You need to write a pipeline that generates this data using MCMC, evaluates its fidelity using distribution distances, estimates confidence intervals via bootstrapping, and includes a regression test.

Write a Python script at `/home/user/synthetic_pipeline.py` that performs the following tasks:
1. **Data Loading**: Read the dataset from `/home/user/original_data.csv` (contains 3 continuous features: `A`, `B`, `C`).
2. **Parameter Estimation**: Compute the empirical mean vector and covariance matrix of the original data.
3. **MCMC Sampling**: Implement a Metropolis-Hastings MCMC sampler to generate synthetic data from a Multivariate Normal (MVN) distribution defined by the empirical mean and covariance.
   - Use the empirical mean as the initial state.
   - Use a random walk proposal: $x_{new} = x_{old} + 0.5 \cdot L \cdot z$, where $L$ is the Cholesky decomposition of the empirical covariance matrix, and $z$ is a vector of standard normal random variables.
   - Run the chain for 2500 iterations. Discard the first 500 iterations as burn-in. Keep the remaining 2000 samples as your synthetic dataset.
   - Use `numpy.random.seed(42)` right before starting the MCMC loop.
4. **Distance Metrics**: For each feature (`A`, `B`, `C`), compute the 1-Wasserstein distance (`scipy.stats.wasserstein_distance`) between the marginal distribution of the 2000 synthetic samples and the 100 original samples.
5. **Bootstrap Confidence Intervals**: For each feature, compute a 95% bootstrap confidence interval for the Wasserstein distance using the percentile method (2.5th and 97.5th percentiles).
   - Perform 100 bootstrap iterations.
   - In each iteration, resample the *synthetic samples* with replacement (size 2000), and compute the Wasserstein distance against the *fixed* original samples.
   - Use `numpy.random.seed(123)` right before the bootstrap loop.
6. **Output**: Save the metrics to `/home/user/metrics.json` in the following format:
   ```json
   {
     "A": {
       "distance": <float>,
       "ci_lower": <float>,
       "ci_upper": <float>
     },
     "B": { ... },
     "C": { ... }
   }
   ```

Additionally, write a regression test file `/home/user/test_pipeline.py` using `pytest`. The test should import a function `calculate_wasserstein(orig, synth)` from `synthetic_pipeline.py` and assert that `calculate_wasserstein([1, 2, 3], [1, 2, 3])` returns `0.0`.

Run your script to generate `/home/user/metrics.json` and ensure your test passes by running `pytest /home/user/test_pipeline.py`.