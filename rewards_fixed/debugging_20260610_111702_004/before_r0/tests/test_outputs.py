import hashlib
import os
import struct
import zlib
from pathlib import Path

import pytest

EXPECTED_FILES = {
    "img_01_alley.png": "7881f185344c645b795752c203d600cdd196f02f24e435161c9ad1ecb0a0d694",
    "img_02_bridge.png": "98a2d45b52736963171c46c643f923608e1c8614572e7f154b93a34ce65a1c71",
    "img_03_canyon.png": "81239631bda85a81db0fd70b5b34c5d11b4b8f89003c8eec3b5a9209beb58074",
    "img_04_dock.png": "65dd2b1df2a8effc7afa0f054460219e56d35e325a106eaf5c64d3b8a7a9149d",
    "img_05_estuary.png": "35a53f58c10ad96fe3563cea0dd8660815caf6b7885d162a124a59eef412882c",
    "img_06_forest.png": "74970b8979711729e43f7cf4bd52161de1aabfe48ea1ba3a4597ba76c60b4f21",
    "img_07_garden.png": "6f76b114425580ac17ab3b9944702fdc52a147fa592ce3b6a751d69600ad51f7",
    "img_08_harbor.png": "a90d51d1249ca04207a16714fd6fa443059bb9d88cc644efec9052de6fffab99",
    "img_09_island.png": "3ba40a4caa6befcc2d3bf897b6978df2e17c112cde1bc314565717de31b8cf97",
    "img_10_jungle.png": "8b8cd8d8f48e096d181caa436b036759ce5546931234503f74ef2ec2db83cc22",
    "img_11_knoll.png": "79f9e7e7082d20c2fbd58dd2cc07b54636ebb38f1878adcb7181659b65da115e",
    "img_12_lagoon.png": "1f2d4da35dc8b90b91501cc87214107e19ccd30e5846b469a2923f533e01024c",
    "img_13_mesa.png": "01d27c647ba2f57fb7a289de6c67582930442a3f9d3a47861aa10d9c3c2d9d18",
    "img_14_narrows.png": "4a61ec86345817ba9f1108090ca386b9bd8fbb227c9977ec1dbcc1313feb9329",
    "img_15_oasis.png": "9735ca2cf22bfb712941907797fe2944a33425ff56fb5525f07f467bfcc65dcb",
    "img_16_peak.png": "a83dd7ac3ff116846c0319ea523624b6f01e5f185038d04974dd04e9578f831c",
    "img_17_quarry.png": "6ab1b460fece3269250022095e9d5f4ce24459cf9ca596e88c21946d09d9ae44",
    "img_18_ridge.png": "0c328054a4b121450ee5a57cd4e294eade6e4aef6a801d13dd03bdbba201339c",
    "img_19_summit.png": "f3168f5520793032ec501f69be4f5ee4fa5a8e5d5e8998d7f488fb118dcf5e82",
    "img_20_valley.png": "45ce40482d854de1ad8469fea15c35c5b81d730f1627ac4644cac50a693c4c65",
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
    """Check that each recovered file matches expected checksum and is a valid PNG."""
    recovered_dir = Path("/app/recovered")
    errors = []
    for filename, expected_sha in EXPECTED_FILES.items():
        filepath = recovered_dir / filename
        if not filepath.exists():
            errors.append(f"{filename}: file not found")
            continue
        data = filepath.read_bytes()

        # Verify checksum
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != expected_sha:
            errors.append(
                f"{filename}: SHA256 mismatch "
                f"(got {actual_sha[:16]}..., expected {expected_sha[:16]}...)"
            )
            continue  # Skip PNG validation if checksum is wrong

        # Verify PNG structure
        png_errors = validate_png(data)
        if png_errors:
            errors.append(f"{filename}: {png_errors}")

    assert len(errors) == 0, (
        f"Recovered file validation errors: {errors}"
    )


def validate_png(data):
    """Validate PNG structure. Returns list of error strings."""
    errors = []
    if len(data) < 8:
        return ["File too short for PNG signature"]

    # Check signature
    expected_sig = b'\x89PNG\r\n\x1a\n'
    if data[:8] != expected_sig:
        errors.append(f"Bad signature: {data[:8].hex()} != {expected_sig.hex()}")

    if errors:
        return errors

    pos = 8
    seen_iend = False
    while pos < len(data):
        if pos + 12 > len(data):
            errors.append(f"Truncated chunk at offset {pos}")
            break

        length = struct.unpack('>I', data[pos:pos+4])[0]
        chunk_type = data[pos+4:pos+8]

        if pos + 12 + length > len(data):
            errors.append(
                f"Chunk {chunk_type} at offset {pos}: "
                f"length {length} exceeds file size"
            )
            break

        chunk_data = data[pos+8:pos+8+length]
        stored_crc = struct.unpack('>I', data[pos+8+length:pos+12+length])[0]

        # Verify CRC
        computed_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if computed_crc != stored_crc:
            errors.append(
                f"Chunk {chunk_type} at offset {pos}: "
                f"CRC mismatch (stored=0x{stored_crc:08x}, "
                f"computed=0x{computed_crc:08x})"
            )

        if chunk_type == b'IEND':
            seen_iend = True
            if length != 0:
                errors.append("IEND chunk has non-zero length")

        pos += 12 + length

    if not seen_iend:
        errors.append("No IEND chunk found")

    return errors