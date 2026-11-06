"""Test module for verifying datatools package CLI commands are properly installed."""

import subprocess
import sys
import json
import tempfile
import os

# Use the virtual environment where packages are installed
VENV_PATH = "/opt/build-venv"


def run_command(cmd, timeout=30):
    """Run a command and return the result."""
    # Use the venv's PATH to find installed commands
    env = os.environ.copy()
    env["PATH"] = f"{VENV_PATH}/bin:{env.get('PATH', '')}"
    
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env
    )
    return result


def test_main_datatools_command_exists():
    """Test that the main 'datatools' command is installed and accessible."""
    result = run_command("datatools")
    assert result.returncode == 0, f"datatools command should succeed but got: {result.stderr}"
    assert "datatools" in result.stdout.lower() or "available commands" in result.stdout.lower(), \
        f"datatools should show available commands but got: {result.stdout}"


def test_csv2json_command_exists():
    """Test that the 'csv2json' command is installed."""
    result = run_command("csv2json --help")
    assert result.returncode == 0, f"csv2json command should exist but got: {result.stderr}"
    assert "csv" in result.stdout.lower() or "json" in result.stdout.lower(), \
        f"csv2json help should mention csv/json but got: {result.stdout}"


def test_json2csv_command_exists():
    """Test that the 'json2csv' command is installed."""
    result = run_command("json2csv --help")
    assert result.returncode == 0, f"json2csv command should exist but got: {result.stderr}"
    assert "csv" in result.stdout.lower() or "json" in result.stdout.lower(), \
        f"json2csv help should mention csv/json but got: {result.stdout}"


def test_validate_command_exists():
    """Test that the 'validate' command is installed."""
    result = run_command("validate --help")
    assert result.returncode == 0, f"validate command should exist but got: {result.stderr}"
    assert "validate" in result.stdout.lower(), \
        f"validate help should mention validation but got: {result.stdout}"


def test_stats_command_exists():
    """Test that the 'stats' command is installed."""
    result = run_command("stats --help")
    assert result.returncode == 0, f"stats command should exist but got: {result.stderr}"
    assert "stats" in result.stdout.lower() or "statistic" in result.stdout.lower(), \
        f"stats help should mention statistics but got: {result.stdout}"


def test_csv2json_functionality():
    """Test that csv2json actually converts CSV to JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, "test.csv")
        json_file = os.path.join(tmpdir, "output.json")
        
        # Create test CSV
        with open(csv_file, 'w') as f:
            f.write("name,value\nAlice,100\nBob,200\n")
        
        # Run csv2json
        result = run_command(f"csv2json {csv_file} -o {json_file}")
        assert result.returncode == 0, f"csv2json should succeed but got: {result.stderr}"
        
        # Verify output
        assert os.path.exists(json_file), "Output JSON file should exist"
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        assert isinstance(data, list), "Output should be a list"
        assert len(data) == 2, f"Should have 2 records but got {len(data)}"
        assert data[0]['name'] == 'Alice', f"First record should have name=Alice"
        assert data[1]['value'] == '200', f"Second record should have value=200"


def test_json2csv_functionality():
    """Test that json2csv actually converts JSON to CSV."""
    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = os.path.join(tmpdir, "test.json")
        csv_file = os.path.join(tmpdir, "output.csv")
        
        # Create test JSON
        with open(json_file, 'w') as f:
            json.dump([{"name": "test1", "count": 1}, {"name": "test2", "count": 2}], f)
        
        # Run json2csv
        result = run_command(f"json2csv {json_file} -o {csv_file}")
        assert result.returncode == 0, f"json2csv should succeed but got: {result.stderr}"
        
        # Verify output
        assert os.path.exists(csv_file), "Output CSV file should exist"
        with open(csv_file, 'r') as f:
            content = f.read()
        
        assert "name,count" in content, "CSV should have header"
        assert "test1,1" in content, "CSV should have first data row"
        assert "test2,2" in content, "CSV should have second data row"


def test_validate_functionality():
    """Test that validate command works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = os.path.join(tmpdir, "valid.json")
        
        # Create valid JSON
        with open(json_file, 'w') as f:
            json.dump({"test": "data"}, f)
        
        result = run_command(f"validate {json_file}")
        assert result.returncode == 0, f"validate should succeed for valid file but got: {result.stderr}"
        assert "valid" in result.stdout.lower(), "Should indicate file is valid"


def test_stats_functionality():
    """Test that stats command works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = os.path.join(tmpdir, "data.json")
        
        # Create test data
        with open(json_file, 'w') as f:
            json.dump([{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}], f)
        
        result = run_command(f"stats {json_file}")
        assert result.returncode == 0, f"stats should succeed but got: {result.stderr}"
        assert "3" in result.stdout, f"stats should show 3 records but got: {result.stdout}"


def test_new_main_function_gets_launcher_after_reinstall():
    """A newly added main_* function must produce a launcher on reinstall."""
    commands_file = "/app/datatools/src/datatools/commands.py"
    original = open(commands_file, "rb").read()
    probe = b'\n\ndef main_semantic_probe2():\n    print("dynamic probe works")\n'
    install = [
        f"{VENV_PATH}/bin/pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        "/app/datatools",
    ]

    try:
        with open(commands_file, "ab") as handle:
            handle.write(probe)
        reinstall = subprocess.run(install, capture_output=True, text=True, timeout=120)
        result = run_command("semantic-probe2")
        assert reinstall.returncode == 0 and result.returncode == 0 and result.stdout.strip() == "dynamic probe works", (
            "adding main_semantic_probe2 and reinstalling should create the semantic-probe2 launcher; "
            f"pip stderr: {reinstall.stderr}; launcher stderr: {result.stderr}; "
            f"launcher stdout: {result.stdout}"
        )
    finally:
        with open(commands_file, "wb") as handle:
            handle.write(original)
        subprocess.run(install, capture_output=True, text=True, timeout=120)
