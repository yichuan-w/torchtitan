"""The integrity baseline reaches the breeding loop. A package's protected lists -- off the
reaudit parquet, off the mix row a rewrite descends from, or off the package's own
tests/protected_paths.json -- land on the row through the one shared helper, and the loop's
two graders, the sandbox tool and the Daytona revalidator, take a baseline at their seams and
hand it to grade_tmax by keyword. The baseline itself is integrity_baseline's and is tested
there; here the fakes record WHERE it is taken and WHAT grading receives."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_sandbox as asb
import build_mix_v2 as bm
import pack_to_dataset as pack

IMAGE = "hamishi740/swerl-tmax-v3:37a79d0fd9b9"
STAMP = "image:" + IMAGE
HOOK = "set -u\nexit 0\n"
PATHS = ["/app/pinned", "/app/data dir/model.bin", "tests"]
CMDS = ["sqlite3 /app/db \"select count(*) from t where n='x'\""]
FILE = "tests/protected_paths.json"


@pytest.fixture(autouse=True)
def _checkout(monkeypatch):
    # pack.to_row delegates to the checkout's own adapters; this file sits inside it.
    monkeypatch.setenv("TRL_TT", str(Path(__file__).resolve().parents[7]))


def _package(root: Path, name: str = "pkg", protected_file=None) -> Path:
    pkg = root / name
    (pkg / "environment").mkdir(parents=True)
    (pkg / "tests").mkdir()
    (pkg / "solution").mkdir()
    (pkg / "environment/Dockerfile").write_text(f"FROM {IMAGE}\n# setup.sh, inlined\n")
    (pkg / "instruction.md").write_text("Do the thing in /home/user.\n")
    (pkg / "tests/test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n")
    (pkg / "solution/solve.sh").write_text("#!/bin/bash\ntouch /home/user/done\n")
    if protected_file is not None:
        text = (
            protected_file
            if isinstance(protected_file, str)
            else json.dumps(protected_file)
        )
        (pkg / FILE).write_text(text)
    return pkg


def _tmax(row: dict) -> dict:
    return row["metadata"]["tmax"]


# ---------------------------------------------------------------- pack.to_row
def test_to_row_carries_the_lists_the_caller_passes_and_nothing_otherwise(
    tmp_path,
) -> None:
    pkg = _package(tmp_path)
    plain = pack.to_row(str(pkg), task_id="task_1")
    row = pack.to_row(str(pkg), task_id="task_1", protected=pack.Protected(PATHS, CMDS))
    tm = _tmax(row)
    assert tm["protected_paths"] == PATHS  # a LIST, the space-containing path intact
    assert tm["protected_cmds"] == CMDS  # the quotes verbatim
    assert list(tm)[-2:] == [
        "protected_paths",
        "protected_cmds",
    ]  # after the adapter's keys
    tm.pop("protected_paths"), tm.pop("protected_cmds")
    assert row == plain  # nothing else on the row moved
    for none in (None, pack.Protected([], []), pack.Protected(None or [], [])):
        got = _tmax(pack.to_row(str(pkg), task_id="task_1", protected=none))
        assert not {"protected_paths", "protected_cmds"} & set(got)  # absent, never []
    only_cmds = _tmax(
        pack.to_row(str(pkg), task_id="task_1", protected=pack.Protected([], CMDS))
    )
    assert "protected_paths" not in only_cmds and only_cmds["protected_cmds"] == CMDS


def test_the_packages_own_file_overrides_the_callers_lists(tmp_path) -> None:
    caller = pack.Protected(PATHS, CMDS)
    pkg = _package(tmp_path, "a", {"paths": ["/x y"], "cmds": []})
    tm = _tmax(pack.to_row(str(pkg), task_id="task_1", protected=caller))
    assert tm["protected_paths"] == ["/x y"] and "protected_cmds" not in tm
    # Either key may be missing from the file; two empty lists CLEAR the caller's.
    tm = _tmax(
        pack.to_row(str(_package(tmp_path, "b", {"cmds": CMDS})), protected=caller)
    )
    assert "protected_paths" not in tm and tm["protected_cmds"] == CMDS
    tm = _tmax(pack.to_row(str(_package(tmp_path, "c", {})), protected=caller))
    assert not {"protected_paths", "protected_cmds"} & set(tm)
    # Read on the host at pack time. The file also rides along as a grading fixture
    # (prepare_rts_data collects tests/*, untouched here) and so reaches the sandbox only
    # when the verifier does -- after the episode's last action.
    assert pack.package_protected(str(pkg)) == pack.Protected(["/x y"], [])
    assert pack.package_protected(str(_package(tmp_path, "d"))) is None
    assert any(
        k.endswith("protected_paths.json")
        for k in _tmax(pack.to_row(str(pkg)))["fixtures"]
    )


def test_malformed_lists_refuse_by_id(tmp_path) -> None:
    cases = [
        ("not json", "not JSON"),
        ({"paths": "x"}, "paths must be a list"),
        ({"paths": ["ok", ""]}, "paths must be a list of non-empty"),
        ({"cmds": [1]}, "cmds must be a list"),
        ({"extra": []}, 'only "paths" and "cmds"'),
        ({"cmds": ["echo 1\necho 2"]}, "newline"),  # integrity_baseline's rule
    ]
    for i, (text, needle) in enumerate(cases):
        pkg = _package(tmp_path, f"p{i}", text)
        with pytest.raises(ValueError) as e:
            pack.to_row(str(pkg), task_id="task_000001_aaaaaaaa")
        assert "task_000001_aaaaaaaa" in str(e.value) and needle in str(e.value), str(
            e.value
        )
    pkg = _package(tmp_path, "caller")
    for bad in (
        pack.Protected(["ok", ""], []),
        pack.Protected([], ["a\nb"]),
        pack.Protected("s", []),
    ):
        with pytest.raises(ValueError, match="caller: protected lists"):
            pack.to_row(str(pkg), protected=bad)


def test_Protected_reads_cells_by_the_prep_scripts_rules_and_rows_by_their_keys() -> None:
    assert pack.Protected.from_cells(None, None) is None
    assert pack.Protected.from_cells("", "  ") is None
    assert pack.Protected.from_cells("[]", "[]") is None  # empty lists are nothing
    assert pack.Protected.from_cells(json.dumps(PATHS), None) == pack.Protected(
        PATHS, []
    )
    assert pack.Protected.from_cells(None, json.dumps(CMDS)) == pack.Protected([], CMDS)
    for cell, needle in (
        ("nope", "not JSON"),
        ('["", "a"]', "non-empty"),
        ('"x"', "JSON list"),
    ):
        with pytest.raises(ValueError, match=needle):
            pack.Protected.from_cells(cell, None)
    with pytest.raises(ValueError, match="protected_cmds"):
        pack.Protected.from_cells(None, "{}")
    assert pack.Protected.from_tmax(
        {"protected_paths": PATHS, "test_sh": "x"}
    ) == pack.Protected(PATHS, [])
    assert pack.Protected.from_tmax({"test_sh": "x"}) is None


# ---------------------------------------------------------------- build_mix_v2
def _mix_inputs(tmp_path: Path, columns: bool, bad_cell: str | None = None):
    tasks = tmp_path / "tasks"
    for tid in ("task_a", "task_b", "task_c"):
        _package(tasks, tid)
    table = {
        "task_id": ["task_a", "task_b", "task_c"],
        "terminal_domain": ["data-science", "security", "debugging"],
        "pre_test_sh": [HOOK, "", ""],
        "pre_test_env_identity": [STAMP, "", ""],
    }
    if columns:
        table["protected_paths"] = [json.dumps(PATHS), "", bad_cell or ""]
        table["protected_cmds"] = [json.dumps(CMDS), "", ""]
    reaudit = tmp_path / "reaudit.parquet"
    pq.write_table(pa.table(table), reaudit)
    return tasks, reaudit


def test_build_mix_v2_reads_the_columns_and_tolerates_their_absence(tmp_path) -> None:
    tasks, reaudit = _mix_inputs(tmp_path, columns=True)
    rows, missing = bm.tmax_rows(tasks, reaudit, None)
    assert missing == []
    by = {r["metadata"]["instance_id"]: _tmax(r) for r in rows}
    assert (
        by["task_a"]["protected_paths"] == PATHS
        and by["task_a"]["protected_cmds"] == CMDS
    )
    assert by["task_a"]["pre_test_sh"] == HOOK  # the hook still rides beside the lists
    for tid in ("task_b", "task_c"):
        assert not {"protected_paths", "protected_cmds"} & set(by[tid])

    tasks, older = _mix_inputs(
        tmp_path / "older", columns=False
    )  # a parquet from before the columns
    rows, missing = bm.tmax_rows(tasks, older, None)
    assert missing == [] and len(rows) == 3
    assert not any({"protected_paths", "protected_cmds"} & set(_tmax(r)) for r in rows)


def test_build_mix_v2_records_a_malformed_cell_and_keeps_the_row_out(tmp_path) -> None:
    tasks, reaudit = _mix_inputs(tmp_path, columns=True, bad_cell='["/ok", ""]')
    rows, missing = bm.tmax_rows(tasks, reaudit, None)
    assert missing == [
        "task_c (protected: protected_paths must be a JSON list of non-empty strings)"
    ]
    assert sorted(r["metadata"]["instance_id"] for r in rows) == ["task_a", "task_b"]


# ---------------------------------------------------------------- the sandbox tool's seams
class _FakeSandbox:
    sandbox_id = "sb-test"

    def __init__(self, events):
        self.events = events

    async def exec(self, cmd, *, check=False, timeout=None, user=None, **_kw):
        self.events.append(("exec", cmd))
        return 0, "", ""

    async def write_file(self, dest, _content, **_kw):
        self.events.append(("write_file", dest))

    async def read_file(self, path, **_kw):
        self.events.append(("read_file", path))
        return ""


def _fake_revalidator(events: list) -> types.ModuleType:
    """daytona_revalidate as agent_sandbox._serve imports it, without Daytona: the boot yields a
    fake sandbox; capture_baseline and grade_tmax record their calls and return distinct objects
    so the test can tell WHICH baseline reached grading."""
    ib = pack._ib_module()
    dr = types.ModuleType("daytona_revalidate")
    dr.pack = pack

    @asynccontextmanager
    async def boot_agent_sandbox(_image, **_kw):
        yield _FakeSandbox(events)

    class Root:
        def __init__(self, inner):
            self._inner = inner

        async def exec(self, cmd, **kw):
            return await self._inner.exec(cmd, **kw)

        async def write_file(self, dest, content, **kw):
            return await self._inner.write_file(dest, content, **kw)

    async def _start_entrypoint(_sb, command, *, workdir):
        events.append(("entrypoint", command, workdir))

    async def seed_workspace(_sb, _tmax):
        events.append(("seed_workspace",))

    async def measure(_sb, _secs, tail=""):
        return {}

    async def capture_baseline(_sb, tmax, *, workdir, timeout):
        entries = ib.protected_entries_of(tmax)
        got = {e: "d" * 64 for _, e in entries} or None
        events.append(("capture_baseline", len(entries), workdir, timeout, got))
        return got

    async def grade_tmax(_sb, _tmax, *, workdir, baseline_digests=None, **_kw):
        events.append(("grade_tmax", workdir, baseline_digests))
        return 1.0

    dr.boot_agent_sandbox = boot_agent_sandbox
    dr._Root = Root
    dr._start_entrypoint = _start_entrypoint
    dr.seed_workspace = seed_workspace
    dr.measure = measure
    dr.capture_baseline = capture_baseline
    dr.grade_tmax = grade_tmax
    dr._harness_provenance = lambda: "test"
    dr.protected_paths_of = ib.protected_paths_of
    dr.protected_cmds_of = ib.protected_cmds_of
    dr.protected_entries_of = ib.protected_entries_of
    return dr


class _Server:
    """agent_sandbox._serve in a thread, against the fake revalidator."""

    def __init__(self, pkg: Path, monkeypatch, events: list):
        monkeypatch.setitem(
            sys.modules, "daytona_revalidate", _fake_revalidator(events)
        )
        self.pkg = pkg
        self.sock = tempfile.mktemp(prefix="asb-protected-", suffix=".sock", dir="/tmp")
        self.rc = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while asb._read_state(pkg).get("status") != "ready":
            assert time.time() < deadline, asb._read_state(pkg)
            assert self.thread.is_alive(), f"server died: rc={self.rc}"
            time.sleep(0.05)
        self.state = asb._read_state(pkg)

    def _run(self):
        self.rc = asyncio.run(asb._serve(self.pkg, self.sock))

    def request(self, op, **fields):
        return asb._request(self.state, op, wait=30, **fields)

    def down(self):
        assert self.request("down")["ok"]
        self.thread.join(20)
        assert self.rc == 0


def test_sandbox_tool_takes_the_baseline_at_up_for_grade_and_before_solve_for_oracle(
    tmp_path, monkeypatch, capsys
) -> None:
    pkg = _package(tmp_path, "pkg", {"paths": PATHS, "cmds": CMDS})
    (pkg / "run").mkdir()
    workdir = pack.to_row(str(pkg))["metadata"]["workdir"]
    events: list = []
    server = _Server(pkg, monkeypatch, events)
    try:
        # up: seeded, then digested, before any request is served
        assert [e[0] for e in events] == ["seed_workspace", "capture_baseline"]
        boot = events[1]
        assert boot[1:4] == (len(PATHS) + len(CMDS), workdir, asb.EXEC_TIMEOUT)
        assert "integrity baseline: 3 paths, 1 cmds" in capsys.readouterr().err

        # grade: the digests taken at up
        del events[:]
        assert server.request("grade") == {"ok": True, "reward": 1.0}
        assert events == [("grade_tmax", workdir, boot[4])]
        assert events[0][2] is boot[4]

        # oracle: solution/ in, THEN a fresh baseline with the package's current lists, THEN solve.sh
        del events[:]
        r = server.request("oracle", solve_timeout=5)
        assert r["ok"] and r["reward"] == 1.0
        kinds = [e[0] for e in events]
        assert kinds == ["write_file", "capture_baseline", "exec", "grade_tmax"], kinds
        assert (
            events[0][1] == "/solution/solve.sh"
            and events[2][1] == "bash /solution/solve.sh"
        )
        fresh = events[1]
        assert fresh[1:4] == (4, workdir, asb.EXEC_TIMEOUT) and fresh[4] is not boot[4]
        assert (
            events[3] == ("grade_tmax", workdir, fresh[4]) and events[3][2] is fresh[4]
        )

        # an edited list needs a reset, like an edited Dockerfile
        (pkg / FILE).write_text(json.dumps({"paths": PATHS[:1], "cmds": []}))
        del events[:]
        r = server.request("grade")
        assert (
            not r["ok"] and "changed since up" in r["error"] and "reset" in r["error"]
        )
        assert events == []  # nothing was graded
        # ...while oracle keeps working: it digests the lists as they are now
        r = server.request("oracle", solve_timeout=5)
        assert r["ok"] and [e for e in events if e[0] == "capture_baseline"][0][1] == 1
    finally:
        server.down()


def test_sandbox_tool_inherits_the_lists_from_run_pretest_json(
    tmp_path, monkeypatch, capsys
) -> None:
    """A package with NO tests/protected_paths.json (every shipped package) still grades with
    the lists its row carries: the harness wrote them into run/pretest.json beside the hook,
    and boot, grade and oracle read them from there."""
    pkg = _package(tmp_path)
    (pkg / "run").mkdir()
    (pkg / "run" / "pretest.json").write_text(
        json.dumps(
            {
                "pre_test_sh": "",
                "pretest_env_identity": "",
                "protected_paths": PATHS,
                "protected_cmds": CMDS,
            }
        )
    )
    events: list = []
    server = _Server(pkg, monkeypatch, events)
    try:
        assert "integrity baseline: 3 paths, 1 cmds" in capsys.readouterr().err
        assert [e[0] for e in events] == ["seed_workspace", "capture_baseline"]
        assert events[1][1] == 4  # the inherited entries were digested at up
        del events[:]
        assert server.request("grade") == {"ok": True, "reward": 1.0}
        assert events[0][2] is not None and len(events[0][2]) == 4
        del events[:]
        assert server.request("oracle", solve_timeout=5)["ok"]
        assert [e for e in events if e[0] == "capture_baseline"][0][1] == 4
        # the package's own file, once written, overrides the inherited lists (grade then refuses
        # until reset, as for any list change; oracle uses the file)
        (pkg / FILE).write_text(json.dumps({"paths": ["/only"], "cmds": []}))
        assert not server.request("grade")["ok"]
        del events[:]
        assert server.request("oracle", solve_timeout=5)["ok"]
        assert [e for e in events if e[0] == "capture_baseline"][0][1] == 1
    finally:
        server.down()


def test_sandbox_tool_without_lists_is_unchanged(tmp_path, monkeypatch, capsys) -> None:
    pkg = _package(tmp_path)
    (pkg / "run").mkdir()
    (pkg / "run" / "pretest.json").write_text(
        json.dumps({"pre_test_sh": HOOK, "pretest_env_identity": STAMP})
    )
    events: list = []
    server = _Server(pkg, monkeypatch, events)
    try:
        err = capsys.readouterr().err
        assert "pin hook: stamped=image:" in err and "integrity baseline" not in err
        workdir = pack.to_row(str(pkg))["metadata"]["workdir"]
        assert events == [
            ("seed_workspace",),
            ("capture_baseline", 0, workdir, asb.EXEC_TIMEOUT, None),
        ]
        del events[:]
        assert server.request("grade") == {"ok": True, "reward": 1.0}
        assert events[0][0] == "grade_tmax" and events[0][2] is None
        del events[:]
        assert server.request("oracle", solve_timeout=5)["ok"]
        assert [e[0] for e in events] == [
            "write_file",
            "capture_baseline",
            "exec",
            "grade_tmax",
        ]
        assert events[1][4] is None and events[3][2] is None
    finally:
        server.down()


# ---------------------------------------------------------------- the revalidator's seam
def _probe(pkg: Path, monkeypatch, events: list, pretest=None) -> dict:
    import daytona_revalidate as dr

    fake = _fake_revalidator(events)
    for name in (
        "boot_agent_sandbox",
        "_start_entrypoint",
        "seed_workspace",
        "measure",
        "capture_baseline",
        "grade_tmax",
    ):
        monkeypatch.setattr(dr, name, getattr(fake, name))
    return asyncio.run(dr.probe(pkg, None, 5, pretest=pretest))


def test_revalidator_takes_the_baseline_after_the_solution_is_in_and_before_it_runs(
    tmp_path, monkeypatch, capsys
) -> None:
    pkg = _package(tmp_path, "pkg", {"paths": PATHS, "cmds": CMDS})
    workdir = pack.to_row(str(pkg))["metadata"]["workdir"]
    events: list = []
    verdict = _probe(pkg, monkeypatch, events, pretest=(HOOK, STAMP))
    assert (
        verdict["ok"]
        and verdict["stage"] == "daytona_oracle"
        and verdict["reward"] == 1.0
    )
    kinds = [e[0] for e in events]
    assert kinds == [
        "seed_workspace",
        "write_file",
        "capture_baseline",
        "exec",
        "grade_tmax",
    ], kinds
    assert (
        events[1][1] == "/solution/solve.sh"
        and events[3][1] == "bash /solution/solve.sh"
    )
    assert events[2][1:4] == (4, workdir, 120)
    assert (
        events[4] == ("grade_tmax", workdir, events[2][4])
        and events[4][2] is events[2][4]
    )
    # log-only: the diagnostic dict says which mode graded, and the pin hook is not consulted
    assert verdict["pretest"] == {"mode": "baseline", "paths": 3, "cmds": 1}
    err = capsys.readouterr().err
    assert "integrity baseline: 3 paths, 1 cmds" in err and "pin hook" not in err


def test_revalidator_without_lists_keeps_the_stamp_hook_and_passes_no_baseline(
    tmp_path, monkeypatch, capsys
) -> None:
    pkg = _package(tmp_path)
    workdir = pack.to_row(str(pkg))["metadata"]["workdir"]
    events: list = []
    verdict = _probe(pkg, monkeypatch, events, pretest=(HOOK, STAMP))
    assert verdict["ok"]
    assert verdict["pretest"] == {
        "mode": "stamp",
        "stamped": STAMP,
        "episode": STAMP,
        "runs": True,
    }
    assert "pin hook: stamped=" in capsys.readouterr().err
    caps = [e for e in events if e[0] == "capture_baseline"]
    assert len(caps) == 1 and caps[0][1] == 0 and caps[0][4] is None
    assert [e for e in events if e[0] == "grade_tmax"] == [
        ("grade_tmax", workdir, None)
    ]
    events.clear()
    assert (
        _probe(pkg, monkeypatch, events)["pretest"] is None
    )  # no hook, no lists: as before


# ---------------------------------------------------------------- the other graders in the loop
# Every other grade_tmax call site under evolution/: each holds a root sandbox after its seed
# step and before whatever acts on it, so each takes the baseline there and hands it to
# grading. The fakes below replace the module's OWN names (boot, seed, grader, capture); the
# module's real code runs in between.
def _wire(
    monkeypatch,
    module,
    events: list,
    names=("boot_agent_sandbox", "seed_workspace", "grade_tmax", "capture_baseline"),
):
    fake = _fake_revalidator(events)
    for name in names:
        monkeypatch.setattr(module, name, getattr(fake, name))


def _captured_then_graded(
    events: list, *, before: str, after: str, n_entries: int
) -> None:
    """One capture, strictly between the ``before`` event and the ``after`` event, and the
    object it returned is the one grade_tmax received."""
    kinds = [e[0] for e in events]
    assert (
        kinds.count("capture_baseline") == 1 and kinds.count("grade_tmax") == 1
    ), kinds
    cap = kinds.index("capture_baseline")
    assert (
        kinds.index(before) < cap < kinds.index(after) < kinds.index("grade_tmax")
    ), kinds
    assert events[cap][1] == n_entries and events[cap][3] == 120
    graded = events[kinds.index("grade_tmax")]
    assert graded[2] is events[cap][4]
    if n_entries == 0:
        assert graded[2] is None


def test_diag_codex_probe_takes_the_baseline_before_the_run(
    tmp_path, monkeypatch
) -> None:
    import daytona_revalidate_diag_codex as dg

    for name, lists in (("a", {"paths": PATHS, "cmds": CMDS}), ("b", None)):
        pkg = _package(tmp_path, name, lists)
        events: list = []
        _wire(monkeypatch, dg, events)
        verdict = asyncio.run(dg.probe(pkg, None, 5))
        assert verdict["ok"] and verdict["reward"] == 1.0
        _captured_then_graded(
            events, before="write_file", after="exec", n_entries=4 if lists else 0
        )
        assert events[[e[0] for e in events].index("exec")][1].endswith(
            "bash /solution/solve.sh"
        )


def test_oom_probe_takes_the_baseline_before_the_run(tmp_path, monkeypatch) -> None:
    import probe_oom_suspects as pom

    for name, lists in (("a", {"paths": PATHS, "cmds": CMDS}), ("b", None)):
        pkg = _package(tmp_path, name, lists)
        events: list = []
        monkeypatch.setattr(pom.sd, "resolve_src", lambda _tid, _p=pkg: _p)
        _wire(monkeypatch, pom, events)
        rec = asyncio.run(pom.probe("task_x", 1, 2, 2, 5))
        assert rec["reward"] == 1.0, rec
        _captured_then_graded(
            events, before="write_file", after="exec", n_entries=4 if lists else 0
        )
        execs = [e[1] for e in events if e[0] == "exec"]
        assert (
            execs[0].endswith("bash /solution/solve.sh") and execs[1] == pom.READ
        )  # counters read before grading


def test_solver_chat_agent_takes_the_baseline_before_its_first_command(
    tmp_path, monkeypatch
) -> None:
    import solve_daytona as sd

    for name, lists in (("a", {"paths": PATHS, "cmds": CMDS}), ("b", None)):
        row = pack.to_row(str(_package(tmp_path, name, lists)))
        events: list = []
        _wire(monkeypatch, sd, events)
        turns = iter(["echo hi", "DONE"])
        monkeypatch.setattr(sd.llm, "agent_step", lambda _i, _h: next(turns))
        got = asyncio.run(sd.attempt(row, 0, 3, agent="chat"))
        assert (got["reward"], got["turns"]) == (1.0, 1), got
        _captured_then_graded(
            events, before="seed_workspace", after="exec", n_entries=4 if lists else 0
        )
        assert [e[1] for e in events if e[0] == "exec"] == ["echo hi"]


def test_solver_codex_agent_takes_the_baseline_before_codex_runs(
    tmp_path, monkeypatch
) -> None:
    import solve_daytona as sd

    for name, lists in (("a", {"paths": PATHS, "cmds": CMDS}), ("b", None)):
        row = pack.to_row(str(_package(tmp_path, name, lists)))
        events: list = []
        _wire(monkeypatch, sd, events)

        async def codex(_sb, _md, _workdir, *, budget):
            events.append(("codex", budget))
            return {"reward": "pending_grade", "turns": None, "codex_exit": 0}

        monkeypatch.setattr(sd, "_codex_attempt", codex)
        got = asyncio.run(sd.attempt(row, 0, 2, agent="codex"))
        assert got["reward"] == 1.0 and got["codex_exit"] == 0, got
        _captured_then_graded(
            events, before="seed_workspace", after="codex", n_entries=4 if lists else 0
        )


def test_provisioning_verifier_takes_the_baseline_before_the_run(
    tmp_path, monkeypatch
) -> None:
    import verify_provisioning as vp

    for name, lists in (("a", {"paths": PATHS, "cmds": CMDS}), ("b", None)):
        pkg = _package(tmp_path, name, lists)
        events: list = []
        monkeypatch.setattr(vp.sd, "resolve_src", lambda _tid, _p=pkg: _p)
        _wire(monkeypatch, vp, events)
        rec = asyncio.run(vp.verify("task_x", 1, 2, 2, asyncio.Semaphore(1), 5))
        assert rec["reward"] == 1.0, rec
        _captured_then_graded(
            events, before="write_file", after="exec", n_entries=4 if lists else 0
        )
        execs = [e[1] for e in events if e[0] == "exec"]
        assert execs[0] == "bash /solution/solve.sh" and execs[1] == vp.CGROUP_READ
