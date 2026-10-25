"""Windows installer for first-prompt naming or the optional legacy worker."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import background

ROOT = Path(__file__).resolve().parent
LABEL = "Codex Chat Auto Title: enqueue only"
PROMPT_LABEL = "Codex Chat Auto Title v2: first message only"


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def hook_definition():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError("pythonw.exe is required for silent Windows hooks")
    # Hook commands are interpreted by the host shell. Refuse shell syntax in
    # installation paths instead of trying to guess the host's escaping rules.
    paths = [str(pythonw), str(ROOT / "hook_queue.py")]
    if any(any(c in path for c in '\"%`$&|<>^!\r\n') for path in paths):
        raise RuntimeError("Install in a path without shell metacharacters")
    command = '"' + paths[0].replace('\\', '/') + '" -X utf8 "'
    command += paths[1].replace('\\', '/') + '"'
    return {"type": "command", "command": command, "async": True,
            "timeout": 3, "statusMessage": LABEL}


def prompt_hook_definition():
    # Synchronous delivery needs redirected stdout, so use python.exe. Codex's
    # hook launcher hides the Windows console; pythonw cannot return this context.
    definition = hook_definition()
    definition["command"] = definition["command"].replace("pythonw.exe", "python.exe").replace(
        "hook_queue.py", "first_prompt.py"
    )
    definition.update({"async": False, "statusMessage": PROMPT_LABEL,
                       "additionalContextLimit": 1200})
    return definition


def merge_hook(config, definition, *, remove=False, event="Stop"):
    """Only touch this checkout's exact command, preserving all other hooks."""
    result = copy.deepcopy(config)
    if not isinstance(result, dict):
        raise ValueError("hooks.json must be an object")
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"{event} hooks must be a list")
    found = False
    kept_groups = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ValueError(f"Each {event} group must contain a hooks list")
        kept = []
        for handler in group["hooks"]:
            owned = (isinstance(handler, dict) and handler.get("type") == "command"
                     and handler.get("command") == definition["command"])
            if owned:
                if not remove and not found:
                    kept.append(copy.deepcopy(definition))
                found = True
            else:
                kept.append(handler)
        if kept:
            kept_groups.append({**group, "hooks": kept})
    if not remove and not found:
        kept_groups.append({"hooks": [copy.deepcopy(definition)]})
    hooks[event] = kept_groups
    return result


def save_hooks(config, path=None):
    path = Path(path) if path else codex_home() / "hooks.json"
    existing = background.read_json(path, {})
    if config == existing:
        return
    if path.exists():
        backup = background.RUNTIME / "backups" / datetime.now().strftime("%Y%m%dT%H%M%S%f")
        backup.mkdir(parents=True)
        shutil.copy2(path, backup / "hooks.json")
    background.write_json(path, config)


def startup_path():
    return (Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
            / "Codex Chat Auto Title.lnk")


def set_startup(remove=False):
    path = startup_path()
    if remove:
        manifest = background.read_json(background.RUNTIME / "installation.json", {})
        if manifest.get("startup_path") == str(path) and path.is_file():
            path.unlink()
        return
    manifest = background.read_json(background.RUNTIME / "installation.json", {})
    if path.exists() and manifest.get("startup_path") != str(path):
        raise RuntimeError("An existing startup shortcut is not owned by this installation")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TITLE_LINK": str(path), "TITLE_PYTHON": str(pythonw),
           "TITLE_ARGS": subprocess.list2cmdline(["-X", "utf8", str(ROOT / "background.py"),
                                                  "run"]), "TITLE_ROOT": str(ROOT)}
    command = ("$ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; "
               "$s=$w.CreateShortcut($env:TITLE_LINK); $s.TargetPath=$env:TITLE_PYTHON; "
               "$s.Arguments=$env:TITLE_ARGS; $s.WorkingDirectory=$env:TITLE_ROOT; "
               "$s.WindowStyle=7; $s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                   env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    manifest["startup_path"] = str(path)
    background.write_json(background.RUNTIME / "installation.json", manifest)


def install(args):
    mode = getattr(args, "mode", "hook_queue")
    event = "UserPromptSubmit" if mode == "first_prompt" else "Stop"
    definition = prompt_hook_definition() if mode == "first_prompt" else hook_definition()
    manifest = background.read_json(background.RUNTIME / "installation.json", {})
    project = getattr(args, "project_root", None)
    path = Path(project).resolve() / ".codex/hooks.json" if project else codex_home() / "hooks.json"
    if manifest.get("hook_path") and Path(manifest["hook_path"]) != path:
        raise ValueError("Uninstall this installation before changing its hook scope")
    settings = {**background.DEFAULTS, **background.read_json(background.SETTINGS, {})}
    # Re-running installation preserves choices, including a deliberate disable.
    for key in ("model", "reasoning_effort", "max_calls_per_day", "idle_seconds"):
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
    settings["include_historical"] = False
    if mode == "first_prompt" and settings.get("mode") != "first_prompt":
        settings["first_prompt_installed_at"] = time.time()
    settings["mode"] = mode
    if settings["max_calls_per_day"] < 1 or settings["idle_seconds"] < 60:
        raise ValueError("Daily limit must be positive; idle time must be at least 60 seconds")
    existing = background.read_json(path, {})
    merged = existing
    if manifest.get("hook"):
        merged = merge_hook(merged, manifest["hook"], remove=True,
                            event=manifest.get("hook_event", "Stop"))
    other = hook_definition() if mode == "first_prompt" else prompt_hook_definition()
    other_event = "Stop" if mode == "first_prompt" else "UserPromptSubmit"
    if other_event in merged.get("hooks", {}):
        merged = merge_hook(merged, other, event=other_event, remove=True)
    merged = merge_hook(merged, definition, event=event)
    if mode == "first_prompt":
        if args.startup or args.start:
            raise ValueError("First-prompt mode needs no worker; omit --startup and --start")
        background.stop()
        set_startup(remove=True)
    elif args.startup:
        set_startup()
    background.write_json(background.SETTINGS, settings)
    save_hooks(merged, path)
    manifest = background.read_json(background.RUNTIME / "installation.json", {})
    background.write_json(background.RUNTIME / "installation.json",
                          {**manifest, "hook": definition, "root": str(ROOT),
                           "hook_event": event, "hook_path": str(path), "mode": mode})
    print("Installed. Review and trust this hook in Codex Settings > Hooks.")
    print("First-prompt mode uses the current model; no separate naming requests."
          if mode == "first_prompt" else "Model calls share your existing Codex account usage.")
    return background.start() if args.start and settings.get("enabled", True) else 0


def doctor():
    # Read-only diagnostics: do not start a model request or read credentials.
    manifest = background.read_json(background.RUNTIME / "installation.json", {})
    mode = manifest.get("mode", "hook_queue")
    event = manifest.get("hook_event", "Stop")
    path = Path(manifest.get("hook_path") or codex_home() / "hooks.json")
    config = background.read_json(path, {})
    definition = prompt_hook_definition() if mode == "first_prompt" else hook_definition()
    found = any(h.get("command") == definition["command"]
                for g in config.get("hooks", {}).get(event, []) for h in g.get("hooks", [])
                if isinstance(h, dict))
    from rename.namers.cli_namer import _run
    proc = _run(["codex", "--version"]) if mode == "hook_queue" else None
    info = background.read_json(background.RUNTIME / "runner.json", {})
    print(json.dumps({"mode": mode, "hook_event": event, "hook_path": str(path),
                      "hook_configured": found,
                      "hook_trust": "Review in Codex Settings > Hooks; not inferred from config",
                      "codex_version": proc.stdout.strip() if proc and proc.returncode == 0
                      else "not required" if mode == "first_prompt" else "unavailable",
                      "running": background.process_alive(info.get("pid")),
                      "settings": {**background.DEFAULTS,
                                   **background.read_json(background.SETTINGS, {})}}, indent=2))
    return 0 if found and (mode == "first_prompt" or proc and proc.returncode == 0) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install")
    p.add_argument("--mode", choices=("first_prompt", "hook_queue"), default="first_prompt")
    p.add_argument("--project-root", type=Path, help="Opt in to project-local .codex/hooks.json")
    p.add_argument("--model")
    p.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"))
    p.add_argument("--max-calls-per-day", type=int)
    p.add_argument("--idle-seconds", type=int)
    p.add_argument("--startup", action="store_true", help="Opt in to Windows sign-in startup")
    p.add_argument("--start", action="store_true", help="Start worker after installation")
    for name in ("doctor", "enable", "disable", "uninstall", "start", "stop", "status"):
        sub.add_parser(name)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The hook installer and background worker currently support Windows only")
    if args.command == "install":
        return install(args)
    if args.command == "doctor":
        return doctor()
    if args.command in ("enable", "disable", "uninstall"):
        settings = {**background.DEFAULTS, **background.read_json(background.SETTINGS, {})}
        settings["enabled"] = args.command == "enable"
        background.write_json(background.SETTINGS, settings)
        if args.command == "enable":
            return background.start() if settings.get("mode") != "first_prompt" else 0
        # Keep the hook cheap and inactive before stopping an in-flight worker.
        rc = background.stop()
        if args.command == "uninstall":
            manifest = background.read_json(background.RUNTIME / "installation.json", {})
            definition = manifest.get("hook")
            if definition:
                path = Path(manifest.get("hook_path") or codex_home() / "hooks.json")
                config = background.read_json(path, {})
                save_hooks(merge_hook(config, definition, remove=True,
                                      event=manifest.get("hook_event", "Stop")), path)
            set_startup(remove=True)
            print("Uninstalled hook and owned startup shortcut; runtime and titles retained.")
        return rc
    return getattr(background, args.command)() if args.command != "status" else _status()


def _status():
    info = background.read_json(background.RUNTIME / "runner.json", {})
    settings = background.read_json(background.SETTINGS, {})
    if settings.get("mode") == "first_prompt":
        print(json.dumps({"mode": "first_prompt", "enabled": settings.get("enabled", True),
                          "worker_required": False,
                          "legacy_worker_running": background.process_alive(info.get("pid")),
                          "separate_naming_requests": False,
                          "delivery": "Needs trusted Hook, new chat and native title tool"},
                         indent=2))
        return 0
    print(json.dumps({"mode": settings.get("mode", "hook_queue"),
                      "enabled": settings.get("enabled", True),
                      "running": background.process_alive(info.get("pid")),
                      "heartbeat": background.read_json(background.RUNTIME / "heartbeat.json", {}),
                      "budget": background.read_json(background.CONTROL, {})}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
