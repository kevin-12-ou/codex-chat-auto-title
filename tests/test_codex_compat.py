"""Regression coverage for Codex 0.154 paginated names and stale writes."""

import json
import sqlite3

import pytest

from rename.adapters import _sqlite, codex
from rename.models import Session


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "Codex 中文 # data"
    root.mkdir()
    db = root / "state_5.sqlite"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, name TEXT, "
            "history_mode TEXT, updated_at INTEGER, updated_at_ms INTEGER, "
            "archived INTEGER DEFAULT 0, rollout_path TEXT, cwd TEXT, "
            "first_user_message TEXT, source TEXT)"
        )
        con.execute(
            "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("thread", "Original prompt", "旧会话名", "paginated", 1000,
             1000001, 0, "", "C:/work", "Build a feature", "cli"),
        )
    monkeypatch.setattr(codex, "_find_state_db", lambda: db)
    return db


def test_paginated_reads_name_and_updates_only_name(store):
    adapter = codex.CodexAdapter()
    s, = adapter.discover(0)
    assert s.title == "旧会话名"
    with sqlite3.connect(store) as con:
        before = con.execute("SELECT * FROM threads").fetchone()
    adapter.set_title(s, "修复自动命名 ✨")
    with sqlite3.connect(store) as con:
        after = con.execute("SELECT * FROM threads").fetchone()
    expected = list(before)
    expected[2] = "修复自动命名 ✨"
    assert after == tuple(expected)
    assert adapter.discover(0)[0].title == "修复自动命名 ✨"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE threads SET name = 'Manually renamed'",
        "UPDATE threads SET updated_at_ms = updated_at_ms + 1",
        "UPDATE threads SET history_mode = 'legacy'",
        "UPDATE threads SET archived = 1",
        "DELETE FROM threads",
    ],
)
def test_changed_or_deleted_thread_never_reports_success(store, sql):
    adapter = codex.CodexAdapter()
    s, = adapter.discover(0)
    with sqlite3.connect(store) as con:
        con.execute(sql)
        before = con.execute("SELECT * FROM threads").fetchall()
    with pytest.raises(RuntimeError):
        adapter.set_title(s, "Must not win")
    with sqlite3.connect(store) as con:
        assert con.execute("SELECT * FROM threads").fetchall() == before


def test_unknown_thread_raises(store):
    s = Session("codex", "missing", "Old", last_active=0, meta={"db": str(store)})
    with pytest.raises(RuntimeError, match="no longer exists"):
        codex.CodexAdapter().set_title(s, "New")


def test_legacy_uses_title_and_does_not_promote_history(store):
    with sqlite3.connect(store) as con:
        con.execute("UPDATE threads SET history_mode = 'legacy'")
    adapter = codex.CodexAdapter()
    s, = adapter.discover(0)
    assert s.title == "Original prompt"
    adapter.set_title(s, "Legacy title")
    with sqlite3.connect(store) as con:
        assert con.execute("SELECT title,name,history_mode FROM threads").fetchone() == (
            "Legacy title", "旧会话名", "legacy",
        )


def test_nullable_millisecond_time_falls_back_to_seconds(store):
    with sqlite3.connect(store) as con:
        con.execute("UPDATE threads SET updated_at_ms = NULL")
    sessions = codex.CodexAdapter().discover(999)
    assert len(sessions) == 1
    assert sessions[0].last_active == 1000


def test_old_seconds_only_schema(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT, title TEXT, updated_at INTEGER)")
        con.execute("INSERT INTO threads VALUES ('old', 'Old', 1000)")
    monkeypatch.setattr(codex, "_find_state_db", lambda: db)
    adapter = codex.CodexAdapter()
    s, = adapter.discover(999)
    adapter.set_title(s, "New")
    assert adapter.discover(0)[0].title == "New"


def test_subagents_are_not_renamed(store):
    with sqlite3.connect(store) as con:
        con.execute(
            "UPDATE threads SET source = ?",
            (json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "root"}}}),),
        )
    assert codex.CodexAdapter().discover(0) == []


def test_codex_home_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    for name in ("state_5.sqlite", "state_9.sqlite"):
        with sqlite3.connect(tmp_path / name):
            pass
    assert codex._find_state_db() == tmp_path / "state_9.sqlite"


@pytest.mark.parametrize("connect", [_sqlite.connect_read, _sqlite.connect_write])
def test_missing_database_is_never_created(tmp_path, connect):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        connect(path)
    assert not path.exists()


def test_read_connection_cannot_write(store):
    con = _sqlite.connect_read(store)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE threads SET name = 'Oops'")
    finally:
        con.close()


def test_targeted_discovery_never_loads_other_threads(store):
    with sqlite3.connect(store) as con:
        con.execute("INSERT INTO threads(id,title,name,history_mode,updated_at) "
                    "VALUES('other','Other','Other','paginated',2000)")
    adapter = codex.CodexAdapter()
    assert [s.id for s in adapter.discover(0, session_ids={"thread"})] == ["thread"]
    assert adapter.discover(0, session_ids=set()) == []
    assert adapter.discover(0, session_ids={"missing"}) == []


@pytest.fixture
def organized_store(store):
    with sqlite3.connect(store) as con:
        for column in ("project_id TEXT", "thread_section_id TEXT", "section_position REAL",
                       "section_entered_at_ms INTEGER", "is_pinned INTEGER",
                       "recency_at INTEGER", "recency_at_ms INTEGER"):
            con.execute("ALTER TABLE threads ADD COLUMN " + column)
        con.execute("UPDATE threads SET project_id='project-a',thread_section_id='section-a',"
                    "section_position=12,section_entered_at_ms=900000,is_pinned=0,"
                    "recency_at=999,recency_at_ms=999001")
    return store


def test_title_write_preserves_project_sections_and_both_sort_orders(organized_store):
    adapter = codex.CodexAdapter()
    session, = adapter.discover(0)
    with sqlite3.connect(organized_store) as con:
        before = con.execute("SELECT * FROM threads").fetchall()
        orders = [con.execute(f"SELECT id FROM threads ORDER BY {key}").fetchall()
                  for key in ("updated_at_ms", "recency_at_ms", "section_position")]
    adapter.set_title(session, "Readable title")
    with sqlite3.connect(organized_store) as con:
        expected = list(before[0])
        expected[2] = "Readable title"
        assert con.execute("SELECT * FROM threads").fetchall() == [tuple(expected)]
        assert [con.execute(f"SELECT id FROM threads ORDER BY {key}").fetchall()
                for key in ("updated_at_ms", "recency_at_ms", "section_position")] == orders


@pytest.mark.parametrize("change", [
    "project_id='project-b'", "thread_section_id='section-b'", "section_position=13",
    "section_entered_at_ms=900001", "is_pinned=1", "recency_at_ms=999002",
])
def test_user_moves_thread_during_naming_cancels_write(organized_store, change):
    adapter = codex.CodexAdapter()
    session, = adapter.discover(0)
    with sqlite3.connect(organized_store) as con:
        con.execute("UPDATE threads SET " + change)
    with pytest.raises(RuntimeError, match="changed since discovery"):
        adapter.set_title(session, "Stale title")


def test_schema_trigger_cannot_bump_recency_or_remove_project(organized_store):
    with sqlite3.connect(organized_store) as con:
        con.execute("CREATE TRIGGER future_title_side_effect AFTER UPDATE OF name ON threads "
                    "BEGIN UPDATE threads SET project_id=NULL,updated_at_ms=9999999,"
                    "recency_at_ms=9999999 WHERE id=NEW.id; END")
        before = con.execute("SELECT * FROM threads").fetchall()
    adapter = codex.CodexAdapter()
    session, = adapter.discover(0)
    with pytest.raises(RuntimeError, match="non-title"):
        adapter.set_title(session, "Must roll back")
    with sqlite3.connect(organized_store) as con:
        assert con.execute("SELECT * FROM threads").fetchall() == before
