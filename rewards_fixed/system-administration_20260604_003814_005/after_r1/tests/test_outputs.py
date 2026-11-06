import re
import subprocess


def _run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def test_apt_update_works_cleanly():
    result = _run(["apt-get", "update"], timeout=60)
    combined = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"apt-get update failed (rc={result.returncode}). "
        f"stdout: {result.stdout[:500]} stderr: {result.stderr[:500]}"
    )

    success_pattern = re.search(
        r"(?:Hit|Get):\d+\s+http://[^\s]+\s+stable\s+(?:InRelease|Release)",
        combined,
    )
    assert success_pattern, (
        "apt-get update did not successfully fetch the local stable repository. "
        f"Output: {combined[:500]}"
    )

    lower_stderr = result.stderr.lower()
    for bad_pattern in [
        "gpg error",
        "not signed",
        "no_pubkey",
        "checksum",
        "hash sum",
        "failed to fetch",
    ]:
        assert bad_pattern not in lower_stderr, (
            f"apt-get update produced warnings: '{bad_pattern}':\n"
            f"{result.stderr[:500]}"
        )


def test_all_packages_installable_via_apt():
    packages = ["hello-deb", "useful-util", "hello-app"]
    result = _run(
        ["apt-get", "install", "-y", "--reinstall", *packages],
        timeout=120,
    )

    assert result.returncode == 0, (
        "The required packages were not all installable via apt. "
        f"stdout: {result.stdout[:1000]} stderr: {result.stderr[:1000]}"
    )
