"""``_build_exec_command`` run through a local bash, as the Daytona session runs it."""

import shutil
import subprocess
import uuid

import pytest

from torchtitan.experiments.rl.harness.sandbox import daytona


def _run(cmd: str, tmp_path, stdin: str = "") -> subprocess.CompletedProcess:
    script = tmp_path / "wrapper.sh"
    script.write_text(daytona._build_exec_command(cmd, user="root", env=None, timeout=10))
    return subprocess.run(
        ["bash", str(script)], input=stdin, capture_output=True, text=True, check=False
    )


def test_errors_read_like_a_plain_shell_not_the_staging_path(tmp_path):
    proc = _run("no_such_cmd_zz", tmp_path)
    assert proc.returncode == 127
    assert proc.stderr.startswith("bash: line 1: no_such_cmd_zz: command not found")
    assert daytona._EXEC_STAGING_DIR not in proc.stderr


def test_stdin_exit_code_and_heredoc_survive(tmp_path):
    proc = _run("read x; cat <<'EOF'\n$x stays literal\nEOF\necho got=$x; exit 3", tmp_path, "hi\n")
    assert (proc.returncode, proc.stdout) == (3, "$x stays literal\ngot=hi\n")


@pytest.mark.parametrize("pad", [100, 200_000])
def test_a_script_past_the_argv_limit_still_runs(tmp_path, pad):
    proc = _run(f"echo {'y' * pad} | wc -c", tmp_path)
    assert (proc.returncode, proc.stdout.strip()) == (0, str(pad + 1))


@pytest.mark.skipif(shutil.which("pkill") is None, reason="needs pkill")
def test_pkill_f_in_the_command_does_not_kill_its_own_shell(tmp_path):
    token = f"tt_self_match_{uuid.uuid4().hex}"
    proc = _run(f"pkill -f {token}; echo survived", tmp_path)
    assert (proc.returncode, proc.stdout) == (0, "survived\n")
