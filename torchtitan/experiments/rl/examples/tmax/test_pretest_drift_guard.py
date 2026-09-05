# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Environment-drift guard for the tmax pre-verify hook (reaudit).

Covers the training-side half: prepare_tmax_data carries the captured environment identity (the row's own
``pre_test_env_identity`` column) and this episode's identity (computed from the bundle: "dockerfile:<sha>" if
the bundle has a Dockerfile, else "image:<ref>") plus task_id into tmax; grading.py runs the pin check ONLY
when the two identities are equal, else skips. Pure/offline -- no sandbox, no torch; imports
prepare_tmax_data (stdlib-only at module level) and mirrors grading.py's run/skip condition.
Run: ``python3 test_pretest_drift_guard.py``.
"""
import hashlib
import importlib.util
import pathlib
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "prep_tmax", _HERE / "prepare_tmax_data.py"
)
PREP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PREP)

_IMG = "hamishi740/swerl-tmax-v3:2df1439e90a7"


def _bundle(dockerfile_bytes: bytes | None) -> str:
    d = pathlib.Path(tempfile.mkdtemp())
    if dockerfile_bytes is not None:
        (d / "environment").mkdir(parents=True)
        (d / "environment" / "Dockerfile").write_bytes(dockerfile_bytes)
    return str(d)


def test_episode_env_identity():
    # no Dockerfile in the bundle -> "image:<ref>"; a Dockerfile -> "dockerfile:<sha256>"
    assert PREP._episode_env_identity(_bundle(None), _IMG) == "image:" + _IMG
    body = b"FROM base\nRUN echo hi\n"
    assert (
        PREP._episode_env_identity(_bundle(body), _IMG)
        == "dockerfile:" + hashlib.sha256(body).hexdigest()
    )


def test_pretest_tmax_fields_carry_identities():
    f = PREP._pretest_tmax_fields(
        "task_1",
        _bundle(None),
        _IMG,
        pre_test_sh="exit 0",
        stamped_identity="image:" + _IMG,
    )
    assert f["pre_test_sh"] == "exit 0" and f["task_id"] == "task_1"
    assert f["pretest_env_identity"] == "image:" + _IMG  # the row's captured stamp
    assert f["pretest_episode_env_identity"] == "image:" + _IMG  # this episode
    # a task with no script in its row contributes nothing at all
    assert PREP._pretest_tmax_fields("task_absent", _bundle(None), _IMG) == {}


# --- grading.py run/skip decision, mirrored (grade_tmax is async + sandbox-bound; this is its exact predicate) ---
def _would_run(stamped: str, episode: str) -> bool:
    return bool(stamped and episode and stamped == episode)


def test_run_vs_skip_predicate():
    assert (
        _would_run("image:" + _IMG, "image:" + _IMG) is True
    )  # same env -> RUN the pin check
    assert (
        _would_run("image:" + _IMG, "dockerfile:" + "a" * 64) is False
    )  # Dockerfile rewritten -> SKIP
    assert _would_run("image:a", "image:b") is False  # image ref changed -> SKIP
    assert _would_run("", "image:" + _IMG) is False  # missing stamp -> SKIP
    assert _would_run("image:" + _IMG, "") is False  # missing episode id -> SKIP


def test_end_to_end_round0_runs_drift_skips():
    stamp = "image:" + _IMG

    def _fields(bundle, image):
        return PREP._pretest_tmax_fields(
            "task_1", bundle, image, pre_test_sh="exit 0", stamped_identity=stamp
        )

    # round 0: bundle has no Dockerfile, boots the stamped image -> identities match -> RUN
    r0 = _fields(_bundle(None), _IMG)
    assert _would_run(r0["pretest_env_identity"], r0["pretest_episode_env_identity"])
    # evolved: the round rewrote a Dockerfile into the bundle -> "dockerfile:<sha>" != "image:<ref>" -> SKIP
    ev = _fields(_bundle(b"FROM other\n"), _IMG)
    assert not _would_run(
        ev["pretest_env_identity"], ev["pretest_episode_env_identity"]
    )
    # evolved: same kind, different image ref -> SKIP
    im = _fields(_bundle(None), "hamishi740/swerl-tmax-v3:deadbeefcafe")
    assert not _would_run(
        im["pretest_env_identity"], im["pretest_episode_env_identity"]
    )


def _task_dir_with(
    instruction=b"do the thing\n", test_sh=b"echo ok\n", dockerfile=None
):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "instruction.md").write_bytes(instruction)
    (d / "tests").mkdir()
    (d / "tests" / "test.sh").write_bytes(test_sh)
    if dockerfile is not None:
        (d / "environment").mkdir(parents=True, exist_ok=True)
        (d / "environment" / "Dockerfile").write_bytes(dockerfile)
    return str(d)


def test_to_row_episode_identity_matches_stamp_under_default_prefix():
    # BLOCKING-defect regression (Fable 4e9966e): _to_row applies image_prefix ("docker.io/") to metadata.image,
    # but the episode identity must be computed from the UNPREFIXED ref so it equals the stamp. Go through the
    # real _to_row with the default prefix and a real stamped row; identities must be EQUAL.
    row = PREP._to_row(
        "task_1",
        _IMG,
        _task_dir_with(),
        PREP._DEFAULT_IMAGE_PREFIX,
        pre_test_sh="exit 0",
        stamped_identity="image:" + _IMG,
    )
    tm = row["metadata"]["tmax"]
    assert (
        row["metadata"]["image"] == PREP._DEFAULT_IMAGE_PREFIX + _IMG
    )  # image IS prefixed for boot
    assert tm["pretest_env_identity"] == "image:" + _IMG  # stamp, unprefixed
    assert (
        tm["pretest_episode_env_identity"] == "image:" + _IMG
    )  # episode, unprefixed -> EQUAL
    assert tm["pretest_env_identity"] == tm["pretest_episode_env_identity"]
    # a Dockerfile in the bundle overrides the image identity -> mismatch (would skip)
    row2 = PREP._to_row(
        "task_1",
        _IMG,
        _task_dir_with(dockerfile=b"FROM x\n"),
        PREP._DEFAULT_IMAGE_PREFIX,
        pre_test_sh="exit 0",
        stamped_identity="image:" + _IMG,
    )
    tm2 = row2["metadata"]["tmax"]
    assert tm2["pretest_episode_env_identity"].startswith("dockerfile:")
    assert tm2["pretest_env_identity"] != tm2["pretest_episode_env_identity"]


def test_selfcheck_raises_on_corpus_wide_mismatch():
    def _row(stamped, episode, pre="exit 0"):
        return {
            "metadata": {
                "tmax": {
                    "pre_test_sh": pre,
                    "pretest_env_identity": stamped,
                    "pretest_episode_env_identity": episode,
                }
            }
        }

    # every stamped row mismatches -> raise (corpus-wide silent skip must fail loudly)
    try:
        PREP.selfcheck_env_identities(
            [_row("image:a", "image:docker.io/a"), _row("image:b", "image:docker.io/b")]
        )
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    # at least one matches -> no raise; returns (matched, total, unstamped)
    assert PREP.selfcheck_env_identities(
        [_row("image:a", "image:a"), _row("image:b", "image:x")]
    ) == (1, 2, 0)
    # no stamped rows -> no raise, (0, 0, 0)
    assert PREP.selfcheck_env_identities([{"metadata": {"tmax": {}}}]) == (0, 0, 0)
    # a script with no usable identity can never run its check: counted, not raised
    assert PREP.selfcheck_env_identities(
        [_row("", "image:a"), _row("image:b", ""), _row("image:c", "image:c")]
    ) == (1, 1, 2)


def test_hook_data_comes_only_from_the_row():
    # The repo-side export is GONE: nothing outside the row may decide whether a task has a check.
    assert not hasattr(PREP, "_pretest_for")
    assert not hasattr(PREP, "_PRETEST_EXPORT_PATH")
    assert not hasattr(PREP, "_PRETEST_BY_ID")
    assert PREP._PRETEST_COLUMNS == ("pre_test_sh", "pre_test_env_identity")


def test_empty_cell_and_absent_column_are_no_ops():
    # the 282 with no check: an empty cell adds no tmax keys at all
    row = PREP._to_row(
        "t",
        _IMG,
        _task_dir_with(),
        PREP._DEFAULT_IMAGE_PREFIX,
        pre_test_sh="",
        stamped_identity="",
    )
    tm = row["metadata"]["tmax"]
    assert "pre_test_sh" not in tm and "pretest_env_identity" not in tm
    # a dataset published before the columns existed: the caller passes nothing
    row = PREP._to_row("t", _IMG, _task_dir_with(), PREP._DEFAULT_IMAGE_PREFIX)
    assert "pre_test_sh" not in row["metadata"]["tmax"]


def test_stamp_is_carried_not_derived():
    # THE TAUTOLOGY GUARD. The stamp must be the value captured with the pins, NOT recomputed from this row's
    # image -- a derived stamp equals the episode identity by construction, so the guard could never fire and a
    # rebuilt environment would have its stale pins asserted against it (refusing an honest episode).
    old, new = (
        "hamishi740/swerl-tmax-v3:OLDcaptured",
        "hamishi740/swerl-tmax-v3:NEWrebuilt",
    )
    row = PREP._to_row(
        "t",
        new,
        _task_dir_with(),
        PREP._DEFAULT_IMAGE_PREFIX,
        pre_test_sh="exit 0",
        stamped_identity="image:" + old,
    )
    tm = row["metadata"]["tmax"]
    assert (
        tm["pretest_env_identity"] == "image:" + old
    )  # what the pins were captured against
    assert (
        tm["pretest_episode_env_identity"] == "image:" + new
    )  # what this episode boots
    assert not _would_run(
        tm["pretest_env_identity"], tm["pretest_episode_env_identity"]
    )  # -> SKIP, which is the whole point


def test_parquet_projection_is_optional():
    # _read_parquet_rows must read a dataset WITH the columns and one WITHOUT: pyarrow raises on a projected
    # column that is not in the file, so the projection is intersected with each file's schema.
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:  # pragma: no cover - environment without pyarrow
        print("SKIP test_parquet_projection_is_optional (no pyarrow)")
        return
    env = {"env_name": "e", "image": _IMG, "task_id": "task_1"}
    for cols, expect in ((True, "exit 0"), (False, None)):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "data").mkdir()
        cells = {"ground_truth": ["task_1"], "env_config": [env]}
        if cols:
            cells["pre_test_sh"] = ["exit 0"]
            cells["pre_test_env_identity"] = ["image:" + _IMG]
        pq.write_table(pa.table(cells), d / "data" / "0.parquet")
        rows = PREP._read_parquet_rows(str(d))
        assert len(rows) == 1 and rows[0]["ground_truth"] == "task_1"
        assert rows[0].get("pre_test_sh") == expect


if __name__ == "__main__":
    import traceback

    tests = sorted(
        k for k, v in list(globals().items()) if k.startswith("test_") and callable(v)
    )
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
