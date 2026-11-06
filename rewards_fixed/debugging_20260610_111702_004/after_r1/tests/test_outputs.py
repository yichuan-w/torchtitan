import hashlib
from pathlib import Path

import pytest

try:
    from PIL import Image
except ImportError:
    Image = None

EXPECTED_FILES = {
    "img_01_alley.png": ((120, 80), "dd4b5856c27bc15c98e690024f1f01a52587ab3fd7d03a14440f5af007080200"),
    "img_02_bridge.png": ((150, 90), "29913b5dcc4ad73977a5c8f6346eea77ecd68db4ac1ea169993ac07713176ca1"),
    "img_03_canyon.png": ((130, 110), "ad7802eb466a93c7f171060eac3a5ff1807c1cf1de797fededbfd182c9907a74"),
    "img_04_dock.png": ((100, 140), "e99fb473aabc0b272ad23384ed229c83b2ed0bd3eeb9037472df5104da76d69d"),
    "img_05_estuary.png": ((160, 100), "828cd5a6ac7b0f332f17cb4056aa4714a69119f474e6fd2733c09b064b15e3ee"),
    "img_06_forest.png": ((200, 150), "8c95b3ce6869380293a4da726bf7288a1e2a03113fcbb8a5c307b8a9f40d7786"),
    "img_07_garden.png": ((180, 180), "971eb43cfbbc1dff4a6ed1d140e9ecc83c1d1692e92acb72940d4835df7381c5"),
    "img_08_harbor.png": ((250, 180), "f9f0e4f91ac72ad4c2052ba5ddd998cd8f522149fbbba0f8a4a8099cca18790a"),
    "img_09_island.png": ((220, 200), "1ce590c1ac1ffddddd6a5e55e8499f4f654d7611770b06ab99a76ed562f3c7c4"),
    "img_10_jungle.png": ((300, 200), "f493917035ad4787b972716f4124d40fa4a06a82b244b16ce22bf69e522aa26e"),
    "img_11_knoll.png": ((128, 128), "93961d363962a6fa5182fcb612a717b571b60b04fc9a7d20f36be308a711287b"),
    "img_12_lagoon.png": ((200, 128), "2cfdd72c8d23668680838b58ab34fac029511033aefeef52bc62aa2f753bbf18"),
    "img_13_mesa.png": ((140, 120), "72a6afed1afe8c975cd93db1438652df1ee69067311e0cfb5318d2f1771a0253"),
    "img_14_narrows.png": ((90, 160), "a0aaa65618c39d36900e168ae6807deb147555d71568840721e308874375b5f8"),
    "img_15_oasis.png": ((160, 120), "84aba2860fda6d57a6c6fc448a6e1c65869b0eacd8130a2e9207176b5b38f714"),
    "img_16_peak.png": ((280, 220), "937332a4bcc87b29534e5093f61d18689f1a87fd86ec5a73436a8a7b9a6237f1"),
    "img_17_quarry.png": ((240, 200), "c4ca8e92440b22917fd86cfdc788f177f136e9a37155887c773051e195bf44a4"),
    "img_18_ridge.png": ((320, 180), "6d59da5f2a0f8d7c40be641d60b80e4ea297cca9123eca5cede5ac94c1eccd36"),
    "img_19_summit.png": ((256, 256), "950051c966c26dc8577077089b27e90c83eac75c29be4b3eefc58f4fe28189c6"),
    "img_20_valley.png": ((350, 250), "c975edc9f2418e5cc805ff9085562814c056a7d2fdb50206bc55a27ce258cd56"),
}


def test_recovered_directory_has_files():
    """Check that /app/recovered/ directory exists and contains recovered files."""
    recovered_dir = Path("/app/recovered")
    assert recovered_dir.exists(), (
        "Directory /app/recovered/ does not exist. "
        "Create it and place recovered PNG files there."
    )
    assert recovered_dir.is_dir(), (
        "/app/recovered/ exists but is not a directory."
    )
    png_files = list(recovered_dir.glob("*.png"))
    assert len(png_files) > 0, (
        "Directory /app/recovered/ exists but contains no PNG files. "
        "Place the recovered PNG files in /app/recovered/."
    )


def test_all_files_recovered():
    """Check that all expected PNG files are present in /app/recovered/."""
    recovered_dir = Path("/app/recovered")
    missing = []
    for filename in EXPECTED_FILES:
        filepath = recovered_dir / filename
        if not filepath.exists():
            missing.append(filename)
    assert len(missing) == 0, (
        f"Missing recovered files: {missing}. "
        f"All {len(EXPECTED_FILES)} files from /app/corrupted/ "
        f"must be recovered in /app/recovered/."
    )


def test_recovered_files_valid():
    """Check that each recovered PNG has the exact original dimensions and pixels."""
    assert Image is not None, "Verifier requires Pillow to decode PNG pixels"
    recovered_dir = Path("/app/recovered")
    errors = []
    for filename, (expected_size, expected_pixel_sha) in EXPECTED_FILES.items():
        filepath = recovered_dir / filename
        if not filepath.exists():
            errors.append(f"{filename}: file not found")
            continue
        try:
            with Image.open(filepath) as image:
                if image.format != "PNG":
                    errors.append(f"{filename}: not a PNG image")
                    continue
                image.load()
                actual_size = image.size
                actual_pixel_sha = hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
        except Exception as exc:
            errors.append(f"{filename}: cannot decode PNG ({exc})")
            continue
        if actual_size != expected_size:
            errors.append(f"{filename}: dimensions {actual_size}, expected {expected_size}")
        if actual_pixel_sha != expected_pixel_sha:
            errors.append(f"{filename}: decoded pixels do not match the original")

    assert len(errors) == 0, (
        f"Recovered file validation errors: {errors}"
    )
