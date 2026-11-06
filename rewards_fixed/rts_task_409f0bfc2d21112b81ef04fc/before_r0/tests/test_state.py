import os
import re
import subprocess

import pytest

REPO_DIR = "/root/git-katas/rebase-branch/exercise"
SPEC_PATH = "/root/git-katas/rebase-branch/CHANGELOG_SPEC.md"
CHANGELOG_PATH = "/root/git-katas/rebase-branch/changelog.md"
DIFF_REPORT_PATH = "/root/git-katas/rebase-branch/diff_report.md"


def run_git(args):
    result = subprocess.run(
        ["git"] + args,
        cwd=REPO_DIR,
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, (
        f"Git command failed: git {' '.join(args)}\n{result.stderr}"
    )
    return result.stdout.strip()


def get_reflog_old_commit():
    """Return the pre-rebase commit hash from the reflog of uppercase."""
    result = subprocess.run(
        ["git", "-C", REPO_DIR, "rev-parse", "uppercase@{1}"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        pytest.fail("Could not find reflog entry for uppercase@{1}. Was the rebase performed?")
    return result.stdout.strip()


# ----- Original rebase checks (preserved) -----

def test_repo_exists():
    assert os.path.isdir(os.path.join(REPO_DIR, ".git")), (
        "Git repository not found. Did you initialise the kata?"
    )


def test_branches_point_to_same_commit():
    master_commit = run_git(["rev-parse", "master"])
    uppercase_commit = run_git(["rev-parse", "uppercase"])
    assert master_commit and uppercase_commit, "Could not resolve branch commits."
    assert master_commit == uppercase_commit, (
        f"Branches do not point to the same commit.\n"
        f"master: {master_commit}\n"
        f"uppercase: {uppercase_commit}"
    )


def test_no_merge_commits():
    parents_output = run_git(["log", "--format=%P", "master"])
    for line in parents_output.splitlines():
        parents = line.strip().split()
        assert len(parents) <= 1, (
            f"Found a merge commit with parents: {parents}. "
            f"History should be linear."
        )


def test_commits_present_and_ordered():
    log_output = run_git(["log", "--format=%s", "master"])
    commits = log_output.splitlines()

    expected_commits = [
        "Change greeting to uppercase",
        "Add readme",
        "Add content to greeting.txt",
        "Add file greeting.txt"
    ]

    for commit in expected_commits:
        assert commit in commits, (
            f"Commit '{commit}' is missing from master's history."
        )

    idx_uppercase = commits.index("Change greeting to uppercase")
    idx_readme = commits.index("Add readme")
    assert idx_uppercase < idx_readme, (
        "The 'uppercase' commit should be rebased on top of the 'master' commits."
    )


# ----- Original changelog checks (preserved) -----

def test_changelog_spec_exists():
    assert os.path.isfile(SPEC_PATH), (
        f"CHANGELOG_SPEC.md not found at {SPEC_PATH}. "
        f"The specification must be present before generating the changelog."
    )


def test_changelog_file_ready():
    assert os.path.isfile(CHANGELOG_PATH), (
        f"changelog.md not found at {CHANGELOG_PATH}."
    )
    assert os.path.getsize(CHANGELOG_PATH) > 0, (
        "changelog.md exists but is empty."
    )


def test_changelog_structure_matches_spec():
    """Ensure the changelog uses the section structure defined in CHANGELOG_SPEC.md."""
    with open(SPEC_PATH, "r") as f:
        spec_lines = [l.rstrip("\n") for l in f]

    # Extract the first markdown table header row from the specification.
    # Look for a separator line (like '|---|---|...') and take the preceding line.
    spec_header = None
    for i, line in enumerate(spec_lines):
        if re.match(r'^\|[\s:-]+\|[\s:-]+\|', line):
            if i > 0:
                spec_header = spec_lines[i - 1]
            break

    assert spec_header is not None, (
        "Could not locate a markdown table header in CHANGELOG_SPEC.md."
    )

    with open(CHANGELOG_PATH, "r") as f:
        changelog = f.read()

    assert spec_header in changelog, (
        f"changelog.md does not contain the table header defined in the spec.\n"
        f"Expected header: {spec_header}"
    )


def test_changelog_contains_master_tip():
    """The changelog must reference the current tip of master."""
    master_tip = run_git(["rev-parse", "master"])
    with open(CHANGELOG_PATH, "r") as f:
        content = f.read()
    assert master_tip in content, (
        f"changelog.md does not contain the master tip commit hash ({master_tip})."
    )


def test_changelog_entries_match_actual_commits():
    """
    Every commit hash, date, author, and message in the changelog must match
    the actual post-rebase `git log` output for master, in order, with no
    extra or missing entries.
    """
    # Generate expected rows directly from the repository
    expected_rows = run_git([
        "log", "--format=| %H | %ad | %an | %s |", "--date=short", "master"
    ]).splitlines()

    if not expected_rows:
        pytest.fail("No commits found on master – cannot verify changelog.")

    with open(CHANGELOG_PATH, "r") as f:
        changelog_lines = [l.rstrip("\n") for l in f]

    # Extract commit rows: lines starting with '|' followed by a space and a 40-hex hash
    commit_pattern = re.compile(r'^\| [a-f0-9]{40} \|')
    recorded_rows = [line for line in changelog_lines if commit_pattern.match(line)]

    assert len(recorded_rows) == len(expected_rows), (
        f"changelog.md has {len(recorded_rows)} commit row(s), "
        f"but master contains {len(expected_rows)}. "
        f"Extra or missing entries are not allowed."
    )

    for idx, (rec, exp) in enumerate(zip(recorded_rows, expected_rows)):
        assert rec == exp, (
            f"Commit row {idx+1} in changelog does not match the actual history.\n"
            f"changelog:  {rec}\n"
            f"expected:   {exp}"
        )


# ----- New forensic diff report checks (reward_checks mapping) -----

def test_diff_report_exists_and_nonempty():
    """check_02: diff_report.md must exist and be non-empty."""
    assert os.path.isfile(DIFF_REPORT_PATH), (
        "diff_report.md not found at /root/git-katas/rebase-branch/diff_report.md"
    )
    assert os.path.getsize(DIFF_REPORT_PATH) > 0, (
        "diff_report.md exists but is empty."
    )


def test_diff_report_references_reflog_commit():
    """check_01: diff_report.md includes the pre-rebase commit hash discoverable via reflog."""
    old_hash = get_reflog_old_commit()
    with open(DIFF_REPORT_PATH, "r") as f:
        content = f.read()
    assert old_hash in content, (
        "diff_report.md does not reference the pre-rebase commit hash obtained from the reflog."
    )


def test_diff_report_pre_rebase_not_ancestor_of_master():
    """check_04: reject shortcuts that use a commit reachable from master (e.g., HEAD~1)."""
    old_hash = get_reflog_old_commit()
    result = subprocess.run(
        ["git", "-C", REPO_DIR, "merge-base", "--is-ancestor", old_hash, "master"],
        capture_output=True
    )
    # exit code 0 if ancestor, 1 if not
    assert result.returncode != 0, (
        "Pre-rebase commit is an ancestor of master. This suggests a shortcut like using HEAD~1 instead of the reflog."
    )


def test_diff_report_no_placeholder():
    """check_04: reject placeholder text or hardcoded dummy hashes."""
    with open(DIFF_REPORT_PATH, "r") as f:
        content = f.read()
    lower = content.lower()
    forbidden = ["todo", "your diff here", "0000000000000000000000000000000000000000", "placeholder"]
    for phrase in forbidden:
        assert phrase not in lower, f"diff_report.md contains a forbidden placeholder: '{phrase}'"
    # Additionally, some diff-like structure should be present.
    assert ("---" in content or "@@" in content or "diff --git" in content), (
        "diff_report.md does not contain recognizable diff output."
    )


def test_diff_report_diff_matches_expected():
    """check_03: the diff inside the report must match the actual git diff between the reflog-commit and master."""
    old_hash = get_reflog_old_commit()
    expected_diff_proc = subprocess.run(
        ["git", "-C", REPO_DIR, "diff", "--patch", old_hash, "master"],
        capture_output=True,
        text=True
    )
    assert expected_diff_proc.returncode == 0, "Failed to compute expected diff from git."
    expected = expected_diff_proc.stdout.strip()

    with open(DIFF_REPORT_PATH, "r") as f:
        report = f.read()

    # Extract the content of the ```diff code block
    pattern = r'```diff\s*\n(.*?)```'
    match = re.search(pattern, report, re.DOTALL)
    if not match:
        pytest.fail("diff_report.md does not contain a ```diff code block with the diff.")
    actual_diff = match.group(1).strip()

    assert expected == actual_diff, (
        "The diff content in diff_report.md does not match the actual git diff "
        "between the pre-rebase commit and the current master tip."
    )
