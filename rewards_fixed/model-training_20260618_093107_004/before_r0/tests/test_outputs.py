import os
import lightgbm as lgb
import pandas as pd
from sklearn.metrics import accuracy_score

ACCURACY_THRESHOLD = 0.872
MAX_MODEL_SIZE = 500 * 1024  # 500 KB


def test_model_accuracy():
    """Test that the LightGBM model achieves sufficient accuracy on the private test set."""
    model_path = "/app/model.txt"
    assert os.path.exists(model_path), f"Model file not found at {model_path}"

    model = lgb.Booster(model_file=model_path)

    # Load private test data
    test_path = "/tests/private_test.csv"
    assert os.path.exists(test_path), f"Private test data not found at {test_path}"

    test_df = pd.read_csv(test_path)
    X_test = test_df.drop("income", axis=1)
    y_test = test_df["income"]

    # Convert categorical columns to category dtype (matching training)
    cat_cols = X_test.select_dtypes(include=["object"]).columns.tolist()
    for col in cat_cols:
        X_test[col] = X_test[col].astype("category")

    # Predict
    y_pred_prob = model.predict(X_test)
    y_pred = (y_pred_prob > 0.5).astype(int)

    accuracy = accuracy_score(y_test, y_pred)
    assert accuracy >= ACCURACY_THRESHOLD, (
        f"Model accuracy {accuracy:.4f} is below threshold {ACCURACY_THRESHOLD}"
    )


def test_model_size():
    """Test that the model file is under the size limit."""
    model_path = "/app/model.txt"
    assert os.path.exists(model_path), f"Model file not found at {model_path}"

    size = os.path.getsize(model_path)
    assert size < MAX_MODEL_SIZE, (
        f"Model size {size} bytes ({size / 1024:.1f} KB) exceeds limit of "
        f"{MAX_MODEL_SIZE} bytes ({MAX_MODEL_SIZE / 1024:.0f} KB)"
    )