import json
import os


def test_results_file_exists():
    """Verify forecast_results.json exists and is valid JSON."""
    path = "/app/forecast_results.json"
    assert os.path.exists(path), f"{path} does not exist"
    assert os.path.getsize(path) > 0, f"{path} is empty"

    with open(path) as f:
        data = json.load(f)
    assert isinstance(data, dict), "forecast_results.json should be a JSON object"


def test_results_structure():
    """Verify forecast_results.json has all required keys."""
    with open("/app/forecast_results.json") as f:
        data = json.load(f)

    required_keys = {"cv_rmse_mean", "cv_rmse_std", "cv_rmse_scores", "predictions"}
    actual_keys = set(data.keys())
    assert required_keys == actual_keys, (
        f"Expected keys {required_keys}, got {actual_keys}"
    )

    assert len(data["cv_rmse_scores"]) == 5, (
        f"Expected 5 CV scores, got {len(data['cv_rmse_scores'])}"
    )

    assert len(data["predictions"]) == 10, (
        f"Expected 10 predictions, got {len(data['predictions'])}"
    )


def test_rmse_not_overly_optimistic():
    """Verify CV RMSE is not artificially low due to time series leakage.

    With standard k-fold (random shuffle), RMSE is ~1.9 due to temporal leakage.
    With proper time series split, RMSE should be >= 2.05.
    """
    with open("/app/forecast_results.json") as f:
        data = json.load(f)

    rmse_mean = data["cv_rmse_mean"]

    assert rmse_mean >= 3.45, (
        f"RMSE {rmse_mean:.4f} is too low — time series data leakage likely present. "
        "Make sure cross-validation respects temporal order. Standard k-fold "
        "lets the model train on future data and test on the past. Use "
        "TimeSeriesSplit instead."
    )

    # Not completely broken
    assert rmse_mean <= 6.0, (
        f"RMSE {rmse_mean:.4f} is too high — the model may be broken."
    )


def test_rmse_scores_are_positive():
    """Verify all CV scores are positive."""
    with open("/app/forecast_results.json") as f:
        data = json.load(f)

    for i, s in enumerate(data["cv_rmse_scores"]):
        assert s > 0, f"Fold {i}: RMSE {s} should be positive"


def test_predictions_are_reasonable():
    """Verify predictions are within a reasonable range."""
    with open("/app/forecast_results.json") as f:
        data = json.load(f)

    preds = data["predictions"]
    for i, p in enumerate(preds):
        # The time series has a strong upward trend, predictions can go up to ~45
        assert -20 <= p <= 50, (
            f"Prediction {i}: {p} is outside plausible range [-20, 50]"
        )


