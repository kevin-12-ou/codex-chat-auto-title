"""Codex adapter.

Paginated threads display ``name``; legacy threads display ``title``. The
``title`` of a paginated thread is derived search metadata, not its display
name (Codex 0.154.0, state/src/runtime/threads.rs). Only update the applicable
column, and reject snapshots changed while the namer was working. A separate
running UI may need to reload the thread list to observe an external write.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from ..models import Message, Session
from ._sqlite import connect_read, connect_write
from .base import Adapter

_VER_RE = re.compile(r"state_(\d+)\.sqlite$")


def _codex_root() -> Path:
    custom = os.environ.get("CODEX_HOME")
    return Path(custom).expanduser() if custom else Path.home() / ".codex"


def _activity_sql(columns: set[str]) -> str:
    if "updated_at_ms" in columns and "updated_at" in columns:
        return "COALESCE(updated_at_ms, updated_at * 1000)"
    if "updated_at_ms" in columns:
        return "updated_at_ms"
    if "updated_at" in columns:
        return "updated_at * 1000"
    raise RuntimeError("Codex threads table has no supported activity timestamp")


def _title_column(row: dict) -> str:
    if row.get("history_mode") == "paginated":
        if "name" not in row:
            raise RuntimeError("Paginated Codex thread has no name column")
        return "name"
    return "title"


_SNAPSHOT_FIELDS = (
    "title", "name", "history_mode", "updated_at_ms", "updated_at", "rollout_path", "is_pinned",
    "recency_at", "recency_at_ms", "project_id", "thread_section_id",
    "section_position", "section_entered_at_ms", "cwd",
)


def write_title_only(con, thread_id, column, title, before):
    """Write within the caller's transaction; verify all other target-row fields."""
    if column not in ("name", "title"):
        raise ValueError("Unsupported display-title column")
    result = con.execute(
        f"UPDATE threads SET {column} = ? WHERE id = ?", (title, thread_id)
    )
    if result.rowcount != 1:
        raise RuntimeError("Codex title update did not affect exactly one thread")
    after = con.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if after is None or dict(after) != {**before, column: title}:
        raise RuntimeError("Codex non-title fields changed; title update rolled back")


def _find_state_db() -> Path | None:
    root = _codex_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        root.glob("state_*.sqlite"),
        key=lambda p: int(m.group(1)) if (m := _VER_RE.search(p.name)) else -1,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    legacy = root / "state.sqlite"
    return legacy if legacy.exists() else None


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("text")
        ]
        return "\n".join(parts)
    return ""


class CodexAdapter(Adapter):
    name = "codex"
    label = "Codex"

    def available(self) -> bool:
        return _find_state_db() is not None

    def discover(self, since: float, *, session_ids: set[str] | None = None) -> list[Session]:
        if session_ids is not None and not session_ids:
            return []
        db = _find_state_db()
        if not db:
            return []
        since_ms = int(since * 1000)
        con = connect_read(db)
        try:
            columns = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
            activity = _activity_sql(columns)
            # Build the selection from known names only; old schemas can lack
            # name/history_mode, optional display metadata, or millisecond times.
            wanted = (
                "id", "title", "name", "history_mode", "rollout_path", "updated_at_ms",
                "updated_at", "cwd", "archived", "first_user_message", "source", "is_pinned",
                "recency_at", "recency_at_ms", "project_id", "thread_section_id",
                "section_position", "section_entered_at_ms",
            )
            selected = ",".join(c for c in wanted if c in columns)
            params = [since_ms]
            where = f"{activity} >= ?"
            if session_ids is not None:
                ids = sorted(session_ids)
                where += " AND id IN (" + ",".join("?" for _ in ids) + ")"
                params.extend(ids)
            cur = con.execute(
                f"SELECT {selected}, {activity} AS activity_ms FROM threads "
                f"WHERE {where} ORDER BY activity_ms DESC", params,
            )
            cols = [d[0] for d in cur.description]
            out: list[Session] = []
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                if r.get("archived"):  # skip archived threads (truthy flag)
                    continue
                source = r.get("source") or ""
                if source.startswith("{"):
                    try:
                        if "subagent" in json.loads(source):
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                updated = r.get("activity_ms")
                if not updated:
                    continue
                out.append(
                    Session(
                        tool=self.name,
                        id=r["id"],
                        title=r.get(_title_column(r)) or r.get("title"),
                        last_active=updated / 1000.0,
                        cwd=r.get("cwd"),
                        meta={
                            "db": str(db),
                            "rollout_path": r.get("rollout_path"),
                            "first_user_message": r.get("first_user_message"),
                            "is_pinned": bool(r.get("is_pinned")),
                            "snapshot": {k: r[k] for k in _SNAPSHOT_FIELDS if k in r},
                        },
                    )
                )
            return out
        finally:
            con.close()

    def read_transcript(self, session: Session) -> list[Message]:
        rollout = session.meta.get("rollout_path")
        msgs: list[Message] = []
        if rollout and Path(rollout).exists():
            try:
                with open(rollout, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"message"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") != "response_item":
                            continue
                        payload = obj.get("payload") or {}
                        if payload.get("type") != "message":
                            continue
                        role = payload.get("role")
                        if role not in ("user", "assistant"):
                            continue
                        text = _content_text(payload.get("content"))
                        if text.strip():
                            msgs.append(Message(role=role, text=text))
            except OSError:
                msgs = []
        if not msgs:
            fum = session.meta.get("first_user_message")
            if fum:
                return [Message(role="user", text=fum)]
        return msgs

    def set_title(self, session: Session, title: str) -> None:
        if not title.strip():
            raise ValueError("Codex title must not be empty")
        db = session.meta["db"]
        con = connect_write(db)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.row_factory = sqlite3.Row
            raw = con.execute("SELECT * FROM threads WHERE id = ?", (session.id,)).fetchone()
            if raw is None:
                raise RuntimeError("Codex thread no longer exists; title was not changed")
            row = dict(raw)
            if row.get("archived"):
                raise RuntimeError("Codex thread was archived; title was not changed")
            for key, expected in session.meta.get("snapshot", {}).items():
                if row.get(key) != expected:
                    raise RuntimeError(
                        "Codex thread changed since discovery; retry on the next pass"
                    )
            column = _title_column(row)
            # Verify inside the same transaction, including schema-trigger effects.
            # Fail closed if a future Codex schema makes naming move a project,
            # bump sidebar order, or change any other field of this thread.
            write_title_only(con, session.id, column, title, row)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
