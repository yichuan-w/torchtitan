from types import SimpleNamespace

import pytest

from run_evolution_daytona import _codex_env_with_local_state


def test_local_rewrite_session_uses_its_existing_codex_home(tmp_path):
    work_root = tmp_path / "work"
    session_path = work_root / "experiment/active/rewrite/sessions/agent"
    codex_home = session_path / "codex"
    codex_home.mkdir(parents=True)
    session = SimpleNamespace(path=session_path, codex_home=codex_home)
    seen = []

    def original(value):
        seen.append(value)
        return {"CODEX_HOME": str(value.codex_home)}

    result = _codex_env_with_local_state(
        original,
        tmp_path / "state",
        tmp_path / "durable",
        work_root,
        session,
    )

    assert result == {"CODEX_HOME": str(codex_home)}
    assert seen == [session]
    assert codex_home.is_dir() and not codex_home.is_symlink()


def test_session_outside_configured_roots_is_rejected(tmp_path):
    session_path = tmp_path / "other/sessions/agent"
    session_path.mkdir(parents=True)
    session = SimpleNamespace(path=session_path, codex_home=session_path / "codex")

    with pytest.raises(RuntimeError, match="outside TRL_BASE"):
        _codex_env_with_local_state(
            lambda value: {},
            tmp_path / "state",
            tmp_path / "durable",
            tmp_path / "work",
            session,
        )
