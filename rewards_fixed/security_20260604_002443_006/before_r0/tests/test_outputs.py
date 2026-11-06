from pathlib import Path


def test_recovery_file_exists():
    """Verify the solver created the recovered secret file."""
    recovered = Path("/app/recovered_secret.txt")
    assert recovered.exists(), (
        f"Expected recovered secret file at {recovered} but it does not exist."
    )


def test_secret_recovered():
    """Verify the recovered file contains the correct secret key."""
    recovered = Path("/app/recovered_secret.txt")
    assert recovered.exists(), "Recovery file must exist first."
    content = recovered.read_text().strip()

    # The hidden flag extracted from the steganography image
    expected = "FLAG=ST3G4N0GR4PHY_2024"

    assert expected in content, (
        f"Recovered content does not contain the expected secret key.\n"
        f"Expected to find: {expected}\n"
        f"Got: {content}"
    )
