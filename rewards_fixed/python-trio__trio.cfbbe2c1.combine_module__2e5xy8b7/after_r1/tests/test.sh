#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier

cat <<'SWESMITH_TESTS' > /tmp/swe_smith_tests.py
import json
import subprocess
import sys

tests = json.loads('["src/trio/_core/_tests/test_guest_mode.py::test_warn_set_wakeup_fd_overwrite", "src/trio/_core/_tests/test_ki.py::test_ki_protection_works", "src/trio/_tests/test_signals.py::test_catch_signals_wrong_thread", "src/trio/_tests/test_subprocess.py::test_pipes[open_process]", "src/trio/_tests/test_subprocess.py::test_interactive[open_process]", "src/trio/_tests/test_subprocess.py::test_run", "src/trio/_tests/test_subprocess.py::test_run_check", "src/trio/_tests/test_subprocess.py::test_stderr_stdout[open_process]", "/tmp/test_revision.py::test_trio_run_in_non_main_thread_has_no_warning", "/tmp/test_revision.py::test_full_wakeup_buffer_has_no_warning"]')
if not tests:
    print("No tests configured for this task.", file=sys.stderr)
    sys.exit(1)

try:
    import pytest_cov  # noqa: F401
    NOCOV = ["--no-cov"]
except ImportError:
    NOCOV = []

base_cmd = [
    "pytest",
    *NOCOV,
    "--disable-warnings",
    "--color=no",
    "--tb=short",
    "--verbose",
]

# Avoid hitting OS argv limits on rows with thousands of selected tests.
chunk_size = 200
exit_code = 0
for start in range(0, len(tests), chunk_size):
    chunk = tests[start : start + chunk_size]
    print(f"Running tests {start + 1}-{start + len(chunk)} of {len(tests)}", flush=True)
    result = subprocess.run(base_cmd + chunk, cwd="/testbed")
    if result.returncode != 0:
        exit_code = result.returncode
        break
sys.exit(exit_code)
SWESMITH_TESTS

cat <<'REVISION_TESTS' > /tmp/test_revision.py
import threading
import warnings
import os
import signal

import trio
from trio._core._wakeup_socketpair import WakeupSocketpair


def test_trio_run_in_non_main_thread_has_no_warning() -> None:
    observed = []

    async def main() -> None:
        pass

    def run_trio() -> None:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                trio.run(main)
            observed.append((None, [str(item.message) for item in caught]))
        except BaseException as exc:
            observed.append((exc, []))

    thread = threading.Thread(target=run_trio)
    thread.start()
    thread.join(30)
    assert not thread.is_alive() and observed == [(None, [])]


def test_full_wakeup_buffer_has_no_warning(capfd) -> None:
    wakeup = WakeupSocketpair()
    previous_handler = signal.signal(signal.SIGUSR1, lambda signum, frame: None)
    try:
        wakeup.wakeup_on_signals()
        while True:
            try:
                wakeup.write_sock.send(b"x" * 65536)
            except BlockingIOError:
                break
        os.kill(os.getpid(), signal.SIGUSR1)
        assert capfd.readouterr().err == ""
    finally:
        wakeup.close()
        signal.signal(signal.SIGUSR1, previous_handler)
REVISION_TESTS

if [ -f /opt/miniconda3/bin/activate ]; then
    source /opt/miniconda3/bin/activate
    conda activate testbed
fi

# Restore the repository's own test files from HEAD before judging: this
# grader runs inside the container the agent worked in, where a test
# edited to pass would otherwise be a passing test. SWE-smith's evaluation
# does the same (git checkout -- <F2P and P2P files>).
(cd /testbed && git checkout -q -- src/trio/_core/_tests/test_asyncgen.py src/trio/_core/_tests/test_exceptiongroup_gc.py src/trio/_core/_tests/test_guest_mode.py src/trio/_core/_tests/test_instrumentation.py src/trio/_core/_tests/test_io.py src/trio/_core/_tests/test_ki.py src/trio/_core/_tests/test_local.py src/trio/_core/_tests/test_mock_clock.py src/trio/_core/_tests/test_parking_lot.py src/trio/_core/_tests/test_run.py src/trio/_core/_tests/test_thread_cache.py src/trio/_core/_tests/test_tutil.py src/trio/_core/_tests/test_unbounded_queue.py src/trio/_tests/test_abc.py src/trio/_tests/test_channel.py src/trio/_tests/test_contextvars.py src/trio/_tests/test_deprecate.py src/trio/_tests/test_deprecate_strict_exception_groups_false.py src/trio/_tests/test_dtls.py src/trio/_tests/test_exports.py src/trio/_tests/test_fakenet.py src/trio/_tests/test_file_io.py src/trio/_tests/test_highlevel_generic.py src/trio/_tests/test_highlevel_open_tcp_listeners.py src/trio/_tests/test_highlevel_open_tcp_stream.py src/trio/_tests/test_highlevel_open_unix_stream.py src/trio/_tests/test_highlevel_serve_listeners.py src/trio/_tests/test_highlevel_socket.py src/trio/_tests/test_highlevel_ssl_helpers.py src/trio/_tests/test_path.py src/trio/_tests/test_repl.py src/trio/_tests/test_scheduler_determinism.py src/trio/_tests/test_signals.py src/trio/_tests/test_socket.py src/trio/_tests/test_ssl.py src/trio/_tests/test_subprocess.py src/trio/_tests/test_sync.py src/trio/_tests/test_testing.py src/trio/_tests/test_testing_raisesgroup.py src/trio/_tests/test_threads.py src/trio/_tests/test_timeouts.py src/trio/_tests/test_tracing.py src/trio/_tests/test_trio.py src/trio/_tests/test_unix_pipes.py src/trio/_tests/test_util.py src/trio/_tests/tools/test_gen_exports.py src/trio/_tests/tools/test_mypy_annotate.py 2>/dev/null) || true

if python /tmp/swe_smith_tests.py; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
