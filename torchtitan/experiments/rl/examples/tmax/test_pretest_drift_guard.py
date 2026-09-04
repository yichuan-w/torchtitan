"""Dockerfile-drift guard for the tmax pre-verify hook (reaudit).

Covers the training-side half: prepare_tmax_data carries the capture stamp (from the export) and this
episode's Dockerfile sha (from the bundle) plus task_id into tmax; grading.py SKIPS the pin check only on a
proven mismatch. Pure/offline -- no sandbox, no torch; imports prepare_tmax_data (stdlib-only at module level)
and mirrors grading.py's skip condition. Run: ``python3 test_pretest_drift_guard.py``.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ptd", _HERE / "prepare_tmax_data.py")
PTD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PTD)


def _bundle(dockerfile_bytes: bytes | None) -> str:
    d = pathlib.Path(tempfile.mkdtemp())
    if dockerfile_bytes is not None:
        (d / "environment").mkdir(parents=True)
        (d / "environment" / "Dockerfile").write_bytes(dockerfile_bytes)
    return str(d)


def _export(rows: list[dict]) -> str:
    p = tempfile.mktemp(suffix=".jsonl")
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def _use_export(path: str) -> None:
    PTD._PRETEST_EXPORT_PATH = path
    PTD._PRETEST_BY_ID = None            # bust the module cache


def test_dockerfile_sha256_bundle():
    body = b"FROM scratch\nRUN echo hi\n"
    tdir = _bundle(body)
    assert PTD._dockerfile_sha256_bundle(tdir) == hashlib.sha256(body).hexdigest()
    assert PTD._dockerfile_sha256_bundle(_bundle(None)) == ""     # verifier-only bundle -> ""


def test_pretest_tmax_fields_carries_both_shas_and_task_id():
    cap = "a" * 64
    _use_export(_export([{"task_id": "task_1", "pre_test_sh": "exit 0", "dockerfile_sha256": cap}]))
    body = b"FROM base\n"
    tdir = _bundle(body)
    f = PTD._pretest_tmax_fields("task_1", tdir)
    assert f["pre_test_sh"] == "exit 0"
    assert f["task_id"] == "task_1"
    assert f["pretest_dockerfile_sha256"] == cap                            # capture stamp from the export
    assert f["pretest_episode_dockerfile_sha256"] == hashlib.sha256(body).hexdigest()  # this episode
    # a task with no exported pre_test contributes no tmax fields
    assert PTD._pretest_tmax_fields("task_absent", tdir) == {}


# --- grading.py skip decision, mirrored (grade_tmax is async + sandbox-bound; this is its exact predicate) ---
def _would_skip(cap: str, cur: str) -> bool:
    cap = cap or ""
    cur = cur or ""
    return bool(cap and cur and cap != cur)


def test_skip_vs_run_predicate():
    assert _would_skip("a" * 64, "b" * 64) is True     # proven drift -> SKIP the pin check
    assert _would_skip("a" * 64, "a" * 64) is False    # same env -> RUN as today
    assert _would_skip("", "b" * 64) is False          # capture unknown -> not proof -> RUN
    assert _would_skip("a" * 64, "") is False          # episode sha uncomputable -> not proof -> RUN
    assert _would_skip("", "") is False                # nothing known -> RUN (inert until stamped)


def test_end_to_end_match_runs_drift_skips():
    cap = hashlib.sha256(b"FROM base\n").hexdigest()
    _use_export(_export([{"task_id": "task_1", "pre_test_sh": "exit 0", "dockerfile_sha256": cap}]))
    same = PTD._pretest_tmax_fields("task_1", _bundle(b"FROM base\n"))
    assert not _would_skip(same["pretest_dockerfile_sha256"], same["pretest_episode_dockerfile_sha256"])
    drift = PTD._pretest_tmax_fields("task_1", _bundle(b"FROM other\n"))
    assert _would_skip(drift["pretest_dockerfile_sha256"], drift["pretest_episode_dockerfile_sha256"])


if __name__ == "__main__":
    import traceback
    tests = sorted(k for k, v in list(globals().items()) if k.startswith("test_") and callable(v))
    fail = 0
    for t in tests:
        try:
            globals()[t]()
            print("PASS", t)
        except Exception:
            fail += 1
            print("FAIL", t)
            traceback.print_exc()
    print(f"{len(tests) - fail}/{len(tests)} passed")
    raise SystemExit(1 if fail else 0)
