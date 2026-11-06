Perform an extreme value analysis on the annual maximum river flow data in `/app/annual_maxima.csv`. The data contains 50 years (1975–2024) of annual maximum flow measurements (m³/s) for a river gauging station.

Fit a Generalized Extreme Value (GEV) distribution to the annual maxima using maximum likelihood estimation implemented from scratch. You may use `scipy.optimize` for numerical optimization but must not rely on any pre-built extreme value analysis packages.

Your analysis must include:

1. **GEV parameter estimation via MLE**: Implement the negative log-likelihood for the GEV distribution (handling the ξ → 0 limit correctly), and use an optimizer to find the MLE parameter estimates.

2. **Return level estimation**: Compute the T-year return levels for T = 10, 50, and 100 years.

3. **Confidence intervals via profile likelihood**: For each return level, compute a 95% confidence interval using the profile likelihood method. This requires re-parameterizing the GEV so that the return level replaces one parameter, then maximizing the constrained likelihood over the remaining free parameters. Use the standard χ²₁ threshold for the likelihood ratio test.

4. **Model diagnostics**: Generate a probability-probability (P-P) plot comparing the fitted GEV to the empirical distribution of the data. Save this plot as `/app/pp_plot.png`.

5. **Goodness-of-fit**: Compute the Anderson-Darling test statistic for the fitted GEV model and save it to `/app/ad_statistic.txt`.

Save your results to these files:
- `/app/return_levels.csv` — CSV with columns `return_period`, `estimate`, `ci_lower`, `ci_upper`
- `/app/ad_statistic.txt` — a single number: the Anderson-Darling statistic
- `/app/pp_plot.png` — the P-P plot image
- `/app/analysis.py` — your complete, reproducible analysis script
