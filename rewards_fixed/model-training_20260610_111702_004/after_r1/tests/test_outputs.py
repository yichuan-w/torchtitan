import os
import pickle
import subprocess
import sys


ACCURACY_THRESHOLD = 0.865
MAX_MODEL_SIZE = 2 * 1024 * 1024  # 2 MB


def test_model_file_exists():
    """Verify the model file exists at /app/model.pkl."""
    assert os.path.exists("/app/model.pkl"), (
        "Model file not found at /app/model.pkl. "
        "The trained model must be saved as /app/model.pkl"
    )


def test_model_file_size():
    """Verify the model file is under 2 MB."""
    model_size = os.path.getsize("/app/model.pkl")
    assert model_size < MAX_MODEL_SIZE, (
        f"Model file is too large: {model_size / (1024 * 1024):.2f} MB. "
        f"Must be under {MAX_MODEL_SIZE / (1024 * 1024):.0f} MB"
    )


def test_model_accuracy():
    """Verify the model achieves at least 0.865 accuracy on the held-out test set."""
    # Load the model
    with open("/app/model.pkl", "rb") as f:
        model = pickle.load(f)

    # Load the test data
    import pandas as pd
    test_df = pd.read_csv("/tests/test_data.csv")

    # Separate features and target
    target_col = "income"
    X_test = test_df.drop(columns=[target_col])
    y_test = test_df[target_col]

    # Map target to binary: <=50K -> 0, >50K -> 1
    y_test_binary = (y_test.str.strip() == ">50K").astype(int)

    # Identify categorical columns for LightGBM
    categorical_cols = X_test.select_dtypes(include=["object"]).columns.tolist()

    # Make predictions
    try:
        # Try LightGBM-style predict (handles categorical features natively)
        if hasattr(model, "predict") and categorical_cols:
            for col in categorical_cols:
                if col in X_test.columns:
                    X_test[col] = X_test[col].astype("category")
            y_pred = model.predict(X_test)
        else:
            y_pred = model.predict(X_test)
    except Exception:
        # Fallback: try without categorical conversion
        y_pred = model.predict(X_test)

    # The public interface does not prescribe whether class labels are returned
    # as the source strings or their binary encoding. Compare either permitted
    # representation to the same binary held-out target.
    import numpy as np
    y_pred_array = np.asarray(y_pred)
    if y_pred_array.dtype.kind in {"O", "U", "S"}:
        y_pred = pd.Series(y_pred_array).astype(str).str.strip().map(
            {"<=50K": 0, ">50K": 1}
        ).to_numpy()

    # Calculate accuracy
    from sklearn.metrics import accuracy_score
    accuracy = accuracy_score(y_test_binary, y_pred)
    assert accuracy >= ACCURACY_THRESHOLD, (
        f"Model accuracy {accuracy:.4f} is below the required threshold of {ACCURACY_THRESHOLD}. "
        f"Try improving preprocessing, feature engineering, or hyperparameter tuning."
    )


def test_model_is_valid_pickle():
    """Verify the model file is a valid pickle that can be loaded."""
    with open("/app/model.pkl", "rb") as f:
        model = pickle.load(f)
    assert hasattr(model, "predict"), (
        "The loaded object does not have a 'predict' method. "
        "Make sure you saved a trained model, not something else."
    )
