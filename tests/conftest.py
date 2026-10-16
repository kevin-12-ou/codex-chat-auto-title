"""Fixture tests must not discover the developer's real local chat stores."""

from pathlib import Path

import pytest

from rename.adapters import antigravity


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setenv("APPDATA", str(root / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / ".local" / "state"))
    monkeypatch.setenv("CODEX_HOME", str(root / ".codex"))
    # These paths were captured at import time, before the fixture ran.
    monkeypatch.setattr(antigravity, "_COMPANION_PB", root / "companion.pb")
    monkeypatch.setattr(antigravity, "_BRAIN_DIR", root / "brain")
