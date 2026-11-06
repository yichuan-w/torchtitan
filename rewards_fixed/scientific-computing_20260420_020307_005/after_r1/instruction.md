The sensor calibration pipeline at `/app/calibration/calibrate.py` produces incorrect time constant (tau) values. All sensors report the same tau value regardless of their actual response characteristics.

The pipeline fits an exponential decay model to temperature sensor step-response data. Run the script and analyze why the optimization is not producing correct results.

Fix the pipeline so each sensor's tau is correctly estimated from the supplied measurements despite anomalous readings. The calibration results should be written to `/app/calibration/calibration_results.json` with tau values accurate within 10% relative error for each sensor.
