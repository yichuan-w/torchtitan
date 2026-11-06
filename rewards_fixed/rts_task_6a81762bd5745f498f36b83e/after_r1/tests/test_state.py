import os
import json

def test_result_file_exists():
    assert os.path.exists("/app/result.txt"), "Result file /app/result.txt does not exist."

def test_result_format_and_value():
    with open("/app/result.txt", "r") as f:
        content = f.read()
    assert content.endswith("\n"), "The result file must end with a newline."
    stripped = content.strip()
    assert stripped, "The result file is empty."
    parts = stripped.split()
    assert len(parts) == 1, f"Expected a single floating-point number, got {len(parts)} parts."
    try:
        similarity = float(parts[0])
    except ValueError:
        assert False, f"Expected a floating-point number, got {repr(parts[0])}"
    assert -1.0 <= similarity <= 1.0, f"Cosine similarity must be between -1.0 and 1.0, got {similarity}"

def test_training_summary_exists():
    assert os.path.exists("/app/training_summary.txt"), (
        "Training summary file /app/training_summary.txt does not exist."
    )

def test_training_summary_structure():
    with open("/app/training_summary.txt", "r") as f:
        raw = f.read()
    assert raw.strip(), "Training summary file is empty."
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError as e:
        assert False, f"Training summary is not valid JSON: {e}"
    required_keys = {"similarity", "dimensions", "compass_words", "word"}
    missing = required_keys - set(summary.keys())
    assert not missing, f"Training summary missing required keys: {missing}"
    assert isinstance(summary["similarity"], (float, int)), (
        "Key 'similarity' must be numeric."
    )
    assert isinstance(summary["dimensions"], int), (
        "Key 'dimensions' must be an integer."
    )
    assert isinstance(summary["compass_words"], int), (
        "Key 'compass_words' must be an integer."
    )
    assert isinstance(summary["word"], str), (
        "Key 'word' must be a string."
    )

def test_similarity_consistency():
    """Check that the similarity in result.txt matches the training summary."""
    with open("/app/result.txt", "r") as f:
        result_val = float(f.read().strip())
    with open("/app/training_summary.txt", "r") as f:
        summary = json.load(f)
    summary_sim = summary["similarity"]
    assert abs(result_val - summary_sim) < 1e-9, (
        f"Similarity in result.txt ({result_val}) does not match "
        f"training summary ({summary_sim})"
    )

def test_summary_values():
    """Check the summary values fixed by the public instruction."""
    with open("/app/training_summary.txt", "r") as f:
        summary = json.load(f)
    assert summary["dimensions"] > 0, (
        f"Embedding dimensions must be positive, got {summary['dimensions']}."
    )
    assert os.path.exists("/app/compass.txt"), (
        "compass.txt not found; cannot verify compass_words."
    )
    with open("/app/compass.txt", "r") as f:
        actual_lines = sum(1 for line in f if line.strip())
    assert actual_lines > 0, "compass.txt is empty, cannot validate compass_words."
    assert summary["compass_words"] == actual_lines, (
        f"compass_words ({summary['compass_words']}) does not match "
        f"non-empty lines in compass.txt ({actual_lines})."
    )
    assert summary["word"] == "like", (
        f"Target word must be 'like', got '{summary['word']}'"
    )
