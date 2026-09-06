# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Integrity baseline for protected paths: the command builder, the two harness halves, and the
grading decision -- driven through the REAL ``grade_tmax`` with a fake sandbox that reproduces
the nonce handshake. Pure / offline: no sandbox, no network, no torch (grading.py and
integrity_baseline.py are stdlib at module level and are loaded from their files, registered
under their package names, as the sibling tests do). The rollouter seam is torch-bound, so its
wiring is asserted on the source and the function it calls is exercised directly.
Run: ``python3 test_integrity_baseline.py``.
"""

import asyncio
import importlib.util
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_PKG = "torchtitan.experiments.rl.examples.tmax"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.{name}", _HERE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


IB = _load("integrity_baseline")
G = _load("grading")

_D1 = "a" * 64
_D2 = "b" * 64
_D3 = "c" * 64


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- the command builder
def test_command_for_a_two_path_fixture_is_exactly_this():
    """One absolute path with a space (quoted), one relative path resolved against the workdir. The
    string is pinned verbatim: both harness halves run it, so any change here is a change to what
    'unchanged' means. Every subcommand's stderr is discarded inside the string, because the executor
    merges stderr into the text it returns and a tool warning would become a malformed line."""
    entries = [("path", "/app/data dir/model.bin"), ("path", "tests")]
    cmd = IB.build_digest_command(entries, "/workspace")
    expected = (
        "if [ -f '/app/data dir/model.bin' ]; then printf '%s 0\\n' \"$(sha256sum -- '/app/data dir/model.bin' 2>/dev/null | cut -d' ' -f1)\"; "
        "elif [ -d '/app/data dir/model.bin' ]; then printf '%s 0\\n' \"$(cd -- '/app/data dir/model.bin' 2>/dev/null && find . -type f -print0 2>/dev/null | LC_ALL=C sort -z | xargs -0 -r sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1)\"; "
        "elif [ -e '/app/data dir/model.bin' ]; then printf 'OTHER 0\\n'; else printf 'ABSENT 0\\n'; fi; "
        "if [ -f /workspace/tests ]; then printf '%s 1\\n' \"$(sha256sum -- /workspace/tests 2>/dev/null | cut -d' ' -f1)\"; "
        "elif [ -d /workspace/tests ]; then printf '%s 1\\n' \"$(cd -- /workspace/tests 2>/dev/null && find . -type f -print0 2>/dev/null | LC_ALL=C sort -z | xargs -0 -r sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1)\"; "
        "elif [ -e /workspace/tests ]; then printf 'OTHER 1\\n'; else printf 'ABSENT 1\\n'; fi"
    )
    assert cmd == expected, cmd
    assert IB.resolve_path("../etc/passwd", "/workspace") == "/etc/passwd"
    assert IB.resolve_path("/abs", "/workspace") == "/abs"
    q = IB.build_digest_command([("path", "it's")], "/w")
    assert "'/w/it'\"'\"'s'" in q  # shlex quoting of a quote


def test_command_entry_is_one_argv_element_via_the_hooks_wrapper():
    """The command enters the string ONLY as a single-quoted assignment and is referenced as "$_cmd" --
    one argv element to `bash -c` under the hook exporter's exact wrapper. Never interpolated: every
    shipped entry contains a quote character."""
    entry = """printf '%s' "it's" '"q"' """
    cmd = IB.build_digest_command([("cmd", entry)], "/workspace")
    expected = (
        "_cmd='printf '\"'\"'%s'\"'\"' \"it'\"'\"'s\" '\"'\"'\"q\"'\"'\"' '; "
        'if _out="$(env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/nonexistent LC_ALL=C bash -c "$_cmd" </dev/null 2>/dev/null)"; then '
        "printf '%s 0\\n' \"$(printf '%s' \"$_out\" | sha256sum | cut -d' ' -f1)\"; else printf 'FAIL 0\\n'; fi"
    )
    assert cmd == expected, cmd
    assert (
        IB.CMD_WRAPPER
        == 'env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/nonexistent LC_ALL=C bash -c "$_cmd" </dev/null 2>/dev/null'
    )
    assert (
        IB.PIN_SAFE_PATH == "/usr/bin:/bin:/usr/sbin:/sbin"
    )  # the exporter's _PIN_SAFE_PATH, verbatim
    # paths first, then commands: the index space both sides derive from the same function
    tmax = {"protected_paths": ["/a", "b c"], "protected_cmds": ["true"]}
    assert IB.protected_entries_of(tmax) == [
        ("path", "/a"),
        ("path", "b c"),
        ("cmd", "true"),
    ]


def test_parse_maps_by_index_and_refuses_anything_incomplete():
    paths = [("path", "/a"), ("path", "dir"), ("path", "gone"), ("cmd", "false")]
    out = f"{_D1} 0\n{_D2} 1\nABSENT 2\nFAIL 3\n"
    assert IB.parse_digest_output(out, paths) == {
        "/a": _D1,
        "dir": _D2,
        "gone": "ABSENT",
        "false": "FAIL",
    }
    assert (
        IB.parse_digest_output(f"FAIL 3\nABSENT 2\n{_D2} 1\n\n{_D1} 0\n", paths)["/a"]
        == _D1
    )  # order-free
    for bad, why in (
        (f"{_D1} 0\n{_D2} 1\nABSENT 2\n", "missing line"),
        (f"{_D1} 0\n{_D2} 1\nABSENT 2\nFAIL 3\n{_D3} 4\n", "unexpected index"),
        (f"{_D1} 0\n{_D1} 0\nABSENT 2\nFAIL 3\n", "repeated index"),
        (f"{_D1} 0\n 1\nABSENT 2\nFAIL 3\n", "empty digest (a failed subshell)"),
        (
            f"{_D1} 0\n{_D2} 1\n{IB.TRUNCATION_MARKER}\nABSENT 2\nFAIL 3\n",
            "truncation marker",
        ),
        ("garbage\n", "malformed"),
    ):
        try:
            IB.parse_digest_output(bad, paths)
        except IB.IntegrityHarnessError:
            pass
        else:
            raise AssertionError(f"must refuse: {why}")


def test_truncation_marker_is_the_backends_own_text():
    """Pinned to the literal in harness/sandbox/daytona.py without importing it (torch-bound)."""
    src = (
        _HERE.parents[1] / "harness" / "sandbox" / "daytona.py"
    ).read_text()  # tmax -> examples -> rl
    m = re.search(
        r'truncation_marker = shlex\.quote\("\\n(\[torchtitan: command output truncated\])\\n"\)',
        src,
    )
    assert m and m.group(1) == IB.TRUNCATION_MARKER, IB.TRUNCATION_MARKER


def test_protected_paths_of_validates():
    assert IB.protected_paths_of({}) == []
    assert IB.protected_paths_of({"protected_paths": ["/a", "b"]}) == ["/a", "b"]
    for bad in ("not-a-list", ["ok", ""], [1], "/a"):
        for key, fn in (
            ("protected_paths", IB.protected_paths_of),
            ("protected_cmds", IB.protected_cmds_of),
        ):
            try:
                fn({key: bad})
            except IB.IntegrityHarnessError:
                pass
            else:
                raise AssertionError(f"must refuse {bad!r} for {key}")
    try:
        IB.protected_cmds_of(
            {"protected_cmds": ["echo 1\necho 2"]}
        )  # the hook's manifest is line-based
    except IB.IntegrityHarnessError:
        pass
    else:
        raise AssertionError("a newline inside a command entry must refuse")


# ---------------------------------------------------------------- the rollouter half
class _Exec:
    """A fake ``exec``: answers the digest command from a script of (rc, stdout, stderr) and records
    every call so the test can see the user/timeout it was given."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return self.answers.pop(0)


def test_capture_returns_none_without_protected_paths_and_digests_with():
    ex = _Exec()
    assert (
        _run(
            IB.capture_integrity_baseline(
                ex, {"test_sh": "x"}, workdir="/w", timeout=900
            )
        )
        is None
    )
    assert ex.calls == []  # nothing ran
    ex = _Exec((0, f"{_D1} 0\nABSENT 1\n", ""))
    got = _run(
        IB.capture_integrity_baseline(
            ex, {"protected_paths": ["/a", "b"]}, workdir="/w", timeout=900
        )
    )
    assert got == {"/a": _D1, "b": "ABSENT"}
    cmd, kw = ex.calls[0]
    assert cmd == IB.build_digest_command([("path", "/a"), ("path", "b")], "/w")
    assert kw == {"check": False, "timeout": 120}  # min(120, 900)
    ex = _Exec((0, f"{_D1} 0\n", ""))
    _run(
        IB.capture_integrity_baseline(
            ex, {"protected_paths": ["/a"]}, workdir="/w", timeout=30
        )
    )
    assert ex.calls[0][1]["timeout"] == 30


def test_capture_raises_on_truncation_and_on_nonzero_exit():
    """A harness failure voids the episode; it never becomes a baseline of nothing."""
    for answer in (
        (0, f"{_D1} 0\n{IB.TRUNCATION_MARKER}\n", ""),
        (1, f"{_D1} 0\n", "sh: boom"),
    ):
        try:
            _run(
                IB.capture_integrity_baseline(
                    _Exec(answer),
                    {"protected_paths": ["/a"]},
                    workdir="/w",
                    timeout=900,
                )
            )
        except IB.IntegrityHarnessError:
            pass
        else:
            raise AssertionError(f"must raise on {answer!r}")


def test_rollouter_captures_at_the_seam_and_hands_the_baseline_to_grading():
    """rollouter.py is torch-bound, so its wiring is asserted on the source: initialised where
    ``submitted`` is, captured right after seed_workspace and before the agent loop, passed by keyword
    to grade_tmax -- never stashed on the sandbox or the sample."""
    src = (_HERE / "rollouter.py").read_text()
    init = src.index("baseline_digests: dict[str, str] | None = None")
    seed = src.index("await seed_workspace(root_sb, sample.tmax)")
    cap = src.index("baseline_digests = await capture_baseline(")
    tmux = src.index('"command -v tmux >/dev/null 2>&1"')
    grade = src.index("reward = await grade_tmax(")
    passed = src.index("baseline_digests=baseline_digests,")
    assert src.index("submitted = False") < init < seed < cap < tmux < grade < passed
    assert (
        "root_sb," in src[cap : cap + 200]
    )  # the root wrapper itself; the helper calls its exec
    assert "root_sb.exec" not in src[cap : cap + 200]
    assert not re.search(r"sandbox\.baseline|sample\.baseline|setattr\(.*baseline", src)


def test_tmax_protected_fields_builds_only_non_empty_validated_keys():
    """Every producer of a row (the dataset prep, the loop's packer) builds the keys here: each
    present only when its list is non-empty, in digest order, validated like the readers do."""
    assert IB.tmax_protected_fields(None, None) == {}
    assert IB.tmax_protected_fields([], []) == {}
    assert IB.tmax_protected_fields(["/a", "b c"], None) == {
        "protected_paths": ["/a", "b c"]
    }
    got = IB.tmax_protected_fields(["/a"], ["printf '%s' \"x\""])
    assert list(got) == ["protected_paths", "protected_cmds"]
    assert got["protected_cmds"] == ["printf '%s' \"x\""]
    for paths, cmds in (
        ("/a", None),
        ([""], None),
        ([1], None),
        (None, ["a\nb"]),
        (None, "cmd"),
    ):
        try:
            IB.tmax_protected_fields(paths, cmds)
        except IB.IntegrityHarnessError:
            pass
        else:
            raise AssertionError(f"must refuse {paths!r}, {cmds!r}")


def test_capture_baseline_drives_the_sandboxs_own_exec():
    """The sandbox-object form every seam calls: it runs the same string through ``sb.exec``
    (root already forced by the wrapper the caller hands in) with the capped timeout."""
    calls = []

    class SB:
        async def exec(self, cmd, *, check, timeout):
            calls.append((cmd, check, timeout))
            return 0, f"{_D1} 0\n", ""

    got = asyncio.run(
        IB.capture_baseline(
            SB(), {"protected_paths": ["/a"]}, workdir="/w", timeout=300
        )
    )
    assert got == {"/a": _D1}
    assert calls == [(IB.build_digest_command([("path", "/a")], "/w"), False, 120)]
    assert asyncio.run(IB.capture_baseline(SB(), {}, workdir="/w", timeout=5)) is None
    assert len(calls) == 1  # nothing runs for a row without entries


# ---------------------------------------------------------------- the grading half, end to end
class _FakeSandbox:
    """Just enough of Sandbox for grade_tmax: the pre-grade command leaves the nonce in the reward file,
    ``bash /tests/test.sh`` writes the verifier's reward, the digest command answers from a script."""

    def __init__(self, digest_answers, verifier_reward="1"):
        self.files = {}
        self.digest_answers = list(digest_answers)
        self.verifier_reward = verifier_reward
        self.tests_ran = False
        self.calls = []

    async def exec(self, cmd, *, user="root", env=None, timeout=120, check=False):
        self.calls.append((cmd, user, timeout))
        m = re.search(r"printf %s (\S+) > (\S+)$", cmd)
        if m:  # the pre-grade sentinel
            nonce = m.group(1).strip("'")
            self.files[m.group(2).strip("'")] = nonce
            return 0, "", ""
        if "sha256sum" in cmd:  # the digest command
            return self.digest_answers.pop(0)
        if "bash /tests/test.sh" in cmd:
            self.tests_ran = True
            self.files["/logs/verifier/reward.txt"] = self.verifier_reward
            return 0, "", ""
        return 0, "", ""

    async def write_file(self, path, content, *, user="root"):
        self.files[path] = content

    async def read_file(self, path, *, user="root"):
        return self.files.get(path, "")


_TMAX = {
    "test_sh": "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n",
    "fixtures": {},
    "task_id": "task_000001_aaaaaaaa",
    "protected_paths": ["/app/pinned", "tests"],
    "protected_cmds": ["sqlite3 /app/db 'select 1'"],
}
_BASE = {"/app/pinned": _D1, "tests": _D2, "sqlite3 /app/db 'select 1'": _D3}


def _grade(sb, tmax=_TMAX, baseline=_BASE):
    return _run(
        G.grade_tmax(
            sb, tmax, workdir="/workspace", timeout_sec=900, baseline_digests=baseline
        )
    )


def test_intact_paths_run_the_verifier_and_the_check_ran_as_root():
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")])
    assert _grade(sb) == 1.0 and sb.tests_ran
    digest_calls = [c for c in sb.calls if "sha256sum" in c[0]]
    assert (
        len(digest_calls) == 1
        and digest_calls[0][1] == "root"
        and digest_calls[0][2] == 120
    )
    assert digest_calls[0][0] == IB.build_digest_command(
        IB.protected_entries_of(_TMAX), "/workspace"
    )  # same builder


def test_one_changed_file_scores_zero_without_running_the_verifier():
    import logging

    records = []
    h = logging.Handler()
    h.emit = records.append
    G.logger.addHandler(h)
    G.logger.setLevel(logging.INFO)
    try:
        sb = _FakeSandbox(
            [(0, f"{_D2} 0\n{_D2} 1\n{_D3} 2\n", "")]
        )  # /app/pinned changed
        assert _grade(sb) == 0.0 and not sb.tests_ran
    finally:
        G.logger.removeHandler(h)
    msgs = [
        r.getMessage()
        for r in records
        if "integrity baseline difference" in r.getMessage()
    ]
    assert (
        len(msgs) == 1 and "/app/pinned" in msgs[0] and "'tests'" not in msgs[0]
    ), msgs  # only the differing path is named
    # ABSENT at baseline, present at grade -> a difference too
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")])
    assert (
        _grade(
            sb,
            baseline={
                "/app/pinned": "ABSENT",
                "tests": _D2,
                "sqlite3 /app/db 'select 1'": _D3,
            },
        )
        == 0.0
        and not sb.tests_ran
    )
    # a command that succeeded at baseline and FAILS at grade (or the reverse) is a difference; FAIL on both is equal
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\nFAIL 2\n", "")])
    assert _grade(sb) == 0.0 and not sb.tests_ran
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\nFAIL 2\n", "")])
    assert (
        _grade(sb, baseline=dict(_BASE, **{"sqlite3 /app/db 'select 1'": "FAIL"}))
        == 1.0
        and sb.tests_ran
    )


def test_missing_baseline_raises_and_harness_failures_void_the_episode():
    for sb, baseline, why in (
        (
            _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")]),
            None,
            "no baseline for a protected row",
        ),
        (
            _FakeSandbox([(0, f"{_D1} 0\n{IB.TRUNCATION_MARKER}\n", "")]),
            _BASE,
            "truncated at grade",
        ),
        (_FakeSandbox([(2, "", "denied")]), _BASE, "nonzero exit at grade"),
    ):
        try:
            _grade(sb, baseline=baseline)
        except IB.IntegrityHarnessError:
            assert not sb.tests_ran
        else:
            raise AssertionError(f"must raise: {why}")


def test_rows_without_protected_paths_keep_the_old_behaviour_byte_for_byte():
    """No protected paths -> no digest command runs, the baseline kwarg is ignored, and the pre_test
    block is consulted exactly as before (here: no pre_test, so straight to the verifier)."""
    sb = _FakeSandbox([])
    tmax = {
        k: v for k, v in _TMAX.items() if k not in ("protected_paths", "protected_cmds")
    }
    assert _grade(sb, tmax=tmax, baseline=None) == 1.0 and sb.tests_ran
    assert not any("sha256sum" in c[0] for c in sb.calls)
    assert (
        _grade(_FakeSandbox([]), tmax=tmax, baseline=_BASE) == 1.0
    )  # a stray baseline is inert
    # and a protected row never consults pre_test_sh: a check that would refuse is not even run
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")])
    tmax = dict(
        _TMAX,
        pre_test_sh="exit 1",
        pretest_env_identity="image:x",
        pretest_episode_env_identity="image:x",
    )
    assert _grade(sb, tmax=tmax) == 1.0 and not any("exit 1" in c[0] for c in sb.calls)


import contextlib
import logging as _logging
import types


@contextlib.contextmanager
def _captured_log():
    """grading's logger lines during the block, as strings."""
    lines: list[str] = []

    class H(_logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    h = H()
    lg = _logging.getLogger(G.__name__)
    lg.addHandler(h)
    lg.setLevel(_logging.INFO)
    try:
        yield lines
    finally:
        lg.removeHandler(h)


# ---------------------------------------------------------------- the raw-SDK grader (local_smoke)
class _FakeSDK:
    """The raw daytona Sandbox surface grade_tmax_daytona / seed_workspace_daytona use: a sync
    ``process.exec(cmd, timeout=)`` returning ``exit_code`` / ``result`` and ``fs.upload_file``.
    Every command arrives wrapped by _root_sh; the fake dispatches on what is inside."""

    class _Proc:
        def __init__(self, outer):
            self.outer = outer

        def exec(self, cmd, timeout=None):
            o = self.outer
            o.calls.append((cmd, timeout))
            m = re.search(r"printf %s (tmax-sentinel-[0-9a-f]+) > ([^\s']+)", cmd)
            if m:
                o.files[m.group(2)] = m.group(1)
                return types.SimpleNamespace(exit_code=0, result="")
            if "sha256sum" in cmd:
                o.digest_execs += 1
                rc, out, _err = o.digest_answers.pop(0)
                return types.SimpleNamespace(exit_code=rc, result=out)
            if "bash /tests/test.sh" in cmd:
                o.tests_ran = True
                o.files["/logs/verifier/reward.txt"] = o.verifier_reward
                return types.SimpleNamespace(exit_code=0, result="")
            m = re.search(r"cat ([^\s']+)", cmd)
            if m:
                return types.SimpleNamespace(
                    exit_code=0, result=o.files.get(m.group(1), "")
                )
            return types.SimpleNamespace(exit_code=0, result="")

    class _FS:
        def __init__(self, outer):
            self.outer = outer

        def upload_file(self, content, dest):
            self.outer.files[dest] = content.decode("utf-8")

    def __init__(self, digest_answers=(), verifier_reward="1"):
        self.files, self.calls, self.digest_answers = {}, [], list(digest_answers)
        self.verifier_reward, self.tests_ran, self.digest_execs = (
            verifier_reward,
            False,
            0,
        )
        self.process, self.fs = self._Proc(self), self._FS(self)


def _grade_sdk(sb, baseline=_BASE, tmax=_TMAX):
    return G.grade_tmax_daytona(
        sb, tmax, workdir="/workspace", baseline_digests=baseline
    )


def test_raw_sdk_grader_holds_a_protected_row_to_the_same_rule():
    """grade_tmax_daytona (local_smoke's grader) must not grade a protected row as if
    unprotected: same seam (after the sentinel, before the verifier), same string through the
    SDK's exec as root, same judgement -- no baseline raises, a difference scores 0 without
    running the verifier, identical proceeds."""
    sb = _FakeSDK([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")])
    assert _grade_sdk(sb) == 1.0 and sb.tests_ran and sb.digest_execs == 1
    digest_call = [c for c in sb.calls if "sha256sum" in c[0]][0]
    assert digest_call[1] == 120  # the capped timeout
    inner = IB.build_digest_command(IB.protected_entries_of(_TMAX), "/workspace")
    assert digest_call[0] == G._root_sh(inner)  # the SAME builder, run as root
    sentinel_i = next(
        i
        for i, c in enumerate(sb.calls)
        if "tmax-sentinel-" in c[0] and "printf" in c[0]
    )
    digest_i = next(i for i, c in enumerate(sb.calls) if "sha256sum" in c[0])
    tests_i = next(i for i, c in enumerate(sb.calls) if "bash /tests/test.sh" in c[0])
    assert sentinel_i < digest_i < tests_i

    sb = _FakeSDK([(0, f"{_D2} 0\n{_D2} 1\n{_D3} 2\n", "")])  # /app/pinned changed
    with _captured_log() as lines:
        assert _grade_sdk(sb) == 0.0
    assert not sb.tests_ran
    assert any(
        "integrity baseline difference" in ln and "/app/pinned" in ln for ln in lines
    )
    assert not any(
        _D1 in ln or _D2 in ln for ln in lines
    )  # entries only, never digests

    sb = _FakeSDK([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")])
    try:
        _grade_sdk(sb, baseline=None)
    except IB.IntegrityHarnessError:
        pass
    else:
        raise AssertionError(
            "a protected row without a baseline must raise, never grade"
        )
    assert not sb.tests_ran and sb.digest_execs == 0

    for bad in (
        [(0, f"{_D1} 0\n{_D2} 1\n", "")],
        [(1, "", "boom")],
    ):  # harness failures void
        sb = _FakeSDK(bad)
        try:
            _grade_sdk(sb)
        except IB.IntegrityHarnessError:
            pass
        else:
            raise AssertionError("a malformed or failing digest run must raise")
        assert not sb.tests_ran


def test_raw_sdk_grader_leaves_unprotected_rows_untouched():
    tmax = {
        k: v for k, v in _TMAX.items() if k not in ("protected_paths", "protected_cmds")
    }
    sb = _FakeSDK([])
    assert _grade_sdk(sb, baseline=None, tmax=tmax) == 1.0 and sb.tests_ran
    assert sb.digest_execs == 0 and not any("sha256sum" in c[0] for c in sb.calls)


def test_capture_baseline_daytona_is_the_rollouter_seam_over_the_sdk():
    sb = _FakeSDK([(0, f"{_D1} 0\n{_D2} 1\n{_D3} 2\n", "")])
    got = G.capture_baseline_daytona(sb, _TMAX, workdir="/workspace", timeout_sec=900)
    assert got == _BASE
    assert sb.calls[-1][1] == 120  # capped
    assert (
        G.capture_baseline_daytona(_FakeSDK([]), {"test_sh": "x"}, workdir="/w") is None
    )


def _load_local_smoke():
    """local_smoke imports the Daytona SDK at module level; stand it in when absent."""
    if "daytona_api_client" not in sys.modules:
        try:
            import daytona_api_client  # noqa: F401
        except ImportError:
            cfg = types.ModuleType("daytona_api_client")

            class Configuration:
                def __init__(self, *a, **k):
                    pass

            cfg.Configuration = Configuration
            sys.modules["daytona_api_client"] = cfg
    if "daytona" not in sys.modules:
        try:
            import daytona  # noqa: F401
        except ImportError:
            d = types.ModuleType("daytona")
            d.CreateSandboxFromImageParams = lambda **k: k
            d.Daytona = object
            d.Resources = lambda **k: k
            sys.modules["daytona"] = d
    spec = importlib.util.spec_from_file_location(
        "local_smoke", _HERE / "local_smoke.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_smoke_captures_after_the_seeds_and_before_the_agent_and_grades_with_it():
    """The smoke holds the sandbox between seed_workspace_daytona and its scripted agent, so it
    captures there and grade_tmax_daytona receives that very object. Its grading loader also
    has to register integrity_baseline by file first: grading's import of it is real now."""
    ls = _load_local_smoke()
    grading = (
        ls._load_grading()
    )  # the real loader, in a process without the training stack
    assert callable(grading.grade_tmax_daytona) and callable(
        grading.capture_baseline_daytona
    )
    assert "torchtitan.experiments.rl.examples.tmax.integrity_baseline" in sys.modules

    events = []
    sentinel = {"/app/pinned": _D1}

    class FakeGrading:
        @staticmethod
        def seed_workspace_daytona(sb, tmax):
            events.append(("seed", tmax["task_id"]))

        @staticmethod
        def capture_baseline_daytona(sb, tmax, *, workdir):
            events.append(("capture", workdir))
            return sentinel

        @staticmethod
        def grade_tmax_daytona(sb, tmax, *, workdir, baseline_digests=None):
            events.append(("grade", workdir, baseline_digests))
            return 1.0

    sb = _FakeSDK([])
    sb.delete = lambda: events.append(("delete",))
    client = types.SimpleNamespace(create=lambda params: sb)
    sample = {
        "metadata": {
            "image": "img:1",
            "workdir": "/app",
            "tmax": _TMAX,
            "instance_id": "task_000001_aaaaaaaa",
            "problem_statement": "do it",
        }
    }
    assert ls._run_one(client, FakeGrading, sample) == 1.0
    agent_i = next(i for i, c in enumerate(sb.calls) if "cd /app" in c[0])
    assert events[:2] == [("seed", "task_000001_aaaaaaaa"), ("capture", "/app")]
    assert events[2] == ("grade", "/app", sentinel) and events[2][2] is sentinel
    assert events[-1] == ("delete",)
    # the agent acted after the capture: its exec is the only sandbox call, and the
    # capture (fake, no exec) was already recorded when it ran
    assert agent_i == 1 and len([c for c in sb.calls if "cd /app" in c[0]]) == 1


# ---------------------------------------------------------------- the arithmetic, in a real shell
# The fake-exec tests never execute the string. These do, in the same `bash` the sandbox launches, and hold
# the command digests to the hook exporter's arithmetic reproduced inline: env -i with the safe PATH, HOME
# /nonexistent, LC_ALL=C, bash -c "$cmd", stdin closed, stderr discarded, $( )-captured (trailing newlines
# stripped), printf '%s' | sha256sum. Skipped only if bash or sha256sum is missing, and says so.
import hashlib
import os
import shutil
import subprocess
import tempfile


def _real(entries, workdir="/workspace", env_extra=None):
    if not (shutil.which("bash") and shutil.which("sha256sum")):
        print("  (skipped: bash/sha256sum not available)")
        return None
    cmd = IB.build_digest_command(entries, workdir)
    r = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        env=dict(os.environ, **(env_extra or {})),
        timeout=60,
    )
    assert r.returncode == 0, r.stderr[:200]
    return IB.parse_digest_output(r.stdout, entries)


def _hook_digest(cmd: str) -> str:
    """The exporter's own arithmetic for a command entry, run independently of the builder."""
    r = subprocess.run(
        [
            "env",
            "-i",
            "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME=/nonexistent",
            "LC_ALL=C",
            "bash",
            "-c",
            cmd,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0
    return hashlib.sha256(r.stdout.rstrip(b"\n")).hexdigest()


def test_real_shell_stderr_is_discarded_and_trailing_newlines_are_stripped():
    got = _real(
        [
            ("cmd", 'echo "hello world"; echo warning >&2'),
            ("cmd", "printf 'a\\nb\\n\\n\\n'"),
        ]
    )
    if got is None:
        return
    assert (
        got['echo "hello world"; echo warning >&2']
        == hashlib.sha256(b"hello world").hexdigest()
    )  # stderr not in it
    assert (
        got["printf 'a\\nb\\n\\n\\n'"] == hashlib.sha256(b"a\nb").hexdigest()
    )  # $( ) strips \n\n\n
    for e in got:
        assert got[e] == _hook_digest(e), e  # equals the exporter's arithmetic


def test_real_shell_both_quotes_run_unchanged_and_a_failing_command_is_FAIL():
    entry = """printf '%s' "it's" '"q"' """
    got = _real([("cmd", entry), ("cmd", "false"), ("cmd", "exit 3")])
    if got is None:
        return
    assert (
        got[entry] == hashlib.sha256(b"""it's"q\"""").hexdigest() == _hook_digest(entry)
    )
    assert got["false"] == "FAIL" and got["exit 3"] == "FAIL"


def test_real_shell_environment_is_part_of_the_value_and_the_caller_env_is_not():
    """`env -i` resets everything, so the digest is the same under two different caller environments, and
    the command sees the wrapper's HOME/LC_ALL rather than the caller's."""
    entry = 'echo "$HOME:$LC_ALL:$FOO:$PATH"'
    a = _real(
        [("cmd", entry)],
        env_extra={"FOO": "one", "LC_ALL": "C.UTF-8", "HOME": "/tmp/x"},
    )
    b = _real(
        [("cmd", entry)], env_extra={"FOO": "two", "LC_ALL": "POSIX", "HOME": "/tmp/y"}
    )
    if a is None:
        return
    assert a == b
    assert (
        a[entry]
        == hashlib.sha256(b"/nonexistent:C::/usr/bin:/bin:/usr/sbin:/sbin").hexdigest()
    )


def test_real_shell_paths_with_spaces_directories_absent_and_other():
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "data dir").mkdir()
    (d / "data dir" / "model.bin").write_bytes(b"\x00\x01weights")
    w = d / "work"
    (w / "tests").mkdir(parents=True)
    (w / "tests" / "b.txt").write_text("b")
    (w / "tests" / "a.txt").write_text("a")
    os.mkfifo(d / "pipe")
    entries = [
        ("path", str(d / "data dir" / "model.bin")),
        ("path", "tests"),
        ("path", "gone"),
        ("path", str(d / "pipe")),
    ]
    got = _real(entries, workdir=str(w))
    if got is None:
        return
    assert got[entries[0][1]] == hashlib.sha256(b"\x00\x01weights").hexdigest()
    assert (
        len(got["tests"]) == 64
        and got["gone"] == "ABSENT"
        and got[str(d / "pipe")] == "OTHER"
    )
    (w / "tests" / "a.txt").write_text("A")
    assert IB.differences(got, _real(entries, workdir=str(w))) == ["tests"]


if __name__ == "__main__":
    _tests = [f for n, f in list(globals().items()) if n.startswith("test_")]
    _declared = len(re.findall(r"^def test_", pathlib.Path(__file__).read_text(), re.M))
    assert (
        len(_tests) == _declared
    ), f"{_declared} declared, {len(_tests)} visible to the runner"
    for f in _tests:
        f()
    print(f"ok {len(_tests)}")
