"""Synchronous, first-turn-only Codex naming instructions. No AI subprocess."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from background import default_title_candidates, is_default_title
from rename.adapters.codex import _content_text, _find_state_db, _title_column

ROOT = Path(__file__).resolve().parent
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
PROTECTED = ("project_id", "cwd", "thread_section_id", "section_position",
             "section_entered_at_ms", "is_pinned", "archived", "history_mode", "rollout_path")


def identifier(value):
    return str(uuid.UUID(str(value)))


def digest(text):
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


def read_thread(thread_id, db=None):
    db = Path(db) if db else _find_state_db()
    if db is None:
        raise ValueError("No local Codex database")
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
        if row is None:
            raise ValueError("Thread not persisted yet")
        return db.resolve(), dict(row)


def first_turn(path, thread_id, turn_id, prompt_hash, *, claiming=False):
    """Fail closed on old, compacted, forked, partial or unfamiliar transcripts.

    Only open the canonical database path within CODEX_HOME/sessions. Hook-supplied
    transcript paths are never opened. Read at most 2 MiB, never scan history trees.
    """
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(home / "sessions") or path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        return False
    meta_seen = False
    prompt_seen = False
    with path.open("rb") as file:
        data = file.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(data) > MAX_TRANSCRIPT_BYTES:
        return False
    for line in data.decode("utf-8").splitlines():
        record = json.loads(line)
        payload = record.get("payload", {})
        kind = record.get("type")
        if kind == "session_meta":
            if meta_seen or (payload.get("id") or payload.get("session_id")) != thread_id:
                return False
            if (payload.get("forked_from_id") or payload.get("forked_from")
                    or "subagent" in json.dumps(payload.get("source", "")).lower()):
                return False
            meta_seen = True
        elif kind in ("compacted", "context_compacted"):
            return False
        elif kind == "turn_context" or (kind == "event_msg"
                                        and payload.get("type") == "task_started"):
            if payload.get("turn_id") != turn_id:
                return False
        elif kind == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
            return False
        elif kind == "response_item" and payload.get("role") == "assistant":
            if not claiming:
                return False
        elif kind == "response_item" and payload.get("role") == "user":
            text = _content_text(payload.get("content")).strip()
            if text.startswith(("# AGENTS.md instructions", "<environment_context>",
                                "<external_codex_apps_open_page>")):
                continue
            if prompt_seen or digest(text) != prompt_hash:
                return False
            prompt_seen = True
    return meta_seen


def state_db(runtime):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(runtime / "first-prompt.sqlite", timeout=0.3)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS sessions (thread_id TEXT PRIMARY KEY, "
                "turn_id TEXT NOT NULL,status TEXT NOT NULL,db TEXT NOT NULL,"
                "snapshot TEXT NOT NULL,title_hashes TEXT NOT NULL,created_at REAL NOT NULL)")
    return con


def eligible(row, prompt, settings):
    if row.get("archived") or row.get("is_pinned"):
        return False
    if "subagent" in str(row.get("source", "")).lower():
        return False
    created = row.get("created_at_ms")
    if created is None:
        created = (row.get("created_at") or 0) * 1000
    if created / 1000 < settings.get("first_prompt_installed_at", time.time()):
        return False
    return is_default_title(row.get(_title_column(row)), prompt)


def instructions(thread_id, turn_id):
    python = Path(sys.executable).with_name("python.exe")
    script = ROOT / "first_prompt.py"
    command = f'"{python}" -X utf8 "{script}" --claim {thread_id} {turn_id} --title <标题>'
    return (
        f"[Codex Chat Auto Title v2 — 仅首轮 {turn_id} 有效]\n"
        "先正常完成用户的第一项任务，不要为命名打断任务。仅在本轮最终回复前，"
        "将当前会话命名一次，格式为「模块名|持续目标」，实际标题不带外层括号，"
        "包括分隔符在内不超过24个Unicode字符。模块用用户看得懂的功能或业务名；"
        "持续目标描述此会话从头到尾要完成的结果，不写当前步骤，不使用“优化”、"
        "“处理问题”、“继续”等空泛目标。例如“登录|完成微信授权登录”。\n"
        f"调用前先运行本地许可检查（标题作为安全引用的数据参数）：{command}\n"
        "仅当该命令返回 allowed=true，才使用返回的 title 调用可用的 "
        "set_thread_title（可能带工具名前缀），仅修改当前会话，省略 threadId。"
        "不得修改项目、目录、分组、置顶或时间字段。用户已手动命名、下一轮已开始、"
        "许可失败、工具不可用或任务被中止时，静默跳过，不用其他写库/后台方式补救。"
        "不要发现或调用其他聊天管理工具，不启动subagent或额外模型请求。"
        "命名只在此轮执行一次；之后追问不得依据这段规则再命名。"
        "不在回复里解释命名，不调用工具重试。\n"
        "这段规则只授权标题修改；任务范围和用户的更明确指令优先。"
    )


def submit(payload, runtime, settings):
    if (not settings.get("enabled", True) or settings.get("mode") != "first_prompt"
            or payload.get("hook_event_name") != "UserPromptSubmit"
            or payload.get("agent_id") or payload.get("agent_type")
            or not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip()):
        return {}
    tid, turn = identifier(payload.get("session_id")), identifier(payload.get("turn_id"))
    con = state_db(runtime)
    try:
        if con.execute("SELECT 1 FROM sessions WHERE thread_id=?", (tid,)).fetchone():
            return {}
        db, row = read_thread(tid)
        if not eligible(row, payload["prompt"], settings):
            return {}
        prompt_hash = digest(payload["prompt"])
        if not first_turn(row.get("rollout_path"), tid, turn, prompt_hash):
            return {}
        snapshot = {k: row[k] for k in PROTECTED if k in row}
        snapshot["display_title"] = row.get(_title_column(row))
        snapshot["prompt_hash"] = prompt_hash
        hashes = {digest(t) for t in default_title_candidates(payload["prompt"])}
        hashes.add(digest(row.get(_title_column(row))))
        with con:
            result = con.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?,?)",
                                 (tid, turn, "issued", str(db), json.dumps(snapshot),
                                  json.dumps(sorted(hashes)), time.time()))
        if result.rowcount != 1:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": instructions(tid, turn)}}
    finally:
        con.close()


def valid_title(title):
    if not isinstance(title, str) or not 3 <= len(title) <= 24 or title.count("|") != 1:
        return False
    if any(ord(c) < 32 or c in "「」[]" for c in title):
        return False
    module, goal = title.split("|")
    return (bool(module.strip()) and bool(goal.strip()) and title == title.strip()
            and goal.strip() not in {
                "优化", "处理问题", "修复问题", "继续", "完善", "修改", "测试"})


def claim(thread_id, turn_id, title, runtime, settings):
    """Consume before the native call: failures never schedule a retry.

    This is a preflight, not an atomic transaction with the desktop title API.
    A manual rename after this check and before the API call remains a race.
    """
    if (not settings.get("enabled", True) or settings.get("mode") != "first_prompt"
            or not valid_title(title)):
        return {"allowed": False}
    tid, turn = identifier(thread_id), identifier(turn_id)
    con = state_db(runtime)
    try:
        with con:
            con.execute("BEGIN IMMEDIATE")
            ticket = con.execute("SELECT * FROM sessions WHERE thread_id=?", (tid,)).fetchone()
            if ticket is None or ticket["status"] != "issued" or ticket["turn_id"] != turn:
                return {"allowed": False}
            con.execute("UPDATE sessions SET status='consumed' WHERE thread_id=?", (tid,))
            _, row = read_thread(tid, ticket["db"])
            before = json.loads(ticket["snapshot"])
            same = all(row.get(k) == v for k, v in before.items()
                       if k not in ("display_title", "prompt_hash"))
            title_ok = digest(row.get(_title_column(row))) in json.loads(ticket["title_hashes"])
            turn_ok = first_turn(row.get("rollout_path"), tid, turn, before["prompt_hash"],
                                 claiming=True)
            return {"allowed": same and title_ok and turn_ok, "title": title}
    finally:
        con.close()


def main():
    # pythonw has no streams. Do not configure an async hook: stdout is required
    # for delivery before the first model request. Windows host hides hook consoles.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    runtime = Path(os.environ.get("RENAME_RUNTIME_DIR") or ROOT / "runtime")
    result = {}
    try:
        settings = json.loads((runtime / "background-settings.json").read_text("utf-8"))
        if "--claim" in sys.argv:
            parser = argparse.ArgumentParser()
            parser.add_argument("--claim", nargs=2, required=True)
            parser.add_argument("--title", required=True)
            args = parser.parse_args()
            result = claim(*args.claim, args.title, runtime, settings)
        else:
            raw = sys.stdin.read(1024 * 1024 + 1)
            payload = json.loads(raw) if len(raw) <= 1024 * 1024 else None
            if isinstance(payload, dict):
                result = submit(payload, runtime, settings)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error):
        # No model request, transcript logging, warning or exception popup.
        result = {"allowed": False} if "--claim" in sys.argv else {}
    if sys.stdout is not None:
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
