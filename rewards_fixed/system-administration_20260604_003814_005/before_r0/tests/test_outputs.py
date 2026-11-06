import os
import subprocess
import gzip
import hashlib


def _run(cmd, **kwargs):
    """Run a command and return stdout, stderr, returncode."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def test_nginx_serving_repo():
    """nginx should be running and serving the repository on port 80."""
    stdout, stderr, rc = _run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost/dists/stable/Release"],
        timeout=10,
    )
    assert rc == 0, f"curl to nginx failed (rc={rc}): {stderr}"
    assert stdout == "200", (
        f"Expected HTTP 200 for /dists/stable/Release, got {stdout}. "
        f"Is nginx running and serving /var/www/repo?"
    )


def test_repo_directory_structure():
    """Repository must have the correct directory structure with packages in pool."""
    base = "/var/www/repo"

    dists_dir = os.path.join(base, "dists", "stable", "main", "binary-amd64")
    assert os.path.isdir(dists_dir), (
        f"Expected directory {dists_dir} to exist."
    )

    # Pool directory must exist and contain .deb files
    pool_dir = os.path.join(base, "pool")
    assert os.path.isdir(pool_dir), (
        f"Expected directory {pool_dir} to exist."
    )

    # Verify there are .deb files somewhere under pool/
    deb_files = []
    for root, dirs, files in os.walk(pool_dir):
        for f in files:
            if f.endswith(".deb"):
                deb_files.append(os.path.join(root, f))

    assert len(deb_files) >= 3, (
        f"Expected at least 3 .deb files under {pool_dir}, found {len(deb_files)}. "
        f"Found: {deb_files}"
    )


def test_packages_file_is_valid_and_complete():
    """Packages file must exist, be properly formatted, and list all packages."""
    packages_path = "/var/www/repo/dists/stable/main/binary-amd64/Packages"
    packages_gz_path = packages_path + ".gz"

    has_packages = os.path.isfile(packages_path)
    has_packages_gz = os.path.isfile(packages_gz_path)

    assert has_packages or has_packages_gz, (
        f"Neither {packages_path} nor {packages_gz_path} exists."
    )

    if has_packages_gz:
        with gzip.open(packages_gz_path, "rt") as f:
            content = f.read()
    else:
        with open(packages_path, "r") as f:
            content = f.read()

    # Verify all three packages are listed with required fields
    for pkg in ["hello-deb", "useful-util", "hello-app"]:
        assert f"Package: {pkg}" in content, (
            f"Packages file does not contain '{pkg}'."
        )

    # Each package entry must have required fields
    for required in ["Architecture:", "Size:", "SHA256:", "Filename:",
                      "MD5sum:", "Description:"]:
        assert required in content, (
            f"Packages file is missing required field '{required}'. "
            f"Regenerate it with dpkg-scanpackages."
        )

    # Verify hello-app declares dependency on hello-deb
    assert "Depends: hello-deb" in content, (
        f"hello-app should declare 'Depends: hello-deb'."
    )


def test_release_checksums_are_valid():
    """Release file must have checksums that match the actual files on disk."""
    release_path = "/var/www/repo/dists/stable/Release"
    assert os.path.isfile(release_path), (
        f"Release file not found at {release_path}."
    )

    # Use apt-ftparchive to verify the Release file integrity
    # Running 'apt-ftparchive release' and comparing to the existing Release
    # is the standard way to check if Release is up-to-date.
    stdout, stderr, rc = _run(
        ["apt-ftparchive", "release", "/var/www/repo/dists/stable"],
        timeout=15,
    )
    assert rc == 0, (
        f"apt-ftparchive release failed: {stderr[:300]}"
    )

    # Parse checksums from the generated Release
    # The key test: apt-get update should not complain about hash sum mismatches
    # We verify this indirectly via test_apt_update_works_cleanly

    # Direct check: compare SHA256 of Packages file with what's in Release
    packages_path = "/var/www/repo/dists/stable/main/binary-amd64/Packages"
    if os.path.isfile(packages_path):
        with open(packages_path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()

        with open(release_path, "r") as f:
            release_content = f.read()

        # The Release file should contain the SHA256 of Packages
        assert sha in release_content, (
            f"Release file SHA256 checksums do not match the Packages file. "
            f"Expected SHA256: {sha}. Regenerate the Release file."
        )


def test_inrelease_valid_and_apt_trusts_key():
    """InRelease must exist, be valid, and its key must be trusted by apt."""
    inrelease_path = "/var/www/repo/dists/stable/InRelease"
    assert os.path.isfile(inrelease_path), (
        f"InRelease file not found at {inrelease_path}."
    )

    with open(inrelease_path, "r") as f:
        content = f.read()

    assert "-----BEGIN PGP SIGNED MESSAGE-----" in content, (
        f"InRelease is not a clearsigned GPG message."
    )
    assert "-----BEGIN PGP SIGNATURE-----" in content, (
        f"InRelease is missing the PGP signature block."
    )

    # Verify the signature is cryptographically valid
    stdout, stderr, rc = _run(
        ["gpg", "--verify", inrelease_path],
        timeout=10,
    )
    assert "Good signature" in stderr or "Good signature" in stdout, (
        f"GPG signature verification failed for InRelease. "
        f"stdout: {stdout[:300]}\nstderr: {stderr[:300]}"
    )

    # Also verify the Release content signed inside InRelease matches
    # the actual Release file on disk
    release_path = "/var/www/repo/dists/stable/Release"
    with open(release_path, "r") as f:
        release_content = f.read()

    # Extract signed content from InRelease
    signed_part = content.split("-----BEGIN PGP SIGNATURE-----")[0]
    signed_part = signed_part.replace("-----BEGIN PGP SIGNED MESSAGE-----\n", "")
    # Remove Hash header line
    lines = signed_part.strip().split("\n")
    if lines and lines[0].startswith("Hash:"):
        signed_part = "\n".join(lines[1:]).strip()
    signed_part = signed_part.strip()

    # Strip comment lines from Release for comparison
    release_lines = [l for l in release_content.strip().split("\n")
                     if not l.strip().startswith("#")]
    release_clean = "\n".join(release_lines).strip()

    assert signed_part == release_clean, (
        f"InRelease signed content does not match the Release file on disk. "
        f"The InRelease is stale — regenerate and re-sign the Release file.\n"
        f"Signed content length: {len(signed_part)}, Release length: {len(release_clean)}"
    )


def test_apt_update_works_cleanly():
    """apt-get update should succeed against the local repository without warnings."""
    stdout, stderr, rc = _run(
        ["apt-get", "update"],
        timeout=60,
    )
    assert rc == 0, (
        f"apt-get update failed (rc={rc}). "
        f"stderr: {stderr[:500]}"
    )

    combined = stdout + stderr

    # Check that the local repo was SUCCESSFULLY fetched, not just attempted
    # Look for "Hit:" or "Get:" followed by "stable InRelease"
    import re
    success_pattern = re.search(
        r'(?:Hit|Get):\d+\s+http://[^\s]+\s+stable\s+InRelease', combined
    )
    assert success_pattern, (
        f"apt-get update did not successfully fetch 'stable InRelease' from the local repo. "
        f"Look for 'Hit: ... stable InRelease' or 'Get: ... stable InRelease'.\n"
        f"Output: {combined[:500]}"
    )

    # No warnings or errors
    lower_stderr = stderr.lower()
    for bad_pattern in ["gpg error", "not signed", "no_pubkey",
                         "checksum", "hash sum", "failed to fetch"]:
        assert bad_pattern not in lower_stderr, (
            f"apt-get update produced warnings: '{bad_pattern}':\n{stderr[:500]}"
        )


def test_all_packages_available_via_apt():
    """All three packages should be available via apt-cache."""
    for pkg in ["hello-deb", "useful-util", "hello-app"]:
        stdout, stderr, rc = _run(
            ["apt-cache", "show", pkg],
            timeout=15,
        )
        assert rc == 0, (
            f"apt-cache show {pkg} failed (rc={rc}). "
            f"Package '{pkg}' is not available.\nstderr: {stderr[:300]}"
        )
        assert f"Package: {pkg}" in stdout, (
            f"apt-cache show {pkg} output unexpected:\n{stdout[:300]}"
        )


def test_dependency_resolution():
    """Verify that hello-app's dependency on hello-deb is recognized."""
    stdout, stderr, rc = _run(
        ["apt-cache", "depends", "hello-app"],
        timeout=15,
    )
    assert rc == 0, (
        f"apt-cache depends hello-app failed (rc={rc}).\nstderr: {stderr[:300]}"
    )
    assert "hello-deb" in stdout, (
        f"hello-app should depend on hello-deb, but not in depends output:\n{stdout}"
    )

