import os
import subprocess


def test_config_file_exists():
    """Test that the required config file exists and has valid content."""
    config_path = "/app/config/processor.cfg"
    assert os.path.exists(config_path), (
        f"Config file not found at {config_path}. "
        "The processor binary requires this config file."
    )
    with open(config_path) as f:
        content = f.read()
    assert "prefix=" in content, (
        "Config file must contain a 'prefix=' setting."
    )


def test_data_directory_exists():
    """Test that the required data directory exists."""
    data_dir = "/app/data"
    assert os.path.exists(data_dir), (
        f"Data directory not found at {data_dir}. "
        "The processor binary requires this directory to exist."
    )
    assert os.path.isdir(data_dir), (
        f"{data_dir} exists but is not a directory."
    )


def test_processor_runs():
    """Test that the processor binary works correctly with stdin/stdout."""
    result = subprocess.run(
        ["/app/bin/processor"],
        input="Hello World\ntest line\n",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Processor exited with code {result.returncode}. stderr: {result.stderr}"
    )
    lines = result.stdout.strip().split("\n")
    assert len(lines) == 2, (
        f"Expected 2 output lines, got {len(lines)}: {result.stdout}"
    )
    assert lines[0].startswith("OUT: "), (
        f"First line should start with 'OUT: ', got: {lines[0]}"
    )
    assert "Hello World" in lines[0], (
        f"Expected 'Hello World' in output, got: {lines[0]}"
    )
    assert lines[1].startswith("OUT: "), (
        f"Second line should start with 'OUT: ', got: {lines[1]}"
    )


def test_processor_handles_empty_input():
    """Test that the processor handles empty input correctly."""
    result = subprocess.run(
        ["/app/bin/processor"],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Processor should exit 0 on empty input, got {result.returncode}"
    )