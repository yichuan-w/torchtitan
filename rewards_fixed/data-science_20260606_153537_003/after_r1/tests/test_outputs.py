import os


def test_result_file_exists():
    """The solver must create /app/best_model.txt with the answer."""
    assert os.path.exists("/app/best_model.txt"), (
        "File /app/best_model.txt does not exist. "
        "The solver should have created it with the best model name."
    )


def test_result_file_nonempty():
    """The result file should contain exactly one line with the model name."""
    assert os.path.exists("/app/best_model.txt"), "best_model.txt does not exist"
    with open("/app/best_model.txt", "r") as f:
        content = f.read()
    assert len(content.strip()) > 0, "best_model.txt is empty"


def test_correct_model():
    """The best model after proper normalization, filtering, deduplication and aggregation."""
    expected_variants = {"xgboost", "xgb", "xgboost_v2"}
    with open("/app/best_model.txt", "r") as f:
        content = f.read().strip()
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    assert len(lines) >= 1, "best_model.txt should have at least one non-empty line"
    result = lines[0].lower()
    assert result in expected_variants, (
        f"Expected best model 'XGBoost' but got '{lines[0]}'. "
        "Make sure you parsed all files, normalized names, excluded non-accuracy entries, "
        "filtered correctly, handled duplicates, and required >= 5 datasets per model."
    )
