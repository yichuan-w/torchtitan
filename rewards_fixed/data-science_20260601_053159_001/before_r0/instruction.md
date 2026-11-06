I'm working on a time series forecasting model in `/app/forecast.py`. It loads data from `/app/data/timeseries.csv`, creates lag features, evaluates a Random Forest with cross-validation, and outputs metrics plus 10-step-ahead predictions to `/app/forecast_results.json`.

The cross-validation RMSE looks off — I'm getting around 3.23 but given the temporal structure of this data I'd expect a higher value. I think there's a methodological issue with how the validation is set up for time series, but I can't spot the exact problem.

Find the issue, fix it, and re-run to produce corrected results. Don't change the model type, features, or prediction logic — only the evaluation methodology needs fixing.
