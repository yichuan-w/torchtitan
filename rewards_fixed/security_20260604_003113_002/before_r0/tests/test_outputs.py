"""Tests for the multi-layer security forensics task."""

import os
from pathlib import Path


def test_solution_file_exists():
    """Test that the solution file exists at the expected path."""
    solution_path = Path("/app/solution.txt")
    assert os.path.exists(solution_path), (
        f"Solution file {solution_path} does not exist. "
        "Create /app/solution.txt with the decrypted content."
    )


def test_solution_content():
    """Test that the solution file contains the correct decrypted content."""
    solution_path = Path("/app/solution.txt")
    
    with open(solution_path, "r") as f:
        content = f.read().strip()
    
    expected = "CONFIDENTIAL: Project Alpha Launch Key: ALPHA-2024-XJ72-K9LM-NP35"
    
    assert content == expected, (
        f"Solution file contains incorrect content.\n"
        f"Expected: {expected}\n"
        f"Got: {content}\n"
        f"Hint: You need to (1) find the passphrase in git history, "
        f"(2) extract hidden data from the image, and (3) decrypt it."
    )
