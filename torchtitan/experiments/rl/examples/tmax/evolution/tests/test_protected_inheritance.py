"""A variant INHERITS its protected lists. Shipped packages carry no tests/protected_paths.json,
so the lists a variant is validated and folded with come from the mix row it descends from --
and they must be the same lists at both ends, or a reward-1 variant folds into a reward-0 row.
The loop snapshots them beside the rewrite (pretest.json, next to the hook), the probe and the
agent's sandbox tool read them from there, the fold reads the parent row, and every build goes
through pack.effective_protected: the package's own file overrides, else the inherited lists."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import evolve_codex as ec
import evolve_ondella as od
import pack_to_dataset as pack
import test_evolve_lineage as tl
from torchtitan.experiments.rl.examples.tmax import layout

PATHS = ["/app/pinned", "/app/data dir/model.bin", "tests"]
CMDS = ["sqlite3 /app/db \"select count(*) from t where n='x'\""]
OWN = {"paths": ["/x y"], "cmds": []}


@pytest.fixture(autouse=True)
def _checkout(monkeypatch):
    monkeypatch.setenv("TRL_TT", str(Path(__file__).resolve().parents[7]))


# ---------------------------------------------------------------- the snapshot
def test_snapshot_carries_the_lists_beside_the_hook_without_widening_the_tuple(
    tmp_path,
) -> None:
    p = tmp_path / "pretest.json"
    layout.write_pretest(
        p, "set -u\nexit 0\n", "image:x", protected_paths=PATHS, protected_cmds=CMDS
    )
    assert layout.read_pretest(p) == (
        "set -u\nexit 0\n",
        "image:x",
    )  # the tuple, unchanged
    assert layout.read_protected_lists(p) == {
        "protected_paths": PATHS,
        "protected_cmds": CMDS,
    }
    assert pack.Protected.from_pretest_file(p) == pack.Protected(PATHS, CMDS)
    # lists without a hook: an empty script, so the hook readers see None and the lists survive
    layout.write_pretest(p, "", "", protected_paths=PATHS)
    assert layout.read_pretest(p) is None
    assert layout.read_protected_lists(p) == {"protected_paths": PATHS}
    assert pack.Protected.from_pretest_file(p) == pack.Protected(PATHS, [])
    # a hook without lists writes no list keys; readers say None
    layout.write_pretest(p, "x\n", "image:x")
    assert set(json.loads(p.read_text())) == {"pre_test_sh", "pretest_env_identity"}
    assert (
        layout.read_protected_lists(p) is None
        and pack.Protected.from_pretest_file(p) is None
    )
    assert pack.Protected.from_pretest_file(tmp_path / "absent.json") is None
    (tmp_path / "bad.json").write_text("not json")
    assert pack.Protected.from_pretest_file(tmp_path / "bad.json") is None


def test_effective_protected_is_the_one_resolver(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "tests").mkdir(parents=True)
    inherited = pack.Protected(PATHS, CMDS)
    assert (
        pack.effective_protected(inherited, str(pkg)) == inherited
    )  # no file: inherit
    assert pack.effective_protected(None, str(pkg)) is None
    (pkg / "tests" / "protected_paths.json").write_text(json.dumps(OWN))
    assert pack.effective_protected(inherited, str(pkg)) == pack.Protected(
        ["/x y"], []
    )  # file wins
    (pkg / "tests" / "protected_paths.json").write_text(json.dumps({}))
    assert pack.effective_protected(inherited, str(pkg)) == pack.Protected(
        [], []
    )  # file clears


def test_sandbox_tools_run_snapshot_and_the_probes_file_carry_the_lists(
    tmp_path,
) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "run").mkdir(parents=True)
    ec._write_pretest(pkg, {"_pretest": None, "_protected": {"protected_paths": PATHS}})
    assert pack.Protected.from_pretest_file(
        pkg / "run" / "pretest.json"
    ) == pack.Protected(PATHS, [])
    assert layout.read_pretest(pkg / "run" / "pretest.json") is None
    ec._write_pretest(
        pkg, {"_pretest": ("x\n", "image:x"), "_protected": {"protected_cmds": CMDS}}
    )
    assert layout.read_pretest(pkg / "run" / "pretest.json") == ("x\n", "image:x")
    assert pack.Protected.from_pretest_file(
        pkg / "run" / "pretest.json"
    ) == pack.Protected([], CMDS)
    (pkg / "run" / "pretest.json").unlink()
    ec._write_pretest(pkg, {"_pretest": None, "_protected": None})
    assert not (
        pkg / "run" / "pretest.json"
    ).exists()  # nothing to say, nothing written


# ---------------------------------------------------------------- the round trip
def _probe_row_as_the_loop_would(rewrite, monkeypatch) -> dict:
    """What daytona_revalidate grades: its real probe, with the sandbox faked, reading the
    rewrite's snapshot exactly as feedback_loop hands it over (--pretest-file)."""
    import daytona_revalidate as dr

    seen = {}

    class Sandbox:
        sandbox_id = "sb"

        async def exec(self, cmd, **_kw):
            return 0, "", ""

        async def write_file(self, dest, content, **_kw):
            pass

    @asynccontextmanager
    async def boot(_image, **_kw):
        yield Sandbox()

    async def seed(_sb, _tmax):
        pass

    async def measure(_sb, _secs, tail=""):
        return {}

    ib = pack._ib_module()

    async def capture(_sb, tmax, *, workdir, timeout):
        return {e: "d" * 64 for _, e in ib.protected_entries_of(tmax)} or None

    async def grade(_sb, tmax, *, workdir, baseline_digests=None, **_kw):
        seen["tmax"] = tmax
        seen["baseline"] = baseline_digests
        return 1.0

    for name, fn in (
        ("boot_agent_sandbox", boot),
        ("seed_workspace", seed),
        ("measure", measure),
        ("capture_baseline", capture),
        ("grade_tmax", grade),
    ):
        monkeypatch.setattr(dr, name, fn)
    pretest_file = (
        rewrite.pretest
        if (
            layout.read_pretest(rewrite.pretest)
            or layout.read_protected_lists(rewrite.pretest)
        )
        else None
    )
    verdict = asyncio.run(
        dr.probe(
            rewrite.package,
            None,
            5,
            pretest=layout.read_pretest(pretest_file) if pretest_file else None,
            protected=pack.Protected.from_pretest_file(pretest_file)
            if pretest_file
            else None,
        )
    )
    assert verdict["ok"]
    return seen


def _round(tmp_path, monkeypatch, *, parent_tmax: dict, package_file: dict | None):
    """One breeding round with the REAL row builder: the parent row carries `parent_tmax`; the
    rewrite ships `package_file` as tests/protected_paths.json (or nothing). Returns the tmax
    the probe graded with and the tmax of the folded row in the published mix."""
    root = tl._root(tmp_path, monkeypatch, tmax=parent_tmax)
    seed = root.data / "sources" / "tw-extract" / "tasks" / "tw_a"
    (seed / "environment" / "Dockerfile").write_text(
        "FROM docker.io/hamishi740/swerl-tmax-v3:37a79d0fd9b9\n"
    )
    tl._signal(root)
    graded = {}

    def fake_process_one(
        rewrite, signal, *, job, seed_dir, resources=None, history=None
    ):
        (rewrite.package / "instruction.md").write_text("harder\n")
        if package_file is not None:
            (rewrite.package / "tests" / "protected_paths.json").write_text(
                json.dumps(package_file)
            )
        (rewrite.package / "run").mkdir(exist_ok=True)
        (rewrite.package / "run" / "checks.jsonl").write_text('{"verdict": "pass"}\n')
        graded.update(_probe_row_as_the_loop_would(rewrite, monkeypatch))
        return {
            "status": "accepted",
            "stage": "daytona_oracle",
            "operator": "x",
            "verdicts": tl.VERDICTS,
            "resources": {
                "cpu": 2,
                "mem_gb": 4,
                "disk_gb": 2,
                "source": "measured:loop_probe",
                "measured": {"mem_peak_mb": 3000},
                "floor": {},
            },
        }

    monkeypatch.setattr(od.fb, "process_one", fake_process_one)
    r = od.run_round(root, workers=1)
    assert (r["handled"], r["accepted"]) == (1, 1), r
    folded = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    return graded["tmax"], graded["baseline"], folded


def _lists(tmax: dict) -> tuple:
    return tmax.get("protected_paths"), tmax.get("protected_cmds")


def test_a_variant_shipping_no_file_is_validated_and_folded_with_its_parents_lists(
    tmp_path, monkeypatch
) -> None:
    parent = {"test_sh": "echo 1\n", "protected_paths": PATHS, "protected_cmds": CMDS}
    graded, baseline, folded = _round(
        tmp_path, monkeypatch, parent_tmax=parent, package_file=None
    )
    assert _lists(graded) == (PATHS, CMDS)  # the probe saw the inherited lists...
    assert (
        baseline is not None and len(baseline) == 4
    )  # ...and took a baseline over them
    assert _lists(folded) == (PATHS, CMDS)  # ...and the folded row carries the same
    assert _lists(graded) == _lists(folded)


def test_a_variant_shipping_its_own_file_is_validated_and_folded_with_the_files_lists(
    tmp_path, monkeypatch
) -> None:
    parent = {"test_sh": "echo 1\n", "protected_paths": PATHS, "protected_cmds": CMDS}
    graded, baseline, folded = _round(
        tmp_path, monkeypatch, parent_tmax=parent, package_file=OWN
    )
    assert _lists(graded) == (["/x y"], None) and _lists(folded) == (["/x y"], None)
    assert baseline is not None and len(baseline) == 1
    assert _lists(graded) == _lists(folded)


def test_a_parent_without_lists_yields_a_variant_without_lists_at_both_ends(
    tmp_path, monkeypatch
) -> None:
    graded, baseline, folded = _round(
        tmp_path, monkeypatch, parent_tmax={"test_sh": "echo 1\n"}, package_file=None
    )
    assert _lists(graded) == (None, None) == _lists(folded) and baseline is None
