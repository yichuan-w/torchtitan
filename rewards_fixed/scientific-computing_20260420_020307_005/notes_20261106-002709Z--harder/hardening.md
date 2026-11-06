# Hardening rationale

## Observed successful strategy

All 12 attempts earned reward. They inspect `calibrate.py`, `data_utils.py`, and the CSV, run the pipeline, then make the optimizer receive float64 data. Attempt 03 turn 11 applies the seed reference's one-line `np.float32` to `np.float64` loader change; attempts 05 and 06 instead convert the two Series in `calibrate.py`; attempts 01, 07, 08, 10, 11, and 12 rewrite that script around the same conversion. This task currently spares them any need to diagnose whether ordinary squared residuals are appropriate for the measurements: every supplied reading closely follows the model, so precision conversion alone recovers each tau.

No rewarded attempt violates the public instruction or edits grading files, and the setup provides every stated input, so there is no repair to declare.

## Primary sources and pinned artifact

- SciPy 1.11.4 `least_squares` source: https://raw.githubusercontent.com/scipy/scipy/v1.11.4/scipy/optimize/_lsq/least_squares.py — its API documentation says the purpose of `rho` is to reduce outlier influence; `linear` is ordinary least squares, `soft_l1` is usually a good robust choice, and `f_scale` is crucial for non-linear losses.
- SciPy 1.11.4 PyPI metadata: https://pypi.org/pypi/scipy/1.11.4/json — it identifies version 1.11.4 and publishes the CPython 3.12 manylinux x86_64 wheel and SHA-256 `ad669df80528aeca5f557712102538f4f37e503f0c5b9541655016dd0932ca79`.

The Dockerfile will pin the actual upstream package as `scipy==1.11.4` rather than relying only on the distribution dependency.

The scratch verifier originally also checked `T_initial` and `T_final` against unstated tolerances. I removed that hidden requirement and recorded it in `run/repair.json`; the public goal and retained check concern tau accuracy.

## Reproduced behavior

Using the shipped data with one isolated reading changed for each sensor (`101@5s=130`, `102@10s=-30`, `103@7.5s=140`), I reproduced the behavior with `./sandbox exec 'cd /app/calibration && sed -i "s/np.float32/np.float64/g" data_utils.py && python3 calibrate.py'`. SciPy 1.11.4's current bounded `curve_fit` call silently converges to tau values `2.575`, `11.628`, and `6.084` seconds. The command exits successfully, but all three exceed the requested 10% error. Running the same bounded fit with the documented robust `soft_l1` loss and a `0.2` degree residual scale gives `3.501`, `14.983`, and `7.204` seconds. Thus this is a real property of the installed optimizer and not a fabricated library failure.

## Changed decision

The revised measurements contain realistic isolated gross errors while retaining enough uncontaminated observations to identify each response. The student must now inspect residuals, recognize that successful ordinary least-squares convergence is biased by outliers, and choose a robust fit or an equivalent defensible outlier treatment. This is one modest step beyond the attempts' demonstrated workflow: they already inspect the data, directly exercise `curve_fit`, adjust its inputs, and validate fitted tau values.

The pipeline's normal output names the optimizer and fitted values but does not announce a fix. I tried merely converting the loader to float64—the first fix in attempt 03—and grading returned reward 0: the script succeeded but produced tau `2.575`, `11.628`, and `6.084`, and failed all three tau checks. I separately replayed attempt 05's fix, converting the two fitting arrays to float64 and adapting indexing; grading again returned reward 0 with the same three tau failures. A package upgrade/reinstall cannot remove the statistical effect of gross residuals, while the environment already pins and imports SciPy 1.11.4. Neither obvious candidate is the intended robust-fitting decision.

## Replay plan and prediction

After the environment change, I replayed attempt 03's successful path (run, change loader to float64, rerun) and attempt 05's successful path (convert the two fitting arrays to float64 and repair indexing, rerun) from separate fresh resets. Both now stop with a generated JSON whose inaccurate tau values fail the accuracy check, rather than at a runtime error; each fails the checks for sensors 101, 102, and 103, at relative errors of 26.4%, 22.5%, and 15.5%. I expect **6 of 12** in the next same-size group: all attempts notice the original precision issue, but roughly half are likely to stop after the now-insufficient familiar conversion while the more investigative rewrites inspect residuals and select robust fitting.
