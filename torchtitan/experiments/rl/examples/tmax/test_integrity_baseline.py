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
    'unchanged' means."""
    cmd = IB.build_digest_command(["/app/data dir/model.bin", "tests"], "/workspace")
    expected = (
        "if [ -f '/app/data dir/model.bin' ]; then printf '%s 0\\n' \"$(sha256sum -- '/app/data dir/model.bin' | cut -d' ' -f1)\"; "
        "elif [ -d '/app/data dir/model.bin' ]; then printf '%s 0\\n' \"$(cd -- '/app/data dir/model.bin' && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1)\"; "
        "elif [ -e '/app/data dir/model.bin' ]; then printf 'OTHER 0\\n'; else printf 'ABSENT 0\\n'; fi; "
        "if [ -f /workspace/tests ]; then printf '%s 1\\n' \"$(sha256sum -- /workspace/tests | cut -d' ' -f1)\"; "
        "elif [ -d /workspace/tests ]; then printf '%s 1\\n' \"$(cd -- /workspace/tests && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1)\"; "
        "elif [ -e /workspace/tests ]; then printf 'OTHER 1\\n'; else printf 'ABSENT 1\\n'; fi"
    )
    assert cmd == expected, cmd
    assert (
        IB.resolve_path("../etc/passwd", "/workspace") == "/etc/passwd"
    )  # normalised, never doubled
    assert IB.resolve_path("/abs", "/workspace") == "/abs"
    q = IB.build_digest_command(["it's"], "/w")
    assert "'/w/it'\"'\"'s'" in q  # shlex quoting of a quote


def test_parse_maps_by_index_and_refuses_anything_incomplete():
    paths = ["/a", "dir", "gone"]
    out = f"{_D1} 0\n{_D2} 1\nABSENT 2\n"
    assert IB.parse_digest_output(out, paths) == {
        "/a": _D1,
        "dir": _D2,
        "gone": "ABSENT",
    }
    assert (
        IB.parse_digest_output(f"ABSENT 2\n{_D2} 1\n\n{_D1} 0\n", paths)["/a"] == _D1
    )  # order-free
    for bad, why in (
        (f"{_D1} 0\n{_D2} 1\n", "missing line"),
        (f"{_D1} 0\n{_D2} 1\nABSENT 2\n{_D3} 3\n", "unexpected index"),
        (f"{_D1} 0\n{_D1} 0\nABSENT 2\n", "repeated index"),
        (f"{_D1} 0\n 1\nABSENT 2\n", "empty digest (a failed subshell)"),
        (f"{_D1} 0\n{_D2} 1\n{IB.TRUNCATION_MARKER}\nABSENT 2\n", "truncation marker"),
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
        try:
            IB.protected_paths_of({"protected_paths": bad})
        except IB.IntegrityHarnessError:
            pass
        else:
            raise AssertionError(f"must refuse {bad!r}")


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
    assert cmd == IB.build_digest_command(["/a", "b"], "/w")
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
    cap = src.index("baseline_digests = await capture_integrity_baseline(")
    tmux = src.index('"command -v tmux >/dev/null 2>&1"')
    grade = src.index("reward = await grade_tmax(")
    passed = src.index("baseline_digests=baseline_digests,")
    assert src.index("submitted = False") < init < seed < cap < tmux < grade < passed
    assert "root_sb.exec," in src[cap : cap + 200]  # the root wrapper's exec
    assert not re.search(r"sandbox\.baseline|sample\.baseline|setattr\(.*baseline", src)


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
}
_BASE = {"/app/pinned": _D1, "tests": _D2}


def _grade(sb, tmax=_TMAX, baseline=_BASE):
    return _run(
        G.grade_tmax(
            sb, tmax, workdir="/workspace", timeout_sec=900, baseline_digests=baseline
        )
    )


def test_intact_paths_run_the_verifier_and_the_check_ran_as_root():
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n", "")])
    assert _grade(sb) == 1.0 and sb.tests_ran
    digest_calls = [c for c in sb.calls if "sha256sum" in c[0]]
    assert (
        len(digest_calls) == 1
        and digest_calls[0][1] == "root"
        and digest_calls[0][2] == 120
    )
    assert digest_calls[0][0] == IB.build_digest_command(
        ["/app/pinned", "tests"], "/workspace"
    )  # same builder


def test_one_changed_file_scores_zero_without_running_the_verifier():
    import logging

    records = []
    h = logging.Handler()
    h.emit = records.append
    G.logger.addHandler(h)
    G.logger.setLevel(logging.INFO)
    try:
        sb = _FakeSandbox([(0, f"{_D3} 0\n{_D2} 1\n", "")])  # /app/pinned changed
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
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n", "")])
    assert (
        _grade(sb, baseline={"/app/pinned": "ABSENT", "tests": _D2}) == 0.0
        and not sb.tests_ran
    )


def test_missing_baseline_raises_and_harness_failures_void_the_episode():
    for sb, baseline, why in (
        (
            _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n", "")]),
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
    tmax = {k: v for k, v in _TMAX.items() if k != "protected_paths"}
    assert _grade(sb, tmax=tmax, baseline=None) == 1.0 and sb.tests_ran
    assert not any("sha256sum" in c[0] for c in sb.calls)
    assert (
        _grade(_FakeSandbox([]), tmax=tmax, baseline=_BASE) == 1.0
    )  # a stray baseline is inert
    # and a protected row never consults pre_test_sh: a check that would refuse is not even run
    sb = _FakeSandbox([(0, f"{_D1} 0\n{_D2} 1\n", "")])
    tmax = dict(
        _TMAX,
        pre_test_sh="exit 1",
        pretest_env_identity="image:x",
        pretest_episode_env_identity="image:x",
    )
    assert _grade(sb, tmax=tmax) == 1.0 and not any("exit 1" in c[0] for c in sb.calls)


if __name__ == "__main__":
    _tests = [f for n, f in list(globals().items()) if n.startswith("test_")]
    _declared = len(re.findall(r"^def test_", pathlib.Path(__file__).read_text(), re.M))
    assert (
        len(_tests) == _declared
    ), f"{_declared} declared, {len(_tests)} visible to the runner"
    for f in _tests:
        f()
    print(f"ok {len(_tests)}")
