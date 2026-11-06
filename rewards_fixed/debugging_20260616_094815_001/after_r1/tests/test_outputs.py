"""Tests for the imgproc C image processing utility.

All tests verify the END STATE after the solver has fixed the bugs.
"""

import os
import struct
import subprocess
import tempfile
from pathlib import Path


# ---- Helpers ----

def build():
    """Build the imgproc binary. Returns True on success."""
    result = subprocess.run(
        ["make", "clean", "&&", "make"],
        shell=True,
        cwd="/app",
        capture_output=True,
        text=True,
    )
    # Try again without clean if it fails
    result = subprocess.run(
        ["make"],
        cwd="/app",
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def read_ppm(path):
    """Read a P6 PPM file and return (width, height, maxval, pixels).
    pixels is a flat list of (r, g, b) tuples."""
    with open(path, "rb") as f:
        magic = f.readline().strip()
        if magic != b"P6":
            raise ValueError(f"Expected P6, got {magic}")
        # Skip comments
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        # Now line has "width height"
        parts = line.split()
        if len(parts) < 2:
            # Might have wrapped, handle
            line2 = f.readline()
            parts = line.strip().split() + line2.strip().split()
        width, height = int(parts[0]), int(parts[1])
        maxval_line = f.readline().strip()
        # maxval_line might be empty if it was part of the previous read
        if not maxval_line:
            maxval_line = f.readline().strip()
        maxval = int(maxval_line)

        data = f.read()
        pixels = []
        for i in range(0, len(data), 3):
            if i + 3 <= len(data):
                pixels.append((data[i], data[i + 1], data[i + 2]))
        return width, height, maxval, pixels


def compute_blur_expected(width, height, pixels):
    """Compute expected 3x3 box blur output (correct version with proper bounds)."""
    result = []
    for y in range(height):
        for x in range(width):
            sum_r, sum_g, sum_b = 0, 0, 0
            count = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width:
                        idx = ny * width + nx
                        r, g, b = pixels[idx]
                        sum_r += r
                        sum_g += g
                        sum_b += b
                        count += 1
            result.append((sum_r // count, sum_g // count, sum_b // count))
    return result


CONVENTIONAL_LUMINANCE_WEIGHTS = tuple(
    (red / 1000, green / 1000, (1000 - red - green) / 1000)
    for red in range(200, 401)
    for green in range(450, 751)
    if 50 <= 1000 - red - green <= 200
)


def compute_grayscale_expected(width, height, pixels, weights):
    """Compute expected grayscale output using conventional luminance weights."""
    result = []
    red_weight, green_weight, blue_weight = weights
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            r, g, b = pixels[idx]
            gray = int(red_weight * r + green_weight * g + blue_weight * b)
            # Grayscale outputs as R=G=B=gray since we write P6
            result.append((gray, gray, gray))
    return result


def compute_edge_expected(width, height, pixels, weights):
    """Compute expected Sobel edge detection output.
    Border pixels should be 0. Interior pixels use Sobel operator."""
    import math

    result = []
    for y in range(height):
        for x in range(width):
            if y == 0 or y == height - 1 or x == 0 or x == width - 1:
                result.append((0, 0, 0))
            else:
                # Get grayscale values for 3x3 neighborhood
                g = []
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        idx = ny * width + nx
                        r, gv, b = pixels[idx]
                        gray = int(
                            weights[0] * r + weights[1] * gv + weights[2] * b
                        )
                        g.append(gray)

                gx = -g[0] + g[2] - 2 * g[3] + 2 * g[5] - g[6] + g[8]
                gy = -g[0] - 2 * g[1] - g[2] + g[6] + 2 * g[7] + g[8]
                mag = int(math.sqrt(gx * gx + gy * gy))
                if mag > 255:
                    mag = 255
                result.append((mag, mag, mag))
    return result


def compute_edge_from_grayscale(width, height, grayscale_pixels):
    """Compute Sobel output from already converted grayscale P6 pixels."""
    result = [(0, 0, 0)] * (width * height)
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            values = [
                grayscale_pixels[(y + dy) * width + (x + dx)][0]
                for dy in (-1, 0, 1)
                for dx in (-1, 0, 1)
            ]
            gx = (
                -values[0] + values[2] - 2 * values[3]
                + 2 * values[5] - values[6] + values[8]
            )
            gy = (
                -values[0] - 2 * values[1] - values[2]
                + values[6] + 2 * values[7] + values[8]
            )
            magnitude = min(255, int((gx * gx + gy * gy) ** 0.5))
            result[y * width + x] = (magnitude, magnitude, magnitude)
    return result


def run_imgproc(op, input_path, output_path):
    """Run ./imgproc <op> <input> <output>. Returns (returncode, stderr)."""
    result = subprocess.run(
        ["./imgproc", op, input_path, output_path],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stderr


def run_valgrind(op, input_path):
    """Run valgrind on imgproc. Returns (returncode, stderr)."""
    result = subprocess.run(
        [
            "valgrind",
            "--leak-check=full",
            "--error-exitcode=99",
            "-q",
            "./imgproc",
            op,
            input_path,
            "/dev/null",
        ],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stderr


def pixels_match(actual, expected, tolerance=1):
    return len(actual) == len(expected) and all(
        abs(actual_channel - expected_channel) <= tolerance
        for actual_pixel, expected_pixel in zip(actual, expected)
        for actual_channel, expected_channel in zip(actual_pixel, expected_pixel)
    )


def matching_grayscale_weights(width, height, input_pixels, output_pixels):
    return [
        weights
        for weights in CONVENTIONAL_LUMINANCE_WEIGHTS
        if pixels_match(
            output_pixels,
            compute_grayscale_expected(width, height, input_pixels, weights),
        )
    ]


# ---- Tests ----


def test_binary_exists():
    """The imgproc binary should exist and be executable."""
    path = Path("/app/imgproc")
    assert path.exists(), f"{path} does not exist — did you build with 'make'?"
    assert os.access(path, os.X_OK), f"{path} is not executable"


def test_grayscale_runs():
    """Grayscale conversion should run without crashing."""
    ret, stderr = run_imgproc("grayscale", "/app/small.ppm", "/tmp/gray_out.ppm")
    assert ret == 0, f"grayscale failed (returncode={ret}): {stderr}"
    assert Path("/tmp/gray_out.ppm").exists()


def test_grayscale_correct():
    """Grayscale output should be correct for small.ppm."""
    _, _, _, input_pix = read_ppm("/app/small.ppm")
    width, height, _, output_pix = read_ppm("/tmp/gray_out.ppm")

    matching_weights = matching_grayscale_weights(
        width, height, input_pix, output_pix
    )
    assert matching_weights, (
        "Grayscale pixels do not match a conventional weighted RGB luminance "
        "approximation (±1 per channel)"
    )


def test_blur_runs():
    """Blur operation should run without crashing on test_img.ppm."""
    ret, stderr = run_imgproc("blur", "/app/test_img.ppm", "/tmp/blur_out.ppm")
    assert ret == 0, f"blur failed (returncode={ret}): {stderr}"
    assert Path("/tmp/blur_out.ppm").exists()


def test_blur_correct():
    """Blur output should be correct for test_img.ppm."""
    _, _, _, input_pix = read_ppm("/app/test_img.ppm")
    width, height, _, output_pix = read_ppm("/tmp/blur_out.ppm")

    expected = compute_blur_expected(width, height, input_pix)

    assert len(output_pix) == len(expected), (
        f"Wrong number of pixels: got {len(output_pix)}, expected {len(expected)}"
    )

    for i, (got, exp) in enumerate(zip(output_pix, expected)):
        for c in range(3):
            assert abs(got[c] - exp[c]) <= 1, (
                f"Pixel {i}: got {got}, expected {exp}"
            )


def test_edge_runs():
    """Edge detection should run without crashing on test_img.ppm."""
    ret, stderr = run_imgproc("edge", "/app/test_img.ppm", "/tmp/edge_out.ppm")
    assert ret == 0, f"edge failed (returncode={ret}): {stderr}"
    assert Path("/tmp/edge_out.ppm").exists()


def test_edge_output_valid():
    """Edge detection output should have all pixel values in valid range [0, 255].
    This catches the uninitialized memory bug (border pixels with garbage values)."""
    _, _, _, pixels = read_ppm("/tmp/edge_out.ppm")
    for i, (r, g, b) in enumerate(pixels):
        assert 0 <= r <= 255, f"Pixel {i}: red={r} out of range [0, 255]"
        assert 0 <= g <= 255, f"Pixel {i}: green={g} out of range [0, 255]"
        assert 0 <= b <= 255, f"Pixel {i}: blue={b} out of range [0, 255]"


def test_edge_borders_zero():
    """Border pixels in edge detection output should be zero (not garbage)."""
    width, height, _, pixels = read_ppm("/tmp/edge_out.ppm")

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            if y == 0 or y == height - 1 or x == 0 or x == width - 1:
                r, g, b = pixels[idx]
                assert r == 0 and g == 0 and b == 0, (
                    f"Border pixel ({x},{y}) should be (0,0,0), got ({r},{g},{b})"
                )


def test_edge_interior_correct():
    """Interior pixels should use Sobel with the same grayscale conversion."""
    _, _, _, input_pix = read_ppm("/app/test_img.ppm")
    width, height, _, output_pix = read_ppm("/tmp/edge_out.ppm")
    gray_ret, gray_stderr = run_imgproc(
        "grayscale", "/app/test_img.ppm", "/tmp/edge_gray_out.ppm"
    )
    assert gray_ret == 0, f"grayscale failed (returncode={gray_ret}): {gray_stderr}"
    _, _, _, grayscale_pixels = read_ppm("/tmp/edge_gray_out.ppm")
    expected = compute_edge_from_grayscale(width, height, grayscale_pixels)
    interior_indexes = [
        y * width + x
        for y in range(1, height - 1)
        for x in range(1, width - 1)
    ]
    assert all(
        all(abs(output_pix[index][channel] - expected[index][channel]) <= 1
            for channel in range(3))
        for index in interior_indexes
    ), "Sobel output does not use the same grayscale conversion"


def test_long_comment_no_crash():
    """Program should handle PPM files with long comments without crashing."""
    ret, stderr = run_imgproc(
        "grayscale", "/app/sample3.ppm", "/tmp/long_comment_out.ppm"
    )
    assert ret == 0, (
        f"Processing file with long comment crashed (returncode={ret}): {stderr}"
    )


def test_valgrind_grayscale_clean():
    """Valgrind should report no errors for grayscale operation."""
    ret, stderr = run_valgrind("grayscale", "/app/small.ppm")
    assert ret != 99, f"Valgrind found errors:\n{stderr}"
    assert ret == 0, f"Valgrind/gray return code unexpected: {ret}\n{stderr}"


def test_valgrind_blur_clean():
    """Valgrind should report no errors for blur operation."""
    ret, stderr = run_valgrind("blur", "/app/small.ppm")
    assert ret != 99, f"Valgrind found errors:\n{stderr}"
    assert ret == 0, f"Valgrind/blur return code unexpected: {ret}\n{stderr}"


def test_valgrind_edge_clean():
    """Valgrind should report no errors for edge detection."""
    ret, stderr = run_valgrind("edge", "/app/small.ppm")
    assert ret != 99, f"Valgrind found errors:\n{stderr}"
    assert ret == 0, f"Valgrind/edge return code unexpected: {ret}\n{stderr}"


def test_valgrind_long_comment_clean():
    """Valgrind should report no errors for long comment file."""
    ret, stderr = run_valgrind("grayscale", "/app/sample3.ppm")
    assert ret != 99, f"Valgrind found errors:\n{stderr}"
    assert ret == 0, (
        f"Valgrind/long_comment return code unexpected: {ret}\n{stderr}"
    )
