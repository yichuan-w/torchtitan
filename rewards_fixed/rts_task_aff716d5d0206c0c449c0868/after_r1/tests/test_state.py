import os
import re
import subprocess
import pytest

REPO_DIR = "/root/git-katas/rebase-branch/exercise"
AUDIT_FILE = "/root/audit.txt"

def run_git(args):
    """Run a git command in REPO_DIR and return its stdout, stripping trailing whitespace."""
    result = subprocess.run(
        ["git"] + args,
        cwd=REPO_DIR,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Git command failed: git {' '.join(args)}\n{result.stderr}"
    return result.stdout.rstrip('\n')


def parse_audit_header(path):
    """
    Parse the forensics header and the diff portion from the audit file.
    Returns (header dict, diff_str).
    """
    with open(path, "r") as f:
        lines = f.readlines()
    diff_start = next(
        (i for i, line in enumerate(lines) if line.startswith("diff --git")),
        None,
    )
    if diff_start is None:
        pytest.fail("Could not locate the start of the diff in /root/audit.txt.")

    header_text = "".join(lines[:diff_start])
    header = {"baseline": None, "final": None, "reflog": None}
    expected_baseline = run_git(["rev-parse", "master@{1}"])
    expected_final = run_git(["rev-parse", "master"])
    reflog_match = re.search(
        r"(?i)\b(?:HEAD|[A-Za-z0-9_./-]+)@\{\d+\}", header_text
    )
    header_hashes = re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", header_text)
    if expected_baseline.lower() in (value.lower() for value in header_hashes):
        header["baseline"] = expected_baseline
    if expected_final.lower() in (value.lower() for value in header_hashes):
        header["final"] = expected_final
    if reflog_match:
        header["reflog"] = reflog_match.group(0)
    return header, "".join(lines[diff_start:])


def test_check_00_git_state():
    """
    Preserved seed check: master and uppercase point to the same commit,
    history is linear with no merge commits, and the expected commits appear
    in the correct rebased order.
    """
    assert os.path.isdir(os.path.join(REPO_DIR, ".git")), (
        "Git repository not found at /root/git-katas/rebase-branch/exercise."
    )

    master_commit = run_git(["rev-parse", "master"])
    uppercase_commit = run_git(["rev-parse", "uppercase"])
    assert master_commit and uppercase_commit, "Could not resolve branch commits."
    assert master_commit == uppercase_commit, (
        f"Branches do not point to the same commit.\n"
        f"master: {master_commit}\n"
        f"uppercase: {uppercase_commit}"
    )

    # No merge commits (each commit has 0 or 1 parent)
    parents_output = run_git(["log", "--format=%P", "master"])
    for line in parents_output.splitlines():
        parents = line.strip().split()
        assert len(parents) <= 1, (
            f"History is not linear – merge commit with parents {parents} detected."
        )

    log_output = run_git(["log", "--format=%s", "master"])
    commits = log_output.splitlines()
    expected_messages = [
        "Change greeting to uppercase",
        "Add readme",
        "Add content to greeting.txt",
        "Add file greeting.txt"
    ]
    for msg in expected_messages:
        assert msg in commits, (
            f"Expected commit message '{msg}' is missing from history."
        )
    idx_uppercase = commits.index("Change greeting to uppercase")
    idx_readme = commits.index("Add readme")
    assert idx_uppercase < idx_readme, (
        "Commit order is incorrect: 'Change greeting to uppercase' must be on top of 'Add readme' after rebase."
    )


def test_check_01_forensics_header():
    """
    Reward check: /root/audit.txt contains a forensics header with
    Baseline commit, Final commit, and Reflog reference lines whose
    hashes correspond to real commits in the repository.
    """
    assert os.path.isfile(AUDIT_FILE), (
        f"Audit file {AUDIT_FILE} not found."
    )
    header, _ = parse_audit_header(AUDIT_FILE)

    assert header["baseline"] is not None, (
        "Forensics header does not document the baseline commit."
    )
    assert header["final"] is not None, (
        "Forensics header does not document the final commit."
    )
    assert header["reflog"] is not None, (
        "Forensics header does not document a reflog reference."
    )
    assert run_git(["rev-parse", header["reflog"]]) == header["baseline"], (
        "The documented reflog reference does not resolve to the baseline commit."
    )

    # Verify that the hashes are valid commit objects in the repo
    for label, commit_hash in [("Baseline", header["baseline"]),
                               ("Final", header["final"])]:
        if not commit_hash:
            continue
        try:
            obj_type = run_git(["cat-file", "-t", commit_hash])
        except AssertionError:
            raise AssertionError(
                f"{label} commit hash '{commit_hash}' does not reference a valid object."
            )
        assert obj_type == "commit", (
            f"{label} hash '{commit_hash}' is not a commit object."
        )


def test_check_02_diff_exists():
    """
    Reward check: /root/audit.txt exists, is non-empty, and contains
    a unified diff header 'diff --git'.
    """
    assert os.path.isfile(AUDIT_FILE), (
        f"Audit file {AUDIT_FILE} not found."
    )
    with open(AUDIT_FILE, "r") as f:
        content = f.read()
    assert len(content.strip()) > 0, (
        f"Audit file {AUDIT_FILE} is empty."
    )
    assert "diff --git" in content, (
        f"Audit file {AUDIT_FILE} does not contain a unified diff header 'diff --git'."
    )


def test_check_03_diff_content_correct():
    """
    Reward check: the unified diff portion of /root/audit.txt matches
    the output of git diff <baseline> master, where baseline is taken
    from the forensics header.
    """
    assert os.path.isfile(AUDIT_FILE), (
        f"Audit file {AUDIT_FILE} not found."
    )
    header, diff_text = parse_audit_header(AUDIT_FILE)
    baseline = header["baseline"]
    assert baseline is not None, (
        "Cannot verify diff: baseline commit missing from header."
    )

    expected_diff = run_git(["diff", baseline, "master"])
    # Normalize newlines and strip trailing spaces for comparison
    diff_normalized = diff_text.rstrip('\n').replace('\r\n', '\n')
    expected_normalized = expected_diff.rstrip('\n').replace('\r\n', '\n')
    assert diff_normalized == expected_normalized, (
        "The diff in /root/audit.txt does not match the actual "
        "git diff between the baseline commit and master. "
        "Ensure the diff is generated correctly from the baseline recorded in the header."
    )


def test_check_04_no_shortcut():
    """
    Reward check: reject placeholder strings, hardcoded hashes, or diffs
    that do not match the actual reflog-derived baseline.
    """
    assert os.path.isfile(AUDIT_FILE), (
        f"Audit file {AUDIT_FILE} not found."
    )
    with open(AUDIT_FILE, "r") as f:
        content = f.read()

    # Reject obvious placeholders
    for placeholder in ("TODO", "PLACEHOLDER", "STUB"):
        if placeholder in content:
            pytest.fail(
                f"/root/audit.txt appears to contain placeholder text '{placeholder}'."
            )

    header, diff_text = parse_audit_header(AUDIT_FILE)
    baseline_from_header = header["baseline"]
    final_from_header = header["final"]

    # The reflog-based baseline must match master@{1}
    try:
        expected_baseline = run_git(["rev-parse", "master@{1}"])
    except AssertionError:
        pytest.fail(
            "Could not resolve master@{1} from the reflog. "
            "Is the repository history intact?"
        )
    assert baseline_from_header == expected_baseline, (
        f"Baseline commit in header ({baseline_from_header}) does not match "
        f"the reflog-derived baseline ({expected_baseline}). "
        "The baseline must correspond to the state of master before the rebase workflow."
    )

    # Final commit must point to current master
    master_commit = run_git(["rev-parse", "master"])
    assert final_from_header == master_commit, (
        f"Final commit in header ({final_from_header}) does not match "
        f"current master ({master_commit})."
    )

    # Sanity: the diff itself must not be empty
    assert len(diff_text.strip()) > 0, (
        "The diff portion of /root/audit.txt is empty; expected changes from the rebase."
    )
