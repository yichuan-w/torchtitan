REFLOG = os.path.join(REPO_DIR, '.git', 'logs', 'HEAD')


def _checkout_sequence():
    """Which of the two PR branches HEAD visited, in order.

    Deleting a branch removes that branch's own reflog but not HEAD's, so the
    record of having checked a PR out survives the cleanup this task also asks
    for. That is what makes it usable here: every other assertion in this file
    is about something NOT existing, and the image satisfies all of them before
    the agent has done anything, so on their own they pay full marks for a
    single `prm list`.
    """
    if not os.path.exists(REFLOG):
        return []
    seq = []
    with open(REFLOG, 'r', errors='replace') as f:
        for line in f:
            if '\tcheckout: moving from ' not in line:
                continue
            dest = line.rsplit(' to ', 1)[-1].strip()
            for pr in ('2731', '2726'):
                if dest.startswith(pr) and (not seq or seq[-1] != pr):
                    seq.append(pr)
    return seq


def test_both_prs_were_checked_out():
    seq = _checkout_sequence()
    assert '2731' in seq, (
        "PR 2731 was never checked out: git's HEAD reflog records no move onto "
        "its branch. Removing a PR that was never added is not the task."
    )
    assert '2726' in seq, (
        "PR 2726 was never checked out: git's HEAD reflog records no move onto "
        "its branch."
    )


def test_prs_were_cycled():
    seq = _checkout_sequence()
    assert seq.count('2731') >= 2 and seq.count('2726') >= 2, (
        "The task asks for both PRs to be checked out and then switched back "
        f"to in turn; HEAD only visited them in this order: {seq}"
    )
