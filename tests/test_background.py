import json
import sqlite3
import time

import pytest

import background
from rename.adapters import codex
from rename.models import Message
from rename.state import StateStore


@pytest.fixture
def isolated_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(background, "RUNTIME", tmp_path)
    monkeypatch.setattr(background, "JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(background, "CONTROL", tmp_path / "control.json")
    monkeypatch.setattr(background, "process_alive", lambda pid: False)
    return tmp_path


def test_open_turn_is_never_treated_as_idle(tmp_path):
    path = tmp_path / "rollout.jsonl"
    def record(kind):
        return json.dumps({"type": "event_msg", "payload": {"type": kind}}) + "\n"
    path.write_text(record("task_started"), encoding="utf-8")
    assert background.turn_is_open(path)
    with path.open("a", encoding="utf-8") as file:
        file.write(record("task_complete"))
    assert not background.turn_is_open(path)
    with path.open("a", encoding="utf-8") as file:
        file.write(record("task_started") + record("turn_aborted"))
    assert not background.turn_is_open(path)


def test_budget_caps_requests_and_failure_backs_off(isolated_runner, monkeypatch):
    namer = background.BudgetNamer({**background.DEFAULTS, "max_calls_per_day": 2})
    calls = []
    monkeypatch.setattr(namer.inner, "generate", lambda *a, **k: calls.append(1) or None)
    messages = [Message("user", "Fix the automatic session naming bug")]
    assert namer.generate(messages) is None
    assert namer.generate(messages) is None
    assert calls == [1]  # backoff prevented a second call
    state = background.read_json(background.CONTROL, {})
    assert state["retry_after"] > time.time()
    state.update(attempts=2, retry_after=0)
    background.write_json(background.CONTROL, state)
    assert namer.generate(messages) is None
    assert calls == [1]  # daily cap is enforced independently of backoff


def test_restore_only_applied_changes_and_respects_manual_edits(isolated_runner):
    db = isolated_runner / "state.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY,name TEXT,title TEXT)")
        con.executemany("INSERT INTO threads VALUES (?,?,?)", [
            ("a", "Auto new", "Derived"), ("b", "User renamed", "Derived"),
            ("c", "Attempt never applied", "Derived"),
        ])
    for tid in ("a", "b", "c"):
        background.journal({
            "event": "prepared", "change_id": tid, "id": tid, "db": str(db),
            "column": "name", "old_value": "Original", "new_value": "Auto new",
        })
        if tid != "c":
            background.journal({"event": "applied", "change_id": tid})
    background.restore(False)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT name FROM threads WHERE id='a'").fetchone()[0] == "Auto new"
    background.restore(True)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT * FROM threads ORDER BY id").fetchall() == [
            ("a", "Original", "Derived"), ("b", "User renamed", "Derived"),
            ("c", "Attempt never applied", "Derived"),
        ]


def test_restore_rolls_back_if_schema_changes_project_or_order(isolated_runner):
    db = isolated_runner / "state.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,name TEXT,project_id TEXT,"
                    "updated_at_ms INTEGER)")
        con.execute("INSERT INTO threads VALUES('a','Auto','project-a',1000)")
        con.execute("CREATE TRIGGER bump_restore AFTER UPDATE OF name ON threads BEGIN "
                    "UPDATE threads SET project_id=NULL,updated_at_ms=2000 WHERE id=NEW.id; END")
    background.journal({"event": "prepared", "change_id": "a", "id": "a", "db": str(db),
                        "column": "name", "old_value": None, "new_value": "Auto"})
    background.journal({"event": "applied", "change_id": "a"})
    with pytest.raises(RuntimeError, match="non-title"):
        background.restore(True)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT * FROM threads").fetchall() == [("a", "Auto", "project-a", 1000)]


@pytest.fixture
def guarded_store(tmp_path, monkeypatch):
    db = tmp_path / "threads.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, "
                    "first_user_message TEXT, updated_at INTEGER, history_mode TEXT)")
        con.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?)", [
            ("default", "帮我安装这个工具", "帮我安装这个工具", "帮我安装这个工具",
             1000, "paginated"),
            ("custom", "我的工具箱", "帮我安装这个工具", "帮我安装这个工具", 1000, "paginated"),
            ("unknown", "不确定来源的名字", "", "", 1000, "paginated"),
        ])
    monkeypatch.setattr(codex, "_find_state_db", lambda: db)
    state = StateStore(tmp_path / "state.json")
    state.set_baseline(2000)
    settings = {**background.DEFAULTS, "include_historical": True}
    return db, state, background.GuardedCodex(state, settings)


def test_only_default_titles_enter_historical_queue(guarded_store):
    _, state, adapter = guarded_store
    assert [s.id for s in adapter.discover(0)] == ["default"]
    assert state.get("codex", "custom")["manual_lock"]
    assert state.get("codex", "unknown")["manual_lock"]


def test_manual_rename_before_first_ai_call_is_preserved(guarded_store):
    db, state, adapter = guarded_store
    adapter.discover(0)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE threads SET name='我的安装记录',updated_at=3000 WHERE id='default'")
    assert adapter.discover(0) == []
    assert state.get("codex", "default")["manual_lock"]


def test_already_named_thread_never_consumes_more_requests(guarded_store):
    db, state, adapter = guarded_store
    state.update("codex", "default", title="自动安装记录", renamed_at=1500)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE threads SET name='自动安装记录',updated_at=3000 WHERE id='default'")
    assert adapter.discover(0) == []
    assert not state.get("codex", "default").get("manual_lock")


@pytest.mark.parametrize("name,first,expected", [
    ("帮我安装这个工具", "帮我安装这个工具", True),
    ("帮我安装这个工具", "帮我  安装这个工具", False),
    ("帮我 安装这个工具", "帮我  安装这个工具", True),
    ("长" * 59 + "…", "长" * 80, True),
    ("很短…", "很短但是我自己起的名字", False),
    ("我的工具箱", "帮我安装这个工具", False),
    ("", "帮我安装这个工具", True),
    ("未知名字", "", False),
    ("安装这个工具", "# Files mentioned by the user:\n附件\n## My request:\n安装这个工具", True),
    ("https://example.com 安装", "[https://example.com](https://example.com) 安装", True),
])
def test_default_title_detection_is_conservative(name, first, expected):
    assert background.is_default_title(name, first) is expected


def test_idle_hook_worker_does_not_discover_or_read_transcripts(isolated_runner, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Idle workers must never scan chat history")
    monkeypatch.setattr(codex.CodexAdapter, "discover", forbidden)
    state = StateStore(isolated_runner / "state.json")
    assert background.process_pending(background.DEFAULTS, state)["candidates"] == 0


def test_hook_queue_deduplicates_turns_and_preserves_newer_events(isolated_runner):
    from hook_queue import Queue
    q = Queue(isolated_runner / "queue.sqlite")
    tid = "00000000-0000-7000-8000-000000000001"
    q.put(tid, "turn-one", now=1000, delay=600)
    q.put(tid, "turn-one", now=1100, delay=600)
    assert q.next_ready(now=1599) is None
    first = q.next_ready(now=1600)
    assert first["not_before"] == 1600
    q.put(tid, "turn-two", now=1500, delay=600)
    q.finish(first, "done")
    assert q.next_ready(now=2099) is None
    assert q.next_ready(now=2100)["turn_id"] == "turn-two"
    q.finish(q.next_ready(now=2100), "failed")
    assert q.next_ready(now=1000000) is None  # no timed auto-retry after a failure


def test_stop_hook_rejects_non_stop_and_path_injection(isolated_runner):
    from hook_queue import enqueue
    assert not enqueue({"hook_event_name": "SessionStart", "session_id": "x"},
                       isolated_runner, delay=600)
    assert not enqueue({"hook_event_name": "Stop", "session_id": "../../outside"},
                       isolated_runner, delay=600)
    assert not list(isolated_runner.glob("*.sqlite"))


def test_budget_cooldown_defers_without_model_call(isolated_runner, monkeypatch):
    namer = background.BudgetNamer({**background.DEFAULTS, "min_call_interval_seconds": 300})
    background.write_json(background.CONTROL, {
        "date": background.datetime.now().date().isoformat(), "attempts": 1,
        "last_attempt": time.time(), "last_success": True,
    })
    def forbidden(*a, **k):
        raise AssertionError("Cooldown must not launch Codex")
    monkeypatch.setattr(namer.inner, "generate", forbidden)
    assert namer.generate([Message("user", "Install the naming tool")]) is None


def test_namer_exception_counts_once_and_ends_event(isolated_runner, guarded_store, monkeypatch):
    from hook_queue import Queue
    _, state, _ = guarded_store
    q = Queue(isolated_runner / "hook-queue.sqlite")
    q.put("default", "turn", now=0, delay=0)
    def raises(*a, **k):
        raise ValueError("Invalid provider configuration")
    monkeypatch.setattr(background.CliNamer, "generate", raises)
    assert background.process_pending(background.DEFAULTS, state)["status"] == (
        "failed_until_new_event"
    )
    assert q.next_ready(now=100000000000) is None
    control = background.read_json(background.CONTROL, {})
    assert control["attempts"] == 1 and control["retry_after"] > time.time()


def test_successful_equal_title_does_not_call_model_again(
    isolated_runner, guarded_store, monkeypatch,
):
    from hook_queue import Queue
    db, state, _ = guarded_store
    q = Queue(isolated_runner / "hook-queue.sqlite")
    q.put("default", "turn-1", now=0, delay=0)
    calls = []
    monkeypatch.setattr(background.CliNamer, "generate",
                        lambda *a, **k: calls.append(1) or "帮我安装这个工具")
    assert background.process_pending(background.DEFAULTS, state)["renamed"] == 0
    assert state.get("codex", "default")["renamed_at"]
    with sqlite3.connect(db) as con:
        con.execute("UPDATE threads SET updated_at=2000 WHERE id='default'")
    q.put("default", "turn-2", now=0, delay=0)
    settings = {**background.DEFAULTS, "min_call_interval_seconds": 0}
    assert background.process_pending(settings, state)["candidates"] == 0
    assert calls == [1]


def test_hook_worker_changes_only_the_enqueued_default(
    isolated_runner, guarded_store, monkeypatch,
):
    from hook_queue import Queue
    db, state, _ = guarded_store
    q = Queue(isolated_runner / "hook-queue.sqlite")
    q.put("default", "turn", now=0, delay=0)
    monkeypatch.setattr(background.CliNamer, "generate", lambda *a, **k: "安装工具记录")
    outcome = background.process_pending(background.DEFAULTS, state)
    assert outcome["renamed"] == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT name FROM threads WHERE id='default'").fetchone()[0] == (
            "安装工具记录"
        )
        assert con.execute("SELECT name FROM threads WHERE id='custom'").fetchone()[0] == (
            "我的工具箱"
        )
    assert q.next_ready() is None
    assert background.read_json(background.CONTROL, {})["attempts"] == 1


def test_queue_never_overwrites_manual_name_or_retries_failed_event(
    isolated_runner, guarded_store, monkeypatch,
):
    from hook_queue import Queue
    _, state, _ = guarded_store
    q = Queue(isolated_runner / "hook-queue.sqlite")
    q.put("custom", "turn", now=0, delay=0)
    def forbidden(*a, **k):
        raise AssertionError("Manual title must not cause a model request")
    monkeypatch.setattr(background.CliNamer, "generate", forbidden)
    assert background.process_pending(background.DEFAULTS, state)["candidates"] == 0
    assert not background.CONTROL.exists()
    q.put("default", "turn", now=0, delay=0)
    monkeypatch.setattr(background.CliNamer, "generate", lambda *a, **k: None)
    assert background.process_pending(background.DEFAULTS, state)["status"] == (
        "failed_until_new_event"
    )
    assert q.next_ready(now=100000000000) is None
