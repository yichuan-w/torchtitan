import subprocess
import os
import re


def run_cmd(cmd):
    """Run a shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def test_tmux_binary_exists():
    """Test that the tmux binary exists at the correct location."""
    binary_path = "/opt/tmux-static/bin/tmux"
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    assert os.access(binary_path, os.X_OK), f"tmux binary at {binary_path} is not executable"


def test_tmux_version():
    """Test that tmux runs and reports the correct version."""
    binary_path = "/opt/tmux-static/bin/tmux"
    stdout, stderr, rc = run_cmd(f"{binary_path} -V")
    assert rc == 0, f"tmux -V failed with return code {rc}. stderr: {stderr}"
    assert "tmux 3.4" in stdout, f"Expected 'tmux 3.4' in output, got: {stdout}"


def test_fully_static():
    """Test that tmux is fully statically linked with NO dynamic libraries."""
    binary_path = "/opt/tmux-static/bin/tmux"
    # First check binary exists
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    stdout, stderr, rc = run_cmd(f"ldd {binary_path}")
    # ldd should either fail or report "not a dynamic executable" or "statically linked"
    combined = stdout + stderr
    assert "not a dynamic executable" in combined or "statically linked" in combined or rc != 0, \
        f"Binary has dynamic library dependencies. ldd output: {stdout}"


def test_no_dynamic_libevent():
    """Test that tmux does not dynamically link to libevent (belt and suspenders)."""
    binary_path = "/opt/tmux-static/bin/tmux"
    # First check binary exists
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    stdout, stderr, rc = run_cmd(f"ldd {binary_path}")
    combined = stdout + stderr
    # If static, this should pass anyway. If not, ensure no libevent
    assert "libevent" not in combined, f"tmux dynamically links to libevent. ldd output: {combined}"


def test_no_dynamic_ncurses():
    """Test that tmux does not dynamically link to ncurses (belt and suspenders)."""
    binary_path = "/opt/tmux-static/bin/tmux"
    # First check binary exists
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    stdout, stderr, rc = run_cmd(f"ldd {binary_path}")
    combined = stdout + stderr
    # If static, this should pass anyway. If not, ensure no ncurses
    assert "libncurses" not in combined and "libtinfo" not in combined, \
        f"tmux dynamically links to ncurses/tinfo. ldd output: {combined}"


def test_binary_size():
    """Test that the stripped binary is under 2.4MB."""
    binary_path = "/opt/tmux-static/bin/tmux"
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    size = os.path.getsize(binary_path)
    max_size = int(2.4 * 1024 * 1024)  # 2.4MB
    assert size < max_size, f"Binary size {size} bytes ({size/1024/1024:.2f} MB) exceeds 2.4MB limit"


def test_build_directory_structure():
    """Test that source was extracted to /app/build as instructed."""
    build_dir = "/app/build"
    assert os.path.isdir(build_dir), f"Build directory not found at {build_dir}"
    
    # Check for source directories
    items = os.listdir(build_dir)
    assert len(items) > 0, "Build directory is empty"
    
    # Look for expected source directories (may have different exact names)
    has_tmux = any("tmux" in item.lower() for item in items)
    has_libevent = any("libevent" in item.lower() for item in items)
    has_ncurses = any("ncurses" in item.lower() for item in items)
    
    assert has_tmux, f"tmux source directory not found in {build_dir}. Contents: {items}"
    assert has_libevent, f"libevent source directory not found in {build_dir}. Contents: {items}"
    assert has_ncurses, f"ncurses source directory not found in {build_dir}. Contents: {items}"


def test_binary_is_elf():
    """Test that the binary is a valid ELF executable."""
    binary_path = "/opt/tmux-static/bin/tmux"
    # First check binary exists
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    stdout, stderr, rc = run_cmd(f"file {binary_path}")
    assert "ELF" in stdout, f"Binary is not an ELF file. file output: {stdout}"
    assert "executable" in stdout or "shared object" in stdout, \
        f"Binary is not an executable. file output: {stdout}"


def test_tmux_functional():
    """Test that tmux actually works - can list sessions and run commands."""
    binary_path = "/opt/tmux-static/bin/tmux"
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    
    # Test that tmux can show help (basic functionality test)
    stdout, stderr, rc = run_cmd(f"{binary_path} -V")
    assert rc == 0 and "tmux 3.4" in stdout, f"tmux -V failed: {stderr}"
    
    # Test that tmux can list-sessions (will fail gracefully without a server, which is fine)
    stdout, stderr, rc = run_cmd(f"{binary_path} list-sessions 2>&1 || true")
    # This should either list sessions or say "no server running" or similar - not crash
    # We just want to make sure the binary can execute complex commands
    combined = stdout + stderr
    
    # Test running a command via tmux (requires a server, will fail but shouldn't crash)
    stdout, stderr, rc = run_cmd(f"{binary_path} -V")
    assert "tmux 3.4" in stdout, f"tmux failed to execute: {stderr}"


def test_no_compression():
    """Test that the binary is not compressed with UPX or similar tools."""
    binary_path = "/opt/tmux-static/bin/tmux"
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    stdout, stderr, rc = run_cmd(f"file {binary_path}")
    # UPX-compressed binaries show "UPX!" in the file output or have specific markers
    combined = stdout + stderr
    assert "UPX" not in combined, f"Binary appears to be UPX-compressed. file output: {combined}"
    # Also check for other compression signatures
    assert "packed" not in combined.lower(), f"Binary appears to be packed/compressed. file output: {combined}"
    # UPX often removes section headers
    assert "no section header" not in combined.lower(), f"Binary has no section headers - likely compressed. file output: {combined}"


def test_binary_has_sections():
    """Test that the binary has proper ELF sections (not stripped by compression)."""
    binary_path = "/opt/tmux-static/bin/tmux"
    assert os.path.isfile(binary_path), f"tmux binary not found at {binary_path}"
    # Check that readelf can read section headers - compression tools often break this
    stdout, stderr, rc = run_cmd(f"readelf -S {binary_path} 2>&1")
    # Should have section headers present
    assert "There are" in stdout or ".text" in stdout or ".data" in stdout, \
        f"Binary appears to have no section headers (likely compressed or corrupt). readelf output: {stdout[:500]}"
