"""Tests for MovieLens SVD recommendation model training task."""

import os
import pickle

import pytest


def test_model_exists():
    """Verify the model file exists at /app/model.pkl."""
    assert os.path.exists("/app/model.pkl"), (
        "Model file not found at /app/model.pkl. "
        "Save the trained SVD model as /app/model.pkl using pickle."
    )


def test_model_loadable():
    """Verify the saved file can be loaded as a valid surprise SVD model."""
    with open("/app/model.pkl", "rb") as f:
        model = pickle.load(f)

    # Check it has the expected SVD interface
    assert hasattr(model, "predict"), (
        "Loaded model does not have 'predict' method. "
        "Expected a trained surprise SVD model."
    )
    assert hasattr(model, "pu"), (
        "Loaded model does not have user factors (pu). "
        "Expected a trained surprise SVD model."
    )
    assert hasattr(model, "qi"), (
        "Loaded model does not have item factors (qi). "
        "Expected a trained surprise SVD model."
    )


def test_rmse_below_threshold():
    """Verify the model achieves RMSE < 0.914 on the held-out test set."""
    import pandas as pd
    from surprise import Dataset, Reader
    from surprise.accuracy import rmse

    # Preserve the integer raw-ID representation used by the provided starter.
    ratings = pd.read_csv("/tests/heldout.csv")
    reader = Reader(rating_scale=(1, 5))
    data = Dataset.load_from_df(ratings[["userId", "movieId", "rating"]], reader)
    testset = data.build_full_trainset().build_testset()

    # Load the trained model
    with open("/app/model.pkl", "rb") as f:
        model = pickle.load(f)

    predictions = model.test(testset)
    error = rmse(predictions)

    assert error < 0.914, (
        f"Model RMSE is {error:.4f}, which is above the 0.914 threshold. "
        "Tune SVD hyperparameters (n_factors, n_epochs, lr_all, reg_all) "
        "to improve prediction accuracy."
    )


def test_model_size():
    """Verify the model file is under 3 MB."""
    size_bytes = os.path.getsize("/app/model.pkl")
    size_mb = size_bytes / (1024 * 1024)

    assert size_bytes < 3 * 1024 * 1024, (
        f"Model file size is {size_mb:.1f} MB, exceeding the 3 MB limit. "
        "Reduce n_factors or use fewer parameters to shrink the model."
    )
