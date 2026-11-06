import os
import tempfile
import git

def test_result_txt_exists_and_correct():
    assert os.path.exists("/app/result.txt"), "/app/result.txt does not exist."
    
    with open("/app/result.txt", "r") as f:
        content = f.read().split()
    
    assert "secret.key" not in content, "secret.key is still listed in /app/result.txt."
    assert "README.md" in content, "README.md is missing from /app/result.txt."
    assert "hello.c" in content, "hello.c is missing from /app/result.txt."

def test_makefile_clean():
    # Clone a fresh copy and verify that Makefile exists and contains no reference to secret.key
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            repo = git.Repo.clone_from("https://github.com/jrogue/sample.git", temp_dir)
        except git.exc.GitCommandError as e:
            assert False, f"Failed to clone the remote repository: {e}"
        
        makefile_path = os.path.join(temp_dir, "Makefile")
        assert os.path.isfile(makefile_path), "Makefile does not exist in the repository working tree."
        
        with open(makefile_path, "r") as mf:
            content = mf.read()
        assert "secret.key" not in content, (
            "Makefile still contains a reference to secret.key. "
            "The reference must be completely removed, not just commented out."
        )

def test_remote_history_corrected():
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            repo = git.Repo.clone_from("https://github.com/jrogue/sample.git", temp_dir)
        except git.exc.GitCommandError as e:
            assert False, f"Failed to clone the remote repository: {e}"
            
        # Verify the file is no longer in the working tree of the latest commit
        assert not os.path.exists(os.path.join(temp_dir, "secret.key")), \
            "secret.key still exists in the latest commit of the remote repository."
        
        # Verify the commit introducing secret.key was completely removed from history
        commits = list(repo.iter_commits(paths="secret.key"))
        assert len(commits) == 0, (
            "The commit adding secret.key was not removed from the remote branch history "
            "(it may have been reverted instead of removed)."
        )
        
        # Ensure no commit looks like a revert (message contains 'revert')
        for commit in repo.iter_commits():
            assert "revert" not in commit.message.lower(), (
                f"A commit with a revert-like message was found: {commit.hexsha}. "
                "The removal must not be a revert commit."
            )

def test_later_release_commit_and_contents_preserved():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = git.Repo.clone_from("https://github.com/jrogue/sample.git", temp_dir)
        release_commits = [
            commit for commit in repo.iter_commits()
            if commit.message.strip() == "document version 1.0 release"
        ]

        assert release_commits, (
            "The unrelated later release commit is missing from the corrected history."
        )
        assert release_commits[0].tree["RELEASE.md"].data_stream.read() == (
            b"Version 1.0 of the sample is released.\n"
        ), "The later release commit's project contents were not preserved."
        assert repo.head.commit.tree["RELEASE.md"].data_stream.read() == (
            b"Version 1.0 of the sample is released.\n"
        ), "The preserved release content is missing or changed at the branch tip."
