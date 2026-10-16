"""Fast, silent Stop hook. Persist a thread ID; never load transcripts or call AI."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Queue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS jobs (thread_id TEXT PRIMARY KEY, "
                        "turn_id TEXT NOT NULL,generation TEXT NOT NULL,not_before REAL NOT NULL,"
                        "status TEXT NOT NULL,updated_at REAL NOT NULL)")
            con.execute("CREATE INDEX IF NOT EXISTS due_jobs ON jobs(status,not_before)")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=0.5)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def put(self, tid, turn_id, *, now=None, delay=600):
        now = time.time() if now is None else now
        with self.connect() as con:
            con.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
                "turn_id=excluded.turn_id,generation=excluded.generation,"
                "not_before=excluded.not_before,status='pending',updated_at=excluded.updated_at "
                "WHERE jobs.turn_id != excluded.turn_id",
                (tid, turn_id, str(uuid.uuid4()), now + delay, "pending", now),
            )

    def next_ready(self, *, now=None):
        with self.connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE status='pending' AND not_before<=? "
                              "ORDER BY not_before LIMIT 1",
                              (time.time() if now is None else now,)).fetchone()
            return dict(row) if row else None

    def finish(self, job, status):
        # A newer Stop must survive if it arrived while the model was working.
        with self.connect() as con:
            con.execute("UPDATE jobs SET status=? WHERE thread_id=? AND generation=?",
                        (status, job["thread_id"], job["generation"]))

    def defer(self, job, until):
        with self.connect() as con:
            con.execute("UPDATE jobs SET not_before=? WHERE thread_id=? AND generation=?",
                        (until, job["thread_id"], job["generation"]))


def enqueue(payload, runtime, *, delay=600):
    if payload.get("hook_event_name") != "Stop" or payload.get("stop_hook_active"):
        return False
    try:
        tid = str(uuid.UUID(str(payload.get("session_id", ""))))
    except (ValueError, TypeError, AttributeError):
        return False
    turn = str(payload.get("turn_id") or "")[:128]
    # Without a turn ID, use a unique event; no payload paths are ever opened.
    Queue(Path(runtime) / "hook-queue.sqlite").put(tid, turn or str(uuid.uuid4()), delay=delay)
    return True


def main():
    runtime = Path(os.environ.get("RENAME_RUNTIME_DIR") or ROOT / "runtime")
    try:
        settings_path = runtime / "background-settings.json"
        settings = json.loads(settings_path.read_text("utf-8")) if settings_path.exists() else {}
        if settings.get("enabled", True):
            payload = json.loads(sys.stdin.read(1024 * 1024))
            if isinstance(payload, dict):
                enqueue(payload, runtime, delay=max(60, int(settings.get("idle_seconds", 600))))
    except Exception:
        # Never turn a maintenance failure into conversation context or a popup.
        # The queue/worker status is inspected separately when diagnosing.
        pass
    if sys.stdout is not None:
        sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
