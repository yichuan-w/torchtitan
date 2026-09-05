from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import finalize_interrupted_traces as fit
from torchtitan.experiments.rl.examples.tmax import layout


def _rewrite(root: layout.Root, stamp_: str, *, status: str,
             sessions: dict[str, str]) -> layout.RewriteDir:
    rw = root.evolution.task("tw_a").rewrite("harder", stamp_)
    layout.write_json_atomic(rw.meta, {"task": "tw_a", "status": status, "finished": None})
    for kind, st in sessions.items():
        sd = rw.session(kind, stamp_)
        layout.write_json_atomic(sd.meta, {"kind": kind, "status": st, "finished": None})
    return rw


def test_finalize_marks_only_running_sessions_and_rewrites(tmp_path) -> None:
    root = layout.Root(tmp_path / "root")
    live = _rewrite(root, "20260904-100000Z", status="running",
                    sessions={"agent": "completed", "repair": "running"})
    done = _rewrite(root, "20260904-090000Z", status="rejected", sessions={"agent": "completed"})

    counts = fit.finalize_interrupted(root, stopped_loop_pid=123)

    assert counts == {"marked": 2, "skipped": 3, "failed": 0}
    meta = json.loads(live.meta.read_text())
    assert meta["status"] == "interrupted" and meta["stopped_loop_pid"] == 123
    assert meta["finished"] and "pid 123" in meta["error"]
    by_kind = {s.path.name.split("--")[1]: json.loads(s.meta.read_text())
               for s in live.session_dirs()}
    assert by_kind["repair"]["status"] == "interrupted" and by_kind["repair"]["finished"]
    assert by_kind["agent"]["status"] == "completed" and by_kind["agent"]["finished"] is None
    assert json.loads(done.meta.read_text())["status"] == "rejected"

    assert fit.finalize_interrupted(root, stopped_loop_pid=123) == {
        "marked": 0, "skipped": 5, "failed": 0}


def test_finalize_over_an_empty_root_is_nothing(tmp_path) -> None:
    assert fit.finalize_interrupted(layout.Root(tmp_path / "root"), stopped_loop_pid=1) == {
        "marked": 0, "skipped": 0, "failed": 0}
