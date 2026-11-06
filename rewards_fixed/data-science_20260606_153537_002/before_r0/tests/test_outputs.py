import os
import subprocess
import csv


def test_return_levels_file_exists():
    """Test that return_levels.csv exists and has correct structure."""
    path = "/app/return_levels.csv"
    assert os.path.exists(path), f"File not found: {path}"

    with open(path, "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 3, f"Expected 3 rows (T=10, 50, 100), got {len(rows)}"

    periods = {int(row["return_period"]) for row in rows}
    assert periods == {10, 50, 100}, f"Expected return periods 10, 50, 100, got {periods}"

    for row in rows:
        for col in ["estimate", "ci_lower", "ci_upper"]:
            assert col in row, f"Missing '{col}' column"

        est = float(row["estimate"])
        lo = float(row["ci_lower"])
        hi = float(row["ci_upper"])

        assert lo < est < hi, (
            f"T={row['return_period']}: ci_lower ({lo}) < estimate ({est}) < ci_upper ({hi}) violated"
        )


def test_return_level_estimates_reasonable():
    """Test that return level estimates are within reasonable bounds."""
    path = "/app/return_levels.csv"
    assert os.path.exists(path), f"File not found: {path}"

    with open(path, "r") as f:
        reader = csv.DictReader(f)
        rows = {int(row["return_period"]): row for row in reader}

    # Monotonicity
    est_10 = float(rows[10]["estimate"])
    est_50 = float(rows[50]["estimate"])
    est_100 = float(rows[100]["estimate"])
    assert est_10 < est_50 < est_100, "Return levels must increase with return period"

    # Bounds from the known data-generating process
    # Data: GEV(xi=-0.15, mu=100, sigma=25), 50 observations
    # MLE estimates: ~141, ~170, ~182
    assert 120 < est_10 < 165, (
        f"T=10 return level {est_10} outside [120, 165]"
    )
    assert 148 < est_50 < 200, (
        f"T=50 return level {est_50} outside [148, 200]"
    )
    assert 158 < est_100 < 215, (
        f"T=100 return level {est_100} outside [158, 215]"
    )


def test_confidence_intervals_reasonable():
    """Test that CIs have reasonable width and structure."""
    path = "/app/return_levels.csv"
    assert os.path.exists(path), f"File not found: {path}"

    with open(path, "r") as f:
        reader = csv.DictReader(f)
        rows = {int(row["return_period"]): row for row in reader}

    widths = {}
    for T in [10, 50, 100]:
        lo = float(rows[T]["ci_lower"])
        hi = float(rows[T]["ci_upper"])
        width = hi - lo
        widths[T] = width
        assert width > 5.0, f"T={T}: CI width {width} too narrow for profile likelihood CI"
        assert width < 180.0, f"T={T}: CI width {width} implausibly large"

    # Wider CIs for longer return periods (more extrapolation uncertainty)
    assert widths[10] < widths[100], (
        f"T=10 CI wider ({widths[10]}) than T=100 ({widths[100]}) — expected opposite"
    )


def test_ad_statistic_exists():
    """Test that Anderson-Darling statistic file exists and is reasonable."""
    path = "/app/ad_statistic.txt"
    assert os.path.exists(path), f"File not found: {path}"

    with open(path, "r") as f:
        content = f.read().strip()

    assert len(content) > 0, "ad_statistic.txt is empty"

    # Extract a number
    import re
    numbers = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", content)
    assert len(numbers) > 0, f"No numeric value found in ad_statistic.txt: {content}"

    ad = float(numbers[0])
    # Anderson-Darling for a well-fitting GEV with 50 obs should be small
    assert 0.0 <= ad < 3.0, (
        f"Anderson-Darling statistic {ad} outside reasonable range [0, 3]"
    )


def test_pp_plot_exists():
    """Test that the P-P plot image exists and is a valid PNG."""
    path = "/app/pp_plot.png"
    assert os.path.exists(path), f"File not found: {path}"

    # Verify it's a valid PNG (starts with PNG signature)
    with open(path, "rb") as f:
        header = f.read(8)
    assert header[:4] == b'\x89PNG', (
        f"pp_plot.png does not appear to be a valid PNG file"
    )

    # Check minimum size (should be > 1KB for a real plot)
    size = os.path.getsize(path)
    assert size > 500, f"pp_plot.png is too small ({size} bytes)"


def test_analysis_script_exists():
    """Test that analysis.py exists with substantial content."""
    path = "/app/analysis.py"
    assert os.path.exists(path), f"File not found: {path}"

    with open(path, "r") as f:
        content = f.read()

    assert len(content) > 200, (
        f"analysis.py is too short ({len(content)} chars)"
    )
    assert "scipy" in content.lower() or "optimize" in content.lower(), (
        "analysis.py should use scipy.optimize for MLE"
    )
    assert "annual_maxima" in content, (
        "analysis.py should reference the data file"
    )


def test_analysis_script_reproducible():
    """Test that analysis.py runs and produces all required output files."""
    path = "/app/analysis.py"
    assert os.path.exists(path), f"File not found: {path}"

    # Find Python with required packages
    python_exe = None
    for candidate in ["/opt/verifier/bin/python", "python3", "python"]:
        try:
            r = subprocess.run(
                [candidate, "-c", "import numpy, scipy, csv; print('ok')"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and "ok" in r.stdout:
                python_exe = candidate
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    assert python_exe is not None, (
        "No Python with numpy and scipy found. Install them before running analysis.py."
    )

    # Remove output files to ensure analysis.py regenerates them
    for f in ["/app/return_levels.csv", "/app/ad_statistic.txt", "/app/pp_plot.png"]:
        if os.path.exists(f):
            os.remove(f)

    # Run analysis.py
    result = subprocess.run(
        [python_exe, path],
        capture_output=True,
        text=True,
        timeout=300,
        cwd="/app",
    )
    assert result.returncode == 0, (
        f"analysis.py failed (rc={result.returncode}):\n"
        f"stderr: {result.stderr[-500:]}\nstdout: {result.stdout[-500:]}"
    )

    # Verify all output files were created
    assert os.path.exists("/app/return_levels.csv"), (
        "analysis.py did not create return_levels.csv"
    )
    assert os.path.exists("/app/ad_statistic.txt"), (
        "analysis.py did not create ad_statistic.txt"
    )
    assert os.path.exists("/app/pp_plot.png"), (
        "analysis.py did not create pp_plot.png"
    )

    # Read results
    with open("/app/return_levels.csv", "r") as f:
        content1 = f.read()

    # Run again and verify reproducibility
    result2 = subprocess.run(
        [python_exe, path],
        capture_output=True,
        text=True,
        timeout=300,
        cwd="/app",
    )
    assert result2.returncode == 0, (
        f"analysis.py failed on second run (rc={result2.returncode}):\n"
        f"stderr: {result2.stderr[-500:]}"
    )

    with open("/app/return_levels.csv", "r") as f:
        content2 = f.read()

    assert content1.strip() == content2.strip(), (
        "analysis.py produced different results on two runs — not reproducible"
    )
