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


def test_first_prompt_install_migrates_owned_worker_without_touching_other_hooks(
        tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(background, "RUNTIME", runtime)
    monkeypatch.setattr(background, "SETTINGS", runtime / "background-settings.json")
    monkeypatch.setattr(background, "stop", lambda: 0)
    monkeypatch.setattr(manage, "set_startup", lambda **kw: None)
    home = manage.codex_home()
    home.mkdir(parents=True)
    old = manage.hook_definition()
    unrelated = {"type": "command", "command": "keep-another-stop-hook"}
    original = {"hooks": {"Stop": [{"hooks": [old, unrelated]}]}}
    (home / "hooks.json").write_text(json.dumps(original), encoding="utf-8")
    args = argparse.Namespace(mode="first_prompt", model=None, reasoning_effort=None,
                             max_calls_per_day=None, idle_seconds=None, startup=False, start=False)
    assert manage.install(args) == 0
    result = json.loads((home / "hooks.json").read_text("utf-8"))
    assert result["hooks"]["Stop"] == [{"hooks": [unrelated]}]
    new = result["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert "first_prompt.py" in new["command"] and "python.exe" in new["command"]
    assert new["async"] is False and new["additionalContextLimit"] > 0
    settings = background.read_json(background.SETTINGS, {})
    since = settings["first_prompt_installed_at"]
    assert settings["mode"] == "first_prompt" and settings["max_calls_per_day"] == 30
    assert manage.install(args) == 0
    assert json.loads((home / "hooks.json").read_text("utf-8")) == result
    assert background.read_json(background.SETTINGS, {})["first_prompt_installed_at"] == since


def test_project_scope_installs_to_project_without_writing_user_hooks(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(background, "RUNTIME", runtime)
    monkeypatch.setattr(background, "SETTINGS", runtime / "background-settings.json")
    monkeypatch.setattr(background, "stop", lambda: 0)
    monkeypatch.setattr(manage, "set_startup", lambda **kw: None)
    args = argparse.Namespace(mode="first_prompt", project_root=tmp_path / "project",
                             startup=False, start=False)
    assert manage.install(args) == 0
    assert (args.project_root / ".codex/hooks.json").is_file()
    assert not (manage.codex_home() / "hooks.json").exists()
    args.project_root = tmp_path / "different-project"
    with pytest.raises(ValueError, match="scope"):
        manage.install(args)


def test_first_prompt_worker_start_does_not_spawn_process(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "settings.json"
    settings.write_text('{"mode":"first_prompt"}', encoding="utf-8")
    monkeypatch.setattr(background, "SETTINGS", settings)
    monkeypatch.setattr(background.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    assert background.start() == 0
    assert "does not use" in capsys.readouterr().out
