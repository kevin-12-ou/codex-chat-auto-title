"""Local Windows deployment: guarded Codex-only runner, with a title journal.

No credentials are read here. CliNamer uses the existing Codex CLI login.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from hook_queue import Queue

ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get("RENAME_RUNTIME_DIR") or ROOT / "runtime")
sys.path.insert(0, str(ROOT / "src"))

from rename import util  # noqa: E402
from rename.adapters.codex import CodexAdapter, write_title_only  # noqa: E402
from rename.config import Config  # noqa: E402
from rename.engine import Engine  # noqa: E402
from rename.namers.cli_namer import CliNamer  # noqa: E402
from rename.state import StateStore  # noqa: E402

SETTINGS = RUNTIME / "background-settings.json"
JOURNAL = RUNTIME / "title-changes.jsonl"
CONTROL = RUNTIME / "control.json"
DEFAULTS = {
    "idle_seconds": 600,
    "poll_seconds": 60,
    "max_calls_per_day": 30,
    "failure_backoff_seconds": 1800,
    "model": "gpt-6-luna",
    "reasoning_effort": "high",
    "include_historical": False,
    "min_call_interval_seconds": 300,
    "enabled": True,
}


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text("utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def journal(entry):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


def turn_is_open(path):
    """A long-running turn must not be mistaken for an idle conversation."""
    if not path or not Path(path).is_file():
        return False
    opened = False
    with Path(path).open(encoding="utf-8", errors="replace") as file:
        for line in file:
            if '"event_msg"' not in line:
                continue
            try:
                record = json.loads(line)
                if record.get("type") != "event_msg":
                    continue
                kind = record.get("payload", {}).get("type")
            except (ValueError, AttributeError):
                continue
            if kind == "task_started":
                opened = True
            elif kind in ("task_complete", "turn_aborted"):
                opened = False
    return opened


def is_default_title(name, first_message):
    """Positive matches only; custom or unknown historical names stay untouched.

    Codex has no author-of-name flag. This intentionally misses some defaults
    rather than guessing that an unrelated short name belongs to the app.
    """
    if not first_message or not first_message.strip():
        return False
    if not name or not name.strip():
        return True
    texts = [first_message]
    request = re.search(r"^## My request:\s*\n(.*)", first_message, re.M | re.S)
    if request:
        texts.append(request.group(1))
    candidates = set()
    for text in texts:
        plain = re.sub(r"\[([^\]]+)\]\([^\n]*?\)", r"\1", text)
        plain = re.sub(r"[*`]+", "", plain)
        for variant in (text, plain):
            normalized = " ".join(variant.split())
            candidates.add(normalized)
            if len(normalized) > 60:
                candidates.add(normalized[:59].rstrip() + "…")
    return " ".join(name.split()) in candidates


class GuardedCodex(CodexAdapter):
    def __init__(self, state, settings, session_ids=None):
        self.state = state
        self.settings = settings
        self.session_ids = session_ids

    def discover(self, since):
        out = []
        baseline = self.state.baseline()
        for session in super().discover(since, session_ids=self.session_ids):
            if session.meta.get("is_pinned"):
                continue
            if (not self.settings.get("include_historical", False)
                    and baseline is not None and session.last_active < baseline):
                continue
            prev = self.state.get("codex", session.id) or {}
            if prev.get("manual_lock"):
                continue
            if prev.get("renamed_at"):
                if session.title != prev.get("title"):
                    self.state.update("codex", session.id, manual_lock=True,
                                      protection_reason="changed_after_auto_name")
                # The user's request is default-title cleanup, once per thread.
                continue
            observed = prev.get("observed_title")
            changed = observed and session.title != observed
            if changed or not is_default_title(
                session.title, session.meta.get("first_user_message")
            ):
                self.state.update("codex", session.id, manual_lock=True,
                                  protection_reason="custom_or_uncertain")
                continue
            self.state.update("codex", session.id, observed_title=session.title)
            if session.idle_seconds(time.time()) >= self.settings["idle_seconds"]:
                if turn_is_open(session.meta.get("rollout_path")):
                    continue
            out.append(session)
        return out

    def set_title(self, session, title):
        # Keep a durable old value before attempting the write; only matching
        # "applied" entries are used by restore, never prepared/failed ones.
        snapshot = session.meta["snapshot"]
        column = "name" if snapshot.get("history_mode") == "paginated" else "title"
        entry = {
            "event": "prepared", "change_id": str(uuid.uuid4()), "id": session.id,
            "db": session.meta["db"], "column": column,
            "old_value": snapshot.get(column), "new_value": title,
            "time": datetime.now().astimezone().isoformat(),
        }
        journal(entry)
        super().set_title(session, title)
        journal({"event": "applied", "change_id": entry["change_id"]})


class RetainedState(StateStore):
    def prune(self, alive, healthy):
        # Guarded discovery hides protected sessions, not deleted sessions.
        # Keep their manual-lock and idempotency records across passes.
        pass


class BudgetNamer:
    name = "codex"

    def __init__(self, settings):
        self.settings = settings
        self.inner = CliNamer("codex", {
            "model": settings["model"], "lightweight": True,
            "reasoning_effort": settings["reasoning_effort"],
        })

    def ready(self):
        status = read_json(CONTROL, {})
        today = datetime.now().date().isoformat()
        if status.get("date") != today:
            status.update(date=today, attempts=0)
        if status.get("retry_after", 0) > time.time():
            return False
        if status.get("attempts", 0) >= self.settings["max_calls_per_day"]:
            return False
        if (time.time() - status.get("last_attempt", 0)
                < self.settings.get("min_call_interval_seconds", 0)):
            return False
        return True

    def generate(self, messages, **kwargs):
        if not self.ready():
            return None
        status = read_json(CONTROL, {})
        today = datetime.now().date().isoformat()
        if status.get("date") != today:
            status.update(date=today, attempts=0)
        status["attempts"] = status.get("attempts", 0) + 1
        write_json(CONTROL, status)
        try:
            title = self.inner.generate(messages, **kwargs)
        except Exception as exc:
            util.log(f"Naming failed ({type(exc).__name__}); keeping title", level="warn")
            title = None
        status = read_json(CONTROL, status)
        status["last_attempt"] = time.time()
        status["last_success"] = bool(title)
        if not title:
            status["retry_after"] = time.time() + self.settings["failure_backoff_seconds"]
            util.log("AI naming failed; keep old titles and back off for 30 minutes", level="warn")
        else:
            status["retry_after"] = 0
        write_json(CONTROL, status)
        return title


def process_pending(settings, state):
    """Only a due Stop event can cause a chat read or a naming request."""
    empty = {"renamed": 0, "candidates": 0, "mode": "hook_queue"}
    if not settings.get("enabled", True):
        return {**empty, "status": "disabled"}
    queue_path = RUNTIME / "hook-queue.sqlite"
    if not queue_path.exists():
        return {**empty, "status": "idle"}
    q = Queue(queue_path)
    job = q.next_ready()
    if job is None:
        return {**empty, "status": "idle"}
    namer = BudgetNamer(settings)
    if not namer.ready():
        return {**empty, "status": "quota_or_cooldown"}
    ids = {job["thread_id"]}
    # The hook is explicit recent activity, even for an older thread.
    adapter = GuardedCodex(state, {**settings, "include_historical": True}, session_ids=ids)
    sessions = adapter.discover(0)
    if not sessions:
        state.save()
        q.finish(job, "protected_or_missing")
        return {**empty, "status": "protected_or_missing"}
    due = sessions[0].last_active + settings["idle_seconds"]
    if due > time.time():
        q.defer(job, due + 1)
        return {**empty, "status": "waiting_for_idle"}
    cfg = Config(tools=("codex",), namer="codex", idle_seconds=settings["idle_seconds"],
                 batch_size=1)
    engine = Engine(cfg, [adapter], namer, state)
    renamed, candidates = engine.tick(limit=1, session_filter=ids, include_historical=True)
    # No timer-driven retries for this event. A new Stop may enqueue a new turn.
    status = "renamed" if renamed else "handled_without_change"
    if candidates and not read_json(CONTROL, {}).get("last_success", False):
        status = "failed_until_new_event"
    q.finish(job, status)
    return {"renamed": renamed, "candidates": candidates, "mode": "hook_queue", "status": status}


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True


def run():
    import msvcrt

    RUNTIME.mkdir(parents=True, exist_ok=True)
    # The held byte lock prevents a second instance from overwriting state or
    # duplicating model requests, including after Windows sign-in.
    with (RUNTIME / "runner.lock").open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 0
        write_json(RUNTIME / "runner.json", {"pid": os.getpid(), "script": str(Path(__file__))})
        state = RetainedState()
        state.ensure_baseline(time.time())
        state.save()
        util.log("Quiet Stop-hook worker started; historical scanning disabled")
        while True:
            settings = {**DEFAULTS, **read_json(SETTINGS, {})}
            try:
                outcome = process_pending(settings, state)
                write_json(RUNTIME / "heartbeat.json", {
                    "pid": os.getpid(), "checked_at": time.time(), **outcome,
                })
            except Exception as exc:
                util.log(
                    f"pass failed ({type(exc).__name__}); keeping titles unchanged", level="warn"
                )
            time.sleep(max(15, settings["poll_seconds"]))


def start():
    info = read_json(RUNTIME / "runner.json", {})
    if process_alive(info.get("pid")):
        print("Already running.")
        return 0
    RUNTIME.mkdir(parents=True, exist_ok=True)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    with (RUNTIME / "background.log").open("a", encoding="utf-8") as output:
        subprocess.Popen(
            [str(pythonw), "-X", "utf8", str(Path(__file__).resolve()), "run"],
            cwd=ROOT, stdout=output, stderr=output, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    for _ in range(30):
        time.sleep(0.1)
        info = read_json(RUNTIME / "runner.json", {})
        if process_alive(info.get("pid")):
            print(f"Running: PID {info['pid']}")
            return 0
    print("Could not start. Check runtime/background.log.")
    return 1


def stop():
    info = read_json(RUNTIME / "runner.json", {})
    pid = info.get("pid")
    if not process_alive(pid):
        print("Not running.")
        return 0
    script = str(Path(__file__).resolve()).replace("'", "''")
    # Validate the exact runner path before stopping a PID that could have
    # been reused after a reboot. No broad Python process matching.
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
        f"if($p -and $p.CommandLine.Contains('{script}')) "
        "{Stop-Process -Id $p.ProcessId -Force; exit 0} else {exit 1}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print("Stopped." if result.returncode == 0 else "PID no longer belongs to this runner.")
    return result.returncode


def restore(apply):
    if process_alive(read_json(RUNTIME / "runner.json", {}).get("pid")):
        raise RuntimeError("Stop the background runner before restoring titles")
    pending = {}
    applied = []
    if JOURNAL.exists():
        for line in JOURNAL.read_text("utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("event") == "prepared":
                pending[entry["change_id"]] = entry
            elif entry.get("event") == "applied" and entry["change_id"] in pending:
                applied.append(pending[entry["change_id"]])
    grouped = {}
    for entry in applied:
        key = (entry["db"], entry["id"], entry["column"])
        if key not in grouped:
            grouped[key] = [entry["old_value"], entry["new_value"]]
        else:
            grouped[key][1] = entry["new_value"]
    count = 0
    for (db, tid, column), (old, new) in grouped.items():
        if column not in ("name", "title"):
            continue
        mode = "rw" if apply else "ro"
        with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=" + mode, uri=True) as con:
            con.row_factory = sqlite3.Row
            if apply:
                con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
            if row is None or row[column] != new:
                continue
            print(f"{tid[:8]}: {new!r} -> {old!r}")
            if apply:
                write_title_only(con, tid, column, old, dict(row))
            count += 1
    print(f"{'Restored' if apply else 'Would restore'} {count} title(s).")
    return 0


def main():
    os.environ["XDG_CONFIG_HOME"] = str(RUNTIME / "config")
    os.environ["XDG_STATE_HOME"] = str(RUNTIME / "state")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "start", "stop", "status", "restore"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "restore":
        return restore(args.apply)
    if args.command == "status":
        info = read_json(RUNTIME / "runner.json", {})
        print(json.dumps({"running": process_alive(info.get("pid")), **info,
                          "heartbeat": read_json(RUNTIME / "heartbeat.json", {}),
                          "budget": read_json(CONTROL, {})}, ensure_ascii=True, indent=2))
        return 0
    return {"run": run, "start": start, "stop": stop}[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
