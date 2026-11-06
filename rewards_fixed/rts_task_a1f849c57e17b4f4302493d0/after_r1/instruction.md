The repository at `https://github.com/jrogue/sample.git` contains a commit that introduced `secret.key`, which must not be present.
Remove that commit from the remote branch's history, ensuring the file no longer exists on the remote.
Preserve all unrelated commits that came after it and their project contents.
While fixing the history, examine whether any other project files refer to `secret.key`.
If such references exist, clean them up so the repository stays internally consistent — builds, listings, or scripts should not break because of a stale mention of the removed file.
After the remote history and project contents are clean, clone a fresh copy of the repository, list its top-level contents, and save the output in `/app/result.txt`.
