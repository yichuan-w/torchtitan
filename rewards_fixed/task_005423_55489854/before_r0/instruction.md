You are a Site Reliability Engineer (SRE) investigating a failing metrics aggregator service. The service computes the mean and standard deviation of request latencies using a custom high-performance C extension for Python. Recently, the pipeline has been failing, and the standard deviation outputs occasionally show up as `NaN` (Not a Number), breaking downstream dashboards.

Your task is to debug and fix the service located in `/home/user/metrics_service`.

Here is what you need to do:
1. **Fix Compilation/Linker Errors:** The previous engineer attempted to refactor the C extension but left it in a broken state. When you try to build the extension using `python3 setup.py build_ext --inplace`, it fails with a linker error. Diagnose and fix the `setup.py` or project structure so the extension compiles successfully.
2. **Fix Numerical Instability:** The calculation logic in the C extension suffers from catastrophic cancellation when processing data with large baseline values and small variations (e.g., latencies consistently around 1,000,000 microseconds). This results in negative variance calculations, which lead to `NaN` when taking the square root. Fix the C code to handle this numerical instability (e.g., by clamping negative variances to 0, or using a more stable algorithm like Welford's).
3. **Add Assertion-Based Validation:** Modify the Python script `/home/user/metrics_service/aggregator.py`. Just before the script writes the results to the output file, add an `assert` statement to validate that the computed standard deviation is not `NaN` and is greater than or equal to 0. Use `math.isnan` for checking.
4. **Run the Service:** Once fixed, run the aggregator on the provided data:
   `python3 aggregator.py /home/user/metrics_service/data.csv /home/user/metrics_service/report.json`

The final `report.json` must be a valid JSON file containing the keys `"mean"` and `"stddev"`, with correct floating-point values (stddev must be a valid non-negative number).