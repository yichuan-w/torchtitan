"""Exercise offline restart discovery without contacting Manifold or W&B."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SEGMENTS = ("offline-run-20261010_000000-old", "offline-run-20261010_010000-new")


def _executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o755)


def _run_cycles(
    tmp_path: Path, *, empty: list[str], failed=(), cycles=1, restart_aware=False
) -> dict:
    tools = tmp_path / "tools"
    tools.mkdir()
    mirror_log = tmp_path / "mirror.jsonl"
    transfer_log = tmp_path / "transfer.jsonl"
    _executable(
        tools / "manifold",
        """import json, os, sys
from pathlib import Path
if sys.argv[1] == "ls":
    print("\\n".join(json.loads(os.environ["TEST_SEGMENTS"])))
elif sys.argv[1] == "get":
    segment = Path(sys.argv[2]).parent.name
    with Path(os.environ["TEST_TRANSFER_LOG"]).open("a") as stream:
        stream.write(json.dumps(segment) + "\\n")
    if segment in json.loads(os.environ["TEST_FAILED"]):
        sys.exit(1)
    content = b"" if segment in json.loads(os.environ["TEST_EMPTY"]) else b"history"
    Path(sys.argv[3]).write_bytes(content)
elif sys.argv[1] == "getr":
    segment = Path(sys.argv[2]).name
    with Path(os.environ["TEST_TRANSFER_LOG"]).open("a") as stream:
        stream.write(json.dumps(segment) + "\\n")
    if segment in json.loads(os.environ["TEST_FAILED"]):
        sys.exit(1)
    folder = Path(sys.argv[3]) / segment
    folder.mkdir(parents=True, exist_ok=True)
    content = b"" if segment in json.loads(os.environ["TEST_EMPTY"]) else b"history"
    (folder / f"run-{segment.rsplit('-', 1)[-1]}.wandb").write_bytes(content)
else:
    sys.exit(2)
""",
    )
    _executable(
        tools / "mirror-python",
        """import json, os, sys
from pathlib import Path
with Path(os.environ["TEST_MIRROR_LOG"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
""",
    )
    _executable(
        tools / "sleep",
        """import os, signal
from pathlib import Path
counter = Path(os.environ["TEST_CYCLE_COUNT"])
value = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(value))
if value >= int(os.environ["TEST_CYCLES"]):
    os.kill(os.getppid(), signal.SIGTERM)
""",
    )
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "WANDB_KEEPALIVE": "0",
        "WANDB_RESTART_AWARE": "1" if restart_aware else "0",
        "WANDB_MIRROR_RUN_ID": "fixture",
        "WANDB_PYTHON": str(tools / "mirror-python"),
        "WANDB_BASE_URL": "http://127.0.0.1",
        "WANDB_BUCKET_WANDB": "fixture/bucket/wandb",
        "WANDB_OFFLINE_RUN": "",
        "TMP_DIR": str(tmp_path / "cache"),
        "TEST_SEGMENTS": json.dumps(SEGMENTS),
        "TEST_EMPTY": json.dumps(empty),
        "TEST_FAILED": json.dumps(failed),
        "TEST_MIRROR_LOG": str(mirror_log),
        "TEST_TRANSFER_LOG": str(transfer_log),
        "TEST_CYCLE_COUNT": str(tmp_path / "cycles"),
        "TEST_CYCLES": str(cycles),
    }
    result = subprocess.run(
        ["bash", str(Path(__file__).with_name("wandb_autosync.sh")), "fixture", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return {
        "stdout": result.stdout,
        "mirrors": (
            [json.loads(line) for line in mirror_log.read_text().splitlines()]
            if mirror_log.exists()
            else []
        ),
        "transfers": [
            json.loads(line) for line in transfer_log.read_text().splitlines()
        ],
    }


def test_empty_preempted_segment_does_not_block_later_history(tmp_path):
    result = _run_cycles(tmp_path, empty=[SEGMENTS[0]], cycles=2)

    assert len(result["mirrors"]) == 2
    assert all(
        call[-1] == str(tmp_path / "cache" / SEGMENTS[1] / "run-new.wandb")
        for call in result["mirrors"]
    )
    assert result["transfers"] == [SEGMENTS[0], SEGMENTS[1], SEGMENTS[1]]
    assert "skipping empty segment" in result["stdout"]
    assert "download failed" not in result["stdout"]


def test_all_empty_segments_do_not_attempt_sync(tmp_path):
    result = _run_cycles(tmp_path, empty=list(SEGMENTS))

    assert result["mirrors"] == []
    assert "no non-empty offline run yet" in result["stdout"]
    assert "download failed" not in result["stdout"]


def test_real_transfer_failure_still_prevents_partial_sync(tmp_path):
    result = _run_cycles(tmp_path, empty=[], failed=[SEGMENTS[0]])

    assert result["mirrors"] == []
    assert "download failed" in result["stdout"]


def test_restart_mode_refreshes_every_attempt_and_passes_a_durable_cursor(tmp_path):
    result = _run_cycles(tmp_path, empty=[], cycles=2, restart_aware=True)
    assert result["transfers"] == list(SEGMENTS) * 2
    assert len(result["mirrors"]) == 2
    for call in result["mirrors"]:
        assert "--restart-aware" in call
        index = call.index("--cursor-file")
        assert call[index + 1] == str(tmp_path / "cache" / "mirror-cursor-fixture.json")
