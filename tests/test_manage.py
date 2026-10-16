import argparse
import json
from pathlib import Path

import pytest

import background
import manage


def definition():
    return {"type": "command", "command": 'pythonw "local/hook_queue.py"',
            "async": True, "timeout": 3, "statusMessage": manage.LABEL}


def test_install_merge_is_idempotent_and_uninstall_preserves_other_hooks():
    other = {"type": "command", "command": "other-hook"}
    config = {"custom": {"kept": True}, "hooks": {
        "SessionStart": [{"hooks": [other]}], "Stop": [{"hooks": [other]}]}}
    updated = manage.merge_hook(config, definition())
    assert len(updated["hooks"]["Stop"]) == 2
    assert manage.merge_hook(updated, definition()) == updated
    assert manage.merge_hook(updated, definition(), remove=True) == config
    assert len(config["hooks"]["Stop"]) == 1  # input was not mutated


def test_uninstall_keeps_unrelated_handler_in_shared_group():
    config = {"hooks": {"Stop": [{"matcher": "*", "hooks": [definition(),
                               {"type": "command", "command": "other"}]}]}}
    result = manage.merge_hook(config, definition(), remove=True)
    assert result["hooks"]["Stop"] == [{"matcher": "*", "hooks": [
        {"type": "command", "command": "other"}]}]


@pytest.mark.parametrize("bad", [[], {"hooks": []}, {"hooks": {"Stop": {}}},
                                 {"hooks": {"Stop": [{"hooks": "bad"}]}}])
def test_invalid_hook_config_is_rejected_before_write(bad):
    with pytest.raises(ValueError):
        manage.merge_hook(bad, definition())


def test_install_uses_isolated_home_backs_up_and_preserves_settings(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(background, "RUNTIME", runtime)
    monkeypatch.setattr(background, "SETTINGS", runtime / "background-settings.json")
    monkeypatch.setattr(manage, "hook_definition", definition)
    home = manage.codex_home()
    home.mkdir(parents=True)
    original = {"hooks": {"Stop": [{"hooks": [{"command": "keep-this"}]}]}}
    (home / "hooks.json").write_text(json.dumps(original), encoding="utf-8")
    args = argparse.Namespace(model="test-model", reasoning_effort="high", max_calls_per_day=30,
                              idle_seconds=600, startup=False, start=False)
    assert manage.install(args) == 0
    first = (home / "hooks.json").read_bytes()
    assert len(list((runtime / "backups").rglob("hooks.json"))) == 1
    settings = background.read_json(background.SETTINGS, {})
    settings.update(enabled=False, max_calls_per_day=7)
    background.write_json(background.SETTINGS, settings)
    args.model = args.max_calls_per_day = None
    assert manage.install(args) == 0
    assert (home / "hooks.json").read_bytes() == first
    assert len(list((runtime / "backups").rglob("hooks.json"))) == 1
    settings = background.read_json(background.SETTINGS, {})
    assert settings["max_calls_per_day"] == 7 and not settings["enabled"]


def test_setup_rejects_shell_metacharacters_in_paths(monkeypatch):
    monkeypatch.setattr(manage, "ROOT", Path("C:/unsafe&folder"))
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    with pytest.raises(RuntimeError, match="metacharacters"):
        manage.hook_definition()
