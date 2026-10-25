"""V2 contracts use synthetic local stores; no model or desktop mutation."""
import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import first_prompt as hook


def record(kind, **payload):
    return json.dumps({"type": kind, "payload": payload}, ensure_ascii=False) + "\n"


@pytest.fixture
def chat(tmp_path):
    home = Path(os.environ["CODEX_HOME"])
    transcript = home / "sessions/2026/10/24/rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    tid, turn = str(uuid.uuid4()), str(uuid.uuid4())
    transcript.write_text(record("session_meta", id=tid, source="vscode") + record(
        "event_msg", type="task_started", turn_id=turn), encoding="utf-8")
    db = home / "state_5.sqlite"
    prompt = "帮我完成微信授权登录"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY,name TEXT,title TEXT,"
                    "history_mode TEXT,rollout_path TEXT,created_at REAL,project_id TEXT,"
                    "cwd TEXT,thread_section_id TEXT,section_position REAL,is_pinned INTEGER,"
                    "archived INTEGER,source TEXT,updated_at INTEGER)")
        con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, prompt, "search", "paginated", str(transcript), time.time(),
                     "project-a", "C:/projects/login", "section-a", 7.0, 0, 0, "vscode", 100))
    return {"tid": tid, "turn": turn, "transcript": transcript, "db": db,
            "runtime": tmp_path / "runtime", "payload": {"hook_event_name": "UserPromptSubmit",
                "session_id": tid, "turn_id": turn, "prompt": prompt},
            "settings": {"enabled": True, "mode": "first_prompt",
                         "first_prompt_installed_at": time.time() - 10}}


def submit(chat):
    return hook.submit(chat["payload"], chat["runtime"], chat["settings"])


def claim(chat, title="登录|完成微信授权登录"):
    return hook.claim(chat["tid"], chat["turn"], title, chat["runtime"], chat["settings"])


def update(chat, column, value):
    with sqlite3.connect(chat["db"]) as con:
        con.execute(f"UPDATE threads SET {column}=? WHERE id=?", (value, chat["tid"]))


def append(chat, text):
    with chat["transcript"].open("a", encoding="utf-8") as file:
        file.write(text)


def test_first_prompt_rules_then_one_claim_without_codex_row_mutation(chat):
    _, before = hook.read_thread(chat["tid"])
    result = submit(chat)
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "UserPromptSubmit" == result["hookSpecificOutput"]["hookEventName"]
    assert "set_thread_title" in context and "24" in context and "模块名|持续目标" in context
    assert chat["turn"] in context and "最终回复前" in context
    assert submit(chat) == {}
    append(chat, record("response_item", type="message", role="user", content=[
        {"type": "input_text", "text": chat["payload"]["prompt"]}]) + record(
            "response_item", type="message", role="assistant", content=[]))
    assert claim(chat) == {"allowed": True, "title": "登录|完成微信授权登录"}
    assert claim(chat) == {"allowed": False}
    assert hook.read_thread(chat["tid"])[1] == before


@pytest.mark.parametrize("column,value", [("name", "我自己起的名字"), ("project_id", "new"),
    ("cwd", "C:/other"), ("thread_section_id", "new"), ("is_pinned", 1), ("archived", 1)])
def test_manual_rename_or_move_while_task_runs_cancels_claim(chat, column, value):
    assert submit(chat)
    update(chat, column, value)
    assert not claim(chat)["allowed"]
    assert claim(chat) == {"allowed": False}


def test_normal_task_activity_time_does_not_cancel_first_turn(chat):
    assert submit(chat)
    update(chat, "updated_at", 200)
    assert claim(chat)["allowed"]


def test_native_default_title_filled_after_empty_name_is_allowed(chat):
    update(chat, "name", None)
    assert submit(chat)
    update(chat, "name", chat["payload"]["prompt"])
    assert claim(chat)["allowed"]


@pytest.mark.parametrize("column,value", [("name", "手动名字"), ("is_pinned", 1),
    ("archived", 1), ("source", '{"subagent": {"thread": "x"}}'), ("created_at", 10)])
def test_skip_custom_pinned_archived_subagent_and_preinstall_chat(chat, column, value):
    update(chat, column, value)
    assert submit(chat) == {}


@pytest.mark.parametrize("field,value", [("hook_event_name", "Stop"), ("agent_id", "child"),
    ("agent_type", "worker"), ("prompt", "")])
def test_unrelated_and_child_events_do_not_issue_ticket(chat, field, value):
    chat["payload"][field] = value
    assert submit(chat) == {}


def test_old_chat_first_seen_after_install_does_not_become_first_turn(chat):
    # Regression: first-seen != first-message; even missing install age is insufficient.
    append(chat, record("turn_context", turn_id=str(uuid.uuid4())))
    assert submit(chat) == {}


def test_history_without_turn_ids_still_rejects_previous_user_or_assistant(chat):
    append(chat, record("response_item", type="message", role="user", content=[
        {"type": "input_text", "text": "以前的任务"}]))
    assert submit(chat) == {}


def test_unrecognized_transcript_path_cannot_read_outside_home(chat, tmp_path):
    outside = tmp_path / "untrusted.jsonl"
    outside.write_text(record("session_meta", id=chat["tid"]), encoding="utf-8")
    update(chat, "rollout_path", str(outside))
    assert submit(chat) == {}


def test_payload_transcript_path_is_ignored(chat):
    chat["payload"]["transcript_path"] = "C:/never-read-me"
    assert submit(chat)


@pytest.mark.parametrize("suffix", [record("event_msg", type="task_complete"),
    record("event_msg", type="turn_aborted"), record("compacted"),
    record("turn_context", turn_id=str(uuid.uuid4()))])
def test_new_turn_compaction_or_cancellation_expires_permission(chat, suffix):
    assert submit(chat)
    append(chat, suffix)
    assert not claim(chat)["allowed"]


def test_fork_with_inherited_history_does_not_get_a_new_title(chat):
    chat["transcript"].write_text(record("session_meta", id=chat["tid"],
        forked_from_id=str(uuid.uuid4())), encoding="utf-8")
    assert submit(chat) == {}


@pytest.mark.parametrize("title", ["模块|优化", "模块|处理问题", "x", "模块|", "|目标",
    "模块|持续目标" * 6, "模块|目标\n", "模块|持续|目标", "「模块|目标」"])
def test_invalid_title_retains_first_permission_for_a_valid_attempt(chat, title):
    assert submit(chat)
    assert not claim(chat, title)["allowed"]
    assert claim(chat)["allowed"]


def test_disabled_or_legacy_mode_does_not_inject_or_claim(chat):
    assert submit(chat)
    chat["settings"]["enabled"] = False
    assert submit(chat) == {}
    assert not claim(chat)["allowed"]
    chat["settings"].update(enabled=True, mode="hook_queue")
    assert submit(chat) == {}
    assert not claim(chat)["allowed"]


def test_concurrent_duplicate_hooks_and_claims_issue_one_permission(chat):
    # Initialize schema before intentionally racing independent connections.
    hook.state_db(chat["runtime"]).close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit(chat), range(4)))
    assert sum(bool(r) for r in results) == 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: claim(chat), range(4)))
    assert sum(r["allowed"] for r in results) == 1


def test_real_cli_utf8_hook_claim_and_followup_are_quiet(chat):
    chat["runtime"].mkdir()
    (chat["runtime"] / "background-settings.json").write_text(
        json.dumps(chat["settings"]), encoding="utf-8")
    env = {**os.environ, "RENAME_RUNTIME_DIR": str(chat["runtime"])}
    command = [sys.executable, "-X", "utf8", str(hook.ROOT / "first_prompt.py")]
    first = subprocess.run(command, input=json.dumps(chat["payload"], ensure_ascii=False),
        text=True, encoding="utf-8", env=env, capture_output=True, timeout=5)
    assert first.returncode == 0 and first.stderr == ""
    assert "模块名|持续目标" in json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
    granted = subprocess.run(command + ["--claim", chat["tid"], chat["turn"], "--title",
        "登录|完成微信授权登录"], env=env, text=True, encoding="utf-8",
        capture_output=True, timeout=5)
    assert granted.returncode == 0 and json.loads(granted.stdout)["allowed"]
    follow = subprocess.run(command, input=json.dumps(chat["payload"]), env=env,
        text=True, encoding="utf-8", capture_output=True, timeout=5)
    assert follow.returncode == 0 and follow.stdout == "{}" and follow.stderr == ""


@pytest.mark.parametrize("raw", ["not json", "[]", "null", '{"session_id":"bad"}'])
def test_malformed_input_exits_success_with_empty_context(chat, raw):
    chat["runtime"].mkdir()
    (chat["runtime"] / "background-settings.json").write_text(
        json.dumps(chat["settings"]), encoding="utf-8")
    result = subprocess.run([sys.executable, "-X", "utf8", str(hook.ROOT / "first_prompt.py")],
        input=raw, env={**os.environ, "RENAME_RUNTIME_DIR": str(chat["runtime"])},
        text=True, encoding="utf-8", capture_output=True, timeout=5)
    assert result.returncode == 0 and result.stdout == "{}" and result.stderr == ""
