def _target_pid():
    """Find the task's target process by its command rather than by its PID.

    PID allocation belongs to the sandbox. The image used to force the counter
    so this process would land on 1179; that worked only where
    /proc/sys/kernel/ns_last_pid could be driven, and burned a core forking
    everywhere else.
    """
    import os

    matches = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0")[0]
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if argv0 == b"/usr/local/bin/target-proc":
            matches.append(entry)
    assert len(matches) == 1, (
        f"Expected exactly one /usr/local/bin/target-proc process, found {matches}"
    )
    return matches[0]
